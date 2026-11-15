import copy
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.middleware.sessions import SessionMiddleware

from . import audit, queries, queue, storage, tenancy
from .ai_service_client import health as ai_health
from .core.config import get_settings
from .core.db import DbSession, create_db_engine, make_session_factory
from .core.errors import register_error_handlers
from .core.logging import RequestIdMiddleware, configure_logging
from .models import (
    ACTIVE_JOB_STATUSES,
    IN_PROGRESS,
    AdminRole,
    AdminUser,
    AIProfile,
    AIProfileStatus,
    Department,
    Faculty,
    GuidelineStatus,
    Institute,
    JobType,
    ProfileStatus,
    Program,
    StudentUser,
    Submission,
    SubmissionStatus,
    SubmissionVersion,
    Teacher,
    Template,
    TemplateVersion,
    University,
    ValidationRule,
)
from .pdf_utils import PDFError, inspect_pdf
from .report_parser import inline_markdown_html, parse_ai_report
from .reporting import build_validation_report
from .security import (
    Login,
    check_login,
    csrf_field,
    hash_password,
    verify_csrf,
    verify_password,
)

PACKAGE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
templates.env.globals["render_md"] = inline_markdown_html
templates.env.globals["csrf_field"] = csrf_field
# Every POST carries the session's CSRF token (P4.2, DEF-041).
router = APIRouter(dependencies=[Depends(verify_csrf)])
security_log = structlog.get_logger("unisubmit.security")
SESSION_HOURS = 8
MIN_STUDENT_PASSWORD = 8


# ---------- helpers ----------
def flash_url(path, message="", error=""):
    q = []
    if message:
        q.append("message=" + message.replace(" ", "%20"))
    if error:
        q.append("error=" + error.replace(" ", "%20"))
    return path + ("?" + "&".join(q) if q else "")


def _semantic_norm(text):
    import re

    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Normalize soft modal wording so the same intent is not duplicated.
    s = re.sub(r"\b(should|shall|must|required to|needs to|need to)\b", "must", s)
    return re.sub(r"\s+", " ", s).strip()


def _dedupe_semantic_requirements(profile):
    """Merge duplicate semantic requirements while preserving every guideline source.

    The canonical rule is keyed by normalized requirement + target/source section.
    This intentionally merges page-boundary duplicates such as the same Abstract
    requirement found on pages 1 and 2. Manual rules are also deduplicated only
    when their normalized requirement and target section are identical.
    """
    if not isinstance(profile, dict):
        return profile
    rules = profile.get("semantic_requirements") or []
    merged = []
    by_key = {}
    for raw in rules:
        if not isinstance(raw, dict):
            continue
        r = dict(raw)
        req = str(r.get("requirement") or "").strip()
        if not req:
            continue
        section = str(r.get("target_section") or r.get("source_section") or "").strip()
        key = (_semantic_norm(req), _semantic_norm(section))
        if key not in by_key:
            pages = []
            for x in r.get("source_pages") or []:
                try:
                    pages.append(int(x))
                except (TypeError, ValueError):
                    pass
            try:
                if r.get("source_page") is not None:
                    pages.append(int(r.get("source_page")))
            except (TypeError, ValueError):
                pass
            r["source_pages"] = sorted(set(pages))
            if r["source_pages"] and not r.get("source_page"):
                r["source_page"] = r["source_pages"][0]
            r["target_section"] = section
            r.setdefault(
                "source_type",
                "Manual Semantic Rule" if r.get("manual") else "Guideline-derived",
            )
            r.setdefault(
                "semantic_id",
                str(r.get("rule_id") or ("SEM-" + uuid.uuid4().hex[:10].upper())),
            )
            by_key[key] = r
            merged.append(r)
        else:
            base = by_key[key]
            pages = set(base.get("source_pages") or [])
            for x in r.get("source_pages") or []:
                try:
                    pages.add(int(x))
                except (TypeError, ValueError):
                    pass
            try:
                if r.get("source_page") is not None:
                    pages.add(int(r.get("source_page")))
            except (TypeError, ValueError):
                pass
            base["source_pages"] = sorted(pages)
            if base["source_pages"]:
                base["source_page"] = base["source_pages"][0]
            # Preserve evidence from both pages without repeating identical text.
            evid = []
            for x in [base.get("evidence"), r.get("evidence")]:
                x = str(x or "").strip()
                if x and x not in evid:
                    evid.append(x)
            if evid:
                base["evidence"] = " | ".join(evid)[:1600]
            base["merged_duplicate_count"] = int(base.get("merged_duplicate_count") or 0) + 1
    profile["semantic_requirements"] = merged
    return profile


def _normalize_profile_severities(profile):
    """Add reviewable severity metadata without inventing new academic rules."""
    if not isinstance(profile, dict):
        return profile
    profile = _dedupe_semantic_requirements(profile)
    for key in ("deterministic_rules", "semantic_requirements"):
        for r in profile.get(key, []) or []:
            suggested = r.get("suggested_severity")
            if suggested not in {"Mandatory", "Warning", "Recommendation", "Ignore"}:
                suggested = "Mandatory" if bool(r.get("mandatory", True)) else "Warning"
            final = r.get("final_severity")
            if final not in {"Mandatory", "Warning", "Recommendation", "Ignore"}:
                final = suggested
            r["suggested_severity"] = suggested
            r["final_severity"] = final
            r["mandatory"] = final == "Mandatory"
            r["enabled"] = final != "Ignore"
    profile.setdefault("review_audit", [])
    return profile


