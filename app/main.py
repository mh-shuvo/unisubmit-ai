import asyncio, json, shutil, uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .db import Base, engine, get_db, SessionLocal
from .models import (
    Faculty,
    Institute,
    Department,
    Program,
    Teacher,
    Template,
    TemplateVersion,
    ValidationRule,
    AIProfile,
    Submission,
    SubmissionVersion,
    AdminUser,
    StudentUser,
    AIJob,
)
from .security import hash_password, verify_password
from .pdf_utils import inspect_pdf, PDFError
from .validators import run_rules
from .format_validation import validate_format_profile, validate_all_profile_rules
from .ai_service_client import (
    health as ai_health,
    rag_validate,
    index_template,
    extract_format_profile,
    parse_guideline_profile,
)
from .reporting import build_validation_report
from .report_parser import parse_ai_report, inline_markdown_html
from .emailer import send_submission_email
from .seed import seed
from .config import settings

ROOT = Path(__file__).resolve().parent.parent
STORAGE = ROOT / "storage"
TEMPLATE_STORE = STORAGE / "templates"
SUBMISSION_STORE = STORAGE / "submissions"
REPORT_STORE = STORAGE / "reports"
for p in [TEMPLATE_STORE, SUBMISSION_STORE, REPORT_STORE]:
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="UniSubmit AI", version="9.10")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret,
    max_age=60 * 60 * 12,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
templates.env.globals["render_md"] = inline_markdown_html

worker_task = None


@app.on_event("startup")
async def startup():
    global worker_task
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed(db)
    db.close()
    worker_task = asyncio.create_task(queue_worker())


@app.on_event("shutdown")
async def shutdown():
    if worker_task:
        worker_task.cancel()


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
                except Exception:
                    pass
            try:
                if r.get("source_page") is not None:
                    pages.append(int(r.get("source_page")))
            except Exception:
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
                except Exception:
                    pass
            try:
                if r.get("source_page") is not None:
                    pages.add(int(r.get("source_page")))
            except Exception:
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
            base["merged_duplicate_count"] = (
                int(base.get("merged_duplicate_count") or 0) + 1
            )
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
    return (
        "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))[:80]
        or "deterministic"
    )


def admin_user(request: Request, db: Session):
    uid = request.session.get("admin_id")
    return db.get(AdminUser, uid) if uid else None


def student_user(request: Request, db: Session):
    uid = request.session.get("student_id")
    return db.get(StudentUser, uid) if uid else None


def require_admin(request: Request, db: Session):
    u = admin_user(request, db)
    if not u or not u.active:
        raise HTTPException(401, "Admin login required")
    return u


def require_super(request: Request, db: Session):
    u = require_admin(request, db)
    if u.role != "SUPER_ADMIN":
        raise HTTPException(403, "Super Admin access required")
    return u


def require_student(request: Request, db: Session):
    u = student_user(request, db)
    if not u or not u.active:
        raise HTTPException(401, "Student login required")
    return u


def unit_programs(db, u):
    q = db.query(Program).filter(Program.active == True)
    if getattr(u, "department_id", None):
        q = q.filter(Program.department_id == u.department_id)
    elif getattr(u, "institute_id", None):
        q = q.filter(Program.institute_id == u.institute_id)
    else:
        return []
    return q.order_by(Program.name).all()


def unit_teachers(db, u):
    q = db.query(Teacher).filter(Teacher.active == True)
    if getattr(u, "department_id", None):
        q = q.filter(Teacher.department_id == u.department_id)
    elif getattr(u, "institute_id", None):
        q = q.filter(Teacher.institute_id == u.institute_id)
    else:
        return []
    return q.order_by(Teacher.name).all()


def same_unit(program, u):
    return (u.department_id and program.department_id == u.department_id) or (
        u.institute_id and program.institute_id == u.institute_id
    )


# ---------- public ----------
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "faculties": db.query(Faculty).count(),
            "departments": db.query(Department).count(),
            "institutes": db.query(Institute).count(),
        },
    )


@app.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse("how_it_works.html", {"request": request})


