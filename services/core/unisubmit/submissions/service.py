"""Uploading a version: checks, fingerprint, caps, numbering and the validation job.

Tasks P3.8 and P3.11 of the restructuring plan.
"""

from datetime import UTC, datetime, timedelta
from typing import IO, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import queue, storage
from ..core.config import get_settings
from ..models import (
    JobType,
    StudentUser,
    Submission,
    SubmissionStatus,
    SubmissionVersion,
)
from ..storage import UploadRefused, store_pdf


def report_run_token(version: SubmissionVersion) -> str:
    """Names the report file and its download URL after the validation run."""
    raw = (version.ai_run_reference or "deterministic").strip() or "deterministic"
    return "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))[:80] or "deterministic"


def check_upload_caps(db: Session, student: StudentUser) -> None:
    """Daily upload caps per student and per university (P3.11). The caller
    holds the student's row lock, so the student's own count cannot race."""
    settings = get_settings()
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)  # naive UTC columns
    recent = (
        select(func.count(SubmissionVersion.id))
        .join(Submission, SubmissionVersion.submission_id == Submission.id)
        .where(SubmissionVersion.created_at > since)
    )
    if (
        db.scalar(recent.where(Submission.student_user_id == student.id)) or 0
    ) >= settings.daily_uploads_per_student:
        raise UploadRefused(
            f"You have uploaded {settings.daily_uploads_per_student} versions in the last "
            "24 hours, the daily limit. Try again later."
        )
    if (
        db.scalar(recent.where(Submission.university_id == student.university_id)) or 0
    ) >= settings.daily_uploads_per_university:
        raise UploadRefused("Your university has reached its daily upload limit. Try again later.")


def queue_version(
    db: Session,
    sub: Submission,
    stored: storage.StoredFile,
    info: dict[str, Any],
    version_no: int,
) -> SubmissionVersion:
    """Add a version for a stored file and queue its validation; the caller commits."""
    sv = SubmissionVersion(
        submission_id=sub.id,
        version_no=version_no,
        pdf_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        page_count=info["page_count"],
        extracted_text=info["text"],
        status=SubmissionStatus.QUEUED,
    )
    db.add(sv)
    sub.status = SubmissionStatus.QUEUED
    db.flush()  # assigns sv.id for the job
    queue.enqueue(
        db,
        JobType.VALIDATE_SUBMISSION,
        f"submission:{sub.id}",
        {"submission_version_id": sv.id},
        university_id=sub.university_id,
    )
    return sv


def save_queue_version(sub: Submission, pdf: IO[bytes], db: Session) -> SubmissionVersion | None:
    """Store an uploaded PDF as the next version of `sub` and queue it.

    Returns None, keeping nothing, when the file is identical to the latest
    version (a double-click). Raises UploadRefused while an earlier version
    is still queued or running: two uploads must not be numbered alike or
    queue a second GPU job (DEF-026).
    """
    stored, info = store_pdf(pdf, sub.university_id, "submissions", f"submission_{sub.id}")
    try:
        # Concurrent uploads for this submission wait here, one at a time.
        db.query(Submission).filter(Submission.id == sub.id).with_for_update().one()
        latest = db.scalars(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == sub.id)
            .order_by(SubmissionVersion.version_no.desc())
            .limit(1)
        ).first()
        # A failed validation may be retried with the same file.
        if (
            latest is not None
            and latest.sha256 == stored.sha256
            and latest.status != SubmissionStatus.VALIDATION_FAILED
        ):
            db.rollback()
            storage.delete(stored.relative_path)
            return None
        if queue.has_active_job(db, f"submission:{sub.id}"):
            raise UploadRefused(
                "This submission is still being validated. "
                "Upload a revised version after the result is ready."
            )
        check_upload_caps(db, sub.student)
        # One commit: committing the version first released the lock before its
        # job existed, and a second upload in that gap saw no active job (DEF-046).
        sv = queue_version(db, sub, stored, info, (latest.version_no if latest else 0) + 1)
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(stored.relative_path)
        raise
    return sv
