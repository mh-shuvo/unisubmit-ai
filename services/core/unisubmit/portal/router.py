"""The home page, how it works, and the student and admin dashboards."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from .. import queries
from ..auth.dependencies import (
    require_admin,
    require_student,
)
from ..core.db import DbSession
from ..core.security import verify_csrf
from ..core.templating import templates
from ..models import IN_PROGRESS, AdminRole, AdminUser, Submission, SubmissionStatus, University
from ..tenancy import service as tenancy

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
