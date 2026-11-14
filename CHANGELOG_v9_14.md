# UniSubmit AI v9.14

Phase 0 of the production restructuring plan: the fixes a live demo could
hit, made without schema changes, because the project has no migrations yet
(`DEF-039`). Branch `phase-0-demo-critical-fixes`.

## Upgrade steps

- Set `APP_SECRET`, `AI_SERVICE_TOKEN` and `POSTGRES_PASSWORD` in `.env`,
  and the same `AI_SERVICE_TOKEN` in `ai_service/.env`
  (`openssl rand -hex 32` for each).
  Nothing starts without them. An existing `pgdata` volume keeps its
  original password, `unisubmit`.
- Run the queue worker: `python -m app.worker` natively, or the new `worker`
  compose service. Without it, submissions queue but are never validated.
- Create the first Super Admin with
  `python -m app.seed super-admin --email …`; the demo account is gone.
- On the Mac, follow `docs/runbook-demo-mac.md`: Ollama and the AI service
  run natively, the rest in Docker. Remove the old container with
  `docker compose rm -sf ai-service`.
- No re-index is needed. Existing guidelines keep their collections; new
  indexing uses `tv_<template version id>`.

## Queue and web process (`DEF-024`, `DEF-025`, `DEF-034`, `DEF-036`, `DEF-045`)

- The validation worker runs as its own process, `python -m app.worker`
  (`app/worker.py`), and as the `worker` compose service. It used to run
  inside the web server's event loop, where PDF analysis, report rendering
  and SMTP froze every page during a validation.
- Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so a second worker
  can never take the same job.
- A job interrupted by stopping the worker goes back to the queue without
  using an attempt; a job left `PROCESSING` by a worker that died is requeued
  once it is older than the AI timeout plus five minutes.
- Job failures are rolled back, logged with the job id, and recorded; the
  worker loop no longer swallows errors silently.
- Six routes that did synchronous database and file work as `async def` are
  plain `def`. `index_guideline` remains, pending `DEF-038`.
- The worker waits for the web app to create the schema instead of creating
  it too (a race found while verifying this release); `app` and `worker`
  restart unless stopped.

## Uploads (`DEF-026`)

- Uploading a revised version locks the submission row, numbers the version
  from the highest existing number, and is refused while the submission's
  previous upload is still queued or running. The submission page shows the
  reason. A double-click no longer produces two "Version 2" rows and two
  model runs.

## Security defaults (`DEF-027` to `DEF-030`)

- `APP_SECRET` is required, at least 32 characters, and the published
  placeholders are rejected. Compose has no fallback value.
- The web startup no longer creates `admin@unisubmit.local` / `Admin@123`.
  `python -m app.seed demo-data` creates the sample structure;
  `python -m app.seed super-admin` creates an admin with a prompted password
  of at least 12 characters.
- The AI service requires `Authorization: Bearer <AI_SERVICE_TOKEN>` on every
  route except `/` and `/health`, refuses to start without a token, and the
  web app sends it on every call.
- Every published port is bound to `127.0.0.1`, and the PostgreSQL password
  comes from `.env`. Qdrant runs without an API key: the AI service connects
  to it by URL, and only this machine can reach it.

## AI service (`DEF-031`, `DEF-032`, `DEF-033`, `DEF-035`, `LIM-008`)

- Qdrant pinned to `v1.19.2`, the version that wrote the existing volume.
- Guideline collections are named by template version id (`tv_<id>`);
  validation reads the collection recorded at indexing. Names derived from
  display names collided for programs such as `M.Sc. (CSE)` and `M.Sc. CSE`.
  The service accepts an optional, validated `collection` on index, search,
  validate and format-profile requests.
- Embedding and Qdrant calls run in worker threads, so `/health` answers
  during a validation (median 0.17 s across a 368 s live run).
- Validation and guideline parsing use the configured `AI_SERVICE_URL`; the
  per-profile URL, which pointed the seeded profile at the app container
  itself, is no longer used for routing.
- `EMBEDDING_DEVICE` selects the embedding device (unset: CUDA, then Apple
  MPS, then CPU); the device in use is reported at startup and in `/health`.

## Tools and documentation

- `tools/measure_validation.py` times one validation stage by stage without
  touching the queue (task P0.11). On the Apple M1 demo machine,
  `qwen3:14b`, a 73-page thesis took 367.7 s, 366.6 s of it in the single
  semantic batch; the existing timeouts are kept.
- `docs/runbook-demo-mac.md`: what runs where, start order, expected timing,
  troubleshooting.
- `README.md`: worker, secrets, seeding and Docker sections rewritten; demo
  credentials removed. `.env.example` files list every required secret.
- Defect register: 23 entries added (`DEF-024` to `DEF-045`, `LIM-008`);
  15 closed by this release, 8 open and planned in later phases
  (`DEF-037` to `DEF-044`). Five classes and themes `T7` and `T8` added to
  `docs/defects/CONVENTIONS.md`. 53 entries: 45 defects, 8 limitations;
  13 open, 40 closed.

## Verification & Regression Suite

- 28 new web-app tests (`tests/test_queue_and_uploads.py`,
  `tests/test_settings_and_deployment.py`, fixtures in `tests/support.py`) and
  13 new AI-service tests (`ai_service/test_service_hardening.py`). Every
  test written for a defect was seen red on the pre-fix code; the guard tests
  (behaviour that must not change) pass on both. The upload lock and the
  worker rollback were also seen red with only that change reverted. The
  existing AI tests now send the service token.
- The concurrent-upload test runs against PostgreSQL when `TEST_POSTGRES_URL`
  is set: `[1, 2, 2]` before the fix, `[1, 2]` after.
- Web app: 94 tests pass (one PostgreSQL test skipped without
  `TEST_POSTGRES_URL`). AI service: 41 of 42 pass; the one failure is the
  already-registered open `DEF-021`.
- The new compose file was booted as a separate project on fresh volumes:
  worker waited for the schema and started, the app container reached the
  native AI service on the host with the token (401 without it), a queued
  73-page thesis went to `COMPLETED` in one attempt, and an immediate second
  upload was refused.
