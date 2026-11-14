# Running the demo on an Apple Silicon Mac

How to start UniSubmit AI for a demonstration on the Mac it was measured on
(Apple M1, 16 GB). Restructuring plan tasks P0.10 and P1.12.

## What runs where

Docker on macOS cannot reach the Apple GPU ([LIM-008](defects/LIM-008.md)), so
everything that needs it runs natively. Everything else stays in Docker,
because the web app and the worker store absolute file paths that are only
valid inside their container ([DEF-042](defects/DEF-042.md)).

| Component | Runs | Address |
|---|---|---|
| Ollama (`qwen3:14b`) | natively, Metal GPU | `127.0.0.1:11434` |
| AI service (BGE-M3 on MPS) | natively, `uv run` | `127.0.0.1:8001` |
| Schema migrations | Docker `migrate`, once per start | none |
| Web app | Docker `app` | `127.0.0.1:8000` |
| Queue worker | Docker `worker` | none |
| PostgreSQL | Docker `db` | `127.0.0.1:5433` |
| Qdrant | Docker `qdrant` | `127.0.0.1:6333` |

The containers reach Ollama and the AI service through
`host.docker.internal`. Every port is bound to `127.0.0.1`, so the demo is only
reachable from this Mac ([DEF-030](defects/DEF-030.md)).

## One-time setup

1. Tools: [uv](https://docs.astral.sh/uv/) (`brew install uv`), then from the
   repository root `uv sync --all-packages`. This installs both services on
   Python 3.13 from `uv.lock`; the first start of the AI service downloads
   BGE-M3 (about 2 GB) into `~/.cache/huggingface`.
2. Secrets. Copy `.env.example` to `.env` and set `UNISUBMIT_SECRET_KEY`,
   `AI_SERVICE_TOKEN` and `POSTGRES_PASSWORD`, each from
   `openssl rand -hex 32`; leave `UNISUBMIT_AI_SERVICE_TOKEN=${AI_SERVICE_TOKEN}`
   as it is. Qdrant needs no key. Nothing starts until they are set.
   For an existing `pgdata` volume, `POSTGRES_PASSWORD` must be the password the
   volume was created with (`unisubmit` for installations from before v9.14).
3. Model. With 16 GB of memory use `qwen3:14b` (9.3 GB on disk), set as
   `OLLAMA_MODEL` in `.env`; `qwen3:32b` does not fit.
   `ollama pull qwen3:14b`.
4. Docker Desktop: give the VM no more memory than it needs (Settings →
   Resources). The containers are light, and every gigabyte left over goes to
   the model.

### Upgrading a v9.14 install to v10.0

The code moved (`app/` → `services/core/`, `ai_service/` → `services/ai/`), the
web app's settings gained a `UNISUBMIT_` prefix, and one `.env` now serves
everything.

1. Stop the native AI service (Ctrl+C in its terminal).
2. In `.env`: rename `APP_SECRET` to `UNISUBMIT_SECRET_KEY`; add
   `UNISUBMIT_AI_SERVICE_TOKEN=${AI_SERVICE_TOKEN}`; copy the AI settings from
   `ai_service/.env` (`OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT`,
   `OLLAMA_JSON_THINK`, `EMBEDDING_*`, `QDRANT_URL`). Old names such as
   `DATABASE_URL` and `AI_SERVICE_URL` are ignored and can go.
3. `uv sync --all-packages`, then `docker compose up -d --build`.
   The `migrate` service adopts the existing database (it logs "Adopted the
   existing schema at revision 0001") and hands the storage volume to the
   non-root user the containers now run as.
4. Start the AI service the new way (below). `ai_service/.venv` and
   `ai_service/.env` are no longer used and can be deleted.
5. `uv run pre-commit install`, once, to run the checks on every commit.

From v10.1 your existing data belongs to a university called "Default
University" (short name `default`), and every Super Admin is now its
University Admin. Create a Platform Admin to rename it or add universities:
`docker compose exec app unisubmit create-platform-admin --email you@example.edu`.
Files uploaded before v10.1 are moved into `storage/<university>/` by the
`migrate` step automatically ([DEF-042](defects/DEF-042.md)).

## Starting the demo

```bash
open -a Docker                                   # wait until Docker is running
ollama serve                                     # or start the Ollama app
uv run uvicorn unisubmit_ai.main:app --host 127.0.0.1 --port 8001
docker compose up -d                             # migrates, then app and worker
caffeinate -dims                                 # keep the Mac awake during a validation
```

Run the AI service and `caffeinate` in their own terminals. Check:

- http://127.0.0.1:8001/health shows `"rag": "ready"` and
  `"embedding": {"device": "mps:0", ...}`.
- `docker compose logs migrate` ends with `The database schema is up to date.`
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
UNISUBMIT_DATABASE_URL=postgresql+psycopg://unisubmit:<password>@127.0.0.1:5433/unisubmit \
  uv run python tools/measure_validation.py --template-version 1 --pdf "<thesis.pdf>"
```

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| `docker compose` refuses to start: "Set … in .env" | A secret is missing; see one-time setup |
| AI status page: offline, but `/health` works in a browser | The AI service was started before `AI_SERVICE_TOKEN` changed in `.env`; restart it (calls get 401) |
| AI service exits at startup: "Service token" failed | `AI_SERVICE_TOKEN` missing or shorter than 32 characters in `.env` |
| AI service exits at startup: Qdrant failed | Docker is not running, or `QDRANT_URL` in `.env` is not `http://127.0.0.1:6333` |
| `app` and `worker` never start; `migrate` exited 1 | `docker compose logs migrate`: a database created before v10.0 that differs from the models is refused, with the differences listed ([DEF-039](defects/DEF-039.md)) |
| The web app exits: "UniSubmit cannot start; set these…" | The listed `UNISUBMIT_*` variables are missing or too short in `.env` |
| A job stays "AI validation in progress" | The worker stopped mid-job; it is requeued when the worker restarts after `RAG_VALIDATE_TIMEOUT` + 5 minutes. `docker compose logs worker` says why it stopped |
| Uploading a revised version says the submission is still being validated | Expected while its job is queued or running ([DEF-026](defects/DEF-026.md)) |

To show the demo on another device, change the `app` port mapping in
`compose.yaml` from `127.0.0.1:${APP_PORT:-8000}:8000` to
`${APP_PORT:-8000}:8000` for that session only, and revert it afterwards. The
other services stay on `127.0.0.1`.
