import httpx
from .config import settings

SYSTEM_PROMPT = """You are a local academic pre-submission reviewer. You do not academically approve a thesis. Based only on the supplied approved guideline context and student text, produce concise improvement suggestions. Separate observations from mandatory compliance. Never invent a rule. Focus on clarity, research structure, methodology description, results/discussion, conclusion, figures/tables, references, and academic writing. Do not rewrite the entire report. Return plain text with 3-8 numbered suggestions. If there is insufficient evidence, say so."""

async def get_ollama_status() -> dict:
    """Return connection/model status without raising to the UI."""
    base = settings.ollama_url.rstrip("/")
    result = {
        "server_online": False,
        "configured_model": settings.ollama_model,
        "configured_url": settings.ollama_url,
        "model_installed": False,
        "models": [],
        "error": "",
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{base}/api/tags")
            r.raise_for_status()
            data = r.json()
            names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            result["server_online"] = True
            result["models"] = names
            configured = settings.ollama_model
            result["model_installed"] = configured in names or any(
                n.split(":")[0] == configured.split(":")[0] and configured.split(":")[-1] == "latest"
                for n in names
            )
    except Exception as exc:
        result["error"] = str(exc)
    return result

async def test_ollama_model() -> tuple[bool, str]:
    base = settings.ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{base}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "You are a concise system health-check assistant."},
                        {"role": "user", "content": "Reply exactly: UniSubmit AI model is ready."},
                    ],
                    "options": {"temperature": 0},
                },
            )
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "").strip()
            if not content:
                return False, "Ollama responded, but the model returned no text."
            return True, content
    except Exception as exc:
        return False, str(exc)

async def get_improvement_suggestions(guideline_text: str, student_text: str) -> str:
    context = guideline_text[:12000]
    content = student_text[:22000]
    prompt = f"APPROVED GUIDELINE CONTEXT:\n{context}\n\nSTUDENT DOCUMENT EXCERPT:\n{content}\n\nGive non-blocking improvement suggestions only."
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0.2},
                },
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception:
        return "Local AI suggestions are temporarily unavailable. Compliance validation was still completed using approved rules."
