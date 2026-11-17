# UniSubmit Isolated AI Service — Qwen3.5 35B

This service is intentionally separate from the UniSubmit Core application.

## Default stack

- LLM: `qwen3:32b` via Ollama
- Embeddings: `BAAI/bge-m3`
- Vector database: Qdrant
- PDF parsing/layout: PyMuPDF
- API: FastAPI

## Setup

The service is a member of the repository's uv workspace (Python 3.13). From
the repository root:

```bash
uv sync --all-packages
ollama pull qwen3:14b
docker compose up -d qdrant
uv run uvicorn unisubmit_ai.main:app --host 127.0.0.1 --port 8001
```

It reads its settings (`AI_SERVICE_TOKEN`, `OLLAMA_MODEL`, `QDRANT_URL`, ...)
from the root `.env`; see `.env.example`. On an Apple Silicon Mac run it
natively, as above, so the embedding model can use the GPU
(`docs/runbook-demo-mac.md`). On Linux, `docker compose --profile ai up -d`
builds it from `services/ai/Dockerfile` with CPU-only torch.

Health check: `http://127.0.0.1:8001/health`. It shows the configured default
model and the embedding device.

## Guideline-first validation

The service parses the uploaded guideline before validation. Qwen extracts only explicit requirements from guideline text into a structured profile. Deterministic PDF checks then evaluate measurable rules such as paper size, font, font size, margins, line spacing, word/page limits, heading properties, and page numbering. Semantic requirements are evaluated with RAG using BGE-M3 + Qdrant + Qwen.

The guideline remains the source of truth. Missing requirements must not be invented as mandatory rules.