def _profile_rule_stats(profile, results, additional_rules):
    profile = _normalize_profile_severities(profile or {})
    sem_rules = [
        r
        for r in (profile.get("semantic_requirements") or [])
        if r.get("final_severity") != "Ignore" and r.get("enabled", True)
    ]
    fmt = [r for r in results if r.get("category") == "Formatting Compliance"]
    sem = [r for r in results if r.get("category") == "Semantic Compliance"]
    add = [
        r
        for r in results
        if r.get("category")
        not in {"Formatting Compliance", "Semantic Compliance", "Validation Statistics"}
    ]

    def counts(rows):
        return {
            "total": len(rows),
            "passed": sum(1 for r in rows if r.get("status") == "PASS"),
            "failed": sum(1 for r in rows if r.get("status") == "FAIL"),
            "warnings": sum(1 for r in rows if r.get("status") == "WARNING"),
            "not_verified": sum(1 for r in rows if r.get("status") == "NOT VERIFIED"),
        }

    fc = counts(fmt)
    sc = counts(sem)
    ac = counts(add)
    checked = fc["total"] + sc["total"] + ac["total"]
    passed = fc["passed"] + sc["passed"] + ac["passed"]
    failed = fc["failed"] + sc["failed"] + ac["failed"]
    warnings = fc["warnings"] + sc["warnings"] + ac["warnings"]
    verified_den = passed + failed + warnings
    compliance = round((passed / verified_den) * 100, 1) if verified_den else None
    cats = {
        "Deterministic Guideline Rules": fc,
        "Semantic Guideline Rules": sc,
        "Additional Restrictions": ac,
    }
    return {
        "total_checked": checked,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "not_verified": fc["not_verified"] + sc["not_verified"] + ac["not_verified"],
        "compliance_rate": compliance,
        "semantic_total": len(sem_rules),
        "semantic_checked": sc["total"],
        "categories": cats,
        "note": "Every active deterministic and semantic guideline rule, plus additional restrictions, is listed with an explicit status. NOT VERIFIED rules are excluded from the compliance-rate denominator.",
    }


def _report_run_token(version):
    raw = (version.ai_run_reference or "deterministic").strip() or "deterministic"
    return "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))[:80] or "deterministic"


def admin_user(request: Request, db: Session):
    uid = request.session.get("admin_id")
    return db.get(AdminUser, uid) if uid else None


def student_user(request: Request, db: Session):
    uid = request.session.get("student_id")
    return db.get(StudentUser, uid) if uid else None


def university_inactive(user) -> bool:
    """Deactivating a university signs out all its users on their next request (P2.6)."""
    return user.university is not None and not user.university.is_active


def signed_out(request: Request, user) -> bool:
    """A session ends when its user or university is deactivated, or when the
    user's password changed after it began (P4.3)."""
    gone = (
        user is None
        or not user.active
        or university_inactive(user)
        or request.session.get("auth_epoch") != user.auth_epoch
    )
    if gone and user is not None:
        request.session.clear()
    return gone


def require_admin(request: Request, db: Session):
    u = admin_user(request, db)
    if signed_out(request, u):
        raise HTTPException(401, "Admin login required")
    return u


def require_role(request: Request, db: Session, role: AdminRole):
    u = require_admin(request, db)
    if u.role != role:
        raise HTTPException(403, f"{role.replace('_', ' ').title()} access required")
    return u


def require_student(request: Request, db: Session):
    u = student_user(request, db)
    if signed_out(request, u):
        raise HTTPException(401, "Student login required")
    return u


def sign_in(request: Request, db: Session, account, password: str, key: str, page: str):
    """Check a login and start the session; returns a redirect back to the
    login page when it fails. Counters and an upgraded hash are committed
    either way (P4.1, P4.4)."""
    outcome = check_login(account, password)
    db.commit()
    kind = key.removesuffix("_id")
    if outcome is Login.LOCKED:
        security_log.warning("login.locked", account=kind, user_id=account.id)
        error = "Too many failed attempts. Try again in 15 minutes."
        return RedirectResponse(flash_url(page, error=error), 303)
    if outcome is Login.WRONG:
        security_log.warning("login.failed", account=kind, user_id=getattr(account, "id", None))
        return RedirectResponse(flash_url(page, error="Invalid email or password"), 303)
    if university_inactive(account):
        security_log.warning("login.university_inactive", account=kind, user_id=account.id)
        error = "Your university's account is deactivated"
        return RedirectResponse(flash_url(page, error=error), 303)
    request.session.clear()  # a new session, and a new CSRF token, at every login
    request.session[key] = account.id
    request.session["auth_epoch"] = account.auth_epoch
    security_log.info("login.succeeded", account=kind, user_id=account.id)
    return None


def change_password(request: Request, db: Session, account, current: str, new: str, page: str):
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


def same_unit(program, u):
    return (u.department_id and program.department_id == u.department_id) or (
        u.institute_id and program.institute_id == u.institute_id
    )


# ---------- public ----------
@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: DbSession):
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "universities": tenancy.active_universities(db)},
    )


@router.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse(request, "how_it_works.html", {"request": request})


def university_page(db: Session, slug: str) -> University:
    university = tenancy.active_university(db, slug)
    if university is None:
        raise HTTPException(404)
    return university


def to_university_page(db: Session, page: str) -> RedirectResponse:
    """Pre-v10 public URLs: straight to the page when there is one university,
    otherwise to the home page, which lists them (P2.7)."""
    universities = tenancy.active_universities(db)
    if len(universities) == 1:
        return RedirectResponse(f"/u/{universities[0].slug}/{page}", 303)
    return RedirectResponse("/#universities", 303)


@router.get("/guidelines")
def guidelines_redirect(db: DbSession):
    return to_university_page(db, "guidelines")


@router.get("/student/register")
def student_register_redirect(db: DbSession):
    return to_university_page(db, "register")


@router.get("/u/{slug}/guidelines", response_class=HTMLResponse)
def guidelines(slug: str, request: Request, db: DbSession):
    university = university_page(db, slug)
    return templates.TemplateResponse(
        request,
        "guidelines.html",
        {
            "request": request,
            "university": university,
            "versions": queries.active_guidelines(db, university.id),
        },
    )


@router.get("/guidelines/{version_id}/download")
def guideline_download(version_id: int, db: DbSession):
    tv = db.get(TemplateVersion, version_id)
    if not tv or tv.status != GuidelineStatus.ACTIVE:
        raise HTTPException(404)
    try:
        path = storage.resolve(tv.pdf_path)
    except FileNotFoundError:
        raise HTTPException(404) from None
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{tv.template.program.code}_{tv.template.submission_type}_{tv.version}.pdf",
    )


# ---------- student auth ----------
@router.get("/u/{slug}/register", response_class=HTMLResponse)
def student_register_page(slug: str, request: Request, db: DbSession):
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
):
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
def student_login_page(request: Request):
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
):
    s = (
        db.query(StudentUser)
        .filter(StudentUser.email == email.strip().lower(), StudentUser.active == True)
        .first()
    )
    refused = sign_in(request, db, s, password, "student_id", "/student/login")
    return refused or RedirectResponse("/student/dashboard", 303)


