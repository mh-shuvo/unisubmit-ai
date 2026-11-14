# UniSubmit AI v10.0

Phase 1 of the production restructuring plan: one uv workspace, the code
moved into `services/`, linting, type checks, a test harness on PostgreSQL,
CI and schema migrations. No feature changes. Branch
`phase-1-tooling-layout-migrations`.

## Upgrade steps

`docs/runbook-demo-mac.md` ("Upgrading a v9.14 install to v10.0") has the
same steps in order.

- Install uv and run `uv sync --all-packages` from the repository root. It
  replaces both `requirements.txt` files and `ai_service/.venv`.
- `.env`: rename `APP_SECRET` to `UNISUBMIT_SECRET_KEY`, add
  `UNISUBMIT_AI_SERVICE_TOKEN=${AI_SERVICE_TOKEN}`, and move the AI settings
  from `ai_service/.env` into it. One `.env` now serves the web app
  (`UNISUBMIT_*`), the AI service and compose; see `.env.example`.
- `docker compose up -d --build`. The new `migrate` service adopts the
  existing database at the baseline revision before the app and worker
  start. `docker-compose.yml` is now `compose.yaml`.
- Start the AI service with
  `uv run uvicorn unisubmit_ai.main:app --host 127.0.0.1 --port 8001`.
- Commands renamed: `python -m app.seed super-admin` →
  `unisubmit create-super-admin`, `python -m app.seed demo-data` →
  `unisubmit seed-demo`, `python -m app.worker` →
  `python -m unisubmit.worker`, `uvicorn app.main:app` →
  `uvicorn unisubmit.main:create_app --factory`.

## Layout and dependencies (P1.1, P1.2)

- `app/` → `services/core/src/unisubmit/`, `ai_service/` →
  `services/ai/src/unisubmit_ai/`, tests beside each service.
- One `uv.lock` for both services on Python 3.13. The web app moves from
  FastAPI 0.115 / Starlette 0.46 / SQLAlchemy 2.0 to FastAPI 0.162 /
  Starlette 1.10 / SQLAlchemy 2.1.
- The embedding stack is pinned to the versions the existing Qdrant index was
  built with (torch 2.14.1, transformers 5.20.1, sentence-transformers
  6.1.0), so `uv lock` cannot change embeddings as a side effect.
- Linux builds take torch from the CPU-only index; macOS keeps MPS.
- The storage directory is a setting (`UNISUBMIT_STORAGE_DIR`) instead of a
  path derived from where the package is installed.

## Code quality (P1.3, P1.4, P1.10, P1.11)

- Ruff formats the whole codebase (one formatting-only commit, listed in
  `.git-blame-ignore-revs`) and lints it. Rules each legacy module broke are
  listed per file; new code is held to all of them.
- mypy in strict mode for new code (`unisubmit.core`, `unisubmit.cli`,
  `unisubmit.seed`, `unisubmit.security`, migrations). Legacy modules are
  listed as a baseline until they are typed.
- import-linter: the two services never import each other, and in the core
  `core/` imports nothing above it.
- CI (`.github/workflows/ci.yml`) runs all of the above, the migrations
  check and every test suite on PostgreSQL 16 (closes `LIM-006`: green on
  pull request #3).
- pre-commit hooks run the same locked tools.

## Settings, database and app factory (P1.5, P1.6, P1.7)

- `core/config.py`: `UNISUBMIT_` prefix, secrets as `SecretStr`. A missing
  or weak value stops startup with one message naming every variable to set.
  `postgresql://` URLs are switched to psycopg 3.
- `core/db.py`: engine factory (pool, 30 s statement timeout), constraint
  naming convention, `get_session` and `DbSession`; every route uses it.
  psycopg2 is replaced by psycopg 3.
- `create_app()` with a lifespan; routes live on an `APIRouter`.
  `@app.on_event` and `create_all` at startup are gone.

## Migrations (P1.8, `DEF-039`)

- Alembic, with the migrations inside the package and baseline revision
  `0001` generated from the models.
- `unisubmit migrate` upgrades the schema. A database created by
  `create_all` before v10.0 is adopted: constraints Postgres named itself are
  renamed to the baseline's names, and the database is stamped at `0001`
  only if it then matches the models exactly. Otherwise the differences are
  listed and nothing changes.
- Verified on a database built by the v9.14 code and on a schema-only copy of
  the demo database: both adopted, with a schema identical to a fresh one.

## Tests (P1.9)

- pytest on PostgreSQL: `TEST_DATABASE_URL` (a database whose name contains
  `test`) is rebuilt with `unisubmit migrate` once per run, and each test
  runs in a transaction that is rolled back. The SQLite stand-in is gone, so
  row locking is always tested.
- New: migration tests, and `PageRenderingTests`, which opens every page as
  the role that may see it.
- Result: 146 passed, 1 expected failure (`DEF-021`, open).

## Docker (P1.12)

- `compose.yaml` with a one-shot `migrate` service; `app` and `worker` start
  after it succeeds. The web app and worker stay in Docker on the Mac while
  stored file paths are container-specific (`DEF-042`).
- The core image runs as a non-root user; `migrate` hands a storage volume
  written by the root-run v9 containers to that user.

## Defects found and fixed in this phase

- `DEF-046`: an upload's version and job were saved in two commits, so the
  row lock was released before the job existed and two quick uploads could
  both be queued. Found when psycopg 3 changed the timing of the DEF-026 test.
- `DEF-047`: Starlette 1.x removed `TemplateResponse(name, context)`; every
  page returned 500 after the upgrade. No test had rendered a page.
- `DEF-048`: the uv-built images pointed every console script at an
  interpreter that did not exist, so no container could start.

`DEF-047` and `DEF-048` were introduced and fixed on this branch; both were
found by rehearsing the upgrade in Docker on a database and storage volume
written by v9.14.

## Not in this phase

- `str_enum`, the `packages/contracts` package, `fakes.py` and the
  `respx`-based client tests from the plan's layout: nothing uses them yet.
  They arrive with the code that needs them (Phases 3 and 6).
- `docker compose up -d` starting only PostgreSQL and Qdrant on the Mac
  waits for `DEF-042` (task P2.9).
