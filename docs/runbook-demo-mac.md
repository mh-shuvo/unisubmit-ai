# Running the demo on an Apple Silicon Mac

How to start UniSubmit AI for a demonstration on the Mac it was measured on
(Apple M1, 16 GB). Restructuring plan task P0.10.

## What runs where

Docker on macOS cannot reach the Apple GPU ([LIM-008](defects/LIM-008.md)), so
everything that needs it runs natively. Everything else stays in Docker,
because the web app and the worker store absolute file paths that are only
valid inside their container ([DEF-042](defects/DEF-042.md)).

| Component | Runs | Address |
|---|---|---|
| Ollama (`qwen3:14b`) | natively, Metal GPU | `127.0.0.1:11434` |
| AI service (BGE-M3 on MPS) | natively, `ai_service/.venv` | `127.0.0.1:8001` |
| Web app | Docker `app` | `127.0.0.1:8000` |
| Queue worker | Docker `worker` | none |
| PostgreSQL | Docker `db` | `127.0.0.1:5433` |
| Qdrant | Docker `qdrant` | `127.0.0.1:6333` |

The containers reach Ollama and the AI service through
`host.docker.internal`. Every port is bound to `127.0.0.1`, so the demo is only
reachable from this Mac ([DEF-030](defects/DEF-030.md)).

## One-time setup

1. Secrets. In `.env` set `APP_SECRET`, `AI_SERVICE_TOKEN`, `QDRANT_API_KEY`
   and `POSTGRES_PASSWORD`; in `ai_service/.env` set the same
   `AI_SERVICE_TOKEN` and `QDRANT_API_KEY`. Generate each value with
   `openssl rand -hex 32`. Nothing starts until they are set.
   For an existing `pgdata` volume, `POSTGRES_PASSWORD` must be the password the
   volume was created with (`unisubmit` for installations from before v9.14).
2. Model. With 16 GB of memory use `qwen3:14b` (9.3 GB on disk), set as
   `OLLAMA_MODEL` in both `.env` files; `qwen3:32b` does not fit.
   `ollama pull qwen3:14b`.
3. AI service environment: `cd ai_service && python3 -m venv .venv &&
   .venv/bin/pip install -r requirements.txt`. The first start downloads
   BGE-M3 (about 2 GB) into `~/.cache/huggingface`.
4. Docker Desktop: give the VM no more memory than it needs (Settings →
   Resources). The four containers are light, and every gigabyte left over
   goes to the model.

### Moving an existing all-Docker install to this layout

```bash
docker compose rm -sf ai-service        # the old containerised AI service
docker compose up -d --build            # recreates app, adds worker, keys Qdrant
docker compose exec app python -m app.seed super-admin --email you@example.edu
```

The last command is only needed when no Super Admin exists: the app no longer
creates `admin@unisubmit.local` ([DEF-028](defects/DEF-028.md)). Guidelines
indexed before this change keep working, because validation reads the
collection name recorded at indexing; anything indexed from now on goes into
`tv_<template version id>` ([DEF-032](defects/DEF-032.md)).

## Starting the demo

```bash
open -a Docker                                   # wait until Docker is running
ollama serve                                     # or start the Ollama app
cd ai_service && .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001
docker compose up -d                             # from the repository root
caffeinate -dims                                 # keep the Mac awake during a validation
```

Run the AI service and `caffeinate` in their own terminals. Check:

- http://127.0.0.1:8001/health shows `"rag": "ready"` and
  `"embedding": {"device": "mps:0", ...}`.
- `docker compose logs worker` ends with `Queue worker started`.
- http://127.0.0.1:8000/admin/ai shows the AI service online.

Close other memory-heavy applications: the model, the embedding model and the
Docker VM together use most of 16 GB.

## Expected timing

Measured with `tools/measure_validation.py` on 2026-11-14 (task P0.11), Apple
M1, 16 GB, `qwen3:14b`, embeddings on `mps:0`, the indexed M.Sc. CSE thesis
guideline (64 deterministic rules, 9 semantic requirements, one batch) and
`resources/sample-thesis/thesis 1.pdf` (73 pages):

| Stage | Seconds |
|---|---|
| PDF inspection | 0.8 |
| Deterministic rules | 0.3 |
| Retrieval and model (one semantic batch) | 366.6 |
| **Total** | **367.7** |

Plan for about six minutes per thesis, one at a time. Time grows with the
number of semantic batches (12 requirements per batch), so a guideline with
30 semantic requirements takes roughly three times as long. The timeouts stay
where they are: `RAG_VALIDATE_TIMEOUT` (1,800 s) covers about four batches at
this speed, and `OLLAMA_TIMEOUT` (900 s per batch) is 2.5 times the measured
batch.

To measure again, with the worker idle:

```bash
DATABASE_URL=postgresql://unisubmit:<password>@127.0.0.1:5433/unisubmit \
  .venv/bin/python tools/measure_validation.py --template-version 1 --pdf "<thesis.pdf>"
```

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| `docker compose` refuses to start: "Set … in .env" | A secret is missing; see one-time setup |
| AI status page: offline, but `/health` works in a browser | `AI_SERVICE_TOKEN` differs between `.env` and `ai_service/.env` (calls get 401) |
| AI service exits at startup: "Service token" failed | `AI_SERVICE_TOKEN` missing or shorter than 32 characters in `ai_service/.env` |
| AI service exits at startup: Qdrant failed | Docker is not running, or `QDRANT_API_KEY` differs from `.env` |
| `ai-service` container exits: "QDRANT_API_KEY is not set" | The container predates v9.14 and was never recreated. `docker compose rm -sf ai-service` and run the AI service natively, or recreate it with `--profile ai` |
| A job stays "AI validation in progress" | The worker stopped mid-job; it is requeued when the worker restarts after `RAG_VALIDATE_TIMEOUT` + 5 minutes. `docker compose logs worker` says why it stopped |
| Uploading a revised version says the submission is still being validated | Expected while its job is queued or running ([DEF-026](defects/DEF-026.md)) |

To show the demo on another device, change the `app` port mapping in
`docker-compose.yml` from `127.0.0.1:${APP_PORT:-8000}:8000` to
`${APP_PORT:-8000}:8000` for that session only, and revert it afterwards. The
other services stay on `127.0.0.1`.
