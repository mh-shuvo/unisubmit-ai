"""Regression tests for the validation queue, uploads and AI-service routing.

DEF-024: the validation worker ran inside the web process and froze every page.
DEF-025: async routes ran synchronous database work on the event loop.
DEF-026: two quick uploads created duplicate versions and duplicate GPU jobs.
DEF-028: the web startup created an admin account with a published password.
DEF-032: Qdrant collections were named from display names, which collide.
DEF-034: a job interrupted mid-run stayed PROCESSING forever.
DEF-035: a stale per-profile AI service URL overrode the configured one.
DEF-036: the worker swallowed failures, so a broken job vanished from the logs.
"""
import asyncio
import inspect
import io
import os
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import support  # noqa: F401  sets the test environment before the app is imported
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

from support import (
    TEST_AI_TOKEN,
    AppTestCase,
    add_version,
    build_world,
    pdf_bytes,
    web,
)
from unisubmit.config import settings
from unisubmit.db import Base, get_db
from unisubmit.models import AIJob, AdminUser, Submission, SubmissionVersion, TemplateVersion


class WebProcessTests(AppTestCase):
    def test_web_startup_does_not_run_validation_jobs(self):
        """DEF-024: only the separate worker process may take jobs off the queue."""
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, "QUEUED")

        with self.client():
            time.sleep(0.5)

        with self.Session() as db:
            self.assertEqual(db.get(AIJob, job_id).status, "QUEUED")

    def test_web_startup_does_not_create_an_admin_account(self):
        """DEF-028: no account with a published password appears on startup."""
        with self.Session() as db:
            db.query(AdminUser).delete()
            db.commit()

        with self.client():
            pass

        with self.Session() as db:
            self.assertEqual(db.query(AdminUser).count(), 0)

    def test_async_routes_do_not_use_the_database_on_the_event_loop(self):
        """DEF-025: a route that holds a sync session must be a plain `def`.

        `index_guideline` still awaits the AI service for minutes inside the
        request; moving that work to the queue is DEF-038.
        """
        allowed = {"index_guideline"}
        offenders = sorted(
            route.endpoint.__name__
            for route in web.app.routes
            if isinstance(route, APIRoute)
            and inspect.iscoroutinefunction(route.endpoint)
            and any(dep.call is get_db for dep in route.dependant.dependencies)
            and route.endpoint.__name__ not in allowed
        )
        self.assertEqual(offenders, [])


class UploadTests(AppTestCase):
    def upload(self, client):
        return client.post(
            f"/submission/{self.world.submission_id}/resubmit",
            files={"pdf": ("revised.pdf", pdf_bytes("Revised"), "application/pdf")},
        )

    def versions(self):
        with self.Session() as db:
            return sorted(
                v.version_no
                for v in db.query(SubmissionVersion).filter_by(
                    submission_id=self.world.submission_id
                )
            )

    def test_upload_while_validation_active_creates_no_version(self):
        """DEF-026: a second click while a job is queued must not add a version."""
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, "QUEUED")

        response = self.upload(self.logged_in_student())

        self.assertEqual(self.versions(), [1])
        self.assertIn("error=", response.headers["location"])

    def test_upload_after_validation_finished_creates_next_version(self):
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, "COMPLETED")

        self.upload(self.logged_in_student())

        self.assertEqual(self.versions(), [1, 2])


