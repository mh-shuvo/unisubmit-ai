# UniSubmit AI v9.10.2

This complete build includes v9.10.1 fixes and a guideline parsing update for incomplete Ollama responses.

## Upgrade an existing v9.10.1 installation

Stop Core and AI services. Copy these files from this ZIP to the matching locations in your existing unisubmit_ai folder:

- ai_service/main.py
- app/ai_service_client.py
- app/templates/template_version.html

Keep your .env, database, storage and virtual environments. No dependency installation, database migration, or Qdrant storage deletion is needed for this patch. The new settings have defaults in code, so existing .env files work.

Restart both services using your existing commands, then select Re-parse & Re-index Guideline. Review the extracted rules before activating them.

## Fresh installation

Use Python 3.13. Install Ollama and Docker Desktop and start them. From the extracted unisubmit_ai folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, from unisubmit_ai:

```powershell
cd ai_service
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
ollama pull qwen3:32b
```

If your existing Qdrant container is present, run `docker start unisubmit-qdrant`. Otherwise create it once:

```powershell
docker run -d --name unisubmit-qdrant -p 6333:6333 -p 6334:6334 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

Then start AI from ai_service:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8000/admin/login. Initial demo credentials are admin@unisubmit.local / Admin@123. Set up the department/program, AI profile and guideline. Change demo credentials and APP_SECRET before real use.

## What changed

- All guideline pages are processed in bounded, overlapping 3,000-character pieces; the old first-60,000-character cutoff was removed.
- Ollama requests explicitly set num_ctx=16384 and num_predict=4096. Structured JSON generation defaults to think=false.
- Truncated or persistently malformed chunk responses trigger smaller source pieces, with a bounded retry depth. Incomplete JSON is never accepted as a successful result.
- Per-chunk schema validation, one JSON repair, page provenance, rule merging, semantic deduplication, and fallback parsing are retained.
- Failed chunks are identified in diagnostics. Successful chunks survive ordinary per-chunk failures. A 540-second overall timeout returns fallback-only diagnostics; Core allows 600 seconds for parsing.
- Parser Diagnostics shows successful/total chunks and distinguishes partial extraction from complete success.
- Abstract fallback recognizes explicit word ranges with en/em dashes, hyphens, 'to', and 'between ... and ...'. Numbers are extracted from the guideline, not supplied as defaults.

Optional overrides in ai_service/.env:

```dotenv
OLLAMA_NUM_CTX=16384
OLLAMA_NUM_PREDICT=4096
OLLAMA_JSON_THINK=false
```

Larger budgets require more memory. A very slow model, unavailable service, or unsupported model configuration can still fail; review the diagnostics. This update does not claim that every guideline will be extracted perfectly.

## Verification

19 offline regression tests passed against the installed AI environment. Tests cover the previous validation fixes plus long documents, page provenance, truncated-response splitting, malformed schema repair, partial failures, request budgets, abstract ranges and the PDF upload endpoint. Ollama/Qdrant calls were mocked; live inference on the user's guideline has not been verified.

From ai_service: `python -m unittest discover -v`

Ollama API reference used for generation options: https://github.com/ollama/ollama/blob/main/docs/api.md
