"""The work behind each job type, run by the worker (P3.3-P3.6).

Each handler reads its inputs in one short transaction, calls the AI service
or SMTP with no transaction open, and writes its results in a second short
one. Handlers are idempotent: running a job again overwrites its results, and
an email is never sent twice for a version that has `emailed_at` set.
"""

import asyncio
import copy
import smtplib
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from . import storage
from .ai_service_client import (
    AIServiceError,
    index_template,
    parse_guideline_profile,
    rag_validate,
)
from .core.config import get_settings
from .emailer import send_submission_email
from .guidelines.profile import normalize_profile_severities
from .models import (
    AIProfile,
    AIProfileStatus,
    EmailStatus,
    JobType,
    ProfileStatus,
    Program,
    RagStatus,
    SubmissionStatus,
    SubmissionVersion,
    TemplateVersion,
)
from .pdf_utils import hidden_text_result, inspect_pdf, visible_text
from .queue import FollowUp, TransientError
from .reporting import build_validation_report
from .submissions.service import report_run_token
from .validation.rules.engine import validate_all_profile_rules
from .validation.semantic import profile_rule_stats, semantic_validation_payload
from .validators import run_rules

Payload = dict[str, Any]


class JobHandler(Protocol):
    async def run(self, payload: Payload) -> Sequence[FollowUp]:
        """Do the job; return the jobs to queue once it has succeeded."""

    def on_final_failure(self, payload: Payload) -> None:
        """Record that the job will not be tried again, so no page shows it as
        still in progress."""


def registry(sessions: sessionmaker[Session]) -> dict[JobType, JobHandler]:
    return {
        JobType.VALIDATE_SUBMISSION: ValidateSubmission(sessions),
        JobType.PARSE_GUIDELINE: ParseGuideline(sessions),
        JobType.INDEX_GUIDELINE: IndexGuideline(sessions),
        JobType.SEND_EMAIL: SendSupervisorEmail(sessions),
    }


async def _ai[T](call: Awaitable[T]) -> T:
    """An unreachable or overloaded AI service is retried; a rejection is not."""
    try:
        return await call
    except AIServiceError as exc:
        if exc.transient:
            raise TransientError(str(exc)) from exc
        raise


def _version(db: Session, payload: Payload) -> SubmissionVersion:
    version = db.get(SubmissionVersion, payload["submission_version_id"])
    if version is None:
        raise LookupError(f"Submission version {payload['submission_version_id']} is gone")
    return version


def _guideline(db: Session, payload: Payload) -> TemplateVersion:
    guideline = db.get(TemplateVersion, payload["template_version_id"])
    if guideline is None:
        raise LookupError(f"Guideline version {payload['template_version_id']} is gone")
    return guideline


@dataclass(frozen=True, slots=True)
class _Checked:
    """A validation's deterministic half, ready for the AI's semantic half."""

    results: list[dict[str, Any]]
    status: SubmissionStatus
    profile: dict[str, Any]
    ai_payload: dict[str, Any] | None
    ai_note: str


SEMANTIC_STATUSES = {"PASS", "FAIL", "WARNING", "NOT VERIFIED"}


def _semantic_only(results: object) -> list[dict[str, Any]]:
    """The model's results, confined to the semantic category (P4.9).

    Whatever the document persuaded the model to write, its rows cannot pose as
    formatting or restriction results, and an unknown status counts as not
    verified. Deterministic results are never replaced: the model's rows are
    only added, and they can only make the outcome stricter.
    """
    rows = results if isinstance(results, list) else []
    return [
        {
            **row,
            "category": "Semantic Compliance",
            "status": row["status"] if row.get("status") in SEMANTIC_STATUSES else "NOT VERIFIED",
        }
        for row in rows
        if isinstance(row, dict)
    ]


