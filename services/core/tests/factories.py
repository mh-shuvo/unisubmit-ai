"""Builders for valid object graphs: one department with everything a
submission needs. Plain functions, no framework."""

from pathlib import Path
from types import SimpleNamespace

import fitz

from unisubmit.models import (
    AdminUser,
    AIJob,
    AIProfile,
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
from unisubmit.security import hash_password

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
