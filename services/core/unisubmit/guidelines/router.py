"""Guideline configurations and versions.

Upload, parsed-rule review, activation and parse requests.
"""

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData

from .. import audit, queries, queue, storage
from ..auth.dependencies import (
    require_admin,
    same_unit,
)
from ..core.db import DbSession
from ..core.security import verify_csrf
from ..core.templating import flash_url, templates
from ..guidelines.profile import dedupe_semantic_requirements, normalize_profile_severities
from ..models import (
    ACTIVE_JOB_STATUSES,
    AdminRole,
    GuidelineStatus,
    JobType,
    ProfileStatus,
    Program,
    Template,
    TemplateVersion,
    ValidationRule,
)
from ..submissions.service import (
    UploadRefused,
    store_pdf,
)
from ..tenancy.dependencies import to_university_page, university_page

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/guidelines")
def guidelines_redirect(db: DbSession) -> Response:
    return to_university_page(db, "guidelines")


@router.get("/u/{slug}/guidelines", response_class=HTMLResponse)
def guidelines(slug: str, request: Request, db: DbSession) -> Response:
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
def guideline_download(version_id: int, db: DbSession) -> Response:
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


@router.get("/admin/configuration", response_class=HTMLResponse)
def unit_configuration(request: Request, db: DbSession) -> Response:
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
) -> Response:
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
) -> Response:
    u = require_admin(request, db)
    t = db.get(Template, template_id)
    if u.role != AdminRole.UNIT_ADMIN or not t or not same_unit(t.program, u):
        raise HTTPException(403)
    if status not in set(GuidelineStatus):
        raise HTTPException(400, "Unknown guideline status")
    try:
        guideline, info = store_pdf(pdf.file, u.own_university_id, "guidelines", "guideline")
    except UploadRefused as e:
        return RedirectResponse(flash_url("/admin/configuration", error=str(e)), 303)
    add_path = ""
    add_text = ""
    if additional_rules_pdf and additional_rules_pdf.filename:
        try:
            extra, extra_info = store_pdf(
                additional_rules_pdf.file, u.own_university_id, "guidelines", "additional"
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
def template_version_page(version_id: int, request: Request, db: DbSession) -> Response:
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    parsed_profile = copy.deepcopy(tv.format_profile_json or {})
    parsed_profile = normalize_profile_severities(parsed_profile)
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


async def form_data(request: Request) -> FormData:
    """Lets a plain `def` route read a form whose field names are dynamic."""
    return await request.form()


@router.post("/admin/template-version/{version_id}/review-rules")
def review_guideline_rules(
    version_id: int,
    request: Request,
    db: DbSession,
    form: FormData = Depends(form_data),
) -> Response:
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
    profile = normalize_profile_severities(profile)
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
                "changed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
                message=f"Saved {len(changes)} rule severity change(s). "
                "Review and activate the profile.",
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
) -> Response:
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
    profile = normalize_profile_severities(
        profile or {"deterministic_rules": [], "semantic_requirements": []}
    )
    new: dict[str, Any] = {
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
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    before = len(profile.get("semantic_requirements") or [])
    profile.setdefault("semantic_requirements", []).append(new)
    profile = dedupe_semantic_requirements(profile)
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
        else "An equivalent semantic rule already exists; "
        "duplicate was merged with the canonical rule."
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
) -> Response:
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    profile = normalize_profile_severities(profile)
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
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )
    profile = dedupe_semantic_requirements(profile)
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
def delete_manual_semantic_rule(
    version_id: int, semantic_id: str, request: Request, db: DbSession
) -> Response:
    u = require_admin(request, db)
    tv = db.get(TemplateVersion, version_id)
    if not tv or u.role != AdminRole.UNIT_ADMIN or not same_unit(tv.template.program, u):
        raise HTTPException(403)
    profile = copy.deepcopy(tv.format_profile_json or {})
    profile = normalize_profile_severities(profile)
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
def activate_guideline_profile(version_id: int, request: Request, db: DbSession) -> Response:
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
                error="Activation blocked: the uploaded guideline appears inconsistent with "
                "the configured program. Upload/re-parse the correct guideline first.",
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
) -> Response:
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
def index_guideline(version_id: int, request: Request, db: DbSession) -> Response:
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
        university_id=u.own_university_id,
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
