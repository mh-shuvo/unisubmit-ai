"""Regression tests for the validation queue, uploads and AI-service routing.

DEF-024: the validation worker ran inside the web process and froze every page.
DEF-025: async routes ran synchronous database work on the event loop.
DEF-026: two quick uploads created duplicate versions and duplicate GPU jobs.
DEF-028: the web startup created an admin account with a published password.
DEF-032: Qdrant collections were named from display names, which collide.
DEF-034: a job interrupted mid-run stayed PROCESSING forever.
DEF-035: a stale per-profile AI service URL overrode the configured one.
DEF-036: the worker swallowed failures, so a broken job vanished from the logs.
DEF-038: guideline parsing and indexing ran inside the admin's HTTP request.
"""

import asyncio
import inspect
import io
import os
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine

from factories import add_version, build_world, pdf_bytes
from support import AppTestCase, api_routes
from unisubmit import handlers, queue
from unisubmit.ai_service_client import AIServiceError
from unisubmit.core.config import get_settings
from unisubmit.core.db import get_session
from unisubmit.main import create_app
from unisubmit.models import (
    AdminUser,
    Job,
    JobStatus,
    JobType,
    Submission,
    SubmissionVersion,
    TemplateVersion,
)
from unisubmit.storage import UploadRefused
from unisubmit.submissions.service import save_queue_version


class WebProcessTests(AppTestCase):
    def test_web_startup_does_not_run_validation_jobs(self):
        """DEF-024: only the separate worker process may take jobs off the queue."""
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, JobStatus.QUEUED)

        with self.client():
            time.sleep(0.5)

        with self.Session() as db:
            self.assertEqual(db.get(Job, job_id).status, JobStatus.QUEUED)

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
        """DEF-025 and DEF-038: a route that holds a sync session must be a plain
        `def`, and none may wait on the AI service."""
        offenders = sorted(
            route.endpoint.__name__
            for route in api_routes(create_app().routes)
            if inspect.iscoroutinefunction(route.endpoint)
            and any(dep.call is get_session for dep in route.dependant.dependencies)
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
            add_version(db, self.world.submission_id, self.tmp, JobStatus.QUEUED)

        response = self.upload(self.logged_in_student())

        self.assertEqual(self.versions(), [1])
        self.assertIn("error=", response.headers["location"])

    def test_upload_after_validation_finished_creates_next_version(self):
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, JobStatus.SUCCEEDED)

        self.upload(self.logged_in_student())

        self.assertEqual(self.versions(), [1, 2])


class ConcurrentUploadTests(unittest.TestCase):
    """DEF-026 and DEF-046: two connections racing on the real row lock."""

    @pytest.fixture(autouse=True)
    def _database(self, committed_session_factory, tmp_path):
        self.Session = committed_session_factory
        self.tmp = tmp_path

    def setUp(self):
        with self.Session() as db:
            self.world = build_world(db, self.tmp)
            add_version(db, self.world.submission_id, self.tmp, JobStatus.SUCCEEDED)

    def test_concurrent_uploads_never_share_a_version_number(self):
        barrier = threading.Barrier(2)
        errors = []

        def upload():
            with self.Session() as db:
                submission = db.get(Submission, self.world.submission_id)
                _ = submission.versions  # load before racing, as the route does
                pdf = io.BytesIO(pdf_bytes("Concurrent"))
                barrier.wait()
                try:
                    save_queue_version(submission, pdf, db)
                except UploadRefused:
                    pass  # the loser is told the first upload is still being validated
                except Exception as exc:  # recorded, asserted below
                    errors.append(exc)

        with patch.dict(os.environ, {"UNISUBMIT_STORAGE_DIR": str(self.tmp)}):
            get_settings.cache_clear()
            self.addCleanup(get_settings.cache_clear)
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


class FakeHandler:
    """Stands in for a real handler; `run` is an AsyncMock."""

    def __init__(self, run=None):
        self.run = run or AsyncMock(return_value=[])
        self.final_failures = []

    def on_final_failure(self, payload):
        self.final_failures.append(payload)


class WorkerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from unisubmit import worker

        self.worker = worker
        self.handler = FakeHandler()

    def run_once(self):
        handlers = {JobType.VALIDATE_SUBMISSION: self.handler}
        return asyncio.run(
            self.worker.run_once(self.Session, handlers, get_settings(), "test-worker")
        )

    def queued(self):
        with self.Session() as db:
            return add_version(db, self.world.submission_id, self.tmp, JobStatus.QUEUED)

    def job(self, job_id):
        with self.Session() as db:
            job = db.get(Job, job_id)
            return job.status, job.attempts, job.last_error

    def test_worker_waits_for_the_schema_instead_of_creating_it(self):
        """DEF-045: two processes creating tables at once crashed the worker."""
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.pool import StaticPool

        empty = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        async def start_then_stop():
            task = asyncio.create_task(self.worker.main())
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with (
            patch.object(self.worker, "create_db_engine", return_value=empty),
            patch.object(self.worker, "POLL_SECONDS", 0.01),
        ):
            asyncio.run(start_then_stop())

        self.assertEqual(sa_inspect(empty).get_table_names(), [])

    def test_worker_completes_a_queued_job(self):
        job_id = self.queued()

        ran = self.run_once()

        self.assertEqual((ran, self.job(job_id)[:2]), (True, (JobStatus.SUCCEEDED, 1)))

    def test_job_left_running_by_a_dead_worker_is_requeued(self):
        """DEF-034: a job whose worker died is put back in the queue."""
        long_ago = datetime.now(UTC) - timedelta(seconds=get_settings().job_timeout_s + 60)
        with self.Session() as db:
            job_id = add_version(
                db,
                self.world.submission_id,
                self.tmp,
                JobStatus.RUNNING,
                locked_at=long_ago,
                attempts=1,
            )
        requeued = self.worker.requeue_stale_jobs(
            self.Session, {JobType.VALIDATE_SUBMISSION: self.handler}, get_settings()
        )

        self.assertEqual((requeued, self.job(job_id)[0]), (1, JobStatus.QUEUED))

    def test_recently_started_job_is_not_requeued(self):
        """DEF-034: a job another worker is still running is left alone."""
        with self.Session() as db:
            job_id = add_version(
                db,
                self.world.submission_id,
                self.tmp,
                JobStatus.RUNNING,
                locked_at=datetime.now(UTC),
                attempts=1,
            )
        self.worker.requeue_stale_jobs(
            self.Session, {JobType.VALIDATE_SUBMISSION: self.handler}, get_settings()
        )

        self.assertEqual(self.job(job_id)[0], JobStatus.RUNNING)

    def test_interrupted_job_is_requeued_without_using_an_attempt(self):
        """DEF-034: stopping the worker mid-job puts the job back, attempt uncounted."""
        job_id = self.queued()
        self.handler.run.side_effect = asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            self.run_once()

        self.assertEqual(self.job(job_id)[:2], (JobStatus.QUEUED, 0))

    def test_permanent_failure_is_recorded_logged_and_not_retried(self):
        """DEF-036: a failing job must not vanish; a corrupt input is not retried."""
        job_id = self.queued()
        self.handler.run.side_effect = ValueError("not a PDF")

        with self.assertLogs("unisubmit.worker", level="ERROR"):
            self.run_once()

        self.assertEqual(
            (self.job(job_id), len(self.handler.final_failures)),
            ((JobStatus.FAILED, 1, "ValueError: not a PDF"), 1),
        )

    def test_transient_failure_is_retried_later(self):
        job_id = self.queued()
        self.handler.run.side_effect = queue.TransientError("AI service unreachable")

        with self.assertLogs("unisubmit.worker", level="WARNING"):
            self.run_once()

        self.assertEqual((self.job(job_id)[0], self.handler.final_failures), (JobStatus.QUEUED, []))

    def test_follow_up_is_queued_when_the_job_succeeds(self):
        self.queued()
        email = queue.FollowUp(JobType.SEND_EMAIL, "email:1", {"submission_version_id": 1})
        self.handler.run.return_value = [email]

        self.run_once()

        with self.Session() as db:
            subjects = [job.subject_key for job in db.query(Job).filter_by(type=JobType.SEND_EMAIL)]
        self.assertEqual(subjects, ["email:1"])


