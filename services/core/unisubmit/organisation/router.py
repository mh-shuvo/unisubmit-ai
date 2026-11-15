"""The academic structure.

Faculties, departments, institutes and unit admins (University Admin);
programs and supervisors (unit admin).
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError

from .. import audit, queries
from ..auth.dependencies import (
    require_admin,
    require_role,
)
from ..core.db import DbSession
from ..core.security import hash_password, verify_csrf
from ..core.templating import flash_url, templates
from ..models import AdminRole, AdminUser, Department, Faculty, Institute, Program, Teacher
from ..tenancy import service as tenancy

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/admin/structure", response_class=HTMLResponse)
def super_structure(request: Request, db: DbSession) -> Response:
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    return templates.TemplateResponse(
        request,
        "super_structure.html",
        {
            "request": request,
            "admin": u,
            "faculties": queries.faculties(db, u.own_university_id),
            "departments": queries.departments(db, u.own_university_id),
            "institutes": queries.institutes(db, u.own_university_id),
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
) -> Response:
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
) -> Response:
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
) -> Response:
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
def admin_accounts(request: Request, db: DbSession) -> Response:
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    return templates.TemplateResponse(
        request,
        "super_admins.html",
        {
            "request": request,
            "admin": u,
            "admins": queries.unit_admins(db, u.own_university_id),
            "departments": queries.departments(db, u.own_university_id, active_only=True),
            "institutes": queries.institutes(db, u.own_university_id, active_only=True),
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
) -> Response:
    u = require_role(request, db, AdminRole.UNIVERSITY_ADMIN)
    units = (
        queries.departments(db, u.own_university_id)
        if unit_type == "department"
        else queries.institutes(db, u.own_university_id)
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


@router.get("/admin/programs", response_class=HTMLResponse)
def unit_program_page(request: Request, db: DbSession) -> Response:
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
) -> Response:
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
def unit_supervisors(request: Request, db: DbSession) -> Response:
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
) -> Response:
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


@router.get("/api/faculty/{faculty_id}/departments")
def faculty_departments(faculty_id: int, db: DbSession) -> list[dict[str, object]]:
    return [
        {"id": d.id, "name": d.name, "code": d.code}
        for d in db.query(Department)
        .filter(Department.faculty_id == faculty_id, Department.active)
        .order_by(Department.name)
        .all()
    ]