@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "set TEST_POSTGRES_URL to a disposable PostgreSQL database to run",
)
class ConcurrentUploadTests(unittest.TestCase):
    """DEF-026 under real row locking; SQLite cannot express FOR UPDATE."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.engine = create_engine(os.environ["TEST_POSTGRES_URL"])
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.addCleanup(Base.metadata.drop_all, self.engine)
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        with self.Session() as db:
            self.world = build_world(db, self.tmp)
            add_version(db, self.world.submission_id, self.tmp, "COMPLETED")

    def test_concurrent_uploads_never_share_a_version_number(self):
        barrier = threading.Barrier(2)
        errors = []

        def upload():
            with self.Session() as db:
                submission = db.get(Submission, self.world.submission_id)
                _ = submission.versions  # load before racing, as the route does
                pdf = UploadFile(io.BytesIO(pdf_bytes("Concurrent")), filename="c.pdf")
                barrier.wait()
                try:
                    result = web.save_queue_version(submission, pdf, db)
                    if inspect.isawaitable(result):
                        asyncio.run(result)
                except Exception as exc:  # recorded, asserted below
                    errors.append(exc)

        with patch.object(web, "SUBMISSION_STORE", self.tmp):
            threads = [threading.Thread(target=upload) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        with self.Session() as db:
            numbers = [
                v.version_no
                for v in db.query(SubmissionVersion).filter_by(
                    submission_id=self.world.submission_id
                )
            ]
        self.assertEqual(sorted(numbers), [1, 2])


class WorkerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from unisubmit import worker

        self.worker = worker
        session_patch = patch.object(worker, "SessionLocal", self.Session)
        session_patch.start()
        self.addCleanup(session_patch.stop)

    def job(self, job_id):
        with self.Session() as db:
            job = db.get(AIJob, job_id)
            return job.status, job.attempt_count, job.error_message

    def test_worker_waits_for_the_schema_instead_of_creating_it(self):
        """DEF-045: two processes creating tables at once crashed the worker."""
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.pool import StaticPool

        empty = create_engine("sqlite://", poolclass=StaticPool,
                              connect_args={"check_same_thread": False})

        async def start_then_stop():
            task = asyncio.create_task(self.worker.main())
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with patch.object(self.worker, "engine", empty), \
                patch.object(self.worker, "POLL_SECONDS", 0.01):
            asyncio.run(start_then_stop())

        self.assertEqual(sa_inspect(empty).get_table_names(), [])

    def test_worker_completes_a_queued_job(self):
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, "QUEUED")

        with patch.object(self.worker, "process_job", new=AsyncMock()):
            ran = asyncio.run(self.worker.run_once())

        self.assertTrue(ran)
        self.assertEqual(self.job(job_id)[:2], ("COMPLETED", 1))

    def test_job_left_processing_by_a_dead_worker_is_requeued(self):
        """DEF-034: a job whose worker died is put back in the queue."""
        long_ago = datetime.utcnow() - self.worker.STALE_AFTER - timedelta(minutes=1)
        with self.Session() as db:
            job_id = add_version(
                db, self.world.submission_id, self.tmp, "PROCESSING",
                started_at=long_ago, attempt_count=1,
            )
            requeued = self.worker.requeue_stale_jobs(db)

        self.assertEqual((requeued, self.job(job_id)[0]), (1, "RETRYING"))

    def test_recently_started_job_is_not_requeued(self):
        """DEF-034: a job another worker is still running is left alone."""
        with self.Session() as db:
            job_id = add_version(
                db, self.world.submission_id, self.tmp, "PROCESSING",
                started_at=datetime.utcnow(), attempt_count=1,
            )
            self.worker.requeue_stale_jobs(db)

        self.assertEqual(self.job(job_id)[0], "PROCESSING")

    def test_interrupted_job_is_requeued_without_using_an_attempt(self):
        """DEF-034: stopping the worker mid-job puts the job back, attempt uncounted."""
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, "QUEUED")

        interrupted = AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(self.worker, "process_job", new=interrupted):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.worker.run_once())

        self.assertEqual(self.job(job_id)[:2], ("RETRYING", 0))

    def test_failure_after_a_database_error_is_recorded_and_logged(self):
        """DEF-036: a failed flush inside the job must not leave it PROCESSING silently."""
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, "QUEUED")

        async def breaks_the_session(db, job):
            # A second job for the same version violates the unique constraint.
            db.add(AIJob(submission_version_id=job.submission_version_id))
            db.flush()

        with patch.object(self.worker, "process_job", new=breaks_the_session), \
                self.assertLogs("unisubmit.worker", level="ERROR"):
            asyncio.run(self.worker.run_once())

        status, attempts, error = self.job(job_id)
        self.assertEqual((status, attempts), ("RETRYING", 1))
        self.assertIn("UNIQUE", error.upper())


class AIServiceRoutingTests(AppTestCase):
    def validate_payload(self):
        """Run the real process_job once and return what it sent to the AI service."""
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, "PROCESSING")
        rag = AsyncMock(return_value={"response": "ok", "run_id": "r1", "semantic_results": []})
        no_email = patch.object(web, "send_submission_email", return_value=(False, "test"))
        with patch.object(web, "rag_validate", new=rag), no_email, self.Session() as db:
            asyncio.run(web.process_job(db, db.get(AIJob, job_id)))
        return rag.await_args.args

    def test_validation_uses_the_configured_ai_service_url(self):
        """DEF-035: the per-profile URL must not override AI_SERVICE_URL."""
        url, _ = self.validate_payload()
        self.assertEqual(url, settings.ai_service_url)

    def test_validation_sends_the_collection_recorded_at_indexing(self):
        """DEF-032: validation reads the collection indexing created, by name on record."""
        _, payload = self.validate_payload()
        self.assertEqual(payload.get("collection"), "tv_legacy_name")

    def test_guideline_indexing_uses_the_configured_url_and_an_id_based_collection(self):
        """DEF-032 and DEF-035, on the indexing path."""
        parse = AsyncMock(return_value={"status": "success", "profile": {}})
        index = AsyncMock(return_value={"status": "success", "collection": "ignored"})
        tv_id = self.world.template_version_id
        with patch.object(web, "parse_guideline_profile", new=parse), \
                patch.object(web, "index_template", new=index):
            self.logged_in_admin().post(f"/admin/template-version/{tv_id}/index")

        self.assertEqual(parse.await_args.args[0], settings.ai_service_url)
        self.assertEqual(index.await_args.args[1].get("collection"), f"tv_{tv_id}")


class AIClientAuthTests(unittest.TestCase):
    """DEF-029: every call to the AI service carries the shared token."""

    def test_every_ai_call_sends_the_service_token(self):
        import tempfile

        import httpx

        from unisubmit import ai_service_client as client

        seen = []

        def handler(request):
            seen.append((request.url.path, request.headers.get("authorization")))
            return httpx.Response(200, json={"status": "success"})

        real_async, real_sync = httpx.AsyncClient, httpx.Client

        def async_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async(*args, **kwargs)

        def sync_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_sync(*args, **kwargs)

        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf, \
                patch.object(client.httpx, "AsyncClient", async_factory), \
                patch.object(client.httpx, "Client", sync_factory):
            url = "http://ai.test"
            asyncio.run(client.rag_validate(url, {}))
            asyncio.run(client.index_template(url, {}, pdf.name))
            asyncio.run(client.extract_format_profile(url, {}))
            asyncio.run(client.parse_guideline_profile(url, {}, pdf.name))
            health = client.health(url)
            if inspect.isawaitable(health):
                asyncio.run(health)

        expected = f"Bearer {TEST_AI_TOKEN}"
        self.assertEqual(
            [path for path, auth in seen if auth != expected and path != "/health"], []
        )


if __name__ == "__main__":
    unittest.main()