class ValidationHandlerTests(AppTestCase):
    """P3.4: the real validation handler, with the AI service and SMTP mocked."""

    def setUp(self):
        super().setUp()
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, JobStatus.RUNNING)
            self.payload = dict(db.get(Job, job_id).payload)
        self.handler = handlers.ValidateSubmission(self.Session)

    def validate(self, rag):
        with patch.object(handlers, "rag_validate", new=rag):
            return asyncio.run(self.handler.run(self.payload))

    def ok(self, **result):
        return AsyncMock(
            return_value={"response": "ok", "run_id": "r1", "semantic_results": [], **result}
        )

    def version(self):
        with self.Session() as db:
            version = db.get(SubmissionVersion, self.payload["submission_version_id"])
            return version.status, version.validation_json, version.submission.email_status

    def test_validation_uses_the_configured_ai_service_url(self):
        """DEF-035: the per-profile URL must not override AI_SERVICE_URL."""
        rag = self.ok()
        self.validate(rag)
        self.assertEqual(rag.await_args.args[0], get_settings().ai_service_url)

    def test_validation_sends_the_collection_recorded_at_indexing(self):
        """DEF-032: validation reads the collection indexing created, by name on record."""
        rag = self.ok()
        self.validate(rag)
        self.assertEqual(rag.await_args.args[1].get("collection"), "tv_legacy_name")

    def test_passed_validation_queues_the_supervisor_email(self):
        """P3.6: the email is its own job, so an SMTP failure cannot fail validation."""
        follow_ups = self.validate(self.ok())

        self.assertEqual(
            ([f.type for f in follow_ups], self.version()[0], self.version()[2]),
            ([JobType.SEND_EMAIL], "PASSED", "Queued"),
        )

    def test_running_validation_again_overwrites_its_results(self):
        self.validate(self.ok())
        first = self.version()[1]

        self.validate(self.ok())

        self.assertEqual(self.version()[1], first)

    def test_unreachable_ai_service_is_a_transient_failure(self):
        down = AsyncMock(side_effect=AIServiceError("connection refused", transient=True))
        with self.assertRaises(queue.TransientError):
            self.validate(down)

    def test_rejected_request_is_a_permanent_failure(self):
        rejected = AsyncMock(side_effect=AIServiceError("HTTP 422", transient=False))
        with self.assertRaises(AIServiceError):
            self.validate(rejected)

    def test_final_failure_marks_the_version_failed(self):
        self.handler.on_final_failure(self.payload)
        self.assertEqual(self.version()[0], "VALIDATION FAILED")


class EmailHandlerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        with self.Session() as db:
            job_id = add_version(db, self.world.submission_id, self.tmp, JobStatus.SUCCEEDED)
            version = db.get(
                SubmissionVersion, db.get(Job, job_id).payload["submission_version_id"]
            )
            version.report_path = version.pdf_path  # any existing file serves as the report
            db.commit()
            self.payload = {"submission_version_id": version.id}
        self.handler = handlers.SendSupervisorEmail(self.Session)

    def send(self, result):
        mock = patch.object(handlers, "send_submission_email", side_effect=result)
        with mock as sent:
            asyncio.run(self.handler.run(self.payload))
        return sent

    def email(self):
        with self.Session() as db:
            version = db.get(SubmissionVersion, self.payload["submission_version_id"])
            return version.emailed_at is not None, version.submission.email_status

    def test_sent_email_is_recorded(self):
        self.send(lambda *args: (True, "Email sent."))
        self.assertEqual(self.email(), (True, "Sent"))

    def test_a_version_already_emailed_is_not_emailed_again(self):
        self.send(lambda *args: (True, "Email sent."))
        self.assertEqual(self.send(lambda *args: (True, "Email sent.")).call_count, 0)

    def test_smtp_failure_is_retried_not_fatal(self):
        def refused(*args):
            raise ConnectionRefusedError("SMTP down")

        with self.assertRaises(queue.TransientError):
            self.send(refused)

    def test_missing_smtp_configuration_is_recorded(self):
        self.send(lambda *args: (False, "SMTP not configured. Email was not sent."))
        self.assertEqual(self.email(), (False, "SMTP Not Configured"))


