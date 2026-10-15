# UniSubmit Isolated AI Service — Qwen3.5 35B

This service is intentionally separate from the UniSubmit Core application.

## Default stack

- LLM: `qwen3.5:35b` via Ollama
- Embeddings: `BAAI/bge-m3`
- Vector database: Qdrant
- PDF parsing/layout: PyMuPDF
- API: FastAPI

## Windows setup

Use Python 3.13 for the AI environment.

```powershell
cd ai_service
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Install the model once:

```powershell
ollama pull qwen3.5:35b
```

Start Qdrant, for example:

```powershell
docker start unisubmit-qdrant
```

If the container does not exist yet:

```powershell
docker run -d --name unisubmit-qdrant -p 6333:6333 -p 6334:6334 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

Run the AI service:

```powershell
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

Health check:

`http://127.0.0.1:8001/health`

The health response exposes the configured default model. It should show `qwen3.5:35b` when `.env` is loaded correctly.

## Guideline-first validation

The service parses the uploaded guideline before validation. Qwen extracts only explicit requirements from guideline text into a structured profile. Deterministic PDF checks then evaluate measurable rules such as paper size, font, font size, margins, line spacing, word/page limits, heading properties, and page numbering. Semantic requirements are evaluated with RAG using BGE-M3 + Qdrant + Qwen.

The guideline remains the source of truth. Missing requirements must not be invented as mandatory rules.
