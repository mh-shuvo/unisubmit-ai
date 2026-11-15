"""The job queue: rows in `jobs`, taken by the worker with `SKIP LOCKED` (P3.2).

Functions work in the caller's session and never commit, so a job is queued in
the same transaction as the rows it refers to: both or neither.

    queued --claim--> running --complete--> succeeded
                         |--retry_or_fail--> queued (after a back-off) or failed
                         |--fail-----------> failed
                         |--release--------> queued (worker stopped; attempt refunded)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from .models import ACTIVE_JOB_STATUSES, Job, JobStatus, JobType, University

ERROR_LIMIT = 2000
# Fairness looks back this far when it compares universities' use of the model.
FAIRNESS_WINDOW = timedelta(hours=1)
# Cheap jobs that do not use the model; they do not count against a university.
NOT_MODEL_WORK = (JobType.SEND_EMAIL,)


class TransientError(Exception):
    """A failure a later attempt may not repeat: a service down, a timeout.

    Retried with back-off. Any other exception fails the job at once, because
    retrying a corrupt PDF only spends the model's time again.
    """


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    type: JobType
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True, slots=True)
class FollowUp:
    """A job to queue when the current one succeeds, in the same transaction
    and for the same university, so a crash between the two cannot lose it."""

    type: JobType
    subject_key: str
    payload: dict[str, Any]
    priority: int = 100


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    job_type: JobType,
    subject_key: str,
    payload: dict[str, Any],
    *,
    university_id: int,
    priority: int = 100,
) -> None:
    session.add(
        Job(
            type=job_type,
            subject_key=subject_key,
            payload=payload,
            university_id=university_id,
            priority=priority,
        )
    )


def has_active_job(session: Session, subject_key: str) -> bool:
    return bool(
        session.scalar(
            sa.select(
                sa.exists().where(
                    Job.subject_key == subject_key, Job.status.in_(ACTIVE_JOB_STATUSES)
                )
            )
        )
    )


def latest_job(session: Session, subject_key: str) -> Job | None:
    return session.scalars(
        sa.select(Job).where(Job.subject_key == subject_key).order_by(Job.id.desc()).limit(1)
    ).first()


def claim_next(session: Session, worker_id: str) -> ClaimedJob | None:
    """The oldest due job of the university that used the model least in the
    last hour, so one university's batch cannot starve the others."""
    recent = aliased(Job)
    recent_load = (
        sa.select(sa.func.count(recent.id))
        .where(
            recent.university_id == Job.university_id,
            recent.locked_at > _now() - FAIRNESS_WINDOW,
            recent.type.not_in(NOT_MODEL_WORK),
        )
        .correlate(Job)
        .scalar_subquery()
    )
    job = session.scalars(
        sa.select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_after <= _now())
        .order_by(recent_load, Job.priority, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Job)  # two workers never take the same row
    ).first()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.locked_by = worker_id
    job.locked_at = _now()
    return ClaimedJob(job.id, job.type, dict(job.payload), job.attempts)


def complete(session: Session, job_id: int, then: Sequence[FollowUp] = ()) -> None:
    job = session.get_one(Job, job_id)
    job.status = JobStatus.SUCCEEDED
    job.finished_at = _now()
    job.last_error = None
    session.flush()  # frees the subject before a follow-up takes it over
    for follow_up in then:
        # A re-run job may find its follow-up still queued from the first run.
        if not has_active_job(session, follow_up.subject_key):
            enqueue(
                session,
                follow_up.type,
                follow_up.subject_key,
                follow_up.payload,
                university_id=job.university_id,
                priority=follow_up.priority,
            )


def fail(session: Session, job_id: int, error: str) -> None:
    job = session.get_one(Job, job_id)
    job.status = JobStatus.FAILED
    job.finished_at = _now()
    job.last_error = error[:ERROR_LIMIT]


def retry_or_fail(session: Session, job_id: int, error: str, max_attempts: int) -> bool:
    """Back off 2, 4, 8 ... minutes; returns True when the job has failed for good."""
    job = session.get_one(Job, job_id)
    if job.attempts >= max_attempts:
        fail(session, job_id, error)
        return True
    job.status = JobStatus.QUEUED
    job.last_error = error[:ERROR_LIMIT]
    job.run_after = _now() + timedelta(minutes=2**job.attempts)
    return False


def release(session: Session, job_id: int, reason: str) -> None:
    """Give back a job the worker was stopped during; the attempt does not count."""
    job = session.get_one(Job, job_id)
    job.status = JobStatus.QUEUED
    job.attempts -= 1
    job.last_error = reason
    job.run_after = _now()


def requeue_stale(
    session: Session, timeout: timedelta, max_attempts: int
) -> tuple[int, list[ClaimedJob]]:
    """Requeue jobs running for longer than `timeout`, whose worker died
    (DEF-034). Returns how many were requeued and the jobs that instead failed
    for good, having used their last attempt."""
    stale = session.scalars(
        sa.select(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < _now() - timeout)
        .with_for_update(skip_locked=True)
    ).all()
    failed = []
    for job in stale:
        reason = f"The worker {job.locked_by} stopped before finishing this job."
        if retry_or_fail(session, job.id, reason, max_attempts):
            failed.append(ClaimedJob(job.id, job.type, dict(job.payload), job.attempts))
        else:
            job.run_after = _now()  # the worker died; the job itself did nothing wrong
    return len(stale) - len(failed), failed


@dataclass(frozen=True, slots=True)
class Durations:
    type: JobType
    succeeded: int
    p50_s: float
    p95_s: float


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    """What the Platform Admin's AI page shows about the queue (P3.12)."""

    waiting: list[tuple[str, int, int]]  # university, queued, running
    oldest_queued_s: float | None
    durations: list[Durations]  # succeeded jobs of the last 24 hours, by type
    failed_24h: int


def metrics(session: Session) -> QueueMetrics:
    day_ago = _now() - timedelta(hours=24)
    waiting = session.execute(
        sa.select(
            University.name,
            sa.func.count().filter(Job.status == JobStatus.QUEUED),
            sa.func.count().filter(Job.status == JobStatus.RUNNING),
        )
        .join(University, Job.university_id == University.id)
        .where(Job.status.in_(ACTIVE_JOB_STATUSES))
        .group_by(University.name)
        .order_by(University.name)
    ).all()
    oldest = session.scalar(
        sa.select(sa.func.min(Job.created_at)).where(Job.status == JobStatus.QUEUED)
    )
    seconds = sa.func.extract("epoch", Job.finished_at - Job.locked_at)
    durations = session.execute(
        sa.select(
            Job.type,
            sa.func.count(),
            sa.func.percentile_cont(0.5).within_group(seconds),
            sa.func.percentile_cont(0.95).within_group(seconds),
        )
        .where(Job.status == JobStatus.SUCCEEDED, Job.finished_at > day_ago)
        .group_by(Job.type)
        .order_by(Job.type)
    ).all()
    failed = session.scalar(
        sa.select(sa.func.count()).where(Job.status == JobStatus.FAILED, Job.finished_at > day_ago)
    )
    return QueueMetrics(
        waiting=[(name, queued, running) for name, queued, running in waiting],
        oldest_queued_s=(_now() - oldest).total_seconds() if oldest else None,
        durations=[
            Durations(job_type, count, round(p50, 1), round(p95, 1))
            for job_type, count, p50, p95 in durations
        ],
        failed_24h=failed or 0,
    )
