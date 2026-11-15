# UniSubmit AI v10.2

Phase 3 of the restructuring plan: one PostgreSQL job queue for all background
work, and uploads that are checked, fingerprinted and capped. Branch
`phase-1-tooling-layout-migrations`, on top of Phases 1 and 2 (pull request #3).

## Upgrade steps

- Nothing to run by hand: `docker compose up -d --build` applies migrations
  `0003` and `0004`. Back up the `pgdata` volume first.
- `0003` copies the `ai_jobs` history into `jobs`, keeping job ids. A job that
  was running when the worker stopped is queued again. `ai_jobs` stays, unread,
  until a later release drops it.
- `0004` maps free-text statuses onto fixed values. A version or submission
  whose status matches nothing becomes `VALIDATION FAILED`, so the student can
  upload again; parsed guideline rules whose status matches nothing need
  activating again. `Validation Failed` is now spelt `VALIDATION FAILED`.
- New optional settings, with their defaults: `UNISUBMIT_MAX_UPLOAD_MB=50`,
  `UNISUBMIT_DAILY_UPLOADS_PER_STUDENT=10`,
  `UNISUBMIT_DAILY_UPLOADS_PER_UNIVERSITY=300`, `UNISUBMIT_MAX_JOB_ATTEMPTS=3`,
  `UNISUBMIT_JOB_TIMEOUT_S=2100`.

## Job queue (P3.1, P3.2, P3.7)

- New `jobs` table: job kind, status (`queued`, `running`, `succeeded`,
  `failed`), a subject such as `submission:42`, a payload of ids, attempts, a
  `run_after` time for back-off, the claiming worker and the last error. A
  partial unique index allows one active job per subject, so the database
  itself refuses a duplicate.
- `queue.py` claims with `FOR UPDATE SKIP LOCKED`, taking the oldest due job of
  the university that used the model least in the last hour; emails do not
  count as model use. A job can name follow-up jobs, queued in the transaction
  that completes it.

## Worker and handlers (P3.3-P3.6)

- One handler per job kind in `handlers.py`. A failure the next attempt may
  not repeat (the AI service unreachable or answering 429, 502, 503 or 504;
  SMTP down) is retried after 2, then 4 minutes; anything else fails the job at
  once, so an unreadable PDF no longer costs three model runs. A final failure
  marks the version, guideline or email as failed, so nothing is left showing
  "in progress".
- Stopping the worker still puts its job back at once without using an attempt;
  a killed worker's job is requeued after `UNISUBMIT_JOB_TIMEOUT_S`.
- Validation reads its inputs in one short transaction, calls the AI service
  with no transaction open, and writes its results in a second. Running it
  again overwrites its results.
- "Parse & Index" queues a parsing job and returns at once; a successful parse
  queues the indexing job, so indexing can be retried without parsing again.
  The guideline page shows the latest job, its attempt and last error, and
  refreshes while one is active (`DEF-038`).
- The supervisor email is its own job, queued only for a PASSED version without
  `emailed_at`; an SMTP failure no longer touches the validation (`DEF-051`).
  New email status values: `Queued`, `SMTP Not Configured`, `Failed`.

## Uploads (P3.8, P3.11)

- Uploads are copied to a `.part` file and renamed when complete; the PDF
  signature and the size limit are checked during the copy, and a rejected
  upload leaves no file. Guideline uploads get the same checks.
- Versions record `sha256`, `size_bytes` and `page_count` (empty for older
  versions). Uploading the latest version's file again queues nothing, unless
  that version's validation failed.
- A repeated first submission of the same file for the same guideline, while
  the first is still queued or validating, opens the existing submission
  instead of creating another (`DEF-037`).
- An upload over the daily cap, per student or per university, is refused with
  a message and queues nothing. The caps count versions created in the last 24
  hours.
- Refusals (still validating, over a cap, not a PDF, too large) are shown on
  the page as messages rather than as HTTP 409 or 400 bodies, because every
  upload comes from a server-rendered form.

## Statuses and stored results (P3.9, P3.10)

- Submission, version, email, guideline, RAG, parsed-rules and AI-profile
  statuses are `StrEnum`s stored as text with CHECK constraints. The values are
  the labels the pages already showed, so templates are unchanged.
- `validation_json` and `format_profile_json` are JSONB. Code that changes a
  loaded profile works on a copy, because SQLAlchemy only notices a new object.

## Pages and metrics (P3.12)

- The unit queue page lists every job kind with its subject, attempts, retry
  time and last error. Students see their version's status, never job errors.
- The Platform Admin's AI page shows jobs waiting and running per university,
  the oldest wait, failures in 24 hours, and p50 and p95 durations per job
  kind.

## Tests (P3.13)

- `tests/integration/test_job_queue.py`: claiming order, back-off, fairness,
  emails outside fairness, the one-active-job index, follow-ups, stale jobs on
  their last attempt, metrics, and two workers on two connections never taking
  the same job.
- Worker, validation, email and guideline handler tests with the AI service
  and SMTP mocked; upload tests for fingerprints, the same file twice, a failed
  version retried, non-PDF and oversized files leaving nothing behind, the
  daily cap and `DEF-037`; migration tests for the `ai_jobs` copy and the
  status and JSON conversion of a v9.14 database.
- Result: 205 passed, 1 expected failure (`DEF-021`).

## Defects

- Closed: `DEF-037` (double submission), `DEF-038` (guideline work inside the
  request). Every route that holds a database session is now a plain `def`.
- `DEF-051`: an SMTP error after a passed validation re-ran the validation
  twice and then marked it failed. Found while splitting out the email.

## Verified

The real worker against PostgreSQL 16 with the AI service stopped: a
validation needing the AI service was requeued with a 2-minute back-off and
its error; a validation without it passed, and its email job recorded
"SMTP Not Configured". SIGTERM stopped the worker cleanly.

## Not in this phase

- While a job waits for its retry, its version still shows PROCESSING.
- A guideline whose re-parse fails for good loses its parsed rules, including
  rules an administrator added by hand. This was already the behaviour when
  parsing ran in the request; it is not yet registered as a defect.
- Uploads still carry no CSRF token (`DEF-041`, Phase 4).
- The per-university cap is checked without a university-wide lock, so two
  students uploading at the same moment can pass it by one.
