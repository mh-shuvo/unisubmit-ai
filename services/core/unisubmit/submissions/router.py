"""Students' submissions and the unit's list of them.

New submissions, revised versions, reports and downloads.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from .. import queries, storage
from ..auth.dependencies import (
    require_role,
    require_student,
    same_unit,
)
from ..core.db import DbSession
from ..core.security import verify_csrf
from ..core.templating import flash_url, templates
from ..models import (
    IN_PROGRESS,
    AdminRole,
    AIProfile,
    AIProfileStatus,
    GuidelineStatus,
    Program,
    StudentUser,
    Submission,
    SubmissionVersion,
    Teacher,
    TemplateVersion,
)
from ..report_parser import parse_ai_report
from ..reporting import build_validation_report
from ..storage import UploadRefused, store_pdf
from ..submissions.service import (
    check_upload_caps,
    queue_version,
    report_run_token,
    save_queue_version,
)

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/api/student/options/{program_id}")
def student_options(program_id: int, request: Request, db: DbSession) -> dict[str, object]:
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


@router.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request, db: DbSession) -> Response:
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
) -> Response:
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
        stored, info = store_pdf(pdf.file, s.university_id, "submissions", f"submission_s{s.id}")
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
) -> Response:
    s = require_student(request, db)
    sub = db.get(Submission, submission_id)
    if not sub or sub.student_user_id != s.id:
        raise HTTPException(404)
    page = f"/submission/{sub.id}"
    try:
        version = save_queue_version(sub, pdf.file, db)
    except UploadRefused as e:
        return RedirectResponse(flash_url(page, error=str(e)), 303)
    if version is None:
        return RedirectResponse(
            flash_url(page, message="This file is the latest version already; nothing was queued."),
            303,
        )
    return RedirectResponse(page, 303)


@router.get("/submission/{submission_id}", response_class=HTMLResponse)
def view_submission(submission_id: int, request: Request, db: DbSession) -> Response:
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
def download_submission(version_id: int, request: Request, db: DbSession) -> Response:
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
def view_validation_report(
    submission_id: int, version_id: int, request: Request, db: DbSession
) -> Response:
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
            "report_run_token": report_run_token(v),
        },
    )


@router.get("/files/report/{version_id}")
def download_report_legacy(version_id: int, request: Request, db: DbSession) -> Response:
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id:
        raise HTTPException(404)
    token = report_run_token(v)
    return RedirectResponse(f"/files/report/{v.submission_id}/{v.id}/{token}", 303)


@router.get("/files/report/{submission_id}/{version_id}/{run_token}")
def download_report_bound(
    submission_id: int,
    version_id: int,
    run_token: str,
    request: Request,
    db: DbSession,
) -> Response:
    s = require_student(request, db)
    v = db.get(SubmissionVersion, version_id)
    if not v or v.submission.student_user_id != s.id or v.submission_id != submission_id:
        raise HTTPException(404)
    current_token = report_run_token(v)
    if run_token != current_token:
        raise HTTPException(
            409,
            "This report link belongs to an older validation run. "
            "Refresh the full report and download the current run.",
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


@router.get("/admin/submissions", response_class=HTMLResponse)
def unit_submissions(request: Request, db: DbSession, page: int = 1) -> Response:
    u = require_role(request, db, AdminRole.UNIT_ADMIN)
    return templates.TemplateResponse(
        request,
        "unit_submissions.html",
        {"request": request, "admin": u, "page": queries.unit_submissions(db, u, page)},
    )
