"""Builders for valid object graphs: one department with everything a
submission needs. Plain functions, no framework."""

from pathlib import Path
from types import SimpleNamespace

import fitz

from unisubmit.models import (
    AdminRole,
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
    University,
)
from unisubmit.security import hash_password

PASSWORD = "correct horse battery staple"


def pdf_bytes(text: str = "Chapter 1 Introduction") -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def stored_pdf(tmp: Path, university_id: int, kind: str, name: str, text: str) -> str:
    """Write a PDF under the storage root `tmp` and return its stored, relative path."""
    path = tmp / str(university_id) / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(text))
    return path.relative_to(tmp).as_posix()


def build_world(db, tmp: Path, slug: str = "test") -> SimpleNamespace:
    """One university with a department, program, supervisor, indexed guideline,
    student, unit admin and University Admin. `tmp` is the storage root.

    A second world needs another `slug`; its emails then end in
    `@{slug}.example.edu`, because login emails are unique installation-wide.
    """
    domain = "example.edu" if slug == "test" else f"{slug}.example.edu"
    university = University(name=f"{slug.title()} University", slug=slug, is_active=True)
    db.add(university)
    db.flush()
    faculty = Faculty(name="Faculty of Science", code="SCI", university_id=university.id)
    db.add(faculty)
    db.flush()
    department = Department(name="Computer Science", code="CSE", faculty_id=faculty.id)
    db.add(department)
    db.flush()
    program = Program(name="M.Sc. in CSE", code="MSC-CSE", department_id=department.id)
    teacher = Teacher(
        name="Dr Supervisor",
        designation="Professor",
        email=f"supervisor@{domain}",
        university_id=university.id,
        department_id=department.id,
    )
    db.add_all([program, teacher])
    db.flush()
    template = Template(submission_type="Thesis", title="M.Sc. Thesis", program_id=program.id)
    db.add(template)
    db.flush()
    template_version = TemplateVersion(
        template_id=template.id,
        version="1.0",
        pdf_path=stored_pdf(tmp, university.id, "guidelines", "guideline.pdf", "Thesis guideline"),
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
        email=f"student@{domain}",
        password_hash=hash_password(PASSWORD),
        university_id=university.id,
        department_id=department.id,
    )
    admin = AdminUser(
        name="Unit Admin",
        email=f"unit.admin@{domain}",
        password_hash=hash_password(PASSWORD),
        role=AdminRole.UNIT_ADMIN,
        university_id=university.id,
        department_id=department.id,
    )
    university_admin = AdminUser(
        name="University Admin",
        email=f"university.admin@{domain}",
        password_hash=hash_password(PASSWORD),
        role=AdminRole.UNIVERSITY_ADMIN,
        university_id=university.id,
    )
    db.add_all([template_version, profile, student, admin, university_admin])
    db.flush()
    submission = Submission(
        university_id=university.id,
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
        domain=domain,
        slug=slug,
        university_id=university.id,
        faculty_id=faculty.id,
        department_id=department.id,
        program_id=program.id,
        teacher_id=teacher.id,
        template_version_id=template_version.id,
        profile_id=profile.id,
        student_id=student.id,
        admin_id=admin.id,
        submission_id=submission.id,
    )


def add_version(db, submission_id: int, tmp: Path, job_status: str, **job_fields) -> int:
    """Add a submission version with its queue job; returns the job id."""
    number = db.query(SubmissionVersion).filter_by(submission_id=submission_id).count() + 1
    university_id = db.get(Submission, submission_id).university_id
    version = SubmissionVersion(
        submission_id=submission_id,
        version_no=number,
        pdf_path=stored_pdf(
            tmp,
            university_id,
            "submissions",
            f"submission_{submission_id}_v{number}.pdf",
            f"Version {number}",
        ),
        status=job_status,
    )
    db.add(version)
    db.flush()
    job = AIJob(submission_version_id=version.id, status=job_status, **job_fields)
    db.add(job)
    db.commit()
    return job.id