@router.post("/student/logout")
def student_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@router.post("/student/password")
def student_change_password(
    request: Request,
    db: DbSession,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    s = require_student(request, db)
    return change_password(request, db, s, current_password, new_password, "/student/profile")


@router.get("/student/dashboard", response_class=HTMLResponse)
def student_dashboard(request: Request, db: DbSession):
    s = require_student(request, db)
    rows = (
        db.query(Submission)
        .filter(Submission.student_user_id == s.id)
        .order_by(Submission.created_at.desc())
        .all()
    )
    counts = {
        "total": len(rows),
        "queued": sum(1 for x in rows if x.status in IN_PROGRESS),
        "revision": sum(1 for x in rows if x.status == SubmissionStatus.REVISION_REQUIRED),
        "passed": sum(1 for x in rows if x.status == SubmissionStatus.PASSED),
    }
    return templates.TemplateResponse(
        request,
        "student_dashboard.html",
        {"request": request, "student": s, "rows": rows, "counts": counts},
    )


@router.get("/student/profile", response_class=HTMLResponse)
def student_profile(request: Request, db: DbSession):
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


# ---------- admin auth ----------
@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
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
):
    u = (
        db.query(AdminUser)
        .filter(AdminUser.email == email.strip().lower(), AdminUser.active == True)
        .first()
    )
    return sign_in(request, db, u, password, "admin_id", "/admin/login") or RedirectResponse(
        "/admin", 303
    )


@router.post("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@router.post("/admin/password")
def admin_change_password(
    request: Request,
    db: DbSession,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    u = require_admin(request, db)
    return change_password(request, db, u, current_password, new_password, "/admin")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: DbSession):
    u = require_admin(request, db)
    if u.role == AdminRole.PLATFORM_ADMIN:
        universities = db.query(University).all()
        metrics = {
            "universities": len(universities),
            "active": sum(1 for x in universities if x.is_active),
            "admins": db.query(AdminUser)
            .filter(AdminUser.role == AdminRole.UNIVERSITY_ADMIN)
            .count(),
        }
    elif u.role == AdminRole.UNIVERSITY_ADMIN:
        metrics = {
            "faculties": len(queries.faculties(db, u.university_id)),
            "departments": len(queries.departments(db, u.university_id)),
            "institutes": len(queries.institutes(db, u.university_id)),
            "admins": len(queries.unit_admins(db, u.university_id)),
        }
    else:
        rows = queries.unit_submissions(db, u)
        metrics = {
            "programs": len(queries.unit_programs(db, u)),
            "supervisors": len(queries.unit_teachers(db, u)),
            "submissions": len(rows),
            "queue": sum(1 for x in rows if x.status in IN_PROGRESS),
        }
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "admin": u,
            "metrics": metrics,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


# ---------- platform admin ----------
@router.get("/platform/universities", response_class=HTMLResponse)
def platform_universities(request: Request, db: DbSession):
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
):
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
):
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
):
    u = require_role(request, db, AdminRole.PLATFORM_ADMIN)
    try:
        university = tenancy.set_university_active(db, university_id, active, actor=u)
    except tenancy.TenancyError as exc:
        return RedirectResponse(flash_url("/platform/universities", error=str(exc)), 303)
    state = "activated" if university.is_active else "deactivated; its users are signed out"
    return RedirectResponse(
        flash_url("/platform/universities", message=f"{university.name} {state}"), 303
    )


@router.get("/admin/ai", response_class=HTMLResponse)
def super_ai(request: Request, db: DbSession):
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


# ---------- university admin ----------
@router.get("/admin/structure", response_class=HTMLResponse)
def super_structure(request: Request, db: DbSession):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    return templates.TemplateResponse(
        request,
        "super_structure.html",
        {
            "request": request,
            "admin": u,
            "faculties": queries.faculties(db, u.university_id),
            "departments": queries.departments(db, u.university_id),
            "institutes": queries.institutes(db, u.university_id),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/admin/faculties")
def add_faculty(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    code: str = Form(...),
):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    faculty = Faculty(name=name.strip(), code=code.strip().upper(), university_id=u.university_id)
    db.add(faculty)
    try:
        db.flush()
        audit.record(db, u, "faculty.create", f"faculty:{faculty.id}", code=faculty.code)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Faculty name/code already exists"), 303
        )
    return RedirectResponse(flash_url("/admin/structure", message="Faculty created"), 303)


@router.post("/admin/institutes")
def add_institute(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    code: str = Form(...),
):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    institute = Institute(
        name=name.strip(), code=code.strip().upper(), university_id=u.university_id
    )
    db.add(institute)
    try:
        db.flush()
        audit.record(db, u, "institute.create", f"institute:{institute.id}", code=institute.code)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Institute name/code already exists"),
            303,
        )
    return RedirectResponse(flash_url("/admin/structure", message="Institute created"), 303)


