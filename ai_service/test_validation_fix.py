"""Offline regression tests. No model download, live server, or database changes."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx
import main as service


class OfflineClient:
    def post(self, path, json):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                                         base_url='http://test') as client:
                return await client.post(path, json=json)
        return asyncio.run(send())


class ValidationFixTests(unittest.TestCase):
    def setUp(self):
        self.client = OfflineClient()  # Deliberately do not run startup.
        self.request = dict(department='CSE', program='MSc', submission_type='Thesis',
                            template_version='v1', student_text='ABSTRACT\nBackground and motivation.\n',
                            explicit_requirements=[dict(semantic_id='SEM-001',
                                requirement='Abstract must include background.', target_section='Abstract',
                                source_pages=[1, 2], manual=True, expected_evidence='Background')])
        self.hits = [dict(page=1, score=0.9, text='Abstract must include background.')]
        self.valid = json.dumps(dict(semantic_results=[dict(semantic_id='SEM-001', status='PASS')],
                                    improvement_suggestions=[], grounding_note='Approved source'))

    def submit(self, outputs):
        with patch.object(service, 'retrieve_chunks', return_value=self.hits), \
             patch.object(service, 'call_ollama', new=AsyncMock(side_effect=outputs)) as llm:
            response = self.client.post('/rag/validate', json=self.request)
            return response, llm

    def test_qwen_thinking_fence_and_preamble(self):
        for text in ['<think>reasoning</think>\n```json\n'+self.valid+'\n```',
                     'Here is the result:\n'+self.valid+'\nDone.']:
            response, llm = self.submit([text])
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()['semantic_results'][0]
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['source_pages'], [1, 2])
            self.assertEqual(result['source_type'], 'Manual Semantic Rule')
            self.assertEqual(llm.await_count, 1)
            self.assertIn('"expected_evidence": "Background"', llm.call_args.args[1])

    def test_invalid_shape_repaired_once(self):
        for invalid in ['[]', 'null', '{"semantic_results":{}}',
                        '{"semantic_results":[],"improvement_suggestions":"text"}', 'broken']:
            response, llm = self.submit([invalid, self.valid])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(llm.await_count, 2)

    def test_invalid_repair_returns_502(self):
        response, llm = self.submit(['broken', '[]'])
        self.assertEqual(response.status_code, 502)
        self.assertIn('JSON invalid after one repair', response.json()['detail'])

    def test_repair_service_failure_returns_503(self):
        response, llm = self.submit(['broken', RuntimeError('model unavailable')])
        self.assertEqual(response.status_code, 503)

    def test_missing_model_result_remains_not_verified(self):
        response, _ = self.submit(['{"semantic_results":[]}'])
        self.assertEqual(response.json()['semantic_results'][0]['status'], 'NOT VERIFIED')
        self.assertEqual(response.json()['semantic_overall_status'], 'PARTIAL')

    def test_warning_and_dedup_and_scalar_pages(self):
        req = self.request['explicit_requirements'][0]
        req.update(final_severity='warning', source_pages=1)
        self.request['explicit_requirements'].append(dict(req, source_pages=[2]))
        response, _ = self.submit([self.valid.replace('PASS', 'FAIL')])
        results = response.json()['semantic_results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'WARNING')
        self.assertEqual(results[0]['source_pages'], [1, 2])

    def test_duplicate_ids_assigned_unique_ids(self):
        self.request['explicit_requirements'].append(dict(self.request['explicit_requirements'][0],
                                                        requirement='Abstract must state motivation.'))
        response, _ = self.submit([self.valid])
        results = response.json()['semantic_results']
        self.assertEqual(len({r['semantic_id'] for r in results}), 2)
        self.assertEqual(results[1]['status'], 'NOT VERIFIED')

    def test_request_bounds_and_severity(self):
        self.request['limit'] = 0
        self.assertEqual(self.client.post('/rag/validate', json=self.request).status_code, 422)
        self.request['limit'] = 6
        self.request['explicit_requirements'][0]['final_severity'] = 'bogus'
        self.assertEqual(self.client.post('/rag/validate', json=self.request).status_code, 422)

    def test_retrieval_failure_is_logged_and_503(self):
        with patch.object(service, 'retrieve_chunks', side_effect=RuntimeError('embedding unavailable')), \
             self.assertLogs('uvicorn.error', level='ERROR'):
            response = self.client.post('/rag/validate', json=self.request)
        self.assertEqual(response.status_code, 503)

    def test_modern_qdrant(self):
        point = SimpleNamespace(score=0.9, payload={'text':'guideline', 'page':1})
        qdrant = SimpleNamespace(query_points=lambda **kw: SimpleNamespace(points=[point]))
        with patch.object(service, 'qdrant_client', qdrant), patch.object(service, 'qdrant_status', 'ready'), \
             patch.object(service, 'embed_texts', return_value=[[1.0, 0.0]]):
            self.assertEqual(service.retrieve_chunks('c', 'query')[0]['text'], 'guideline')

    def test_legacy_rest_with_new_client(self):
        class Unsupported(Exception):
            status_code = 404
        qdrant = SimpleNamespace(query_points=lambda **kw: (_ for _ in ()).throw(Unsupported()),
                                 get_collection=lambda **kw: None)
        response = httpx.Response(200, request=httpx.Request('POST', 'http://qdrant'),
                                  json={'result':[dict(id=1, version=0, score=0.9,
                                                     payload={'text':'legacy', 'page':1})]})
        with patch.object(service, 'qdrant_client', qdrant), patch.object(service, 'qdrant_status', 'ready'), \
             patch.object(service, 'embed_texts', return_value=[[1.0, 0.0]]), \
             patch.object(service.httpx, 'post', return_value=response) as post:
            self.assertEqual(service.retrieve_chunks('c', 'query')[0]['text'], 'legacy')
            self.assertTrue(post.call_args.args[0].endswith('/collections/c/points/search'))

    def test_missing_collection_does_not_fallback(self):
        class Missing(Exception):
            status_code = 404
        qdrant = SimpleNamespace(query_points=lambda **kw: (_ for _ in ()).throw(Missing()),
                                 get_collection=lambda **kw: (_ for _ in ()).throw(Missing()))
        with patch.object(service, 'qdrant_client', qdrant), patch.object(service, 'qdrant_status', 'ready'), \
             patch.object(service, 'embed_texts', return_value=[[1.0]]), \
             patch.object(service.httpx, 'post') as post:
            with self.assertRaises(Missing):
                service.retrieve_chunks('missing', 'query')
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
