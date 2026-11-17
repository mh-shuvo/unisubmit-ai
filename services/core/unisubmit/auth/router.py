"""Registration, login, logout and password changes for students and admins."""

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import audit
from ..auth.dependencies import (
    Account,
    require_admin,
    require_student,
    university_inactive,
)
from ..core.db import DbSession
from ..core.security import (
    MIN_ADMIN_PASSWORD,
    Login,
    check_login,
    hash_password,
    verify_csrf,
    verify_password,
)
from ..core.templating import flash_url, templates
from ..models import AdminUser, StudentUser

router = APIRouter(dependencies=[Depends(verify_csrf)])


security_log = structlog.get_logger("unisubmit.security")
MIN_STUDENT_PASSWORD = 8


def sign_in(
    request: Request, db: Session, account: Account | None, password: str, key: str, page: str
) -> RedirectResponse | None:
    """Check a login and start the session; returns a redirect back to the
    login page when it fails. Counters and an upgraded hash are committed
    either way (P4.1, P4.4)."""
    outcome = check_login(account, password)
    db.commit()
    kind = key.removesuffix("_id")
    if account is None or outcome is Login.WRONG:
        security_log.warning("login.failed", account=kind, user_id=getattr(account, "id", None))
        return RedirectResponse(flash_url(page, error="Invalid email or password"), 303)
    if outcome is Login.LOCKED:
        security_log.warning("login.locked", account=kind, user_id=account.id)
        error = "Too many failed attempts. Try again in 15 minutes."
        return RedirectResponse(flash_url(page, error=error), 303)
    if university_inactive(account):
        security_log.warning("login.university_inactive", account=kind, user_id=account.id)
        error = "Your university's account is deactivated"
        return RedirectResponse(flash_url(page, error=error), 303)
    request.session.clear()  # a new session, and a new CSRF token, at every login
    request.session[key] = account.id
    request.session["auth_epoch"] = account.auth_epoch
    security_log.info("login.succeeded", account=kind, user_id=account.id)
    return None


def change_password(
    request: Request, db: Session, account: Account, current: str, new: str, page: str
) -> RedirectResponse:
    """Ends every other session of the account; this one stays signed in."""
    if not verify_password(current, account.password_hash):
        return RedirectResponse(flash_url(page, error="The current password is wrong"), 303)
    minimum = MIN_STUDENT_PASSWORD if isinstance(account, StudentUser) else MIN_ADMIN_PASSWORD
    if len(new) < minimum:
        error = f"The new password must be at least {minimum} characters"
        return RedirectResponse(flash_url(page, error=error), 303)
    account.password_hash = hash_password(new)
    account.auth_epoch += 1
    if isinstance(account, AdminUser):
        audit.record(db, account, "account.change_password", f"admin:{account.id}")
    db.commit()
    request.session["auth_epoch"] = account.auth_epoch
    security_log.info("password.changed", user_id=account.id)
    return RedirectResponse(
        flash_url(page, message="Password changed; your other sessions are signed out"), 303
    )


@router.get("/student/login", response_class=HTMLResponse)
def student_login_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "student_login.html",
        {"request": request, "error": request.query_params.get("error", "")},
    )


@router.post("/student/login")
def student_login(
    request: Request,
    db: DbSession,
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    s = (
        db.query(StudentUser)
        .filter(StudentUser.email == email.strip().lower(), StudentUser.active)
        .first()
    )
    refused = sign_in(request, db, s, password, "student_id", "/student/login")
    return refused or RedirectResponse("/student/dashboard", 303)


@router.post("/student/logout")
def student_logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/", 303)


@router.post("/student/password")
def student_change_password(
    request: Request,
    db: DbSession,
    current_password: str = Form(...),
    new_password: str = Form(...),
) -> Response:
    s = require_student(request, db)
    return change_password(request, db, s, current_password, new_password, "/student/profile")


@router.get("/student/profile", response_class=HTMLResponse)
def student_profile(request: Request, db: DbSession) -> Response:
    s = require_student(request, db)
    return templates.TemplateResponse(
        request,
        "student_profile.html",
        {
            "request": request,
            "student": s,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"request": request, "error": request.query_params.get("error", "")},
    )


@router.post("/admin/login")
def admin_login(
    request: Request,
    db: DbSession,
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    u = (
        db.query(AdminUser)
        .filter(AdminUser.email == email.strip().lower(), AdminUser.active)
        .first()
    )
    return sign_in(request, db, u, password, "admin_id", "/admin/login") or RedirectResponse(
        "/admin", 303
    )


@router.post("/admin/logout")
def admin_logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/", 303)


@router.post("/admin/password")
def admin_change_password(
    request: Request,
    db: DbSession,
    current_password: str = Form(...),
    new_password: str = Form(...),
) -> Response:
    u = require_admin(request, db)
    return change_password(request, db, u, current_password, new_password, "/admin")