@router.post("/admin/departments")
def add_department(
    request: Request,
    db: DbSession,
    faculty_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    faculty = db.get(Faculty, faculty_id)
    if not faculty or faculty.university_id != u.university_id:
        raise HTTPException(404)
    department = Department(name=name.strip(), code=code.strip().upper(), faculty_id=faculty.id)
    db.add(department)
    try:
        db.flush()
        audit.record(
            db, u, "department.create", f"department:{department.id}", code=department.code
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Department name/code already exists"),
            303,
        )
    return RedirectResponse(flash_url("/admin/structure", message="Department created"), 303)


@router.get("/admin/admins", response_class=HTMLResponse)
def admin_accounts(request: Request, db: DbSession):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    return templates.TemplateResponse(
        request,
        "super_admins.html",
        {
            "request": request,
            "admin": u,
            "admins": queries.unit_admins(db, u.university_id),
            "departments": queries.departments(db, u.university_id, active_only=True),
            "institutes": queries.institutes(db, u.university_id, active_only=True),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/admin/admins")
def create_unit_admin(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    unit_type: str = Form(...),
    unit_id: int = Form(...),
):
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    units = (
        queries.departments(db, u.university_id)
        if unit_type == "department"
        else queries.institutes(db, u.university_id)
        if unit_type == "institute"
        else []
    )
    if unit_id not in {unit.id for unit in units}:
        raise HTTPException(404)
    if len(password) < tenancy.MIN_ADMIN_PASSWORD:
        error = f"The password must be at least {tenancy.MIN_ADMIN_PASSWORD} characters"
        return RedirectResponse(flash_url("/admin/admins", error=error), 303)
    unit_admin = AdminUser(
        name=name.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=AdminRole.UNIT_ADMIN,
        university_id=u.university_id,
        department_id=unit_id if unit_type == "department" else None,
        institute_id=unit_id if unit_type == "institute" else None,
    )
    db.add(unit_admin)
    try:
        db.flush()
        audit.record(db, u, "unit_admin.create", f"admin:{unit_admin.id}", email=unit_admin.email)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(flash_url("/admin/admins", error="Admin email already exists"), 303)
    return RedirectResponse(
        flash_url("/admin/admins", message="Department/Institute Admin created"), 303
    )


# ---------- unit admin ----------
@router.get("/admin/programs", response_class=HTMLResponse)
def unit_program_page(request: Request, db: DbSession):
    u = require_admin(request, db)
    if u.role != AdminRole.UNIT_ADMIN:
        raise HTTPException(403)
    return templates.TemplateResponse(
        request,
        "unit_programs.html",
        {
            "request": request,
            "admin": u,
            "programs": queries.unit_programs(db, u),
            "message": request.query_params.get("message", ""),
        },
    )


@router.post("/admin/programs")
def unit_add_program(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    code: str = Form(...),
    level: str = Form(...),
):
    u = require_admin(request, db)
    if u.role != AdminRole.UNIT_ADMIN:
        raise HTTPException(403)
    program = Program(
        name=name.strip(),
        code=code.strip().upper(),
        level=level,
        department_id=u.department_id,
        institute_id=u.institute_id,
    )
    db.add(program)
    db.flush()
    audit.record(db, u, "program.create", f"program:{program.id}", code=program.code)
    db.commit()
    return RedirectResponse(flash_url("/admin/programs", message="Program added"), 303)


@router.get("/admin/supervisors", response_class=HTMLResponse)
def unit_supervisors(request: Request, db: DbSession):
    u = require_admin(request, db)
    if u.role != AdminRole.UNIT_ADMIN:
        raise HTTPException(403)
    return templates.TemplateResponse(
        request,
        "unit_supervisors.html",
        {
            "request": request,
            "admin": u,
            "teachers": queries.unit_teachers(db, u),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/admin/supervisors")
def unit_add_supervisor(
    request: Request,
    db: DbSession,
    name: str = Form(...),
    designation: str = Form(...),
    email: str = Form(...),
):
    u = require_admin(request, db)
    if u.role != AdminRole.UNIT_ADMIN:
        raise HTTPException(403)
    allowed = {"Professor", "Associate Professor", "Assistant Professor", "Lecturer"}
    if designation not in allowed:
        raise HTTPException(400)
    teacher = Teacher(
        name=name.strip(),
        designation=designation,
        email=email.strip().lower(),
        university_id=u.university_id,
        department_id=u.department_id,
        institute_id=u.institute_id,
    )
    db.add(teacher)
    try:
        db.flush()
        audit.record(db, u, "supervisor.create", f"teacher:{teacher.id}", email=teacher.email)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(flash_url("/admin/supervisors", error="Email already exists"), 303)
    return RedirectResponse(flash_url("/admin/supervisors", message="Supervisor added"), 303)


@router.get("/admin/configuration", response_class=HTMLResponse)
def unit_configuration(request: Request, db: DbSession):
    u = require_admin(request, db)
    if u.role != AdminRole.UNIT_ADMIN:
        raise HTTPException(403)
    programs = queries.unit_programs(db, u)
    ids = [p.id for p in programs]
    configs = db.query(Template).filter(Template.program_id.in_(ids)).all() if ids else []
    return templates.TemplateResponse(
        request,
        "unit_configuration.html",
        {
            "request": request,
            "admin": u,
            "programs": programs,
            "configs": configs,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/admin/configuration")
def add_configuration(
    request: Request,
    db: DbSession,
    program_id: int = Form(...),
    submission_type: str = Form(...),
    title: str = Form(...),
):
    u = require_admin(request, db)
    p = db.get(Program, program_id)
    if u.role != AdminRole.UNIT_ADMIN or not p or not same_unit(p, u):
        raise HTTPException(403)
    t = (
        db.query(Template)
        .filter(
            Template.program_id == program_id,
            Template.submission_type == submission_type,
        )
        .first()
    )
    if not t:
        t = Template(program_id=program_id, submission_type=submission_type, title=title.strip())
        db.add(t)
        db.flush()
        audit.record(
            db, u, "configuration.create", f"template:{t.id}", submission_type=submission_type
        )
        db.commit()
    return RedirectResponse(
        flash_url("/admin/configuration", message="Submission configuration saved"), 303
    )


@router.post("/admin/configuration/{template_id}/version")
def add_version(
    request: Request,
    template_id: int,
    db: DbSession,
    version: str = Form(...),
    effective_from: str = Form(""),
    status: str = Form("Draft"),
    change_note: str = Form(""),
    pdf: UploadFile = File(...),
    additional_rules_pdf: UploadFile | None = File(None),
):
    u = require_admin(request, db)
    t = db.get(Template, template_id)
    if u.role != AdminRole.UNIT_ADMIN or not t or not same_unit(t.program, u):
        raise HTTPException(403)
    if status not in set(GuidelineStatus):
        raise HTTPException(400, "Unknown guideline status")
    try:
        guideline, info = store_pdf(pdf, u.university_id, "guidelines", "guideline")
    except UploadRefused as e:
        return RedirectResponse(flash_url("/admin/configuration", error=str(e)), 303)
    add_path = ""
    add_text = ""
    if additional_rules_pdf and additional_rules_pdf.filename:
        try:
            extra, extra_info = store_pdf(
                additional_rules_pdf, u.university_id, "guidelines", "additional"
            )
            add_path = extra.relative_path
            add_text = extra_info["text"]
        except UploadRefused as e:
            storage.delete(guideline.relative_path)
            error = f"Additional rules document: {e}"
            return RedirectResponse(flash_url("/admin/configuration", error=error), 303)
    tv = TemplateVersion(
        template_id=t.id,
        version=version.strip(),
        effective_from=effective_from,
        status=GuidelineStatus(status),
        change_note=change_note,
        pdf_path=guideline.relative_path,
        extracted_text=info["text"],
        additional_rules_pdf_path=add_path,
        additional_rules_text=add_text,
    )
    db.add(tv)
    db.flush()
    # Useful base restriction only; academic requirements come from uploaded documents via RAG.
    db.add(
        ValidationRule(
            template_version_id=tv.id,
            name="Searchable PDF",
            category="System Restriction",
            severity="Mandatory",
            validator="searchable_pdf",
            expected="true",
            source_text="System restriction",
        )
    )
    audit.record(
        db, u, "guideline.upload", f"template_version:{tv.id}", version=tv.version, status=status
    )
    db.commit()
    return RedirectResponse(f"/admin/template-version/{tv.id}", 303)


@router.get("/admin/template-version/{version_id}", response_class=HTMLResponse)
def template_version_page(version_id: int, request: Request, db: DbSession):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    parsed_profile = copy.deepcopy(tv.format_profile_json or {})
    parsed_profile = _normalize_profile_severities(parsed_profile)
    job = queue.latest_job(db, f"template_version:{tv.id}")
    return templates.TemplateResponse(
        request,
        "template_version.html",
        {
            "request": request,
            "admin": u,
            "tv": tv,
            "job": job,
            "job_active": job is not None and job.status in ACTIVE_JOB_STATUSES,
            "parsed_profile": parsed_profile,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


async def _form_data(request: Request) -> FormData:
    """Lets a plain `def` route read a form whose field names are dynamic."""
    return await request.form()


@router.post("/admin/template-version/{version_id}/review-rules")
def review_guideline_rules(
    version_id: int,
    request: Request,
    db: DbSession,
    form: FormData = Depends(_form_data),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    if not profile:
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Parse the guideline before reviewing rule severity",
            ),
            303,
        )
    profile = _normalize_profile_severities(profile)
    allowed = {"Mandatory", "Warning", "Recommendation", "Ignore"}
    changes = []
    for prefix, key in [
        ("det", "deterministic_rules"),
        ("sem", "semantic_requirements"),
    ]:
        rules = profile.get(key, []) or []
        for idx, r in enumerate(rules):
            val = str(form.get(f"{prefix}_{idx}", r.get("final_severity", "Mandatory")))
            if val not in allowed:
                continue
            old = r.get("final_severity", "Mandatory")
            if old != val:
                changes.append(
                    {
                        "kind": key,
                        "index": idx,
                        "rule": r.get("type") or r.get("requirement") or f"Rule {idx + 1}",
                        "from": old,
                        "to": val,
                    }
                )
            r["final_severity"] = val
            r["mandatory"] = val == "Mandatory"
            r["enabled"] = val != "Ignore"
    if changes:
        review_log = profile.setdefault("review_audit", [])
        review_log.append(
            {
                "changed_at": datetime.utcnow().isoformat() + "Z",
                "admin_id": u.id,
                "admin_name": u.name,
                "changes": changes,
            }
        )
        profile["review_audit"] = review_log[-100:]
        tv.format_profile_json = profile
        tv.format_profile_status = ProfileStatus.REVIEW_REQUIRED
        audit.record(
            db, u, "guideline.review_rules", f"template_version:{tv.id}", changes=len(changes)
        )
        db.commit()
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                message=f"Saved {len(changes)} rule severity change(s). Review and activate the profile.",
            ),
            303,
        )
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Rule review saved; no severity changes were made.",
        ),
        303,
    )


@router.post("/admin/template-version/{version_id}/semantic-rules")
def add_manual_semantic_rule(
    version_id: int,
    request: Request,
    db: DbSession,
    requirement: str = Form(...),
    target_section: str = Form(""),
    severity: str = Form("Mandatory"),
    expected_evidence: str = Form(""),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    requirement = requirement.strip()
    target_section = target_section.strip()
    expected_evidence = expected_evidence.strip()
    if not requirement:
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Semantic requirement cannot be empty",
            ),
            303,
        )
    if severity not in {"Mandatory", "Warning", "Recommendation", "Ignore"}:
        severity = "Mandatory"
    profile = copy.deepcopy(tv.format_profile_json or {})
    profile = _normalize_profile_severities(
        profile or {"deterministic_rules": [], "semantic_requirements": []}
    )
    new = {
        "semantic_id": "MAN-" + uuid.uuid4().hex[:10].upper(),
        "requirement": requirement,
        "target_section": target_section,
        "source_section": target_section,
        "source_page": None,
        "source_pages": [],
        "evidence": expected_evidence,
        "expected_evidence": expected_evidence,
        "manual": True,
        "source_type": "Manual Semantic Rule",
        "suggested_severity": severity,
        "final_severity": severity,
        "mandatory": severity == "Mandatory",
        "enabled": severity != "Ignore",
        "created_by": u.name,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    before = len(profile.get("semantic_requirements") or [])
    profile.setdefault("semantic_requirements", []).append(new)
    profile = _dedupe_semantic_requirements(profile)
    after = len(profile.get("semantic_requirements") or [])
    tv.format_profile_json = profile
    tv.format_profile_status = ProfileStatus.REVIEW_REQUIRED
    audit.record(
        db, u, "guideline.add_rule", f"template_version:{tv.id}", semantic_id=new["semantic_id"]
    )
    db.commit()
    msg = (
        "Manual semantic rule added."
        if after > before
        else "An equivalent semantic rule already exists; duplicate was merged with the canonical rule."
    )
    return RedirectResponse(flash_url(f"/admin/template-version/{version_id}", message=msg), 303)


@router.post("/admin/template-version/{version_id}/semantic-rules/{semantic_id}/update")
def update_manual_semantic_rule(
    version_id: int,
    semantic_id: str,
    request: Request,
    db: DbSession,
    requirement: str = Form(...),
    target_section: str = Form(""),
    severity: str = Form("Mandatory"),
    expected_evidence: str = Form(""),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    profile = _normalize_profile_severities(profile)
    found = None
    for r in profile.get("semantic_requirements", []) or []:
        if str(r.get("semantic_id")) == semantic_id and r.get("manual"):
            found = r
            break
    if not found:
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Manual semantic rule was not found",
            ),
            303,
        )
    if severity not in {"Mandatory", "Warning", "Recommendation", "Ignore"}:
        severity = "Mandatory"
    found.update(
        {
            "requirement": requirement.strip(),
            "target_section": target_section.strip(),
            "source_section": target_section.strip(),
            "expected_evidence": expected_evidence.strip(),
            "evidence": expected_evidence.strip(),
            "final_severity": severity,
            "suggested_severity": severity,
            "mandatory": severity == "Mandatory",
            "enabled": severity != "Ignore",
            "updated_by": u.name,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    profile = _dedupe_semantic_requirements(profile)
    tv.format_profile_json = profile
    tv.format_profile_status = ProfileStatus.REVIEW_REQUIRED
    audit.record(
        db, u, "guideline.update_rule", f"template_version:{tv.id}", semantic_id=semantic_id
    )
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Manual semantic rule updated. Review and activate the profile.",
        ),
        303,
    )


@router.post("/admin/template-version/{version_id}/semantic-rules/{semantic_id}/delete")
def delete_manual_semantic_rule(version_id: int, semantic_id: str, request: Request, db: DbSession):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    profile = _normalize_profile_severities(profile)
    old = profile.get("semantic_requirements", []) or []
    new = [r for r in old if not (str(r.get("semantic_id")) == semantic_id and r.get("manual"))]
    if len(new) == len(old):
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Only manually added semantic rules can be deleted here",
            ),
            303,
        )
    profile["semantic_requirements"] = new
    tv.format_profile_json = profile
    tv.format_profile_status = ProfileStatus.REVIEW_REQUIRED
    audit.record(
        db, u, "guideline.delete_rule", f"template_version:{tv.id}", semantic_id=semantic_id
    )
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Manual semantic rule deleted. Review and activate the profile.",
        ),
        303,
    )


