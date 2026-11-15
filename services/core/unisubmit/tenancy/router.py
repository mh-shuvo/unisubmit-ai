"""Platform Admin pages: universities and their first admins (P2.5)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..auth.dependencies import (
    require_role,
)
from ..core.db import DbSession
from ..core.security import verify_csrf
from ..core.templating import flash_url, templates
from ..models import AdminRole, University
from ..tenancy import service as tenancy

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/platform/universities", response_class=HTMLResponse)
def platform_universities(request: Request, db: DbSession) -> Response:
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    return templates.TemplateResponse(
        request,
        "platform_universities.html",
        {
            "request": request,
            "admin": u,
            "universities": db.query(University).order_by(University.name).all(),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/platform/universities")
def platform_create_university(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    slug: str = Form(...),
    admin_name: str = Form(...),
    admin_email: str = Form(...),
    admin_password: str = Form(...),
) -> Response:
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    try:
        tenancy.create_university(
            db,
            name,
            slug,
            admin_name=admin_name,
            admin_email=admin_email,
            admin_password=admin_password,
            actor=u,
        )
    except tenancy.TenancyError as exc:
        db.rollback()
        return RedirectResponse(flash_url("/platform/universities", error=str(exc)), 303)
    return RedirectResponse(
        flash_url("/platform/universities", message="University and its admin created"), 303
    )


@router.post("/platform/universities/{university_id}/rename")
def platform_rename_university(
    university_id: int,
    request: Request,
    db: DbSession,
    name: str = Form(...),
    slug: str = Form(...),
) -> Response:
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    try:
        tenancy.rename_university(db, university_id, name, slug, actor=u)
    except tenancy.TenancyError as exc:
        db.rollback()
        return RedirectResponse(flash_url("/platform/universities", error=str(exc)), 303)
    return RedirectResponse(flash_url("/platform/universities", message="University renamed"), 303)


@router.post("/platform/universities/{university_id}/active")
def platform_set_university_active(
    university_id: int,
    request: Request,
    db: DbSession,
    active: bool = Form(...),
) -> Response:
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    try:
        university = tenancy.set_university_active(db, university_id, active, actor=u)
    except tenancy.TenancyError as exc:
        return RedirectResponse(flash_url("/platform/universities", error=str(exc)), 303)
    state = "activated" if university.is_active else "deactivated; its users are signed out"
    return RedirectResponse(
        flash_url("/platform/universities", message=f"{university.name} {state}"), 303
    )
