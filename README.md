# UniSubmit AI v6

A role-scoped, mobile-friendly academic pre-submission system with an isolated local AI service.

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
Every uploaded version creates an AI job. A persistent worker uses concurrency 1 and processes jobs sequentially until the queue is empty. It then stays idle and resumes when new jobs arrive. Failed jobs retry up to 3 times and do not block the rest of the queue.

## Run Core System
Recommended Python: 3.13

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Public portal: http://127.0.0.1:8000
Admin login: http://127.0.0.1:8000/admin/login

Demo Super Admin:
- Email: `admin@unisubmit.local`
- Password: `Admin@123`

Change the demo password before any real deployment.

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
uvicorn main:app --port 8001
```

AI health: http://127.0.0.1:8001/health
AI docs: http://127.0.0.1:8001/docs

## Development note
SQLite and the in-process persistent queue are suitable for an MVP / single-server deployment. For larger production deployment, keep the same workflow but move PostgreSQL + Redis/Celery (concurrency 1 or higher) behind the service.


## v9 guideline-first rule parsing and validation

Re-index each active guideline after upgrading. The AI service now parses the approved guideline PDF itself before validation. Qwen extracts only explicit rules from the guideline text into a structured validation profile. Admin reviews and activates that profile; rules are not manually retyped.

Deterministic rules such as paper size, font, font size, margins, line spacing, page limits, required sections, word limits, page numbering and selected heading rules are checked by Python/PyMuPDF. Semantic requirements are checked by Qwen using targeted BGE-M3/Qdrant retrieval from the same guideline. A labelled front/title-page sample is also captured as a normalized cover-layout profile for first-page layout checking.

Same-type failures across multiple pages are consolidated while preserving affected page numbers, occurrence count, snippets, coordinates, expected vs detected values, correction guidance and guideline source page/section.

## v9.2 model configuration

The default local LLM is now `qwen3.5:35b`. Run `ollama pull qwen3.5:35b` before starting the AI service. The AI service reads `ai_service/.env`; copy `ai_service/.env.example` to `ai_service/.env` and edit it whenever you want to switch models without changing code.

If upgrading with an existing SQLite database, previously saved AI profiles can still point to `qwen3:14b`. Update the Base model field in **Admin → AI Management** to `qwen3.5:35b` for those profiles.

## v9.3 parser hardening
The guideline parser now uses strict structured extraction with one JSON-repair retry, a stronger standard-text fallback parser, cover-page pattern inference, coverage auditing, and a program-guideline consistency gate. Re-parse each active guideline after upgrading.


## v9.4 validation hardening

Margin checks now validate body-text boundary intrusion rather than treating unused whitespace as margin error. Required chapter matching tolerates `CHAPTER N:` prefixes and excludes TOC entries. Cover-page comparison is relative and anchor-based to avoid false failures when the sample is embedded inside the guideline page.