@router.post("/admin/template-version/{version_id}/activate-profile")
def activate_guideline_profile(version_id: int, request: Request, db: DbSession):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    if not profile or (
        not profile.get("deterministic_rules") and not profile.get("semantic_requirements")
    ):
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="No parsed guideline profile is available to activate",
            ),
            303,
        )
    diag = profile.get("parser_diagnostics") or {}
    if diag.get("activation_blocked"):
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Activation blocked: the uploaded guideline appears inconsistent with the configured program. Upload/re-parse the correct guideline first.",
            ),
            303,
        )
    tv.format_profile_status = ProfileStatus.ACTIVE
    audit.record(db, u, "guideline.activate_profile", f"template_version:{tv.id}")
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Parsed guideline rules activated for validation",
        ),
        303,
    )


@router.post("/admin/template-version/{version_id}/rules")
def add_restriction(
    version_id: int,
    request: Request,
    db: DbSession,
    name: str = Form(...),
    severity: str = Form(...),
    validator: str = Form(...),
    target: str = Form(""),
    expected: str = Form(""),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    db.add(
        ValidationRule(
            template_version_id=version_id,
            name=name.strip(),
            category="Additional Restriction",
            severity=severity,
            validator=validator,
            target=target.strip(),
            expected=expected.strip(),
            source_text="Admin-defined additional restriction",
        )
    )
    audit.record(
        db, u, "guideline.add_restriction", f"template_version:{tv.id}", validator=validator
    )
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Additional restriction added",
        ),
        303,
    )