@app.get("/guidelines", response_class=HTMLResponse)
def guidelines(request: Request, db: Session = Depends(get_db)):
    versions = (
        db.query(TemplateVersion)
        .filter(TemplateVersion.status == "Active")
        .order_by(TemplateVersion.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "guidelines.html", {"request": request, "versions": versions}
    )


@app.get("/guidelines/{version_id}/download")
def guideline_download(version_id: int, db: Session = Depends(get_db)):
    tv = db.get(TemplateVersion, version_id)
    if not tv:
        raise HTTPException(404)
    return FileResponse(
        tv.pdf_path,
        media_type="application/pdf",
        filename=f"{tv.template.program.code}_{tv.template.submission_type}_{tv.version}.pdf",
    )


# ---------- student auth ----------
@app.get("/student/register", response_class=HTMLResponse)
def student_register_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "student_register.html",
        {
            "request": request,
            "departments": db.query(Department).filter(Department.active == True).all(),
            "institutes": db.query(Institute).filter(Institute.active == True).all(),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/student/register")
def student_register(
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    unit_type: str = Form(...),
    unit_id: int = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(StudentUser).filter(StudentUser.email == email.strip().lower()).first():
        return RedirectResponse(
            flash_url("/student/register", error="Email already registered"), 303
        )
    dep_id = unit_id if unit_type == "department" else None
    inst_id = unit_id if unit_type == "institute" else None
    if unit_type == "department" and not db.get(Department, unit_id):
        raise HTTPException(400)
    if unit_type == "institute" and not db.get(Institute, unit_id):
        raise HTTPException(400)
    s = StudentUser(
        name=name.strip(),
        student_id=student_id.strip(),
        email=email.strip().lower(),
        password_hash=hash_password(password),
        department_id=dep_id,
        institute_id=inst_id,
        verified=True,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    request.session["student_id"] = s.id
    return RedirectResponse("/student/dashboard", 303)


@app.get("/student/login", response_class=HTMLResponse)
def student_login_page(request: Request):
    return templates.TemplateResponse(
        "student_login.html",
        {"request": request, "error": request.query_params.get("error", "")},
    )


@app.post("/student/login")
def student_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    s = (
        db.query(StudentUser)
        .filter(StudentUser.email == email.strip().lower(), StudentUser.active == True)
        .first()
    )
    if not s or not verify_password(password, s.password_hash):
        return RedirectResponse(
            flash_url("/student/login", error="Invalid email or password"), 303
        )
    request.session.clear()
    request.session["student_id"] = s.id
    return RedirectResponse("/student/dashboard", 303)


@app.get("/student/logout")
def student_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@app.get("/student/dashboard", response_class=HTMLResponse)
def student_dashboard(request: Request, db: Session = Depends(get_db)):
    s = require_student(request, db)
    rows = (
        db.query(Submission)
        .filter(Submission.student_user_id == s.id)
        .order_by(Submission.created_at.desc())
        .all()
    )
    counts = {
        "total": len(rows),
        "queued": sum(
            1 for x in rows if x.status in ("QUEUED", "PROCESSING", "RETRYING")
        ),
        "revision": sum(1 for x in rows if x.status == "REVISION REQUIRED"),
        "passed": sum(1 for x in rows if x.status == "PASSED"),
    }
    return templates.TemplateResponse(
        "student_dashboard.html",
        {"request": request, "student": s, "rows": rows, "counts": counts},
    )


@app.get("/student/profile", response_class=HTMLResponse)
def student_profile(request: Request, db: Session = Depends(get_db)):
    s = require_student(request, db)
    return templates.TemplateResponse(
        "student_profile.html", {"request": request, "student": s}
    )


# ---------- admin auth ----------
@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": request.query_params.get("error", "")},
    )


@app.post("/admin/login")
def admin_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    u = (
        db.query(AdminUser)
        .filter(AdminUser.email == email.strip().lower(), AdminUser.active == True)
        .first()
    )
    if not u or not verify_password(password, u.password_hash):
        return RedirectResponse(
            flash_url("/admin/login", error="Invalid admin credentials"), 303
        )
    request.session.clear()
    request.session["admin_id"] = u.id
    return RedirectResponse("/admin", 303)


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        metrics = {
            "faculties": db.query(Faculty).count(),
            "departments": db.query(Department).count(),
            "institutes": db.query(Institute).count(),
            "admins": db.query(AdminUser)
            .filter(AdminUser.role == "UNIT_ADMIN")
            .count(),
        }
    else:
        programs = unit_programs(db, u)
        teachers = unit_teachers(db, u)
        q = db.query(Submission)
        q = q.join(Program)
        q = (
            q.filter(Program.department_id == u.department_id)
            if u.department_id
            else q.filter(Program.institute_id == u.institute_id)
        )
        rows = q.all()
        metrics = {
            "programs": len(programs),
            "supervisors": len(teachers),
            "submissions": len(rows),
            "queue": sum(
                1 for x in rows if x.status in ("QUEUED", "PROCESSING", "RETRYING")
            ),
        }
    return templates.TemplateResponse(
        "admin_dashboard.html", {"request": request, "admin": u, "metrics": metrics}
    )


# ---------- super admin ----------
@app.get("/admin/structure", response_class=HTMLResponse)
def super_structure(request: Request, db: Session = Depends(get_db)):
    u = require_super(request, db)
    return templates.TemplateResponse(
        "super_structure.html",
        {
            "request": request,
            "admin": u,
            "faculties": db.query(Faculty).all(),
            "departments": db.query(Department).all(),
            "institutes": db.query(Institute).all(),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/admin/faculties")
def add_faculty(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    require_super(request, db)
    db.add(Faculty(name=name.strip(), code=code.strip().upper()))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Faculty name/code already exists"), 303
        )
    return RedirectResponse(
        flash_url("/admin/structure", message="Faculty created"), 303
    )


@app.post("/admin/institutes")
def add_institute(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    require_super(request, db)
    db.add(Institute(name=name.strip(), code=code.strip().upper()))
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Institute name/code already exists"),
            303,
        )
    return RedirectResponse(
        flash_url("/admin/structure", message="Institute created"), 303
    )


@app.post("/admin/departments")
def add_department(
    request: Request,
    faculty_id: int = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
):
    require_super(request, db)
    db.add(
        Department(name=name.strip(), code=code.strip().upper(), faculty_id=faculty_id)
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/structure", error="Department name/code already exists"),
            303,
        )
    return RedirectResponse(
        flash_url("/admin/structure", message="Department created"), 303
    )


@app.get("/admin/admins", response_class=HTMLResponse)
def admin_accounts(request: Request, db: Session = Depends(get_db)):
    u = require_super(request, db)
    return templates.TemplateResponse(
        "super_admins.html",
        {
            "request": request,
            "admin": u,
            "admins": db.query(AdminUser).filter(AdminUser.role == "UNIT_ADMIN").all(),
            "departments": db.query(Department).filter(Department.active == True).all(),
            "institutes": db.query(Institute).filter(Institute.active == True).all(),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/admin/admins")
def create_unit_admin(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    unit_type: str = Form(...),
    unit_id: int = Form(...),
    db: Session = Depends(get_db),
):
    require_super(request, db)
    dep = unit_id if unit_type == "department" else None
    inst = unit_id if unit_type == "institute" else None
    db.add(
        AdminUser(
            name=name.strip(),
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role="UNIT_ADMIN",
            department_id=dep,
            institute_id=inst,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/admins", error="Admin email already exists"), 303
        )
    return RedirectResponse(
        flash_url("/admin/admins", message="Department/Institute Admin created"), 303
    )


@app.get("/admin/ai", response_class=HTMLResponse)
async def super_ai(request: Request, db: Session = Depends(get_db)):
    u = require_super(request, db)
    status = await ai_health(settings.ai_service_url)
    profiles = db.query(AIProfile).all()
    return templates.TemplateResponse(
        "super_ai.html",
        {"request": request, "admin": u, "status": status, "profiles": profiles},
    )


# ---------- unit admin ----------
@app.get("/admin/programs", response_class=HTMLResponse)
def unit_program_page(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    return templates.TemplateResponse(
        "unit_programs.html",
        {
            "request": request,
            "admin": u,
            "programs": unit_programs(db, u),
            "message": request.query_params.get("message", ""),
        },
    )


@app.post("/admin/programs")
def unit_add_program(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    level: str = Form(...),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    db.add(
        Program(
            name=name.strip(),
            code=code.strip().upper(),
            level=level,
            department_id=u.department_id,
            institute_id=u.institute_id,
        )
    )
    db.commit()
    return RedirectResponse(flash_url("/admin/programs", message="Program added"), 303)


@app.get("/admin/supervisors", response_class=HTMLResponse)
def unit_supervisors(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    return templates.TemplateResponse(
        "unit_supervisors.html",
        {
            "request": request,
            "admin": u,
            "teachers": unit_teachers(db, u),
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/admin/supervisors")
def unit_add_supervisor(
    request: Request,
    name: str = Form(...),
    designation: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    allowed = {"Professor", "Associate Professor", "Assistant Professor", "Lecturer"}
    if designation not in allowed:
        raise HTTPException(400)
    db.add(
        Teacher(
            name=name.strip(),
            designation=designation,
            email=email.strip().lower(),
            department_id=u.department_id,
            institute_id=u.institute_id,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(
            flash_url("/admin/supervisors", error="Email already exists"), 303
        )
    return RedirectResponse(
        flash_url("/admin/supervisors", message="Supervisor added"), 303
    )


@app.get("/admin/configuration", response_class=HTMLResponse)
def unit_configuration(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        return HTTPException(403)
    programs = unit_programs(db, u)
    ids = [p.id for p in programs]
    configs = (
        db.query(Template).filter(Template.program_id.in_(ids)).all() if ids else []
    )
    return templates.TemplateResponse(
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


@app.post("/admin/configuration")
def add_configuration(
    request: Request,
    program_id: int = Form(...),
    submission_type: str = Form(...),
    title: str = Form(...),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    p = db.get(Program, program_id)
    if u.role == "SUPER_ADMIN" or not p or not same_unit(p, u):
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
        db.add(
            Template(
                program_id=program_id,
                submission_type=submission_type,
                title=title.strip(),
            )
        )
        db.commit()
    return RedirectResponse(
        flash_url("/admin/configuration", message="Submission configuration saved"), 303
    )


@app.post("/admin/configuration/{template_id}/version")
async def add_version(
    request: Request,
    template_id: int,
    version: str = Form(...),
    effective_from: str = Form(""),
    status: str = Form("Draft"),
    change_note: str = Form(""),
    pdf: UploadFile = File(...),
    additional_rules_pdf: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    t = db.get(Template, template_id)
    if u.role == "SUPER_ADMIN" or not t or not same_unit(t.program, u):
        raise HTTPException(403)
    if not pdf.filename.lower().endswith(".pdf"):
        return RedirectResponse(
            flash_url("/admin/configuration", error="Guideline must be PDF"), 303
        )
    dest = TEMPLATE_STORE / f"guideline_{uuid.uuid4().hex}.pdf"
    with dest.open("wb") as f:
        shutil.copyfileobj(pdf.file, f)
    try:
        info = inspect_pdf(str(dest))
    except PDFError as e:
        dest.unlink(missing_ok=True)
        return RedirectResponse(flash_url("/admin/configuration", error=str(e)), 303)
    add_path = ""
    add_text = ""
    if additional_rules_pdf and additional_rules_pdf.filename:
        if not additional_rules_pdf.filename.lower().endswith(".pdf"):
            return RedirectResponse(
                flash_url(
                    "/admin/configuration",
                    error="Additional rules document must be PDF",
                ),
                303,
            )
        ad = TEMPLATE_STORE / f"additional_{uuid.uuid4().hex}.pdf"
        with ad.open("wb") as f:
            shutil.copyfileobj(additional_rules_pdf.file, f)
        try:
            ai = inspect_pdf(str(ad))
            add_path = str(ad)
            add_text = ai["text"]
        except PDFError:
            ad.unlink(missing_ok=True)
    tv = TemplateVersion(
        template_id=t.id,
        version=version.strip(),
        effective_from=effective_from,
        status=status,
        change_note=change_note,
        pdf_path=str(dest),
        extracted_text=info["text"],
        additional_rules_pdf_path=add_path,
        additional_rules_text=add_text,
    )
    db.add(tv)
    db.commit()
    db.refresh(tv)
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
    db.commit()
    return RedirectResponse(f"/admin/template-version/{tv.id}", 303)


@app.get("/admin/template-version/{version_id}", response_class=HTMLResponse)
def template_version_page(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    try:
        parsed_profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        parsed_profile = {}
    parsed_profile = _normalize_profile_severities(parsed_profile)
    return templates.TemplateResponse(
        "template_version.html",
        {
            "request": request,
            "admin": u,
            "tv": tv,
            "parsed_profile": parsed_profile,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/admin/template-version/{version_id}/review-rules")
async def review_guideline_rules(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    try:
        profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        profile = {}
    if not profile:
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Parse the guideline before reviewing rule severity",
            ),
            303,
        )
    profile = _normalize_profile_severities(profile)
    form = await request.form()
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
                        "rule": r.get("type")
                        or r.get("requirement")
                        or f"Rule {idx+1}",
                        "from": old,
                        "to": val,
                    }
                )
            r["final_severity"] = val
            r["mandatory"] = val == "Mandatory"
            r["enabled"] = val != "Ignore"
    if changes:
        audit = profile.setdefault("review_audit", [])
        audit.append(
            {
                "changed_at": datetime.utcnow().isoformat() + "Z",
                "admin_id": u.id,
                "admin_name": u.name,
                "changes": changes,
            }
        )
        profile["review_audit"] = audit[-100:]
        tv.format_profile_json = json.dumps(profile)
        tv.format_profile_status = "Parsed - Review Required"
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


@app.post("/admin/template-version/{version_id}/semantic-rules")
def add_manual_semantic_rule(
    version_id: int,
    request: Request,
    requirement: str = Form(...),
    target_section: str = Form(""),
    severity: str = Form("Mandatory"),
    expected_evidence: str = Form(""),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
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
    try:
        profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        profile = {}
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
    tv.format_profile_json = json.dumps(profile)
    tv.format_profile_status = "Parsed - Review Required"
    db.commit()
    msg = (
        "Manual semantic rule added."
        if after > before
        else "An equivalent semantic rule already exists; duplicate was merged with the canonical rule."
    )
    return RedirectResponse(
        flash_url(f"/admin/template-version/{version_id}", message=msg), 303
    )


@app.post("/admin/template-version/{version_id}/semantic-rules/{semantic_id}/update")
def update_manual_semantic_rule(
    version_id: int,
    semantic_id: str,
    request: Request,
    requirement: str = Form(...),
    target_section: str = Form(""),
    severity: str = Form("Mandatory"),
    expected_evidence: str = Form(""),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    try:
        profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        profile = {}
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
    tv.format_profile_json = json.dumps(profile)
    tv.format_profile_status = "Parsed - Review Required"
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Manual semantic rule updated. Review and activate the profile.",
        ),
        303,
    )


@app.post("/admin/template-version/{version_id}/semantic-rules/{semantic_id}/delete")
def delete_manual_semantic_rule(
    version_id: int, semantic_id: str, request: Request, db: Session = Depends(get_db)
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    try:
        profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        profile = {}
    profile = _normalize_profile_severities(profile)
    old = profile.get("semantic_requirements", []) or []
    new = [
        r
        for r in old
        if not (str(r.get("semantic_id")) == semantic_id and r.get("manual"))
    ]
    if len(new) == len(old):
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error="Only manually added semantic rules can be deleted here",
            ),
            303,
        )
    profile["semantic_requirements"] = new
    tv.format_profile_json = json.dumps(profile)
    tv.format_profile_status = "Parsed - Review Required"
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Manual semantic rule deleted. Review and activate the profile.",
        ),
        303,
    )


@app.post("/admin/template-version/{version_id}/activate-profile")
def activate_guideline_profile(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    try:
        profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        profile = {}
    if not profile or (
        not profile.get("deterministic_rules")
        and not profile.get("semantic_requirements")
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
    tv.format_profile_status = "Active"
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Parsed guideline rules activated for validation",
        ),
        303,
    )


@app.post("/admin/template-version/{version_id}/rules")
def add_restriction(
    version_id: int,
    request: Request,
    name: str = Form(...),
    severity: str = Form(...),
    validator: str = Form(...),
    target: str = Form(""),
    expected: str = Form(""),
    db: Session = Depends(get_db),
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
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
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            message="Additional restriction added",
        ),
        303,
    )


@app.post("/admin/template-version/{version_id}/index")
async def index_guideline(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role == "SUPER_ADMIN" or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    p = tv.template.program

    # v9.1: guideline-first means parse the approved PDF BEFORE RAG indexing.
    profile = (
        db.query(AIProfile)
        .filter(
            AIProfile.program_id == p.id,
            AIProfile.submission_type == tv.template.submission_type,
        )
        .first()
    )
    if not profile:
        profile = AIProfile(
            program_id=p.id,
            submission_type=tv.template.submission_type,
            service_url=settings.ai_service_url,
            status="Active",
        )
        db.add(profile)
        db.flush()
    parse_fields = {
        "department": p.unit_name,
        "program": p.name,
        "submission_type": tv.template.submission_type,
        "template_version": tv.version,
        "base_model": profile.base_model,
    }
    parsed_profile = await parse_guideline_profile(
        profile.service_url, parse_fields, tv.pdf_path
    )
    if parsed_profile.get("status") != "success":
        tv.format_profile_json = "{}"
        tv.format_profile_status = "Parsing Failed"
        db.commit()
        detail = parsed_profile.get("detail", "Unknown parser error")
        return RedirectResponse(
            flash_url(
                f"/admin/template-version/{version_id}",
                error=f"Guideline parsing failed: {detail}",
            ),
            303,
        )

    # Preserve administrator-authored semantic rules across guideline re-parsing.
    try:
        previous = json.loads(tv.format_profile_json or "{}")
    except Exception:
        previous = {}
    manual_semantic = [
        dict(r)
        for r in (previous.get("semantic_requirements") or [])
        if isinstance(r, dict) and r.get("manual")
    ]
    previous_audit = list(previous.get("review_audit") or [])
    parsed = parsed_profile.get("profile") or {
        "deterministic_rules": [],
        "semantic_requirements": [],
    }
    parsed.setdefault("semantic_requirements", []).extend(manual_semantic)
    if previous_audit:
        parsed["review_audit"] = previous_audit[-100:]
    parsed = _normalize_profile_severities(parsed)
    tv.format_profile_json = json.dumps(parsed)
    rule_count = len(parsed.get("deterministic_rules", [])) + len(
        parsed.get("semantic_requirements", [])
    )
    tv.format_profile_status = (
        "Parsed - Review Required" if rule_count else "Parsed - No Explicit Rules Found"
    )
    db.flush()

    # Index the same approved guideline after parsing so RAG supports grounded semantic validation.
    fields = {
        "department": p.unit_name,
        "program": p.name,
        "submission_type": tv.template.submission_type,
        "template_version": tv.version,
        "recreate": "true",
    }
    result = await index_template(settings.ai_service_url, fields, tv.pdf_path)
    if result.get("status") == "success" and tv.additional_rules_pdf_path:
        extra_fields = dict(fields)
        extra_fields["recreate"] = "false"
        extra_result = await index_template(
            settings.ai_service_url, extra_fields, tv.additional_rules_pdf_path
        )
        if extra_result.get("status") != "success":
            result = {
                "status": "error",
                "detail": "Primary guideline indexed but additional rules indexing failed",
            }

    if result.get("status") == "success":
        tv.rag_status = "Indexed"
        tv.rag_collection = result.get("collection", "")
        profile.rag_collection = tv.rag_collection
        profile.status = "Active"
        db.commit()
        if rule_count:
            msg = f"Guideline parsed first ({rule_count} explicit requirements), then indexed into RAG. Review and activate the parsed rules."
        else:
            msg = "Guideline parsed first, but no explicit rules were extracted. RAG indexing succeeded; review the parser diagnostics."
        return RedirectResponse(
            flash_url(f"/admin/template-version/{version_id}", message=msg), 303
        )

    tv.rag_status = "Index Failed"
    db.commit()
    return RedirectResponse(
        flash_url(
            f"/admin/template-version/{version_id}",
            error=f'Guideline parsing succeeded, but RAG indexing failed: {result.get("detail","Unknown indexing error")}',
        ),
        303,
    )


@app.get("/admin/submissions", response_class=HTMLResponse)
def unit_submissions(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    q = db.query(Submission).join(Program)
    q = (
        q.filter(Program.department_id == u.department_id)
        if u.department_id
        else q.filter(Program.institute_id == u.institute_id)
    )
    rows = q.order_by(Submission.created_at.desc()).all()
    return templates.TemplateResponse(
        "unit_submissions.html", {"request": request, "admin": u, "rows": rows}
    )


@app.get("/admin/queue", response_class=HTMLResponse)
def unit_queue(request: Request, db: Session = Depends(get_db)):
    u = require_admin(request, db)
    if u.role == "SUPER_ADMIN":
        raise HTTPException(403)
    jobs = db.query(AIJob).join(SubmissionVersion).join(Submission).join(Program)
    jobs = (
        jobs.filter(Program.department_id == u.department_id)
        if u.department_id
        else jobs.filter(Program.institute_id == u.institute_id)
    )
    jobs = jobs.order_by(AIJob.created_at.desc()).all()
    return templates.TemplateResponse(
        "unit_queue.html", {"request": request, "admin": u, "jobs": jobs}
    )


# ---------- APIs ----------
@app.get("/api/faculty/{faculty_id}/departments")
def faculty_departments(faculty_id: int, db: Session = Depends(get_db)):
    return [
        {"id": d.id, "name": d.name, "code": d.code}
        for d in db.query(Department)
        .filter(Department.faculty_id == faculty_id, Department.active == True)
        .order_by(Department.name)
        .all()
    ]


@app.get("/api/student/options/{program_id}")
def student_options(program_id: int, request: Request, db: Session = Depends(get_db)):
    s = require_student(request, db)
    p = db.get(Program, program_id)
    if not p or not same_unit(p, s):
        raise HTTPException(403)
    teachers = unit_teachers(db, s)
    versions = []
    for t in p.templates:
        for v in t.versions:
            if v.status == "Active":
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
@app.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request, db: Session = Depends(get_db)):
    s = require_student(request, db)
    return templates.TemplateResponse(
        "submit.html",
        {"request": request, "student": s, "programs": unit_programs(db, s)},
    )


@app.post("/submit")
async def submit(
    request: Request,
    title: str = Form(...),
    program_id: int = Form(...),
    teacher_id: int = Form(...),
    template_version_id: int = Form(...),
    pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    s = require_student(request, db)
    p = db.get(Program, program_id)
    teacher = db.get(Teacher, teacher_id)
    tv = db.get(TemplateVersion, template_version_id)
    if (
        not p
        or not same_unit(p, s)
        or not teacher
        or not tv
        or tv.template.program_id != p.id
    ):
        raise HTTPException(400, "Invalid selection")
    if s.department_id and teacher.department_id != s.department_id:
        raise HTTPException(403)
    if s.institute_id and teacher.institute_id != s.institute_id:
        raise HTTPException(403)
    aip = (
        db.query(AIProfile)
        .filter(
            AIProfile.program_id == p.id,
            AIProfile.submission_type == tv.template.submission_type,
            AIProfile.status == "Active",
        )
        .first()
    )
    sub = Submission(
        student_user_id=s.id,
        title=title.strip(),
        program_id=p.id,
        teacher_id=teacher.id,
        template_version_id=tv.id,
        ai_profile_id=aip.id if aip else None,
        status="QUEUED",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    await save_queue_version(sub, pdf, db)
    return RedirectResponse(f"/submission/{sub.id}", 303)


@app.post("/submission/{submission_id}/resubmit")
async def resubmit(
    submission_id: int,
    request: Request,
    pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    if not sub or sub.student_user_id != s.id:
        raise HTTPException(404)
    await save_queue_version(sub, pdf, db)
    return RedirectResponse(f"/submission/{sub.id}", 303)


async def save_queue_version(sub, pdf, db):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are allowed")
    version_no = len(sub.versions) + 1
    dest = (
        SUBMISSION_STORE
        / f"submission_{sub.id}_v{version_no}_{uuid.uuid4().hex[:8]}.pdf"
    )
    with dest.open("wb") as f:
        shutil.copyfileobj(pdf.file, f)
    try:
        info = inspect_pdf(str(dest))
    except PDFError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    sv = SubmissionVersion(
        submission_id=sub.id,
        version_no=version_no,
        pdf_path=str(dest),
        extracted_text=info["text"],
        status="QUEUED",
    )
    db.add(sv)
    sub.status = "QUEUED"
    db.commit()
    db.refresh(sv)
    db.add(AIJob(submission_version_id=sv.id, status="QUEUED"))
    db.commit()


@app.get("/submission/{submission_id}", response_class=HTMLResponse)
def view_submission(
    submission_id: int, request: Request, db: Session = Depends(get_db)
):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    if not sub or sub.student_user_id != s.id:
        raise HTTPException(404)
    versions = [
        {"obj": v, "results": json.loads(v.validation_json or "[]")}
        for v in sorted(sub.versions, key=lambda x: x.version_no, reverse=True)
    ]
    return templates.TemplateResponse(
        "submission.html",
        {"request": request, "student": s, "sub": sub, "versions": versions},
    )


@app.get("/files/submission/{version_id}")
def download_submission(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id:
        raise HTTPException(404)
    return FileResponse(
        v.pdf_path,
        media_type="application/pdf",
        filename=f"submission_v{v.version_no}.pdf",
    )


@app.get("/submission/{submission_id}/report/{version_id}", response_class=HTMLResponse)
def view_validation_report(
    submission_id: int, version_id: int, request: Request, db: Session = Depends(get_db)
):
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    v = db.get(SubmissionVersion, version_id)
    if not sub or sub.student_user_id != s.id or not v or v.submission_id != sub.id:
        raise HTTPException(404)
    parsed = parse_ai_report(v.ai_suggestions or "")
    results = json.loads(v.validation_json or "[]")
    stats_entry = next(
        (r for r in results if r.get("category") == "Validation Statistics"), None
    )
    visible_results = [
        r for r in results if r.get("category") != "Validation Statistics"
    ]
    format_results = [
        r for r in visible_results if r.get("category") == "Formatting Compliance"
    ]
    semantic_results = [
        r for r in visible_results if r.get("category") == "Semantic Compliance"
    ]
    restriction_results = [
        r
        for r in visible_results
        if r.get("category") not in {"Formatting Compliance", "Semantic Compliance"}
    ]
    failed_format = [r for r in format_results if r.get("status") == "FAIL"]
    affected_pages = sorted(
        {p for r in failed_format for p in (r.get("affected_pages") or [])}
    )
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


@app.get("/files/report/{version_id}")
def download_report_legacy(
    version_id: int, request: Request, db: Session = Depends(get_db)
):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id:
        raise HTTPException(404)
    token = _report_run_token(v)
    return RedirectResponse(f"/files/report/{v.submission_id}/{v.id}/{token}", 303)


@app.get("/files/report/{submission_id}/{version_id}/{run_token}")
def download_report_bound(
    submission_id: int,
    version_id: int,
    run_token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if (
        not v
        or v.submission.student_user_id != s.id
        or v.submission_id != submission_id
    ):
        raise HTTPException(404)
    current_token = _report_run_token(v)
    if run_token != current_token:
        raise HTTPException(
            409,
            "This report link belongs to an older validation run. Refresh the full report and download the current run.",
        )
    results = json.loads(v.validation_json or "[]")
    report = (
        REPORT_STORE
        / f"validation_submission_{v.submission_id}_v{v.version_no}_{current_token}.pdf"
    )
    build_validation_report(
        str(report), v.submission, v, results, v.ai_suggestions or ""
    )
    if v.report_path != str(report):
        v.report_path = str(report)
        db.commit()
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-UniSubmit-Validation-Run": current_token,
    }
    filename = (
        f"validation_submission_{v.submission_id}_v{v.version_no}_{current_token}.pdf"
    )
    return FileResponse(
        str(report), media_type="application/pdf", filename=filename, headers=headers
    )


# ---------- sequential persistent worker ----------
async def queue_worker():
    while True:
        try:
            db = SessionLocal()
            job = (
                db.query(AIJob)
                .filter(AIJob.status.in_(["QUEUED", "RETRYING"]))
                .order_by(AIJob.priority.asc(), AIJob.created_at.asc())
                .first()
            )
            if not job:
                db.close()
                await asyncio.sleep(2)
                continue
            job.status = "PROCESSING"
            job.started_at = datetime.utcnow()
            job.attempt_count += 1
            db.commit()
            try:
                await process_job(db, job)
                job.status = "COMPLETED"
                job.completed_at = datetime.utcnow()
                job.error_message = ""
                db.commit()
            except Exception as e:
                job.error_message = str(e)
                job.status = "RETRYING" if job.attempt_count < 3 else "FAILED"
                if job.status == "FAILED":
                    job.completed_at = datetime.utcnow()
                    job.submission_version.status = "Validation Failed"
                    job.submission_version.submission.status = "Validation Failed"
                db.commit()
            db.close()
        except asyncio.CancelledError:
            break
        except Exception:
            try:
                db.close()
            except:
                pass
            await asyncio.sleep(3)


async def process_job(db, job):
    sv = job.submission_version
    sub = sv.submission
    tv = sub.template_version
    info = inspect_pdf(sv.pdf_path)
    results, det_status = run_rules(tv.rules, info)
    try:
        fmt_profile = json.loads(tv.format_profile_json or "{}")
    except Exception:
        fmt_profile = {}
    fmt_profile = _normalize_profile_severities(fmt_profile)
    format_results = (
        validate_all_profile_rules(fmt_profile, info)
        if tv.format_profile_status == "Active"
        else []
    )
    results = format_results + results
    format_failed = any(
        r.get("category") == "Formatting Compliance"
        and r.get("severity") == "Mandatory"
        and r.get("status") == "FAIL"
        for r in format_results
    )
    ai_text = "AI validation unavailable."
    ai_ref = ""
    final_status = "REVISION REQUIRED" if format_failed else det_status
    if sub.ai_profile and tv.rag_status == "Indexed":
        payload = {
            "department": sub.program.unit_name,
            "program": sub.program.name,
            "submission_type": tv.template.submission_type,
            "template_version": tv.version,
            "student_text": info["text"][:30000],
            "query": "Evaluate this submitted document against all relevant requirements in the approved guideline and additional rules.",
            "base_model": sub.ai_profile.base_model,
            "limit": 10,
            "explicit_requirements": (
                [
                    r
                    for r in fmt_profile.get("semantic_requirements", [])
                    if r.get("enabled", True) and r.get("final_severity") != "Ignore"
                ]
                if isinstance(fmt_profile, dict)
                and tv.format_profile_status == "Active"
                else []
            ),
        }
        ai = await rag_validate(sub.ai_profile.service_url, payload)
        if not ai:
            raise RuntimeError("AI service unavailable or RAG validation failed")
        ai_text = ai.get("response", "")
        ai_ref = ai.get("run_id", "")
        semantic_results = ai.get("semantic_results") or []
        results.extend(semantic_results)
        if any(
            r.get("severity") == "Mandatory" and r.get("status") == "FAIL"
            for r in semantic_results
        ):
            final_status = "REVISION REQUIRED"
    elif sub.ai_profile:
        ai_text = "Guideline has not been indexed into the AI knowledge base yet. Additional deterministic restrictions were checked."
    stats = _profile_rule_stats(fmt_profile, results, tv.rules)
    results.append(
        {"category": "Validation Statistics", "status": "META", "statistics": stats}
    )
    sv.validation_json = json.dumps(results)
    sv.ai_suggestions = ai_text
    sv.ai_run_reference = ai_ref
    sv.status = final_status
    sub.status = final_status
    run_token = _report_run_token(sv)
    report = (
        REPORT_STORE
        / f"validation_submission_{sub.id}_v{sv.version_no}_{run_token}.pdf"
    )
    build_validation_report(str(report), sub, sv, results, ai_text)
    sv.report_path = str(report)
    db.commit()
    if final_status == "PASSED":
        body = f"Student: {sub.student.name}\nStudent ID: {sub.student.student_id}\nProgram: {sub.program.name}\nSubmission: {tv.template.submission_type}\nTitle: {sub.title}\nTemplate Version: {tv.version}\nPre-Submission Validation: PASSED\nSubmission Version: {sv.version_no}\n\nThis is a pre-submission compliance check only and does not constitute academic approval."
        ok, msg = send_submission_email(
            sub.teacher.email,
            f"Validated {tv.template.submission_type} Submission - {sub.student.name}",
            body,
            [sv.pdf_path, str(report)],
        )
        sub.email_status = "Sent" if ok else "Failed / SMTP not configured"
        if ok:
            sv.emailed_at = datetime.utcnow()
        db.commit()


# Browser-friendly auth redirects
from fastapi.responses import RedirectResponse as _Redirect
from fastapi.exceptions import HTTPException as _HTTPException


@app.exception_handler(_HTTPException)
async def friendly_http_error(request: Request, exc: _HTTPException):
    if exc.status_code == 401:
        if request.url.path.startswith("/admin"):
            return _Redirect("/admin/login", 303)
        if (
            request.url.path.startswith("/student")
            or request.url.path.startswith("/submit")
            or request.url.path.startswith("/submission")
        ):
            return _Redirect("/student/login", 303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
