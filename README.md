# UniSubmit AI v6

A role-scoped, mobile-friendly academic pre-submission system with an isolated local AI service.

Known defects, fixed defects and standing limitations are recorded in the
[defect register](docs/defects/README.md).

## Core roles

Several universities share one installation. Each is a separate tenant: its
units, guidelines, accounts and files are invisible to the others.

### Platform Admin
- Create a university together with its first University Admin
- Rename a university, or deactivate it, which signs out all its users
- AI service monitoring
- Belongs to no university and sees no university's academic data

### University Admin (was Super Admin)
- Create Faculty, Department and Institute records for one university
- Create Department/Institute Admin accounts and assign each to one unit
- Does **not** manage unit programs, supervisors or student submissions

### Department / Institute Admin
Restricted to the assigned academic unit:
- Manage Programs
- Manage unit-level Supervisor Directory
- Configure Thesis / Project Report / Internship Report / Field Report / Capstone
- Upload Primary Guideline PDF (source of truth)
- Optionally upload an Additional Rules PDF
- Add explicit deterministic restrictions
- Version and activate/archive guideline configurations
- Trigger RAG indexing
- View own unit submissions, queue and validation feedback

### Student
- Register with email under a Department or Institute, not a Program
- At submission time, see only Programs under that Department/Institute
- Choose an available submission type/configuration
- Choose a Supervisor from the unit-level directory
- Upload PDF
- Track queue, feedback, versions and email delivery in a private dashboard

## Guideline design
Academic rules are not manually retyped into the application. The official guideline PDF is indexed by the AI service and used as the primary validation source. An optional Additional Rules PDF can be indexed into the same program/version RAG collection. Admins can still add special explicit restrictions such as searchable PDF, file-size limit or minimum pages.

## Queue
Every uploaded version creates an AI job. A separate worker process,
`python -m unisubmit.worker`, takes one job at a time until the queue is empty,
then waits for new jobs. It never runs inside the web server, so pages stay
responsive while a thesis is validated. Failed jobs retry up to 3 times and do
not block the rest of the queue. A job left running by a worker that stopped is
put back in the queue.

## Repository layout

| Path | What |
|---|---|
| `services/core/src/unisubmit/` | Web app (`main.py`), queue worker, `unisubmit` CLI, migrations |
| `services/ai/src/unisubmit_ai/` | AI service: guideline parsing, RAG indexing, semantic validation |
| `services/*/tests/` | Test suites; the core's `integration/` tests need PostgreSQL |
| `pyproject.toml`, `uv.lock` | One uv workspace for both services, Python 3.13 |
| `compose.yaml` | PostgreSQL, Qdrant, migrations, web app, worker; AI service under profile `ai` |

## Configuration
Everything is read from one `.env` in the repository root; copy
`.env.example`. The web app, worker and CLI read `UNISUBMIT_*` variables, the
AI service reads the plain names, and docker compose reads both. Nothing has a
default secret; generate each with `openssl rand -hex 32`:

| Variable | Used by |
|---|---|
| `UNISUBMIT_SECRET_KEY` | web app: signs session cookies |
| `AI_SERVICE_TOKEN` | AI service, and compose passes it to the web app and worker |
| `UNISUBMIT_AI_SERVICE_TOKEN` | web app and worker run natively; `.env.example` sets it to `${AI_SERVICE_TOKEN}` |
| `POSTGRES_PASSWORD` | PostgreSQL in Docker (an existing volume keeps the password it was created with) |

A missing or weak secret stops the web app at startup with the variables to
set. Qdrant needs no key: the AI service connects to it by URL (`QDRANT_URL`),
and its port is bound to `127.0.0.1`.

## Run with Docker

```bash
docker compose up -d --build
```

This starts PostgreSQL and Qdrant, runs `unisubmit migrate` once, then starts
the web app and the queue worker. On an Apple Silicon Mac, Ollama and the AI
service run natively so they can use the GPU: see
[`docs/runbook-demo-mac.md`](docs/runbook-demo-mac.md). On a Linux host, run
the AI service in Docker too:

```bash
DOCKER_AI_SERVICE_URL=http://ai-service:8001 docker compose --profile ai up -d --build
```

- App: http://127.0.0.1:8000 (admin: http://127.0.0.1:8000/admin/login)
- Qdrant: http://127.0.0.1:6333/dashboard
- PostgreSQL: host port 5433, user `unisubmit`, password from `POSTGRES_PASSWORD`

