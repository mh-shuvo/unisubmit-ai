"""Registration, login, logout and password changes for students and admins."""

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import audit, queries
from ..auth.dependencies import (
    Account,
    require_admin,
    require_student,
    university_inactive,
)
from ..core.db import DbSession
from ..core.security import Login, check_login, hash_password, verify_csrf, verify_password
from ..core.templating import flash_url, templates
from ..models import AdminUser, StudentUser
from ..tenancy import service as tenancy
from ..tenancy.dependencies import to_university_page, university_page

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
    minimum = (
        MIN_STUDENT_PASSWORD if isinstance(account, StudentUser) else tenancy.MIN_ADMIN_PASSWORD
    )
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


@router.get("/student/register")
def student_register_redirect(db: DbSession) -> Response:
    return to_university_page(db, "register")


@router.get("/u/{slug}/register", response_class=HTMLResponse)
def student_register_page(slug: str, request: Request, db: DbSession) -> Response:
    university = university_page(db, slug)
    return templates.TemplateResponse(
        request,
        "student_register.html",
        {
            "request": request,
            "university": university,
            "departments": queries.departments(db, university.id, active_only=True),
            "institutes": queries.institutes(db, university.id, active_only=True),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/u/{slug}/register")
def student_register(
    slug: str,
    request: Request,
    db: DbSession,
    name: str = Form(...),
    student_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    unit_type: str = Form(...),
    unit_id: int = Form(...),
) -> Response:
    university = university_page(db, slug)
    if db.query(StudentUser).filter(StudentUser.email == email.strip().lower()).first():
        return RedirectResponse(
            flash_url(f"/u/{slug}/register", error="Email already registered"), 303
        )
    # Only units of this university: the id comes from the form (P2.8).
    units = (
        queries.departments(db, university.id, active_only=True)
        if unit_type == "department"
        else queries.institutes(db, university.id, active_only=True)
        if unit_type == "institute"
        else []
    )
    if unit_id not in {unit.id for unit in units}:
        raise HTTPException(400)
    s = StudentUser(
        name=name.strip(),
        student_id=student_id.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        university_id=university.id,
        department_id=unit_id if unit_type == "department" else None,
        institute_id=unit_id if unit_type == "institute" else None,
        verified=True,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    request.session["student_id"] = s.id
    return RedirectResponse("/student/dashboard", 303)


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
