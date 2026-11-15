"""What validation sends to the AI service, and the statistics it records."""

from typing import Any

from ..guidelines.profile import normalize_profile_severities
from ..models import ProfileStatus, Program, TemplateVersion, ValidationRule

Result = dict[str, Any]
STATISTICS_NOTE = (
    "Every active deterministic and semantic guideline rule, plus additional restrictions, "
    "is listed with an explicit status. NOT VERIFIED rules are excluded from the "
    "compliance-rate denominator."
)
SEMANTIC_QUERY = (
    "Evaluate this submitted document against all relevant requirements in the approved "
    "guideline and additional rules."
)


def _counts(rows: list[Result]) -> dict[str, int]:
    return {
        "total": len(rows),
        "passed": sum(1 for r in rows if r.get("status") == "PASS"),
        "failed": sum(1 for r in rows if r.get("status") == "FAIL"),
        "warnings": sum(1 for r in rows if r.get("status") == "WARNING"),
        "not_verified": sum(1 for r in rows if r.get("status") == "NOT VERIFIED"),
    }


def profile_rule_stats(
    profile: dict[str, Any] | None,
    results: list[Result],
    additional_rules: list[ValidationRule],
) -> dict[str, Any]:
    profile = normalize_profile_severities(profile or {})
    sem_rules = [
        r
        for r in (profile.get("semantic_requirements") or [])
        if r.get("final_severity") != "Ignore" and r.get("enabled", True)
    ]
    fc = _counts([r for r in results if r.get("category") == "Formatting Compliance"])
    sc = _counts([r for r in results if r.get("category") == "Semantic Compliance"])
    ac = _counts(
        [
            r
            for r in results
            if r.get("category")
            not in {"Formatting Compliance", "Semantic Compliance", "Validation Statistics"}
        ]
    )
    passed = fc["passed"] + sc["passed"] + ac["passed"]
    failed = fc["failed"] + sc["failed"] + ac["failed"]
    warnings = fc["warnings"] + sc["warnings"] + ac["warnings"]
    verified = passed + failed + warnings
    return {
        "total_checked": fc["total"] + sc["total"] + ac["total"],
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "not_verified": fc["not_verified"] + sc["not_verified"] + ac["not_verified"],
        "compliance_rate": round((passed / verified) * 100, 1) if verified else None,
        "semantic_total": len(sem_rules),
        "semantic_checked": sc["total"],
        "categories": {
            "Deterministic Guideline Rules": fc,
            "Semantic Guideline Rules": sc,
            "Additional Restrictions": ac,
        },
        "note": STATISTICS_NOTE,
    }


def semantic_validation_payload(
    program: Program,
    tv: TemplateVersion,
    fmt_profile: dict[str, Any],
    student_text: str,
    base_model: str,
) -> dict[str, Any]:
    """The /rag/validate request for one document; tools/measure_validation.py sends the same."""
    return {
        "department": program.unit_name,
        "program": program.name,
        "submission_type": tv.template.submission_type,
        "template_version": tv.version,
        "student_text": student_text[:30000],
        "query": SEMANTIC_QUERY,
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