class ValidateSubmission:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def run(self, payload: Payload) -> Sequence[FollowUp]:
        with self._sessions.begin() as db:
            checked = self._check(_version(db, payload))
        ai = None
        if checked.ai_payload is not None:
            # One AI service per installation: a URL stored on an old profile
            # row must not override the configured one (DEF-035).
            ai = await _ai(rag_validate(get_settings().ai_service_url, checked.ai_payload))
            if not ai:
                raise TransientError("The AI service returned an empty validation result")
        with self._sessions.begin() as db:
            return self._record(_version(db, payload), checked, ai)

    def on_final_failure(self, payload: Payload) -> None:
        with self._sessions.begin() as db:
            version = db.get(SubmissionVersion, payload["submission_version_id"])
            if version is not None:
                version.status = version.submission.status = SubmissionStatus.VALIDATION_FAILED

    @staticmethod
    def _check(version: SubmissionVersion) -> _Checked:
        submission = version.submission
        guideline = submission.template_version
        version.status = submission.status = SubmissionStatus.PROCESSING
        info = inspect_pdf(str(storage.resolve(version.pdf_path)))
        results, outcome = run_rules(guideline.rules, info)
        if (hidden := hidden_text_result(info)) is not None:
            results.append(hidden)
        status = SubmissionStatus(outcome)
        profile = normalize_profile_severities(copy.deepcopy(guideline.format_profile_json or {}))
        format_results = (
            validate_all_profile_rules(profile, info)
            if guideline.format_profile_status == ProfileStatus.ACTIVE
            else []
        )
        if any(
            r.get("severity") == "Mandatory" and r.get("status") == "FAIL" for r in format_results
        ):
            status = SubmissionStatus.REVISION_REQUIRED
        ai_payload, ai_note = None, "AI validation unavailable."
        if submission.ai_profile and guideline.rag_status == RagStatus.INDEXED:
            ai_payload = semantic_validation_payload(
                submission.program,
                guideline,
                profile,
                visible_text(info),
                submission.ai_profile.base_model,
            )
        elif submission.ai_profile:
            ai_note = (
                "Guideline has not been indexed into the AI knowledge base yet. "
                "Additional deterministic restrictions were checked."
            )
        return _Checked(format_results + results, status, profile, ai_payload, ai_note)

    @staticmethod
    def _record(
        version: SubmissionVersion, checked: _Checked, ai: dict[str, Any] | None
    ) -> Sequence[FollowUp]:
        submission = version.submission
        results, status, ai_text = list(checked.results), checked.status, checked.ai_note
        version.ai_run_reference = ""
        if ai is not None:
            ai_text = ai.get("response", "")
            version.ai_run_reference = ai.get("run_id", "")
            semantic = _semantic_only(ai.get("semantic_results"))
            results.extend(semantic)
            if any(
                r.get("severity") == "Mandatory" and r.get("status") == "FAIL" for r in semantic
            ):
                status = SubmissionStatus.REVISION_REQUIRED
        stats = profile_rule_stats(checked.profile, results, submission.template_version.rules)
        results.append({"category": "Validation Statistics", "status": "META", "statistics": stats})
        version.validation_json = results
        version.ai_suggestions = ai_text
        version.status = submission.status = status
        report = (
            storage.directory(submission.university_id, "reports")
            / f"validation_submission_{submission.id}_v{version.version_no}_"
            f"{report_run_token(version)}.pdf"
        )
        build_validation_report(str(report), submission, version, results, ai_text)
        version.report_path = storage.relative(report)
        if status != SubmissionStatus.PASSED or version.emailed_at is not None:
            return []
        submission.email_status = EmailStatus.QUEUED
        # Its own subject: a slow SMTP server must not block the next upload.
        return [
            FollowUp(
                JobType.SEND_EMAIL,
                f"email:{version.id}",
                {"submission_version_id": version.id},
                priority=10,
            )
        ]