@router.post("/admin/template-version/{version_id}/index")
def index_guideline(version_id: int, request: Request, db: DbSession):
    """Queue parsing, which queues indexing when it succeeds; the worker runs
    both, so the request never waits on the model (DEF-038)."""
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    page = f"/admin/template-version/{version_id}"
    subject = f"template_version:{tv.id}"
    # A double-click waits here, then finds the first click's job.
    db.query(TemplateVersion).filter(TemplateVersion.id == tv.id).with_for_update().one()
    if queue.has_active_job(db, subject):
        db.rollback()
        return RedirectResponse(
            flash_url(page, error="This guideline is already queued for parsing and indexing"),
            303,
        )
    queue.enqueue(
        db,
        JobType.PARSE_GUIDELINE,
        subject,
        {"template_version_id": tv.id},
        university_id=u.university_id,
    )
    audit.record(db, u, "guideline.queue_parse", subject)
    db.commit()
    return RedirectResponse(
        flash_url(
            page,
            message="Queued: the guideline is parsed first, then indexed. "
            "This page refreshes until both have finished.",
        ),
        303,
    )


@router.get("/admin/submissions", response_class=HTMLResponse)
def unit_submissions(request: Request, db: DbSession):
    u = require_role(request, db, AdminRole.UNIT_ADMIN)
    return templates.TemplateResponse(
        request,
        "unit_submissions.html",
        {"request": request, "admin": u, "rows": queries.unit_submissions(db, u)},
    )


@router.get("/admin/queue", response_class=HTMLResponse)
def unit_queue(request: Request, db: DbSession):
    u = require_role(request, db, AdminRole.UNIT_ADMIN)
    return templates.TemplateResponse(
        request,
        "unit_queue.html",
        {"request": request, "admin": u, "rows": queries.unit_jobs(db, u)},
    )


# ---------- APIs ----------
@router.get("/api/faculty/{faculty_id}/departments")
def faculty_departments(faculty_id: int, db: DbSession):
    return [
        {"id": d.id, "name": d.name, "code": d.code}
        for d in db.query(Department)
        .filter(Department.faculty_id == faculty_id, Department.active == True)
        .order_by(Department.name)
        .all()
    ]


@router.get("/api/student/options/{program_id}")
def student_options(program_id: int, request: Request, db: DbSession):
    s = require_student(request, db)
    p = db.get(Program, program_id)
    if not p or not same_unit(p, s):
        raise HTTPException(403)
    teachers = queries.unit_teachers(db, s)
    versions = []
    for t in p.templates:
        for v in t.versions:
            if v.status == GuidelineStatus.ACTIVE:
                versions.append(
                    {
                        "id": v.id,
                        "submission_type": t.submission_type,
                        "title": t.title,
                        "version": v.version,
                    }
                )
    return {
        "teachers": [
            {"id": x.id, "name": x.name, "designation": x.designation, "email": x.email}
            for x in teachers
        ],
        "templates": versions,
    }


