"""P3.8-P3.11: uploads are checked as they arrive, fingerprinted and capped,
and stored results are JSON documents.

DEF-037: a double-click on the new-submission form created two submissions.
"""

import os
from unittest.mock import patch

from factories import add_version, pdf_bytes
from support import AppTestCase
from unisubmit.core.config import get_settings
from unisubmit.models import Job, JobStatus, Submission, SubmissionVersion


class UploadHardeningTests(AppTestCase):
    def setting(self, name: str, value: str) -> None:
        env = patch.dict(os.environ, {f"UNISUBMIT_{name}": value})
        env.start()
        self.addCleanup(env.stop)
        get_settings.cache_clear()

    def finished_version(self) -> None:
        with self.Session() as db:
            add_version(db, self.world.submission_id, self.tmp, JobStatus.SUCCEEDED)

    def upload(self, client, content: bytes):
        return client.post(
            f"/submission/{self.world.submission_id}/resubmit",
            files={"pdf": ("revised.pdf", content, "application/pdf")},
        )

    def submit(self, client, content: bytes):
        return client.post(
            "/submit",
            data={
                "title": "A Study",
                "program_id": self.world.program_id,
                "teacher_id": self.world.teacher_id,
                "template_version_id": self.world.template_version_id,
            },
            files={"pdf": ("thesis.pdf", content, "application/pdf")},
        )

    def versions(self) -> list[tuple[int, str]]:
        with self.Session() as db:
            return [
                (v.version_no, v.status)
                for v in db.query(SubmissionVersion)
                .filter_by(submission_id=self.world.submission_id)
                .order_by(SubmissionVersion.version_no)
            ]

    def stored_uploads(self) -> list[str]:
        folder = self.tmp / str(self.world.university_id) / "submissions"
        return sorted(path.name for path in folder.iterdir()) if folder.exists() else []

    def test_upload_records_its_fingerprint(self):
        self.finished_version()
        content = pdf_bytes("Revised")

        self.upload(self.logged_in_student(), content)

        with self.Session() as db:
            latest = db.query(SubmissionVersion).filter_by(version_no=2).one()
            fingerprint = (len(latest.sha256), latest.size_bytes, latest.page_count)
        self.assertEqual(fingerprint, (64, len(content), 1))

    def test_same_file_twice_queues_nothing_the_second_time(self):
        self.finished_version()
        client, content = self.logged_in_student(), pdf_bytes("Revised")
        self.upload(client, content)

        response = self.upload(client, content)

        self.assertEqual(
            ([n for n, _ in self.versions()], "message=" in response.headers["location"]),
            ([1, 2], True),
        )

    def test_failed_validation_can_be_retried_with_the_same_file(self):
        self.finished_version()
        client, content = self.logged_in_student(), pdf_bytes("Revised")
        self.upload(client, content)
        with self.Session() as db:
            db.query(Job).filter_by(status=JobStatus.QUEUED).one().status = JobStatus.FAILED
            db.query(SubmissionVersion).filter_by(version_no=2).one().status = "VALIDATION FAILED"
            db.commit()

        self.upload(client, content)

        self.assertEqual([n for n, _ in self.versions()], [1, 2, 3])

    def test_non_pdf_is_rejected_and_leaves_no_file(self):
        self.finished_version()
        before = self.stored_uploads()

        response = self.upload(self.logged_in_student(), b"MZ\x90\x00not a pdf")

        self.assertEqual(
            (self.stored_uploads(), "error=" in response.headers["location"]), (before, True)
        )

    def test_file_over_the_size_limit_is_rejected_and_leaves_no_file(self):
        self.setting("MAX_UPLOAD_MB", "1")
        self.finished_version()
        before = self.stored_uploads()

        response = self.upload(self.logged_in_student(), b"%PDF-" + bytes(1024 * 1024))

        self.assertEqual(
            (self.stored_uploads(), "larger%20than" in response.headers["location"]),
            (before, True),
        )

    def test_daily_cap_refuses_the_upload_and_queues_nothing(self):
        self.setting("DAILY_UPLOADS_PER_STUDENT", "1")
        self.finished_version()

        response = self.upload(self.logged_in_student(), pdf_bytes("Revised"))

        self.assertEqual(
            ([n for n, _ in self.versions()], "error=" in response.headers["location"]),
            ([1], True),
        )

    def test_double_submit_creates_one_submission(self):
        """DEF-037: the second request finds the first one's submission."""
        client, content = self.logged_in_student(), pdf_bytes("Thesis")

        first = self.submit(client, content)
        second = self.submit(client, content)

        with self.Session() as db:
            count = db.query(Submission).filter_by(student_user_id=self.world.student_id).count()
        self.assertEqual(
            (count, second.headers["location"].split("?")[0]),
            (2, first.headers["location"]),  # the world's own submission, plus one
        )

    def test_stored_results_render_on_the_student_pages(self):
        """P3.10: results are JSON documents, not text; both pages still render them."""
        self.finished_version()
        with self.Session() as db:
            version = db.query(SubmissionVersion).one()
            version.validation_json = [
                {
                    "category": "Additional Restriction",
                    "name": "Searchable PDF",
                    "status": "PASS",
                    "severity": "Mandatory",
                    "message": "Text found",
                }
            ]
            db.commit()
            paths = [
                f"/submission/{self.world.submission_id}",
                f"/submission/{self.world.submission_id}/report/{version.id}",
            ]
        client = self.logged_in_student()

        pages = [client.get(path) for path in paths]

        self.assertEqual(
            [(page.status_code, "Searchable PDF" in page.text) for page in pages],
            [(200, True), (200, True)],
        )
