import logging
import os

import httpx

from .config import settings

logger = logging.getLogger("uvicorn.error")

# Semantic validation runs one local-LLM call per batch of rules, so the budget
# scales with the size of the guideline's rule set rather than a single request.
RAG_VALIDATE_TIMEOUT = float(os.getenv("RAG_VALIDATE_TIMEOUT", "1800"))


def _auth() -> dict:
    """The AI service rejects calls without the shared token (DEF-029)."""
    return {"Authorization": f"Bearer {settings.ai_service_token}"}


def health(service_url: str) -> dict:
    """Synchronous so the admin page can call it from a plain `def` route (DEF-025)."""
    try:
        with httpx.Client(timeout=4, headers=_auth()) as c:
            r = c.get(service_url.rstrip("/") + "/health")
            r.raise_for_status()
            return {"online": True, "data": r.json()}
    except Exception as e:
        return {"online": False, "error": str(e)}


async def rag_validate(service_url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=RAG_VALIDATE_TIMEOUT, headers=_auth()) as c:
            r = await c.post(service_url.rstrip("/") + "/rag/validate", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", "No service error detail")
        except (ValueError, AttributeError):
            detail = "Non-JSON error response; inspect the AI service log"
        logger.error("AI validation HTTP %s: %s", exc.response.status_code, detail)
        raise RuntimeError(f"AI validation HTTP {exc.response.status_code}: {detail}") from exc
    except Exception as exc:
        logger.exception("AI validation request failed")
        raise RuntimeError(f"AI validation request failed: {type(exc).__name__}: {exc}") from exc


async def index_template(service_url: str, fields: dict, pdf_path: str):
    try:
        async with httpx.AsyncClient(timeout=300, headers=_auth()) as c:
            with open(pdf_path, "rb") as f:
                r = await c.post(
                    service_url.rstrip("/") + "/rag/index-template",
                    data=fields,
                    files={"pdf": ("guideline.pdf", f, "application/pdf")},
                )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def extract_format_profile(service_url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=300, headers=_auth()) as c:
            r = await c.post(service_url.rstrip("/") + "/format/profile", json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def parse_guideline_profile(service_url: str, fields: dict, pdf_path: str):
    try:
        async with httpx.AsyncClient(timeout=1500, headers=_auth()) as c:
            with open(pdf_path, "rb") as f:
                r = await c.post(
                    service_url.rstrip("/") + "/guideline/profile",
                    data=fields,
                    files={"pdf": ("guideline.pdf", f, "application/pdf")},
                )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}
