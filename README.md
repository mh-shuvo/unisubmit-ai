# UniSubmit AI v6

A role-scoped, mobile-friendly academic pre-submission system with an isolated local AI service.

Known defects, fixed defects and standing limitations are recorded in the
[defect register](docs/defects/README.md).

## Core roles

### Super Admin
- Create Faculty, Department and Institute records
- Create Department/Institute Admin accounts and assign each to one unit
- View system structure
- High-level AI service monitoring
- System, SMTP, audit and security settings
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
`python -m app.worker`, takes one job at a time until the queue is empty, then
waits for new jobs. It never runs inside the web server, so pages stay
responsive while a thesis is validated. Failed jobs retry up to 3 times and do
not block the rest of the queue. A job left running by a worker that stopped is
put back in the queue.

## Secrets
Nothing has a default secret. Set these in `.env` before the first start, each
generated with `openssl rand -hex 32`:

| Variable | Used by |
|---|---|
| `APP_SECRET` | web app and worker: signs session cookies |
| `AI_SERVICE_TOKEN` | web app, worker and AI service: every AI call needs it |
| `QDRANT_API_KEY` | Qdrant and the AI service |
| `POSTGRES_PASSWORD` | PostgreSQL in Docker (an existing volume keeps the password it was created with) |

The AI service reads `ai_service/.env` when run natively, so `AI_SERVICE_TOKEN`
and `QDRANT_API_KEY` go there as well.

## Run with Docker

```bash
docker compose up -d --build
```

This starts the web app, the queue worker, Qdrant and PostgreSQL. On an Apple
Silicon Mac, Ollama and the AI service run natively so they can use the GPU:
see [`docs/runbook-demo-mac.md`](docs/runbook-demo-mac.md). On a Linux host,
run the AI service in Docker too:

```bash
DOCKER_AI_SERVICE_URL=http://ai-service:8001 docker compose --profile ai up -d --build
```

- App: http://127.0.0.1:8000 (admin: http://127.0.0.1:8000/admin/login)
- Qdrant: http://127.0.0.1:6333/dashboard (needs `QDRANT_API_KEY`)
- PostgreSQL: host port 5433, user `unisubmit`, password from `POSTGRES_PASSWORD`

Every port is bound to `127.0.0.1`, so nothing is reachable from another
machine. Create the first Super Admin once:

```bash
docker compose exec app python -m app.seed super-admin --email you@example.edu
```

Add `python -m app.seed demo-data` for the sample faculty, programs and
supervisors. Stop everything with `docker compose down` (add `-v` to also
delete the database, Qdrant index and model cache).

## Run Core System natively
Recommended Python: 3.13

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m app.seed super-admin --email you@example.edu
uvicorn app.main:app --reload --port 8000
python -m app.worker
```

The web server and the worker are two processes; run each in its own terminal.

Public portal: http://127.0.0.1:8000
Admin login: http://127.0.0.1:8000/admin/login

There is no built-in admin account. `python -m app.seed super-admin` asks for a
password of at least 12 characters.

## Run Isolated AI Service
The AI service is in `ai_service/` and is separate from the core system.

It is designed for:
- Ollama + Qwen3:14b
- BGE-M3
- Qdrant
- RAG indexing/search/validation

```bat
cd ai_service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8001
```

AI health: http://127.0.0.1:8001/health (open; every other route needs the
`AI_SERVICE_TOKEN` as `Authorization: Bearer <token>`).
The embedding model uses the GPU when one is visible (CUDA, or Apple MPS when
run natively on a Mac); `/health` reports the device. Set `EMBEDDING_DEVICE`
to override.

## Development note
SQLite and the database-backed queue are suitable for an MVP / single-server deployment. For larger production deployment, keep the same workflow and move to PostgreSQL with more worker processes.


## v9 guideline-first rule parsing and validation

Re-index each active guideline after upgrading. The AI service now parses the approved guideline PDF itself before validation. Qwen extracts only explicit rules from the guideline text into a structured validation profile. Admin reviews and activates that profile; rules are not manually retyped.

Deterministic rules such as paper size, font, font size, margins, line spacing, page limits, required sections, word limits, page numbering and selected heading rules are checked by Python/PyMuPDF. Semantic requirements are checked by Qwen using targeted BGE-M3/Qdrant retrieval from the same guideline. A labelled front/title-page sample is also captured as a normalized cover-layout profile for first-page layout checking.

Same-type failures across multiple pages are consolidated while preserving affected page numbers, occurrence count, snippets, coordinates, expected vs detected values, correction guidance and guideline source page/section.

## v9.2 model configuration

The default local LLM is now `qwen3:32b`. Run `ollama pull qwen3:32b` before starting the AI service. The AI service reads `ai_service/.env`; copy `ai_service/.env.example` to `ai_service/.env` and edit it whenever you want to switch models without changing code.

If upgrading with an existing SQLite database, previously saved AI profiles can still point to `qwen3:14b`. Update the Base model field in **Admin → AI Management** to `qwen3:32b` for those profiles.

## v9.3 parser hardening
The guideline parser now uses strict structured extraction with one JSON-repair retry, a stronger standard-text fallback parser, cover-page pattern inference, coverage auditing, and a program-guideline consistency gate. Re-parse each active guideline after upgrading.


## v9.4 validation hardening

Margin checks now validate body-text boundary intrusion rather than treating unused whitespace as margin error. Required chapter matching tolerates `CHAPTER N:` prefixes and excludes TOC entries. Cover-page comparison is relative and anchor-based to avoid false failures when the sample is embedded inside the guideline page.
