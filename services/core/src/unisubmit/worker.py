"""Validation queue worker. Runs as its own process:

    python -m unisubmit.worker

It used to run inside the web process, where the PDF analysis, report
rendering and SMTP work of every job froze all pages while it ran (DEF-024).
One worker process takes one job at a time, matching the single local model.
"""
import asyncio
import logging
import signal
from datetime import datetime, timedelta

from sqlalchemy import inspect

from .ai_service_client import RAG_VALIDATE_TIMEOUT
from .db import SessionLocal, engine
from .main import process_job
from .models import AIJob

logger = logging.getLogger("unisubmit.worker")

POLL_SECONDS = 2
MAX_ATTEMPTS = 3
# No job can legitimately run longer than the AI call it waits on, so a job
# PROCESSING for longer than this belongs to a worker that died (DEF-034).
STALE_AFTER = timedelta(seconds=RAG_VALIDATE_TIMEOUT + 300)


def requeue_stale_jobs(db) -> int:
    """Put jobs abandoned by a dead worker back in the queue (DEF-034)."""
    cutoff = datetime.utcnow() - STALE_AFTER
    stale = (
        db.query(AIJob)
        .filter(AIJob.status == "PROCESSING", AIJob.started_at < cutoff)
        .all()
    )
    for job in stale:
        job.status = "RETRYING"
        job.error_message = "The worker stopped before finishing this job; requeued."
        logger.warning("Requeued job %s abandoned since %s", job.id, job.started_at)
    db.commit()
    return len(stale)


def claim_next_job(db):
    """Take the next job. SKIP LOCKED keeps a second worker off the same row."""
    job = (
        db.query(AIJob)
        .filter(AIJob.status.in_(["QUEUED", "RETRYING"]))
        .order_by(AIJob.priority.asc(), AIJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        db.rollback()
        return None
    job.status = "PROCESSING"
    job.started_at = datetime.utcnow()
    job.attempt_count += 1
    db.commit()
    return job


async def run_job(db, job) -> None:
    try:
        await process_job(db, job)
    except asyncio.CancelledError:
        # Stopped by the operator: give the job back without spending an attempt.
        db.rollback()
        job.status = "RETRYING"
        job.attempt_count -= 1
        job.error_message = "The worker was stopped during this job; requeued."
        db.commit()
        logger.warning("Worker stopped during job %s; requeued", job.id)
        raise
    except Exception as exc:
        # Roll back first: a failed flush leaves the session unusable, and the
        # commit below would fail too (DEF-036).
        db.rollback()
        logger.exception("Validation job %s failed on attempt %s", job.id, job.attempt_count)
        job.error_message = str(exc)
        job.status = "RETRYING" if job.attempt_count < MAX_ATTEMPTS else "FAILED"
        if job.status == "FAILED":
            job.completed_at = datetime.utcnow()
            job.submission_version.status = "Validation Failed"
            job.submission_version.submission.status = "Validation Failed"
        db.commit()
    else:
        job.status = "COMPLETED"
        job.completed_at = datetime.utcnow()
        job.error_message = ""
        db.commit()


def schema_ready() -> bool:
    return inspect(engine).has_table(AIJob.__tablename__)


async def wait_for_schema() -> None:
    """The web app creates the tables; the worker only waits for them.

    Both processes calling create_all on an empty database raced, and the
    loser crashed (DEF-045).
    """
    while not schema_ready():
        logger.info("Waiting for the web app to create the database schema")
        await asyncio.sleep(POLL_SECONDS)


async def run_once() -> bool:
    """Process at most one job; returns whether one was found."""
    db = SessionLocal()
    try:
        job = claim_next_job(db)
        if job is None:
            return False
        await run_job(db, job)
        return True
    finally:
        db.close()


async def run_forever() -> None:
    with SessionLocal() as db:
        requeue_stale_jobs(db)
    while True:
        try:
            found = await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never silent (DEF-036): a loop that keeps failing must say why.
            logger.exception("Queue worker iteration failed; retrying in %s s", POLL_SECONDS)
            found = False
        if not found:
            await asyncio.sleep(POLL_SECONDS)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    task = asyncio.current_task()
    try:
        # Docker stops containers with SIGTERM; Ctrl+C is handled by asyncio.run.
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    except NotImplementedError:  # Windows event loops have no signal handlers
        pass
    try:
        await wait_for_schema()
        logger.info("Queue worker started; one job at a time, polling every %s s", POLL_SECONDS)
        await run_forever()
    except asyncio.CancelledError:
        logger.info("Queue worker stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
