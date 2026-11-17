"""Queue worker. Runs as its own process:

    python -m unisubmit.worker

It used to run inside the web process, where the PDF analysis, report
rendering and SMTP work of every job froze all pages while it ran (DEF-024).
One worker process takes one job at a time, matching the single local model.
The jobs and their states are in `queue.py`, the work itself in `handlers.py`.

Stopping the worker (SIGTERM, Ctrl+C) gives the current job back to the queue
at once, without using up an attempt. A worker that is killed outright leaves
its job running; another worker, or this one after a restart, requeues it once
`job_timeout_s` has passed (DEF-034).
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
from collections.abc import Mapping
from datetime import timedelta

import sqlalchemy as sa
import structlog
from sqlalchemy.orm import Session, sessionmaker

from . import handlers, queue
from .core.config import Settings, get_settings
from .core.db import create_db_engine, make_session_factory
from .core.logging import configure_logging
from .handlers import JobHandler
from .models import Job, JobType

logger = logging.getLogger("unisubmit.worker")

POLL_SECONDS = 2
REAP_EVERY_SECONDS = 60

Handlers = Mapping[JobType, JobHandler]


def _final_failure(handler: JobHandler, job: queue.ClaimedJob) -> None:
    try:
        handler.on_final_failure(job.payload)
    except Exception:
        logger.exception("Recording the failure of job %s (%s) failed", job.id, job.type)


def requeue_stale_jobs(
    sessions: sessionmaker[Session], handlers: Handlers, settings: Settings
) -> int:
    """Put jobs abandoned by a dead worker back in the queue (DEF-034)."""
    with sessions.begin() as db:
        requeued, failed = queue.requeue_stale(
            db, timedelta(seconds=settings.job_timeout_s), settings.max_job_attempts
        )
    if requeued:
        logger.warning("Requeued %s job(s) abandoned by a stopped worker", requeued)
    for job in failed:
        logger.error("Job %s (%s) was abandoned on its last attempt", job.id, job.type)
        _final_failure(handlers[job.type], job)
    return requeued


async def run_once(
    sessions: sessionmaker[Session], handlers: Handlers, settings: Settings, worker_id: str
) -> bool:
    """Run at most one job; returns whether one was found."""
    with sessions.begin() as db:  # commits at once: the row lock lasts milliseconds
        job = queue.claim_next(db, worker_id)
    if job is None:
        return False
    handler = handlers[job.type]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        job_id=job.id, request_id=job.payload.get("request_id") or f"job-{job.id}"
    )
    try:
        follow_ups = await handler.run(job.payload)
    except asyncio.CancelledError:
        with sessions.begin() as db:
            queue.release(db, job.id, "The worker was stopped during this job; requeued.")
        logger.warning("Worker stopped during job %s (%s); requeued", job.id, job.type)
        raise
    except queue.TransientError as exc:
        logger.warning("Job %s (%s) attempt %s failed: %s", job.id, job.type, job.attempts, exc)
        with sessions.begin() as db:
            final = queue.retry_or_fail(db, job.id, str(exc), settings.max_job_attempts)
        if final:
            _final_failure(handler, job)
    except Exception as exc:
        # Never silent (DEF-036), and never retried: the same input fails the same way.
        logger.exception("Job %s (%s) failed", job.id, job.type)
        with sessions.begin() as db:
            queue.fail(db, job.id, f"{type(exc).__name__}: {exc}")
        _final_failure(handler, job)
    else:
        with sessions.begin() as db:
            queue.complete(db, job.id, follow_ups)
    return True


async def run_forever(
    sessions: sessionmaker[Session], handlers: Handlers, settings: Settings, worker_id: str
) -> None:
    next_reap = 0.0
    while True:
        try:
            if time.monotonic() >= next_reap:
                requeue_stale_jobs(sessions, handlers, settings)
                next_reap = time.monotonic() + REAP_EVERY_SECONDS
            found = await run_once(sessions, handlers, settings, worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never silent (DEF-036): a loop that keeps failing must say why.
            logger.exception("Queue worker iteration failed; retrying in %s s", POLL_SECONDS)
            found = False
        if not found:
            await asyncio.sleep(POLL_SECONDS)


def schema_ready(engine: sa.Engine) -> bool:
    return sa.inspect(engine).has_table(Job.__tablename__)


async def wait_for_schema(engine: sa.Engine) -> None:
    """`unisubmit migrate` creates the tables; the worker only waits for them.

    Two processes creating tables on an empty database raced, and the loser
    crashed (DEF-045).
    """
    while not schema_ready(engine):
        logger.info("Waiting for the database schema; run `unisubmit migrate`")
        await asyncio.sleep(POLL_SECONDS)


async def main() -> None:
    settings = get_settings()
    configure_logging(json=settings.log_json)
    task = asyncio.current_task()
    # Docker stops containers with SIGTERM; Ctrl+C is handled by asyncio.run.
    # Windows event loops have no signal handlers.
    if task is not None:
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    engine = create_db_engine(str(settings.database_url))
    try:
        await wait_for_schema(engine)
        sessions = make_session_factory(engine)
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        logger.info("Queue worker %s started; one job at a time", worker_id)
        await run_forever(sessions, handlers.registry(sessions), settings, worker_id)
    except asyncio.CancelledError:
        logger.info("Queue worker stopped")
    finally:
        engine.dispose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
