import logging
import os

import httpx

from .core.config import get_settings

logger = logging.getLogger("uvicorn.error")

# Semantic validation runs one local-LLM call per batch of rules, so the budget
# scales with the size of the guideline's rule set rather than a single request.
RAG_VALIDATE_TIMEOUT = float(os.getenv("RAG_VALIDATE_TIMEOUT", "1800"))


def _auth() -> dict:
    """The AI service rejects calls without the shared token (DEF-029)."""
    return {"Authorization": f"Bearer {get_settings().ai_service_token.get_secret_value()}"}


def health(service_url: str) -> dict:
    """Synchronous so the admin page can call it from a plain `def` route (DEF-025)."""
    try:
        with httpx.Client(timeout=4, headers=_auth()) as c:
            r = c.get(service_url.rstrip("/") + "/health")
            r.raise_for_status()
            return {"online": True, "data": r.json()}
    except Exception as e:
        return {"online": False, "error": str(e)}


class AIServiceError(RuntimeError):
    """A failed AI-service call. `transient` when a retry may succeed: the
    service was unreachable, timed out or answered 429, 502, 503 or 504."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


TRANSIENT_STATUSES = {429, 502, 503, 504}


def _service_error(action: str, exc: httpx.HTTPError) -> AIServiceError:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail", "No service error detail")
        except (ValueError, AttributeError):
            detail = "Non-JSON error response; inspect the AI service log"
        code = exc.response.status_code
        logger.error("%s HTTP %s: %s", action, code, detail)
        return AIServiceError(
            f"{action} HTTP {code}: {detail}", transient=code in TRANSIENT_STATUSES
        )
    logger.error("%s request failed: %s: %s", action, type(exc).__name__, exc)
    return AIServiceError(
        f"{action} request failed: {type(exc).__name__}: {exc}",
        transient=isinstance(exc, httpx.TransportError),
    )


async def rag_validate(service_url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=RAG_VALIDATE_TIMEOUT, headers=_auth()) as c:
            r = await c.post(service_url.rstrip("/") + "/rag/validate", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise _service_error("AI validation", exc) from exc


async def index_template(service_url: str, fields: dict, pdf_path: str):
    """Raises AIServiceError when the call fails."""
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
    except httpx.HTTPError as exc:
        raise _service_error("Guideline indexing", exc) from exc


async def extract_format_profile(service_url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=300, headers=_auth()) as c:
            r = await c.post(service_url.rstrip("/") + "/format/profile", json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def parse_guideline_profile(service_url: str, fields: dict, pdf_path: str):
    """Raises AIServiceError when the call fails."""
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
    except httpx.HTTPError as exc:
        raise _service_error("Guideline parsing", exc) from exc
