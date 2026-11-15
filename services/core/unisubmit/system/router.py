"""Operations pages: the AI service and queue (Platform Admin), the unit's queue, health probes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from .. import queries, queue
from ..ai_service_client import health as ai_health
from ..auth.dependencies import (
    require_role,
)
from ..core.config import get_settings
from ..core.db import DbSession
from ..core.security import verify_csrf
from ..core.templating import templates
from ..models import AdminRole, AIProfile

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/admin/ai", response_class=HTMLResponse)
def super_ai(request: Request, db: DbSession) -> Response:
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    status = ai_health(get_settings().ai_service_url)
    profiles = db.query(AIProfile).all()
    return templates.TemplateResponse(
        request,
        "super_ai.html",
        {
            "request": request,
            "admin": u,
            "status": status,
            "profiles": profiles,
            "queue": queue.metrics(db),
        },
    )


@router.get("/admin/queue", response_class=HTMLResponse)
def unit_queue(request: Request, db: DbSession) -> Response:
    u = require_role(request, db, AdminRole.UNIT_ADMIN)
    return templates.TemplateResponse(
        request,
        "unit_queue.html",
        {"request": request, "admin": u, "rows": queries.unit_jobs(db, u)},
    )


@router.get("/health/live")
def health_live() -> dict[str, str]:
    """The process answers; nothing else is checked."""
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready(db: DbSession) -> dict[str, str]:
    """The database answers. The AI service is reported on the AI page, not here:
    the web app is usable without it."""
    db.execute(select(1))
    return {"status": "ok"}
