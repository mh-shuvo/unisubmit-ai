"""Offline regression tests for AI-service hardening. No model, Qdrant or Ollama.

DEF-029: every route except the liveness probes requires the shared token.
DEF-032: callers name the collection; names derived from display names collide.
DEF-033: embedding and Qdrant calls must not block the event loop.
LIM-008: the embedding device can be chosen and is reported.
"""
import asyncio
import json
import sys
import threading
import time
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from unisubmit_ai import main as service

TOKEN = "test-ai-token-" + "y" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(coro):
    return asyncio.run(coro)


async def call(method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service.app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


class TokenTests(unittest.TestCase):
    """DEF-029"""

    def setUp(self):
        patcher = patch.object(service, "AI_SERVICE_TOKEN", TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_without_token_is_rejected(self):
        self.assertEqual(run(call("GET", "/rag/collections")).status_code, 401)

    def test_destructive_request_without_token_is_rejected(self):
        with patch.object(service, "qdrant_client", MagicMock()) as qdrant, \
                patch.object(service, "qdrant_status", "ready"):
            response = run(call("DELETE", "/rag/collection/tv_1"))
        self.assertEqual((response.status_code, qdrant.delete_collection.called), (401, False))

    def test_wrong_token_is_rejected(self):
        response = run(call("GET", "/rag/collections",
                            headers={"Authorization": "Bearer wrong"}))
        self.assertEqual(response.status_code, 401)

    def test_correct_token_reaches_the_route(self):
        # Qdrant is not loaded offline, so the route itself answers 503.
        self.assertEqual(run(call("GET", "/rag/collections", headers=AUTH)).status_code, 503)

    def test_liveness_probe_needs_no_token(self):
        self.assertEqual(run(call("GET", "/")).status_code, 200)

    def test_unset_token_fails_closed(self):
        with patch.object(service, "AI_SERVICE_TOKEN", None):
            response = run(call("GET", "/rag/collections", headers=AUTH))
        self.assertEqual(response.status_code, 503)


VALID = json.dumps(dict(semantic_results=[dict(semantic_id="SEM-001", status="PASS")],
                        improvement_suggestions=[], grounding_note="Approved source"))
HITS = [dict(page=1, score=0.9, text="Abstract must include background.")]
VALIDATE = dict(department="CSE", program="M.Sc. (CSE)", submission_type="Thesis",
                template_version="1.0", student_text="ABSTRACT\nBackground.\n",
                explicit_requirements=[dict(semantic_id="SEM-001",
                                            requirement="Abstract must include background.",
                                            target_section="Abstract")])


class CollectionNameTests(unittest.TestCase):
    """DEF-032"""

    def setUp(self):
        patcher = patch.object(service, "AI_SERVICE_TOKEN", TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def index(self, **fields):
        form = dict(department="CSE", program="M.Sc. (CSE)", submission_type="Thesis",
                    template_version="1.0", **fields)
        with patch.object(service, "extract_pdf_pages", return_value=[{"page": 1, "text": "Rule."}]), \
                patch.object(service, "ensure_collection") as ensure, \
                patch.object(service, "embed_texts", return_value=[[0.0] * 4]), \
                patch.object(service, "qdrant_client", MagicMock()):
            response = run(call("POST", "/rag/index-template", data=form, headers=AUTH,
                                files={"pdf": ("g.pdf", b"%PDF-1.4", "application/pdf")}))
        return response, ensure

    def test_programs_with_colliding_names_get_separate_collections(self):
        """Two programs whose names slugify alike used to overwrite each other's index."""
        self.assertEqual(service.make_collection_name("CSE", "M.Sc. (CSE)", "Thesis", "1.0"),
                         service.make_collection_name("CSE", "M.Sc. CSE", "Thesis", "1.0"))
        first, ensure_first = self.index(collection="tv_7")
        second, ensure_second = self.index(collection="tv_8")
        self.assertEqual(
            [ensure_first.call_args.args[0], ensure_second.call_args.args[0],
             first.json()["collection"], second.json()["collection"]],
            ["tv_7", "tv_8", "tv_7", "tv_8"],
        )

    def test_validation_reads_the_named_collection(self):
        with patch.object(service, "retrieve_chunks", return_value=HITS) as retrieve, \
                patch.object(service, "call_ollama", new=AsyncMock(return_value=VALID)):
            response = run(call("POST", "/rag/validate", headers=AUTH,
                                json=dict(VALIDATE, collection="tv_7")))
        self.assertEqual((response.status_code, retrieve.call_args.args[0]), (200, "tv_7"))

    def test_unsafe_collection_name_is_rejected(self):
        response, ensure = self.index(collection="../other")
        self.assertEqual((response.status_code, ensure.called), (422, False))

    def test_name_derived_collection_still_works_without_a_name(self):
        """Callers that predate DEF-032 keep their existing collections."""
        response, ensure = self.index()
        self.assertEqual(ensure.call_args.args[0],
                         service.make_collection_name("CSE", "M.Sc. (CSE)", "Thesis", "1.0"))


class EventLoopTests(unittest.TestCase):
    """DEF-033: the liveness probe answers while a validation is retrieving context."""

    def setUp(self):
        patcher = patch.object(service, "AI_SERVICE_TOKEN", TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_probe_answers_while_retrieval_is_running(self):
        retrieval_started = threading.Event()
        started_at = {}

        def slow_retrieve(*args, **kwargs):
            if not retrieval_started.is_set():
                started_at["t"] = time.perf_counter()
                retrieval_started.set()
            time.sleep(0.6)  # stands in for embedding plus a Qdrant query
            return HITS

        async def probe(client):
            # Timed from the moment retrieval starts: a blocked loop cannot
            # answer until the blocking call returns.
            while not retrieval_started.is_set():
                await asyncio.sleep(0.005)
            await client.get("/")
            return time.perf_counter() - started_at["t"]

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service.app), base_url="http://test"
            ) as client:
                validation = asyncio.create_task(
                    client.post("/rag/validate", json=VALIDATE, headers=AUTH)
                )
                elapsed = await probe(client)
                await validation
                return elapsed

        with patch.object(service, "retrieve_chunks", side_effect=slow_retrieve), \
                patch.object(service, "call_ollama", new=AsyncMock(return_value=VALID)):
            elapsed = run(scenario())
        self.assertLess(elapsed, 0.3)


class EmbeddingDeviceTests(unittest.TestCase):
    """LIM-008: the device is configurable and reported, not guessed at."""

    def load(self, device):
        created = {}

        class FakeModel:
            def __init__(self, name, **kwargs):
                created.update(kwargs)
                self.target_devices = [kwargs.get("devices") or "mps:0"]

        fake = ModuleType("FlagEmbedding")
        fake.BGEM3FlagModel = FakeModel
        with patch.dict(sys.modules, {"FlagEmbedding": fake}), \
                patch.object(service, "EMBEDDING_DEVICE", device), \
                patch.object(service, "embedding_model", None), \
                patch.object(service, "embedding_status", "not_loaded"):
            ok, detail = service._check_embedding_sync()
            reported = service.embedding_device()
        return ok, created, reported

    def test_configured_device_is_passed_to_the_model(self):
        ok, created, reported = self.load("cpu")
        self.assertEqual((ok, created.get("devices"), reported), (True, "cpu", "cpu"))

    def test_unset_device_lets_the_library_choose_and_reports_it(self):
        ok, created, reported = self.load(None)
        self.assertEqual((ok, created.get("devices"), reported), (True, None, "mps:0"))


if __name__ == "__main__":
    unittest.main()
