# UniSubmit AI v9.10.1 validation fix

## Diagnosis

Inspected the downloaded v9.10 source and original release ZIP. No synced project files, active installation, database, uploaded submissions, or virtual environments were changed.

The latest screenshot shows successful startup and GET /health 200, followed by POST /rag/validate 502. In the inspected v9.10 endpoint, 502 is raised only when JSON decoding or its repair fails. This identifies the failing code path, but the screenshot does not contain the response body, model output, or traceback needed to identify the precise malformed output. The earlier 500 screenshot cannot establish an exact cause by itself.

Confirmed code defects:

- Semantic validation used plain json.loads instead of the existing tolerant parser used by guideline parsing. Qwen thinking blocks, fences, or a preamble could therefore trigger unnecessary repair and 502.
- Valid JSON with a wrong root or wrong field types could bypass parsing and crash during result processing with 500.
- All retrieval exceptions became 500, without traceback logging.
- Qdrant retrieval unconditionally used query_points. That API was introduced in server 1.10; the installed Python client was 1.19.1. The screenshot shows a compatibility warning, but the server version has not been verified.
- The Core client discarded service error details and had a 240-second timeout, shorter than the AI service's 300-second generation timeout and possible second repair call.
- Malformed source_pages values or duplicate semantic IDs could break or misassociate semantic results.

References: https://qdrant.tech/blog/qdrant-1.10.x/ and https://api.qdrant.tech/v-1-12-x/api-reference/search/points

## Changes

ai_service/main.py:

- Reuse and improve the tolerant object parser; validate semantic result/suggestion field shapes before consuming them.
- Perform at most one repair, validating the repaired payload too. Persistent bad output still returns 502; an unavailable repair service returns 503. No fabricated PASS results.
- Keep missing per-rule responses as NOT VERIFIED. Empty semantic reviews have PARTIAL status.
- Serialize semantic inputs as JSON. Preserve manual rules, expected evidence, merged source pages, severity, targeted section extraction, reports, and response fields.
- Bound request limits, normalize severity, skip disabled/ignored rules, support scalar source_pages, and ensure unique IDs after deduplication.
- Keep Qdrant compatibility checks enabled. Fall back to legacy dense search only for an unavailable query API, after checking that the collection exists. New clients without search() use the legacy REST endpoint with optional QDRANT_API_KEY. Authentication, vector mismatch, and other failures do not trigger compatibility fallback.
- Log startup versions and traceback details for retrieval, generation, repair, and unexpected failures. Logs do not intentionally include student text or raw model output.

app/ai_service_client.py:

- Preserve HTTP status and JSON error detail in the exception surfaced to Core.
- Increase the validation timeout to 660 seconds to accommodate generation and one repair.

## Installation into the existing v9.10 workspace

Stop the Core and AI services. Copy these two files from this build into the matching paths in your existing unisubmit_ai directory:

1. ai_service/main.py
2. app/ai_service_client.py

Keep your existing .env files, storage, database, and virtual environments. This patch needs no database migration or Qdrant collection recreation. Do not delete Qdrant storage. Dependency files were left unchanged. A new extraction can instead be installed following the existing README with your configuration.

Restart the AI service from ai_service with:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Restart Core using your existing startup command. Retry validation. If 502 remains, the new AI log and Core exception will identify whether the first or repaired output is invalid and which shape is wrong. A successful health response alone does not verify model generation, collection existence, or response correctness.

## Validation

12 offline regression tests passed using the installed AI virtual environment. They exercise the actual FastAPI endpoint through ASGI with mocked model/retrieval dependencies: Qwen wrappers, invalid JSON shapes, one repair, persistent 502, unavailable repair 503, missing responses, manual rules, page deduplication, warning severity, duplicate IDs, input bounds, modern Qdrant, legacy REST fallback, and missing collection handling. All Python source files passed syntax parsing.

Live Qwen generation and the actual Qdrant server were not verified. This build fixes confirmed code defects; it cannot guarantee that arbitrary truncated or nonsensical model output becomes a usable validation result.

Run the included tests from ai_service:

```powershell
python -m unittest -v test_validation_fix
```

The ZIP excludes .env secrets, databases, submissions, and virtual environments. It contains the original release application assets plus this patch, tests, and notes.
