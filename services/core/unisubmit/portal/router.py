"""The home page, how it works, and the student and admin dashboards."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import queries
from ..auth.dependencies import (
    require_admin,
    require_student,
)
from ..core.db import DbSession
from ..core.security import hash_password, verify_csrf
from ..core.templating import flash_url, templates
from ..models import (
    IN_PROGRESS,
    AdminRole,
    AdminUser,
    StudentUser,
    Submission,
    SubmissionStatus,
    University,
)
from ..tenancy import service as tenancy
from ..tenancy.dependencies import to_university_page, university_page

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: DbSession) -> Response:
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "universities": tenancy.active_universities(db)},
    )


@router.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request) -> Response:
    return templates.TemplateResponse(request, "how_it_works.html", {"request": request})


@router.get("/student/dashboard", response_class=HTMLResponse)
def student_dashboard(request: Request, db: DbSession) -> Response:
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


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: DbSession) -> Response:
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
            "faculties": len(queries.faculties(db, u.own_university_id)),
            "departments": len(queries.departments(db, u.own_university_id)),
            "institutes": len(queries.institutes(db, u.own_university_id)),
            "admins": len(queries.unit_admins(db, u.own_university_id)),
        }
    else:
        submissions, in_progress = queries.unit_submission_counts(db, u)
        metrics = {
            "programs": len(queries.unit_programs(db, u)),
            "supervisors": len(queries.unit_teachers(db, u)),
            "submissions": submissions,
            "queue": in_progress,
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