# ---------- submission ----------
@router.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request, db: DbSession):
    s = require_student(request, db)
    return templates.TemplateResponse(
        request,
        "submit.html",
        {
            "request": request,
            "student": s,
            "programs": queries.unit_programs(db, s),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/submit")
def submit(
    request: Request,
    db: DbSession,
    title: str = Form(...),
    program_id: int = Form(...),
    teacher_id: int = Form(...),
    template_version_id: int = Form(...),
    pdf: UploadFile = File(...),
):
    s = require_student(request, db)
    p = db.get(Program, program_id)
    teacher = db.get(Teacher, teacher_id)
    tv = db.get(TemplateVersion, template_version_id)
    if not p or not same_unit(p, s) or not teacher or not tv or tv.template.program_id != p.id:
        raise HTTPException(400, "Invalid selection")
    if teacher.university_id != s.university_id:
        raise HTTPException(403)
    if s.department_id and teacher.department_id != s.department_id:
        raise HTTPException(403)
    if s.institute_id and teacher.institute_id != s.institute_id:
        raise HTTPException(403)
    try:
        stored, info = store_pdf(pdf, s.university_id, "submissions", f"submission_s{s.id}")
    except UploadRefused as e:
        return RedirectResponse(flash_url("/submit", error=str(e)), 303)
    try:
        # A double-click's second request waits here for the first (DEF-037).
        db.query(StudentUser).filter(StudentUser.id == s.id).with_for_update().one()
        duplicate = db.scalars(
            select(Submission)
            .join(SubmissionVersion, SubmissionVersion.submission_id == Submission.id)
            .where(
                Submission.student_user_id == s.id,
                Submission.template_version_id == tv.id,
                Submission.status.in_(IN_PROGRESS),
                SubmissionVersion.sha256 == stored.sha256,
            )
        ).first()
        if duplicate is not None:
            db.rollback()
            storage.delete(stored.relative_path)
            return RedirectResponse(
                flash_url(
                    f"/submission/{duplicate.id}",
                    message="This file is already being validated as this submission.",
                ),
                303,
            )
        check_upload_caps(db, s)
        aip = (
            db.query(AIProfile)
            .filter(
                AIProfile.program_id == p.id,
                AIProfile.submission_type == tv.template.submission_type,
                AIProfile.status == AIProfileStatus.ACTIVE,
            )
            .first()
        )
        sub = Submission(
            university_id=s.university_id,
            student_user_id=s.id,
            title=title.strip(),
            program_id=p.id,
            teacher_id=teacher.id,
            template_version_id=tv.id,
            ai_profile_id=aip.id if aip else None,
        )
        db.add(sub)
        db.flush()
        queue_version(db, sub, stored, info, version_no=1)
        db.commit()  # submission, version and job: all or none
    except UploadRefused as e:
        db.rollback()
        storage.delete(stored.relative_path)
        return RedirectResponse(flash_url("/submit", error=str(e)), 303)
    except BaseException:
        db.rollback()
        storage.delete(stored.relative_path)
        raise
    return RedirectResponse(f"/submission/{sub.id}", 303)


@router.post("/submission/{submission_id}/resubmit")
def resubmit(
    submission_id: int,
    request: Request,
    db: DbSession,
    pdf: UploadFile = File(...),
):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    if not sub or sub.student_user_id != s.id:
        raise HTTPException(404)
    page = f"/submission/{sub.id}"
    try:
        version = save_queue_version(sub, pdf, db)
    except UploadRefused as e:
        return RedirectResponse(flash_url(page, error=str(e)), 303)
    if version is None:
        return RedirectResponse(
            flash_url(page, message="This file is the latest version already; nothing was queued."),
            303,
        )
    return RedirectResponse(page, 303)


class UploadRefused(ValueError):
    """An upload that was not accepted; the message is shown to the uploader."""


def store_pdf(pdf: UploadFile, university_id: int, kind: storage.Kind, prefix: str):
    """Store an upload and read it; returns (StoredFile, inspect_pdf info).

    Rejects files that are not PDFs, too large, encrypted or unreadable, and
    leaves no file behind when it does (P3.8).
    """
    try:
        stored = storage.save_pdf(pdf.file, university_id, kind, prefix)
    except storage.UploadRejected as e:
        raise UploadRefused(str(e)) from None
    try:
        return stored, inspect_pdf(str(storage.resolve(stored.relative_path)))
    except PDFError as e:
        storage.delete(stored.relative_path)
        raise UploadRefused(str(e)) from None


def check_upload_caps(db: Session, student: StudentUser) -> None:
    """Daily upload caps per student and per university (P3.11). The caller
    holds the student's row lock, so the student's own count cannot race."""
    settings = get_settings()
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)  # naive UTC columns
    recent = (
        select(func.count(SubmissionVersion.id))
        .join(Submission, SubmissionVersion.submission_id == Submission.id)
        .where(SubmissionVersion.created_at > since)
    )
    if (
        db.scalar(recent.where(Submission.student_user_id == student.id))
        >= settings.daily_uploads_per_student
    ):
        raise UploadRefused(
            f"You have uploaded {settings.daily_uploads_per_student} versions in the last "
            "24 hours, the daily limit. Try again later."
        )
    if (
        db.scalar(recent.where(Submission.university_id == student.university_id))
        >= settings.daily_uploads_per_university
    ):
        raise UploadRefused("Your university has reached its daily upload limit. Try again later.")


def queue_version(db: Session, sub: Submission, stored, info, version_no: int):
    """Add a version for a stored file and queue its validation; the caller commits."""
    sv = SubmissionVersion(
        submission_id=sub.id,
        version_no=version_no,
        pdf_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        page_count=info["page_count"],
        extracted_text=info["text"],
        status=SubmissionStatus.QUEUED,
    )
    db.add(sv)
    sub.status = SubmissionStatus.QUEUED
    db.flush()  # assigns sv.id for the job
    queue.enqueue(
        db,
        JobType.VALIDATE_SUBMISSION,
        f"submission:{sub.id}",
        {"submission_version_id": sv.id},
        university_id=sub.university_id,
    )
    return sv


def save_queue_version(sub, pdf, db):
    """Store an uploaded PDF as the next version of `sub` and queue it.

    Returns None, keeping nothing, when the file is identical to the latest
    version (a double-click). Raises UploadRefused while an earlier version
    is still queued or running: two uploads must not be numbered alike or
    queue a second GPU job (DEF-026).
    """
    stored, info = store_pdf(pdf, sub.university_id, "submissions", f"submission_{sub.id}")
    try:
        # Concurrent uploads for this submission wait here, one at a time.
        db.query(Submission).filter(Submission.id == sub.id).with_for_update().one()
        latest = db.scalars(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == sub.id)
            .order_by(SubmissionVersion.version_no.desc())
            .limit(1)
        ).first()
        # A failed validation may be retried with the same file.
        if (
            latest is not None
            and latest.sha256 == stored.sha256
            and latest.status != SubmissionStatus.VALIDATION_FAILED
        ):
            db.rollback()
            storage.delete(stored.relative_path)
            return None
        if queue.has_active_job(db, f"submission:{sub.id}"):
            raise UploadRefused(
                "This submission is still being validated. "
                "Upload a revised version after the result is ready."
            )
        check_upload_caps(db, sub.student)
        # One commit: committing the version first released the lock before its
        # job existed, and a second upload in that gap saw no active job (DEF-046).
        sv = queue_version(db, sub, stored, info, (latest.version_no if latest else 0) + 1)
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(stored.relative_path)
        raise
    return sv


