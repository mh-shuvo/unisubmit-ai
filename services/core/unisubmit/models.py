from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.config import get_settings
from .core.db import Base, str_enum


class SubmissionStatus(StrEnum):
    """A submission's status is the status of its latest version."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PASSED = "PASSED"
    REVISION_REQUIRED = "REVISION REQUIRED"
    VALIDATION_FAILED = "VALIDATION FAILED"  # the job gave up; the student may upload again


IN_PROGRESS = (SubmissionStatus.QUEUED, SubmissionStatus.PROCESSING)


class EmailStatus(StrEnum):
    NOT_SENT = "Not Sent"
    QUEUED = "Queued"
    SENT = "Sent"
    NOT_CONFIGURED = "SMTP Not Configured"
    FAILED = "Failed"


class GuidelineStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    ARCHIVED = "Archived"


class RagStatus(StrEnum):
    NOT_INDEXED = "Not Indexed"
    INDEXED = "Indexed"
    INDEX_FAILED = "Index Failed"


class ProfileStatus(StrEnum):
    """The parsed guideline rules; only ACTIVE rules take part in validation."""

    NOT_EXTRACTED = "Not Extracted"
    PARSING_FAILED = "Parsing Failed"
    REVIEW_REQUIRED = "Parsed - Review Required"
    NO_RULES = "Parsed - No Explicit Rules Found"
    ACTIVE = "Active"


class AIProfileStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"


class AdminRole(StrEnum):
    PLATFORM_ADMIN = "PLATFORM_ADMIN"  # the whole installation; belongs to no university
    UNIVERSITY_ADMIN = "UNIVERSITY_ADMIN"  # one university (was SUPER_ADMIN)
    UNIT_ADMIN = "UNIT_ADMIN"  # one department or institute


class University(Base):
    """A tenant. Every other record belongs to exactly one, directly or via its parent."""

    __tablename__ = "universities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)  # /u/{slug}/...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Faculty(Base):
    __tablename__ = "faculties"
    __table_args__ = (
        UniqueConstraint("university_id", "name"),
        UniqueConstraint("university_id", "code"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    university = relationship("University")
    departments = relationship("Department", back_populates="faculty")


class Institute(Base):
    __tablename__ = "institutes"
    __table_args__ = (
        UniqueConstraint("university_id", "name"),
        UniqueConstraint("university_id", "code"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    university = relationship("University")
    programs = relationship("Program", back_populates="institute")
    teachers = relationship("Teacher", back_populates="institute")


class Department(Base):
    __tablename__ = "departments"
    # The university comes from the faculty; unique per faculty is unique per university.
    __table_args__ = (
        UniqueConstraint("faculty_id", "name"),
        UniqueConstraint("faculty_id", "code"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50))
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    faculty = relationship("Faculty", back_populates="departments")
    programs = relationship("Program", back_populates="department")
    teachers = relationship("Teacher", back_populates="department")


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(220))
    code: Mapped[str] = mapped_column(String(80))
    level: Mapped[str] = mapped_column(String(80), default="Undergraduate")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    institute_id: Mapped[int | None] = mapped_column(ForeignKey("institutes.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department = relationship("Department", back_populates="programs")
    institute = relationship("Institute", back_populates="programs")
    templates = relationship("Template", back_populates="program")
    ai_profiles = relationship("AIProfile", back_populates="program")

    @property
    def unit_name(self):
        return (
            self.department.name
            if self.department
            else (self.institute.name if self.institute else "")
        )

    @property
    def unit_kind(self):
        return "department" if self.department_id else "institute"

    @property
    def unit_id(self):
        return self.department_id or self.institute_id


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        # Only a Platform Admin stands outside every university.
        CheckConstraint(
            "(role = 'PLATFORM_ADMIN') = (university_id IS NULL)", name="university_matches_role"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int | None] = mapped_column(
        ForeignKey("universities.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    # Login emails stay unique across the installation: no university picker at login.
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[AdminRole] = mapped_column(str_enum(AdminRole), default=AdminRole.UNIT_ADMIN)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    institute_id: Mapped[int | None] = mapped_column(ForeignKey("institutes.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Raised on a password change: sessions holding an older value end (P4.3).
    auth_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Ten wrong passwords in a row lock the account for 15 minutes (P4.4).
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    university = relationship("University")
    department = relationship("Department")
    institute = relationship("Institute")

    @property
    def unit_name(self):
        if self.department or self.institute:
            return self.department.name if self.department else self.institute.name
        return self.university.name if self.university else "Platform"


class StudentUser(Base):
    __tablename__ = "student_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    student_id: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    institute_id: Mapped[int | None] = mapped_column(ForeignKey("institutes.id"), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Raised on a password change: sessions holding an older value end (P4.3).
    auth_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Ten wrong passwords in a row lock the account for 15 minutes (P4.4).
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    university = relationship("University")
    department = relationship("Department")
    institute = relationship("Institute")

    @property
    def unit_name(self):
        return (
            self.department.name
            if self.department
            else (self.institute.name if self.institute else "")
        )

    @property
    def unit_kind(self):
        return "department" if self.department_id else "institute"

    @property
    def unit_id(self):
        return self.department_id or self.institute_id


class Teacher(Base):
    __tablename__ = "teachers"
    # A directory entry, not a login: one supervisor may appear in two universities (DEF-043).
    __table_args__ = (UniqueConstraint("university_id", "email"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    designation: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    institute_id: Mapped[int | None] = mapped_column(ForeignKey("institutes.id"), nullable=True)
    department = relationship("Department", back_populates="teachers")
    institute = relationship("Institute", back_populates="teachers")

    @property
    def unit_name(self):
        return (
            self.department.name
            if self.department
            else (self.institute.name if self.institute else "")
        )


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("program_id", "submission_type", name="uq_program_submission_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_type: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"))
    program = relationship("Program", back_populates="templates")
    versions = relationship(
        "TemplateVersion", back_populates="template", cascade="all, delete-orphan"
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id"))
    version: Mapped[str] = mapped_column(String(50))
    pdf_path: Mapped[str] = mapped_column(String(500))
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    additional_rules_pdf_path: Mapped[str] = mapped_column(String(500), default="")
    additional_rules_text: Mapped[str] = mapped_column(Text, default="")
    effective_from: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[GuidelineStatus] = mapped_column(
        str_enum(GuidelineStatus), default=GuidelineStatus.DRAFT
    )
    change_note: Mapped[str] = mapped_column(Text, default="")
    rag_collection: Mapped[str] = mapped_column(String(220), default="")
    rag_status: Mapped[RagStatus] = mapped_column(
        str_enum(RagStatus), default=RagStatus.NOT_INDEXED
    )
    # Never mutate the loaded value in place: change tracking only sees a new object.
    format_profile_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    format_profile_status: Mapped[ProfileStatus] = mapped_column(
        str_enum(ProfileStatus), default=ProfileStatus.NOT_EXTRACTED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    template = relationship("Template", back_populates="versions")
    rules = relationship(
        "ValidationRule", back_populates="template_version", cascade="all, delete-orphan"
    )


class ValidationRule(Base):
    __tablename__ = "validation_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_version_id: Mapped[int] = mapped_column(ForeignKey("template_versions.id"))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), default="Additional Restriction")
    severity: Mapped[str] = mapped_column(String(30), default="Mandatory")
    validator: Mapped[str] = mapped_column(String(80), default="searchable_pdf")
    target: Mapped[str] = mapped_column(String(200), default="")
    expected: Mapped[str] = mapped_column(String(200), default="")
    source_page: Mapped[str] = mapped_column(String(40), default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    template_version = relationship("TemplateVersion", back_populates="rules")


class AIProfile(Base):
    __tablename__ = "ai_profiles"
    __table_args__ = (UniqueConstraint("program_id", "submission_type", name="uq_ai_program_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"))
    submission_type: Mapped[str] = mapped_column(String(80))
    service_url: Mapped[str] = mapped_column(String(250), default="http://127.0.0.1:8001")
    base_model: Mapped[str] = mapped_column(
        String(120), default=lambda: get_settings().ollama_model
    )
    adapter_name: Mapped[str] = mapped_column(String(160), default="")
    adapter_version: Mapped[str] = mapped_column(String(60), default="")
    embedding_model: Mapped[str] = mapped_column(String(120), default="BAAI/bge-m3")
    rag_collection: Mapped[str] = mapped_column(String(180), default="")
    prompt_version: Mapped[str] = mapped_column(String(80), default="v1")
    training_dataset_version: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[AIProfileStatus] = mapped_column(
        str_enum(AIProfileStatus), default=AIProfileStatus.DRAFT
    )
    program = relationship("Program", back_populates="ai_profiles")


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("student_users.id"))
    title: Mapped[str] = mapped_column(String(400))
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    template_version_id: Mapped[int] = mapped_column(ForeignKey("template_versions.id"))
    ai_profile_id: Mapped[int | None] = mapped_column(ForeignKey("ai_profiles.id"), nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        str_enum(SubmissionStatus), default=SubmissionStatus.QUEUED
    )
    email_status: Mapped[EmailStatus] = mapped_column(
        str_enum(EmailStatus), default=EmailStatus.NOT_SENT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    student = relationship("StudentUser")
    program = relationship("Program")
    teacher = relationship("Teacher")
    template_version = relationship("TemplateVersion")
    ai_profile = relationship("AIProfile")
    versions = relationship(
        "SubmissionVersion", back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionVersion(Base):
    __tablename__ = "submission_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    pdf_path: Mapped[str] = mapped_column(String(500))
    # Empty for versions uploaded before v10.2 (P3.8).
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[SubmissionStatus] = mapped_column(
        str_enum(SubmissionStatus), default=SubmissionStatus.QUEUED
    )
    validation_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    ai_suggestions: Mapped[str] = mapped_column(Text, default="")
    report_path: Mapped[str] = mapped_column(String(500), default="")
    ai_run_reference: Mapped[str] = mapped_column(String(120), default="")
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    submission = relationship("Submission", back_populates="versions")


class JobType(StrEnum):
    VALIDATE_SUBMISSION = "validate_submission"
    PARSE_GUIDELINE = "parse_guideline"
    INDEX_GUIDELINE = "index_guideline"
    SEND_EMAIL = "send_email"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class Job(Base):
    """Background work for `python -m unisubmit.worker`; see `queue.py` (P3.1).

    `subject_key` names what the job works on, e.g. "submission:42", and the
    database allows one active job per subject. The payload holds IDs only.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "priority", "id", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_university_locked", "university_id", "locked_at"),
        Index(
            "uq_jobs_one_active_per_subject",
            "subject_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
    # Server defaults (run_after, created_at) are read back on insert.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"))
    type: Mapped[JobType] = mapped_column(str_enum(JobType))
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.QUEUED)
    subject_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)  # for admins, never shown to students
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """A permanent record of an admin action (P4.8); logs rotate, this does not.

    `university_id` is the university acted in or on, empty for actions that
    concern the whole installation; `admin_id` is empty for the CLI.
    """

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_university_created", "university_id", "created_at"),)
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    university_id: Mapped[int | None] = mapped_column(ForeignKey("universities.id"))
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    action: Mapped[str] = mapped_column(String(60))  # e.g. "guideline.activate_profile"
    subject: Mapped[str] = mapped_column(String(100))  # e.g. "template_version:7"
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    admin = relationship("AdminUser")


class AIJob(Base):
    """The pre-v10.2 validation queue. Migration 0003 copied it into `jobs`;
    nothing reads or writes it, and a later release drops it (P3.7)."""

    __tablename__ = "ai_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_version_id: Mapped[int] = mapped_column(
        ForeignKey("submission_versions.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(40), default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