class GuidelineJobTests(AppTestCase):
    """DEF-038: parsing and indexing run in the worker, never in the request."""

    def index_page(self):
        tv_id = self.world.template_version_id
        return self.logged_in_admin().post(f"/admin/template-version/{tv_id}/index")

    def jobs(self):
        with self.Session() as db:
            return [(job.type, job.status) for job in db.query(Job).order_by(Job.id)]

    def test_parse_and_index_button_only_queues_a_job(self):
        parse = AsyncMock()
        with patch.object(handlers, "parse_guideline_profile", new=parse):
            self.index_page()

        self.assertEqual(
            (self.jobs(), parse.await_count), ([(JobType.PARSE_GUIDELINE, JobStatus.QUEUED)], 0)
        )

    def test_second_click_while_queued_adds_no_job(self):
        self.index_page()

        response = self.index_page()

        self.assertEqual((len(self.jobs()), "error=" in response.headers["location"]), (1, True))

    def test_parsing_then_indexing_use_the_configured_url_and_an_id_based_collection(self):
        """DEF-032 and DEF-035, on the indexing path."""
        tv_id = self.world.template_version_id
        payload = {"template_version_id": tv_id}
        parse = AsyncMock(return_value={"status": "success", "profile": {}})
        index = AsyncMock(return_value={"status": "success", "collection": f"tv_{tv_id}"})
        with (
            patch.object(handlers, "parse_guideline_profile", new=parse),
            patch.object(handlers, "index_template", new=index),
        ):
            follow_ups = asyncio.run(handlers.ParseGuideline(self.Session).run(payload))
            asyncio.run(handlers.IndexGuideline(self.Session).run(payload))

        self.assertEqual(
            (
                [f.type for f in follow_ups],
                parse.await_args.args[0],
                index.await_args.args[1].get("collection"),
            ),
            ([JobType.INDEX_GUIDELINE], get_settings().ai_service_url, f"tv_{tv_id}"),
        )

    def test_failed_indexing_is_shown_on_the_guideline(self):
        payload = {"template_version_id": self.world.template_version_id}

        handlers.IndexGuideline(self.Session).on_final_failure(payload)

        with self.Session() as db:
            status = db.get(TemplateVersion, self.world.template_version_id).rag_status
        self.assertEqual(status, "Index Failed")


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

        with (
            tempfile.NamedTemporaryFile(suffix=".pdf") as pdf,
            patch.object(client.httpx, "AsyncClient", async_factory),
            patch.object(client.httpx, "Client", sync_factory),
        ):
            url = "http://ai.test"
            asyncio.run(client.rag_validate(url, {}))
            asyncio.run(client.index_template(url, {}, pdf.name))
            asyncio.run(client.extract_format_profile(url, {}))
            asyncio.run(client.parse_guideline_profile(url, {}, pdf.name))
            health = client.health(url)
            if inspect.isawaitable(health):
                asyncio.run(health)

        expected = f"Bearer {os.environ['UNISUBMIT_AI_SERVICE_TOKEN']}"
        self.assertEqual(
            [path for path, auth in seen if auth != expected and path != "/health"], []
        )


if __name__ == "__main__":
    unittest.main()
