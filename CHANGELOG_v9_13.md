# UniSubmit AI v9.13

## One-command Docker deployment (`a2fa76d`)

- `docker compose up -d --build` now runs the full stack: the web app
  (port 8000), the AI service (port 8001), Qdrant (6333/6334) and
  PostgreSQL 16 (host port 5433, so a locally installed Postgres on 5432 is
  untouched). Previously the compose file ran only the web app and the AI
  service, Qdrant and the database each had to be started by hand.
- Added `ai_service/Dockerfile`: CPU-build torch (smaller than the default
  CUDA wheels), uvicorn on 8001, and `HF_HOME` pointed at a named volume so
  the ~3 GB BAAI/bge-m3 download happens once, not on every rebuild.
- Ollama stays on the host (Docker on macOS has no GPU access); containers
  reach it via `host.docker.internal:11434`, overridable with
  `DOCKER_OLLAMA_URL` in `.env`.
- Persistent named volumes: `pgdata` (database), `qdrant_storage` (vector
  index), `hf_cache` (embedding model), `app_storage` (uploads — a named
  volume rather than a bind mount so the stack runs regardless of Docker
  Desktop file-sharing settings).
- Host ports overridable without editing the file: `APP_PORT`,
  `AI_SERVICE_PORT`, `QDRANT_PORT`, `QDRANT_GRPC_PORT`, `DB_PORT`.
- Health-check-gated startup order: the app waits for Postgres, the AI
  service waits for Qdrant.
- Added `psycopg2-binary` to `requirements.txt`: the v9.12-era switch to a
  `postgresql://` `DATABASE_URL` had no driver listed, so a fresh install
  (and the Docker image) could not connect to PostgreSQL at all.
- Added the missing `ollama_url` field to `app/config.py`: `app/ai.py`
  reads `settings.ollama_url`, which raised `AttributeError` on the admin
  AI-status path.
- Added `.dockerignore` files for both build contexts, a "Run everything
  with Docker" section in `README.md`, and `*.db-shm`/`*.db-wal` to
  `.gitignore`.

## Program-guideline consistency gate (`DEF-023`, `4f49fc3`)

- Fixed `_program_guideline_consistency` in `ai_service/main.py`: a
  guideline template explicitly written for both B.Sc. and M.Sc. programs
  was blocked from activation for **every** program ("Program-guideline
  mismatch"), because the gate flagged any opposite-level degree marker in
  the text without checking whether the configured program's own level was
  also present. The mismatch now fires only when the guideline's degree
  statements exclude the configured level, so a dual-level guideline
  activates for either program while a genuinely wrong-level upload is
  still blocked.
- Extended the degree-marker vocabulary from four science-degree patterns
  to three full levels: loose `Bachelor's`/`Masters` wording now counts,
  and a doctoral level (`Doctor of Philosophy`, `Ph.D.`/`PhD`, `doctoral`)
  exists at all — previously a PhD program matched neither branch, so the
  gate was inert for doctoral programs. Program names are classified the
  same way, including dotless `MSc`/`BSc` forms that previously matched
  nothing.
- Re-run parse & index on any guideline blocked by the old gate; the
  blocked flag is stored in the parse result and clears on the next parse.

## Documentation

- Registered `DEF-023` in the defect register with its full narrative
  (class `nl-to-rule-translation`, severity blocking, theme T1 — the
  fail-closed counterpart of `DEF-020`/`DEF-022`). `docs/defects/README.md`
  counts, closed table and theme groupings updated (30 entries: 23 defects,
  7 limitations; 5 open, 25 closed).

## Verification & Regression Suite

- Added `ai_service/test_program_consistency.py` with 10 offline tests:
  the two dual-level reproductions, the two genuine mismatches that must
  keep blocking, a matching-level pass, loose "Masters programme" wording,
  PhD against doctoral-only and bachelor-only guidelines, a bachelor
  program against a doctoral-only guideline, and a guideline with no
  degree statement. Seen red against the pre-fix code (the two dual-level
  cases fail, the guards pass) and green against the fix, run inside the
  service container.
- Docker stack verified end to end: app serving on the containerised
  PostgreSQL with tables created and seeded, AI service pre-flight green
  (Ollama via `host.docker.internal`, Qdrant, embedding model) and
  `/health` reporting `rag: ready`.
