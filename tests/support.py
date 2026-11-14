"""Shared fixtures for the web-app regression tests.

Importing this module first sets the environment the app needs, so the tests
never read secrets or a database URL from the developer's `.env`.
"""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_SECRET = "test-app-secret-" + "x" * 40
TEST_AI_TOKEN = "test-ai-token-" + "y" * 40
os.environ["APP_SECRET"] = TEST_SECRET
os.environ["AI_SERVICE_TOKEN"] = TEST_AI_TOKEN
os.environ["DATABASE_URL"] = "sqlite://"  # never used: tests bind their own engine

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import main as web  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.models import (  # noqa: E402
    AIJob,
    AIProfile,
    AdminUser,
    Department,
    Faculty,
    Program,
    StudentUser,
    Submission,
    SubmissionVersion,
    Teacher,
    Template,
    TemplateVersion,
)
from app.security import hash_password  # noqa: E402

PASSWORD = "correct horse battery staple"


def pdf_bytes(text: str = "Chapter 1 Introduction") -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def build_world(db, tmp: Path) -> SimpleNamespace:
    """One department with a program, a supervisor, an indexed guideline and a student."""
    faculty = Faculty(name="Faculty of Science", code="SCI")
    db.add(faculty)
    db.flush()
    department = Department(name="Computer Science", code="CSE", faculty_id=faculty.id)
    db.add(department)
    db.flush()
    program = Program(name="M.Sc. in CSE", code="MSC-CSE", department_id=department.id)
    teacher = Teacher(
        name="Dr Supervisor",
        designation="Professor",
        email="supervisor@example.edu",
        department_id=department.id,
    )
    db.add_all([program, teacher])
    db.flush()
    template = Template(submission_type="Thesis", title="M.Sc. Thesis", program_id=program.id)
    db.add(template)
    db.flush()
    guideline = tmp / "guideline.pdf"
    guideline.write_bytes(pdf_bytes("Thesis guideline"))
    template_version = TemplateVersion(
        template_id=template.id,
        version="1.0",
        pdf_path=str(guideline),
        status="Active",
        rag_status="Indexed",
        rag_collection="tv_legacy_name",
    )
    # A stale per-profile URL, as left behind by the seed or a host move (DEF-035).
    profile = AIProfile(
        program_id=program.id,
        submission_type="Thesis",
        service_url="http://stale-host:8001",
        base_model="qwen3:14b",
        status="Active",
    )
    student = StudentUser(
        name="Test Student",
        student_id="S-001",
        email="student@example.edu",
        password_hash=hash_password(PASSWORD),
        department_id=department.id,
    )
    admin = AdminUser(
        name="Unit Admin",
        email="unit.admin@example.edu",
        password_hash=hash_password(PASSWORD),
        role="UNIT_ADMIN",
        department_id=department.id,
    )
    db.add_all([template_version, profile, student, admin])
    db.flush()
    submission = Submission(
        student_user_id=student.id,
        title="A Study of Formatting Compliance",
        program_id=program.id,
        teacher_id=teacher.id,
        template_version_id=template_version.id,
        ai_profile_id=profile.id,
        status="QUEUED",
    )
    db.add(submission)
    db.commit()
    return SimpleNamespace(
        program_id=program.id,
        template_version_id=template_version.id,
        profile_id=profile.id,
        student_id=student.id,
        admin_id=admin.id,
        submission_id=submission.id,
    )


def add_version(db, submission_id: int, tmp: Path, job_status: str, **job_fields) -> int:
    """Add a submission version with its queue job; returns the job id."""
    number = db.query(SubmissionVersion).filter_by(submission_id=submission_id).count() + 1
    path = tmp / f"submission_{submission_id}_v{number}.pdf"
    path.write_bytes(pdf_bytes(f"Version {number}"))
    version = SubmissionVersion(
        submission_id=submission_id,
        version_no=number,
        pdf_path=str(path),
        status=job_status,
    )
    db.add(version)
    db.flush()
    job = AIJob(submission_version_id=version.id, status=job_status, **job_fields)
    db.add(job)
    db.commit()
    return job.id


class AppTestCase(unittest.TestCase):
    """Runs the real FastAPI app against a private in-memory SQLite database."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        web.app.dependency_overrides[get_db] = override_get_db
        self.addCleanup(web.app.dependency_overrides.clear)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in ("SUBMISSION_STORE", "REPORT_STORE", "TEMPLATE_STORE"):
            stack.enter_context(patch.object(web, name, self.tmp))
        # If the web startup ever opens a session again (DEF-024, DEF-028), it
        # must land in this test database, where the tests can see its effect.
        stack.enter_context(patch.object(web, "SessionLocal", self.Session, create=True))
        with self.Session() as db:
            self.world = build_world(db, self.tmp)

    def client(self) -> TestClient:
        return TestClient(web.app, follow_redirects=False)

    def logged_in_student(self) -> TestClient:
        client = self.client()
        response = client.post(
            "/student/login", data={"email": "student@example.edu", "password": PASSWORD}
        )
        self.assertEqual(response.headers.get("location"), "/student/dashboard")
        return client

    def logged_in_admin(self) -> TestClient:
        client = self.client()
        response = client.post(
            "/admin/login", data={"email": "unit.admin@example.edu", "password": PASSWORD}
        )
        self.assertEqual(response.headers.get("location"), "/admin")
        return client