Every port is bound to `127.0.0.1`, so nothing is reachable from another
machine. Create the first Platform Admin once, then sign in and create a
university with its University Admin under **Universities**:

```bash
docker compose exec app unisubmit create-platform-admin --email you@example.edu
```

Add `docker compose exec app unisubmit seed-demo` for a demo university with a
sample faculty, programs and supervisors. Students register at
`/u/<university>/register`; the home page lists every active university. Stop everything with `docker compose down` (add `-v`
to also delete the database, Qdrant index and model cache).

## Database schema
The schema is managed by Alembic. `unisubmit migrate` creates or upgrades it;
the web app and the worker never create tables. A database created before
v10.0 is adopted on its first `migrate`: its constraints are renamed to the
migration names and it is stamped at the baseline, or refused with a list of
differences if it does not match the models.

After changing a model, add a migration and check it:

```bash
uv run alembic -c services/core/alembic.ini revision --autogenerate --rev-id 0002 -m "what changed"
uv run alembic -c services/core/alembic.ini check
```

## Run natively (development)
Install [uv](https://docs.astral.sh/uv/), then from the repository root:

```bash
uv sync --all-packages
cp .env.example .env                       # and fill it in
uv run unisubmit migrate
uv run unisubmit create-platform-admin --email you@example.edu
uv run uvicorn unisubmit.main:create_app --factory --reload --port 8000
uv run python -m unisubmit.worker
uv run uvicorn unisubmit_ai.main:app --host 127.0.0.1 --port 8001
```

The web app, the worker and the AI service are separate processes; run each in
its own terminal. There is no built-in admin account;
`unisubmit create-platform-admin` asks for a password of at least 12 characters.

AI health: http://127.0.0.1:8001/health (open; every other route needs the
`AI_SERVICE_TOKEN` as `Authorization: Bearer <token>`). The embedding model
uses the GPU when one is visible (CUDA, or Apple MPS when run natively on a
Mac); `/health` reports the device. Set `EMBEDDING_DEVICE` to override.

## Checks
CI runs these on every pull request; run them before pushing:

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy services/core/src services/ai/src
uv run lint-imports
TEST_DATABASE_URL=postgresql+psycopg://unisubmit:PASSWORD@127.0.0.1:5433/unisubmit_test uv run pytest
```

`TEST_DATABASE_URL` must name a disposable database whose name contains
`test`: the suite drops and rebuilds its schema. Without it the database tests
are skipped. `uv run pre-commit install` runs the same tools on every commit.

## v9 guideline-first rule parsing and validation

Re-index each active guideline after upgrading. The AI service now parses the approved guideline PDF itself before validation. Qwen extracts only explicit rules from the guideline text into a structured validation profile. Admin reviews and activates that profile; rules are not manually retyped.

Deterministic rules such as paper size, font, font size, margins, line spacing, page limits, required sections, word limits, page numbering and selected heading rules are checked by Python/PyMuPDF. Semantic requirements are checked by Qwen using targeted BGE-M3/Qdrant retrieval from the same guideline. A labelled front/title-page sample is also captured as a normalized cover-layout profile for first-page layout checking.

Same-type failures across multiple pages are consolidated while preserving affected page numbers, occurrence count, snippets, coordinates, expected vs detected values, correction guidance and guideline source page/section.

## v9.2 model configuration

The default local LLM is now `qwen3:32b`. Run `ollama pull qwen3:32b` before starting the AI service. The AI service reads `ai_service/.env`; copy `ai_service/.env.example` to `ai_service/.env` and edit it whenever you want to switch models without changing code. (Since v10.0 the AI service reads the root `.env`.)

If upgrading with an existing SQLite database, previously saved AI profiles can still point to `qwen3:14b`. Update the Base model field in **Admin → AI Management** to `qwen3:32b` for those profiles.

## v9.3 parser hardening
The guideline parser now uses strict structured extraction with one JSON-repair retry, a stronger standard-text fallback parser, cover-page pattern inference, coverage auditing, and a program-guideline consistency gate. Re-parse each active guideline after upgrading.


## v9.4 validation hardening

Margin checks now validate body-text boundary intrusion rather than treating unused whitespace as margin error. Required chapter matching tolerates `CHAPTER N:` prefixes and excludes TOC entries. Cover-page comparison is relative and anchor-based to avoid false failures when the sample is embedded inside the guideline page.
