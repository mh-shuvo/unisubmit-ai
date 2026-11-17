"""P3.13: the job queue on PostgreSQL: claiming, fairness, back-off, stale
jobs and the one-active-job-per-subject rule."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from unisubmit import queue
from unisubmit.models import Job, JobStatus, JobType, University


def make_university(db: Session, slug: str) -> int:
    university = University(name=f"{slug.title()} University", slug=slug)
    db.add(university)
    db.flush()
    return university.id


def add_job(db: Session, university_id: int, subject: str, **fields: object) -> int:
    job = Job(
        university_id=university_id,
        type=fields.pop("type", JobType.VALIDATE_SUBMISSION),
        subject_key=subject,
        payload={},
        **fields,
    )
    db.add(job)
    db.flush()
    return job.id


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def university(db: Session) -> int:
    return make_university(db, "queue")


def test_claim_takes_the_oldest_queued_job(db: Session, university: int) -> None:
    first = add_job(db, university, "submission:1")
    add_job(db, university, "submission:2")

    claimed = queue.claim_next(db, "w1")

    assert (claimed.id, claimed.attempts) == (first, 1) if claimed else False


def test_claim_skips_a_job_whose_back_off_has_not_passed(db: Session, university: int) -> None:
    add_job(db, university, "submission:1", run_after=datetime.now(UTC) + timedelta(minutes=5))

    assert queue.claim_next(db, "w1") is None


def test_claim_prefers_the_university_that_used_the_model_least(db: Session) -> None:
    busy, quiet = make_university(db, "busy"), make_university(db, "quiet")
    add_job(
        db,
        busy,
        "submission:1",
        status=JobStatus.SUCCEEDED,
        locked_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    add_job(db, busy, "submission:2")
    expected = add_job(db, quiet, "submission:3")

    claimed = queue.claim_next(db, "w1")

    assert claimed is not None and claimed.id == expected


def test_emails_do_not_count_as_model_use(db: Session) -> None:
    mailing, other = make_university(db, "mailing"), make_university(db, "other")
    add_job(
        db,
        mailing,
        "email:1",
        type=JobType.SEND_EMAIL,
        status=JobStatus.SUCCEEDED,
        locked_at=datetime.now(UTC),
    )
    expected = add_job(db, mailing, "submission:1")
    add_job(db, other, "submission:2")

    claimed = queue.claim_next(db, "w1")

    assert claimed is not None and claimed.id == expected


def test_a_subject_has_at_most_one_active_job(db: Session, university: int) -> None:
    add_job(db, university, "submission:1")

    with pytest.raises(IntegrityError):
        add_job(db, university, "submission:1", status=JobStatus.RUNNING)


def test_a_finished_job_frees_its_subject(db: Session, university: int) -> None:
    add_job(db, university, "submission:1", status=JobStatus.FAILED)

    add_job(db, university, "submission:1")

    assert queue.has_active_job(db, "submission:1")


def test_transient_failure_backs_off_exponentially(db: Session, university: int) -> None:
    add_job(db, university, "submission:1", attempts=1)
    claimed = queue.claim_next(db, "w1")
    assert claimed is not None

    final = queue.retry_or_fail(db, claimed.id, "AI service down", max_attempts=3)

    job = db.get_one(Job, claimed.id)
    delay = job.run_after - datetime.now(UTC)
    assert (final, job.status, timedelta(minutes=3) < delay <= timedelta(minutes=4)) == (
        False,
        JobStatus.QUEUED,
        True,
    )


def test_last_attempt_fails_for_good(db: Session, university: int) -> None:
    add_job(db, university, "submission:1", attempts=2)
    claimed = queue.claim_next(db, "w1")
    assert claimed is not None

    final = queue.retry_or_fail(db, claimed.id, "AI service down", max_attempts=3)

    assert (final, db.get_one(Job, claimed.id).status) == (True, JobStatus.FAILED)


def test_stale_job_on_its_last_attempt_is_reported_for_the_failure_hook(
    db: Session, university: int
) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    job_id = add_job(
        db, university, "submission:1", status=JobStatus.RUNNING, locked_at=long_ago, attempts=3
    )

    requeued, failed = queue.requeue_stale(db, timedelta(minutes=35), max_attempts=3)

    assert (requeued, [job.id for job in failed]) == (0, [job_id])


def test_completing_a_job_queues_its_follow_up_on_the_same_subject(
    db: Session, university: int
) -> None:
    add_job(db, university, "template_version:1", type=JobType.PARSE_GUIDELINE)
    claimed = queue.claim_next(db, "w1")
    assert claimed is not None
    index = queue.FollowUp(JobType.INDEX_GUIDELINE, "template_version:1", {})

    queue.complete(db, claimed.id, [index])

    db.flush()
    active = db.query(Job).filter(Job.status == JobStatus.QUEUED).one()
    assert (active.type, active.university_id) == (JobType.INDEX_GUIDELINE, university)


def test_metrics_count_waiting_and_failed_jobs(db: Session, university: int) -> None:
    add_job(db, university, "submission:1")
    add_job(
        db,
        university,
        "submission:2",
        status=JobStatus.FAILED,
        finished_at=datetime.now(UTC),
    )

    metrics = queue.metrics(db)

    assert (metrics.waiting, metrics.failed_24h) == ([("Queue University", 1, 0)], 1)


def test_two_workers_never_claim_the_same_job(
    committed_session_factory: sessionmaker[Session],
) -> None:
    with committed_session_factory.begin() as db:
        university = make_university(db, "race")
        for number in range(2):
            add_job(db, university, f"submission:{number}")
    barrier = threading.Barrier(2)
    claimed: list[int] = []

    def claim(worker: str) -> None:
        with committed_session_factory() as db, db.begin():
            barrier.wait()
            job = queue.claim_next(db, worker)
            if job is not None:
                claimed.append(job.id)
            barrier.wait()  # both hold their row locks until each has claimed

    threads = [threading.Thread(target=claim, args=(f"w{n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(claimed)) == 2