@router.get("/submission/{submission_id}", response_class=HTMLResponse)
def view_submission(submission_id: int, request: Request, db: DbSession):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    if not sub or sub.student_user_id != s.id:
        raise HTTPException(404)
    versions = [
        {"obj": v, "results": (v.validation_json or [])}
        for v in sorted(sub.versions, key=lambda x: x.version_no, reverse=True)
    ]
    return templates.TemplateResponse(
        request,
        "submission.html",
        {
            "request": request,
            "student": s,
            "sub": sub,
            "versions": versions,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.get("/files/submission/{version_id}")
def download_submission(version_id: int, request: Request, db: DbSession):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id:
        raise HTTPException(404)
    try:
        path = storage.resolve(v.pdf_path)
    except FileNotFoundError:
        raise HTTPException(404) from None
    return FileResponse(
        path, media_type="application/pdf", filename=f"submission_v{v.version_no}.pdf"
    )


@router.get("/submission/{submission_id}/report/{version_id}", response_class=HTMLResponse)
def view_validation_report(submission_id: int, version_id: int, request: Request, db: DbSession):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    v = db.get(SubmissionVersion, version_id)
    if not sub or sub.student_user_id != s.id or not v or v.submission_id != sub.id:
        raise HTTPException(404)
    parsed = parse_ai_report(v.ai_suggestions or "")
    results = v.validation_json or []
    stats_entry = next((r for r in results if r.get("category") == "Validation Statistics"), None)
    visible_results = [r for r in results if r.get("category") != "Validation Statistics"]
    format_results = [r for r in visible_results if r.get("category") == "Formatting Compliance"]
    semantic_results = [r for r in visible_results if r.get("category") == "Semantic Compliance"]
    restriction_results = [
        r
        for r in visible_results
        if r.get("category") not in {"Formatting Compliance", "Semantic Compliance"}
    ]
    failed_format = [r for r in format_results if r.get("status") == "FAIL"]
    affected_pages = sorted({p for r in failed_format for p in (r.get("affected_pages") or [])})
    stats = (stats_entry or {}).get("statistics") or {}
    report_summary = {
        "format_failures": len(failed_format),
        "warnings": sum(1 for r in visible_results if r.get("status") == "WARNING"),
        "affected_pages": affected_pages,
        "mandatory_failures": sum(
            1
            for r in visible_results
            if r.get("severity") == "Mandatory" and r.get("status") == "FAIL"
        ),
        **stats,
    }
    return templates.TemplateResponse(
        request,
        "validation_report.html",
        {
            "request": request,
            "student": s,
            "sub": sub,
            "version": v,
            "parsed": parsed,
            "results": restriction_results,
            "format_results": format_results,
            "semantic_results": semantic_results,
            "report_summary": report_summary,
            "report_run_token": _report_run_token(v),
        },
    )


@router.get("/files/report/{version_id}")
def download_report_legacy(version_id: int, request: Request, db: DbSession):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id:
        raise HTTPException(404)
    token = _report_run_token(v)
    return RedirectResponse(f"/files/report/{v.submission_id}/{v.id}/{token}", 303)


@router.get("/files/report/{submission_id}/{version_id}/{run_token}")
def download_report_bound(
    submission_id: int,
    version_id: int,
    run_token: str,
    request: Request,
    db: DbSession,
):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id or v.submission_id != submission_id:
        raise HTTPException(404)
    current_token = _report_run_token(v)
    if run_token != current_token:
        raise HTTPException(
            409,
            "This report link belongs to an older validation run. Refresh the full report and download the current run.",
        )
    results = v.validation_json or []
    report = (
        storage.directory(v.submission.university_id, "reports")
        / f"validation_submission_{v.submission_id}_v{v.version_no}_{current_token}.pdf"
    )
    build_validation_report(str(report), v.submission, v, results, v.ai_suggestions or "")
    if v.report_path != storage.relative(report):
        v.report_path = storage.relative(report)
        db.commit()
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-UniSubmit-Validation-Run": current_token,
    }
    filename = f"validation_submission_{v.submission_id}_v{v.version_no}_{current_token}.pdf"
    return FileResponse(
        str(report), media_type="application/pdf", filename=filename, headers=headers
    )


# ---------- validation job (run by the worker, see handlers.py) ----------
def semantic_validation_payload(program, tv, fmt_profile, student_text, base_model):
    """The /rag/validate request for one document; tools/measure_validation.py sends the same."""
    return {
        "department": program.unit_name,
        "program": program.name,
        "submission_type": tv.template.submission_type,
        "template_version": tv.version,
        "student_text": student_text[:30000],
        "query": "Evaluate this submitted document against all relevant requirements in the approved guideline and additional rules.",
        "base_model": base_model,
        # The collection indexing actually created (DEF-032).
        "collection": tv.rag_collection or None,
        "limit": 10,
        "explicit_requirements": (
            [
                r
                for r in fmt_profile.get("semantic_requirements", [])
                if r.get("enabled", True) and r.get("final_severity") != "Ignore"
            ]
            if isinstance(fmt_profile, dict) and tv.format_profile_status == ProfileStatus.ACTIVE
            else []
        ),
    }


def create_app() -> FastAPI:
    """Run with `uvicorn unisubmit.main:create_app --factory`.

    The app creates no tables and no accounts at startup: the schema comes from
    `unisubmit migrate` (DEF-039), accounts from `unisubmit create-super-admin`
    (DEF-028). Validation jobs run in `python -m unisubmit.worker` (DEF-024).
    """
    settings = get_settings()
    configure_logging(json=settings.log_json)
    engine = create_db_engine(str(settings.database_url))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="UniSubmit AI", version="10.0", lifespan=lifespan)
    app.state.session_factory = make_session_factory(engine)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        max_age=SESSION_HOURS * 60 * 60,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    app.add_middleware(RequestIdMiddleware)  # added last, so it runs first
    register_error_handlers(app, templates)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    templates.env.globals["default_llm_model"] = settings.ollama_model
    return app