class SendSupervisorEmail:
    """P3.6: an SMTP failure is retried on its own and never fails the validation."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def run(self, payload: Payload) -> Sequence[FollowUp]:
        with self._sessions.begin() as db:
            version = _version(db, payload)
            if version.emailed_at is not None:
                return []
            submission, guideline = version.submission, version.submission.template_version
            kind = guideline.template.submission_type
            body = (
                f"Student: {submission.student.name}\n"
                f"Student ID: {submission.student.student_id}\n"
                f"Program: {submission.program.name}\n"
                f"Submission: {kind}\n"
                f"Title: {submission.title}\n"
                f"Template Version: {guideline.version}\n"
                "Pre-Submission Validation: PASSED\n"
                f"Submission Version: {version.version_no}\n\n"
                "This is a pre-submission compliance check only and does not constitute "
                "academic approval."
            )
            message = (
                submission.teacher.email,
                f"Validated {kind} Submission - {submission.student.name}",
                body,
                [str(storage.resolve(version.pdf_path)), str(storage.resolve(version.report_path))],
            )
        try:
            sent, _ = await asyncio.to_thread(send_submission_email, *message)
        except (smtplib.SMTPException, OSError) as exc:
            raise TransientError(f"SMTP: {type(exc).__name__}: {exc}") from exc
        with self._sessions.begin() as db:
            version = _version(db, payload)
            if sent:
                version.emailed_at = datetime.now(UTC).replace(tzinfo=None)  # naive UTC column
            version.submission.email_status = (
                EmailStatus.SENT if sent else EmailStatus.NOT_CONFIGURED
            )
        return []

    def on_final_failure(self, payload: Payload) -> None:
        with self._sessions.begin() as db:
            version = db.get(SubmissionVersion, payload["submission_version_id"])
            if version is not None:
                version.submission.email_status = EmailStatus.FAILED


def _ai_profile(db: Session, guideline: TemplateVersion) -> AIProfile:
    program, kind = guideline.template.program, guideline.template.submission_type
    profile = (
        db.query(AIProfile)
        .filter(AIProfile.program_id == program.id, AIProfile.submission_type == kind)
        .first()
    )
    if profile is None:
        profile = AIProfile(
            program_id=program.id,
            submission_type=kind,
            service_url=get_settings().ai_service_url,
            status=AIProfileStatus.ACTIVE,
        )
        db.add(profile)
        db.flush()
    return profile


def _fields(guideline: TemplateVersion) -> dict[str, str]:
    program: Program = guideline.template.program
    return {
        "department": program.unit_name,
        "program": program.name,
        "submission_type": guideline.template.submission_type,
        "template_version": guideline.version,
    }


class ParseGuideline:
    """Guideline-first (v9.1): parse the approved PDF into rules, then index it."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def run(self, payload: Payload) -> Sequence[FollowUp]:
        with self._sessions.begin() as db:
            guideline = _guideline(db, payload)
            fields = {**_fields(guideline), "base_model": _ai_profile(db, guideline).base_model}
            pdf = str(storage.resolve(guideline.pdf_path))
        parsed = await _ai(parse_guideline_profile(get_settings().ai_service_url, fields, pdf))
        if parsed.get("status") != "success":
            raise RuntimeError(f"Guideline parsing failed: {parsed.get('detail', 'no detail')}")
        with self._sessions.begin() as db:
            guideline = _guideline(db, payload)
            # Rules and audit entries written by administrators survive re-parsing.
            previous = guideline.format_profile_json or {}
            profile = parsed.get("profile") or {
                "deterministic_rules": [],
                "semantic_requirements": [],
            }
            profile.setdefault("semantic_requirements", []).extend(
                dict(r)
                for r in previous.get("semantic_requirements") or []
                if isinstance(r, dict) and r.get("manual")
            )
            if previous.get("review_audit"):
                profile["review_audit"] = list(previous["review_audit"])[-100:]
            profile = normalize_profile_severities(profile)
            guideline.format_profile_json = profile
            rules = len(profile.get("deterministic_rules", [])) + len(
                profile.get("semantic_requirements", [])
            )
            guideline.format_profile_status = (
                ProfileStatus.REVIEW_REQUIRED if rules else ProfileStatus.NO_RULES
            )
        return [FollowUp(JobType.INDEX_GUIDELINE, f"template_version:{guideline.id}", payload)]

    def on_final_failure(self, payload: Payload) -> None:
        with self._sessions.begin() as db:
            guideline = db.get(TemplateVersion, payload["template_version_id"])
            if guideline is not None:
                guideline.format_profile_json = {}
                guideline.format_profile_status = ProfileStatus.PARSING_FAILED


class IndexGuideline:
    """Index the parsed guideline and its additional rules for RAG."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    async def run(self, payload: Payload) -> Sequence[FollowUp]:
        with self._sessions.begin() as db:
            guideline = _guideline(db, payload)
            program = guideline.template.program
            fields = {
                **_fields(guideline),
                # Named by id: display names are not unique and slugify alike (DEF-032).
                "collection": f"tv_{guideline.id}",
                # In every point's payload, to audit which tenant owns it (P2.10).
                "university_id": str(program.unit_university_id),
                "recreate": "true",
            }
            pdfs = [str(storage.resolve(guideline.pdf_path))]
            if guideline.additional_rules_pdf_path:
                pdfs.append(str(storage.resolve(guideline.additional_rules_pdf_path)))
        url = get_settings().ai_service_url
        collection = ""
        for number, pdf in enumerate(pdfs):
            result = await _ai(index_template(url, fields, pdf))
            if result.get("status") != "success":
                which = "Guideline" if number == 0 else "Additional rules"
                raise RuntimeError(f"{which} indexing failed: {result.get('detail', 'no detail')}")
            collection = collection or result.get("collection", "")
            fields = {**fields, "recreate": "false"}  # additional rules join the collection
        with self._sessions.begin() as db:
            guideline = _guideline(db, payload)
            guideline.rag_status = RagStatus.INDEXED
            guideline.rag_collection = collection
            profile = _ai_profile(db, guideline)
            profile.rag_collection = collection
            profile.status = AIProfileStatus.ACTIVE
        return []

    def on_final_failure(self, payload: Payload) -> None:
        with self._sessions.begin() as db:
            guideline = db.get(TemplateVersion, payload["template_version_id"])
            if guideline is not None:
                guideline.rag_status = RagStatus.INDEX_FAILED
