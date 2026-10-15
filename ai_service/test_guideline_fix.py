import asyncio
import json
import unittest
from unittest.mock import patch, AsyncMock

import httpx
import main as service


class GuidelineFixTests(unittest.TestCase):
    def result(self, page=1):
        return json.dumps({'deterministic_rules': [dict(type='section_max_words',
            target='Abstract', value=300, mandatory=True, source_page=999)],
            'semantic_requirements': [dict(requirement='Explain the background.', source_section='Abstract')]})

    def test_long_document_not_truncated_and_page_provenance(self):
        pages = [dict(page=i, text=('guideline content ' * 210)) for i in range(1, 23)]
        with patch.object(service, 'call_ollama', new=AsyncMock(return_value=self.result())) as llm:
            result, diag = asyncio.run(service._parse_guideline_chunks(pages, 'qwen', '{full}'))
        self.assertEqual(diag['total'], diag['succeeded'])
        self.assertGreater(diag['total'], 22)
        self.assertTrue(any('[GUIDELINE PAGE 22]' in call.args[1] for call in llm.call_args_list))
        self.assertNotIn(999, result['semantic_requirements'][0]['source_pages'])
        self.assertIn(22, result['semantic_requirements'][0]['source_pages'])

    def test_truncated_response_splits_source(self):
        with patch.object(service, 'call_ollama', new=AsyncMock(side_effect=[
            service.IncompleteOllamaResponse('length'), self.result(), self.result()])) as llm:
            result, diag = asyncio.run(service._parse_guideline_chunks(
                [dict(page=3, text='text ' * 250)], 'qwen', '{full}'))
        self.assertEqual(llm.await_count, 3)
        self.assertEqual(diag['succeeded'], 1)
        self.assertEqual(result['deterministic_rules'][0]['source_page'], 3)

    def test_malformed_schema_gets_repaired(self):
        with patch.object(service, 'call_ollama', new=AsyncMock(side_effect=[
            '{"deterministic_rules":{}}', self.result()])) as llm:
            result, diag = asyncio.run(service._parse_guideline_chunks(
                [dict(page=1, text='short text')], 'qwen', '{full}'))
        self.assertEqual(llm.await_count, 2)
        self.assertEqual(diag['succeeded'], 1)

    def test_partial_success_retained_and_reported(self):
        with patch.object(service, 'call_ollama', new=AsyncMock(side_effect=[
            self.result(), RuntimeError('Ollama offline')])):
            result, diag = asyncio.run(service._parse_guideline_chunks(
                [dict(page=1, text='first page'), dict(page=2, text='second page')], 'qwen', '{full}'))
        self.assertEqual(diag['succeeded'], 1)
        self.assertEqual(diag['total'], 2)
        self.assertIn('Page 2', diag['errors'][0])
        self.assertTrue(result['deterministic_rules'])

    def test_abstract_word_range_fallback(self):
        for text in ['Abstract\nWrite between 250 and 300 words.',
                     'ABSTRACT\nLength: 250—300 words.', 'Abstract: 250 to 300 words.']:
            rules = service._standard_text_rule_fallback([dict(page=2, text=text)])['deterministic_rules']
            self.assertEqual({r['type']:r['value'] for r in rules if r['type'].startswith('section_')},
                {'section_min_words':250, 'section_max_words':300})

    def test_ollama_request_budget_and_truncation(self):
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'response':self.result(), 'done':True, 'done_reason':'length'})
        original = httpx.AsyncClient
        with patch.object(service.httpx, 'AsyncClient', side_effect=lambda **kw:
                          original(transport=httpx.MockTransport(handle), **kw)):
            with self.assertRaises(service.IncompleteOllamaResponse):
                asyncio.run(service.call_ollama('qwen', 'prompt', json_mode=True))
        self.assertFalse(captured[0]['think'])
        self.assertGreaterEqual(captured[0]['options']['num_ctx'], 8192)
        self.assertGreaterEqual(captured[0]['options']['num_predict'], 4096)

    def test_pdf_endpoint_diagnostics(self):
        doc = service.fitz.open()
        doc.new_page().insert_text((72,72), 'Abstract: 250 to 300 words.')
        pdf = doc.tobytes()
        doc.close()
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url='http://test') as c:
                return await c.post('/guideline/profile',
                    data=dict(department='CSE', program='BSc', submission_type='Thesis', template_version='1'),
                    files={'pdf':('guideline.pdf', pdf, 'application/pdf')})
        with patch.object(service, 'call_ollama', new=AsyncMock(return_value=self.result())):
            response = asyncio.run(send())
        self.assertEqual(response.status_code, 200, response.text)
        diag = response.json()['profile']['parser_diagnostics']
        self.assertTrue(diag['qwen_json_parsed'])
        self.assertEqual(diag['qwen_chunks_succeeded'], 1)
        self.assertTrue(diag['coverage_ok'])


if __name__ == '__main__':
    unittest.main()
