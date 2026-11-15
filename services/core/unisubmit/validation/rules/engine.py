"""Runs a parsed guideline profile against an inspected PDF (P5.6).

`validate_format_profile` dispatches each rule to its check by rule kind;
`validate_all_profile_rules` adds a row for every rule nothing checked."""

import json
from collections.abc import Callable
from typing import Any

from . import layout, structure, typography
from .cover import cover_layout_results
from .model import Info, ProfileRule, Result
from .results import not_verified, occurrence
from .sections import find_actual_heading
from .values import profile_rules

Check = Callable[[ProfileRule, Info], list[Result]]
# One check per rule kind; a kind with no entry is listed as NOT VERIFIED.
CHECKS: dict[str, Check] = {
    "page_size": layout.check_page_size,
    "body_font": typography.check_body_font,
    "body_font_size_pt": typography.check_body_font_size,
    "margin_bottom_mm": layout.check_margin,
    "margin_left_mm": layout.check_margin,
    "margin_right_mm": layout.check_margin,
    "margin_top_mm": layout.check_margin,
    "line_spacing": layout.check_line_spacing,
    "max_pages": layout.check_page_count,
    "min_pages": layout.check_page_count,
    "required_section": structure.check_required_section,
    "section_max_words": structure.check_section_words,
    "section_min_words": structure.check_section_words,
    "page_numbering_required": layout.check_page_numbering,
    "section_forbidden_element": structure.check_forbidden_element,
    "body_regular_required": typography.check_body_regular,
    "heading_case": typography.check_heading_case,
    "heading_alignment": typography.check_heading_format,
    "heading_bold_required": typography.check_heading_format,
    "heading_font_size_pt": typography.check_heading_format,
    "body_alignment": layout.check_body_alignment,
}


def validate_format_profile(profile: Any, info: Info) -> list[Result]:
    if not profile:
        return []
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError:
            return []
    rules = profile_rules(profile)
    out = []

    for idx, r in enumerate(rules, start=1):
        severity = (
            r.get("final_severity")
            or r.get("severity")
            or ("Mandatory" if bool(r.get("mandatory", True)) else "Warning")
        )
        if severity == "Ignore" or r.get("enabled") is False:
            continue
        kind = r.get("type")
        if not isinstance(kind, str) or (check := CHECKS.get(kind)) is None:
            continue
        rule = ProfileRule(
            raw=r,
            idx=idx,
            severity=severity,
            mandatory=severity == "Mandatory",
            kind=kind,
            expected=r.get("value"),
            source=r.get("source_page"),
            section=r.get("source_section", ""),
            evidence=r.get("evidence", ""),
            target=(r.get("target") or "").strip(),
        )
        out.extend(check(rule, info))

    out.extend(cover_layout_results(profile, info))
    # Advisory rules never block a submission. Represent their mismatches as warnings.
    for item in out:
        if item.get("severity") in {"Warning", "Recommendation"} and item.get("status") == "FAIL":
            item["status"] = "WARNING"
    return out


SUPPORTED_RULE_KINDS = {
    "page_size",
    "body_font",
    "body_font_size_pt",
    "margin_left_mm",
    "margin_right_mm",
    "margin_top_mm",
    "margin_bottom_mm",
    "line_spacing",
    "min_pages",
    "max_pages",
    "required_section",
    "section_max_words",
    "section_min_words",
    "page_numbering_required",
    "section_forbidden_element",
    "body_regular_required",
    "heading_case",
    "heading_alignment",
    "heading_font_size_pt",
    "heading_bold_required",
    "body_alignment",
}


def rule_display_name(rule: dict[str, Any], index: int) -> str:
    kind = (rule.get("type") or "").strip()
    target = (rule.get("target") or "").strip()
    labels = {
        "page_size": "Page size",
        "body_font": "Body font",
        "body_font_size_pt": "Body font size",
        "margin_left_mm": "Left margin",
        "margin_right_mm": "Right margin",
        "margin_top_mm": "Top margin",
        "margin_bottom_mm": "Bottom margin",
        "line_spacing": "Line spacing",
        "min_pages": "Minimum page count",
        "max_pages": "Maximum page count",
        "page_numbering_required": "Page numbering",
        "body_regular_required": "Body text style",
        "body_alignment": "Body text alignment",
    }
    if kind == "required_section":
        return f"Required section: {target}"
    if kind == "section_max_words":
        return f"{target} maximum word count"
    if kind == "section_min_words":
        return f"{target} minimum word count"
    if kind == "section_forbidden_element":
        return f"{target}: prohibited {rule.get('value')}"
    # An untargeted heading rule must still read as a name, not a bare ': ...'.
    heading_labels = {
        "heading_case": "heading case",
        "heading_alignment": "heading alignment",
        "heading_font_size_pt": "heading font size",
        "heading_bold_required": "heading bold",
    }
    if kind in heading_labels:
        label = heading_labels[kind]
        return f"{target}: {label}" if target else label.capitalize()
    return labels.get(kind, kind.replace("_", " ").title() or f"Rule {index}")


def expected_label(rule: dict[str, Any]) -> str:
    kind = rule.get("type")
    value = rule.get("value")
    target = rule.get("target") or ""
    if kind == "page_size":
        return str(value or "")
    if kind == "body_font_size_pt" or kind == "heading_font_size_pt":
        return f"{value} pt"
    if kind in {"margin_left_mm", "margin_right_mm", "margin_top_mm", "margin_bottom_mm"}:
        return f"at least {value} mm"
    if kind == "required_section":
        return "Present"
    if kind == "section_max_words":
        return f"not exceed {value} words"
    if kind == "section_min_words":
        return f"at least {value} words"
    if kind == "page_numbering_required":
        return "Required" if value else "Not required"
    if kind == "body_regular_required":
        return "Regular" if value else str(value)
    if kind == "heading_bold_required":
        return "Bold" if value else "Not bold"
    if kind == "section_forbidden_element":
        return f"No {value}"
    return str(value if value is not None else target)


def validate_all_profile_rules(profile: Any, info: Info) -> list[Result]:
    """Return one result row for every active deterministic guideline rule.

    Existing validation logic emits exceptions only. This wrapper evaluates each
    parsed rule independently, preserving the detailed exception result when one
    exists and creating an explicit PASS row when no exception is produced.
    Unsupported rule types are surfaced as NOT VERIFIED rather than falsely PASSing.
    The cover-page profile is also represented as an explicit rule result.
    """
    if not profile:
        return []
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError:
            return []
    rules = profile_rules(profile)
    rows: list[Result] = []
    for idx, r in enumerate(rules, start=1):
        severity = (
            r.get("final_severity")
            or r.get("severity")
            or ("Mandatory" if bool(r.get("mandatory", True)) else "Warning")
        )
        if severity == "Ignore" or r.get("enabled") is False:
            continue
        kind = (r.get("type") or "").strip()
        name = rule_display_name(r, idx)
        expected = expected_label(r)
        source = str(r.get("source_page") or "")
        section = r.get("source_section", "") or ""
        evidence = (
            r.get("evidence", "") or "Explicit requirement parsed from the approved guideline"
        )
        if kind not in SUPPORTED_RULE_KINDS:
            rows.append(
                {
                    "rule_id": f"GUIDE-UNVERIFIED-{idx}",
                    "name": name,
                    "category": "Formatting Compliance",
                    "severity": severity,
                    "status": "NOT VERIFIED",
                    "expected": expected,
                    "detected": "Not automatically verifiable",
                    "message": "This parsed guideline rule is listed in the report, but the current deterministic engine does not yet have a reliable validator for it.",
                    "correction": "No automatic correction is prescribed. Review this rule manually.",
                    "source_page": source,
                    "source_section": section,
                    "source_text": evidence,
                    "affected_pages": [],
                    "occurrence_count": 0,
                    "occurrences": [],
                }
            )
            continue
        if r.get("value") is None or (isinstance(r.get("value"), str) and not r["value"].strip()):
            # The parser produced no requirement value, so there is nothing to check.
            # Reporting PASS here would wrongly assure the student they comply.
            rows.append(
                not_verified(
                    f"NO_VALUE_{idx}",
                    name,
                    expected or "(not parsed)",
                    source,
                    "The guideline parser extracted no required value for this rule",
                    severity,
                    evidence,
                    section,
                )
            )
            continue
        mini = {"deterministic_rules": [r]}
        issues = validate_format_profile(mini, info)
        if issues:
            row = issues[0]
            row["profile_rule_index"] = idx
            rows.append(row)
            continue
        detected = "Requirement satisfied"
        occ = []
        if kind == "required_section":
            pg, ln = find_actual_heading(info, (r.get("target") or "").strip())
            if pg is not None:
                detected = f"Found on page {pg}"
                occ = [
                    occurrence(
                        pg,
                        "Document heading",
                        "Present",
                        (ln or {}).get("text", ""),
                        (ln or {}).get("bbox"),
                    )
                ]
        elif kind == "page_size":
            detected = "All checked pages use the required size"
        elif kind.startswith("margin_"):
            detected = "No body text intrudes into the required margin"
        elif kind == "body_font":
            vals = []
            for pg in info.get("pages", []):
                v = pg.get("dominant_font")
                if v and v not in vals:
                    vals.append(v)
            detected = ", ".join(vals) if vals else "Compliant"
        elif kind == "body_font_size_pt":
            vals = []
            for pg in info.get("pages", []):
                v = pg.get("dominant_font_size")
                if v is not None and v not in vals:
                    vals.append(v)
            detected = ", ".join(f"{v} pt" for v in vals[:6]) if vals else "Compliant"
        rows.append(
            {
                "rule_id": f"GUIDE-PASS-{idx}",
                "profile_rule_index": idx,
                "name": name,
                "category": "Formatting Compliance",
                "severity": severity,
                "status": "PASS",
                "expected": expected,
                "detected": detected,
                "message": "The submitted PDF satisfies this parsed guideline rule.",
                "correction": "No correction required.",
                "source_page": source,
                "source_section": section,
                "source_text": evidence,
                "affected_pages": sorted({o.get("page") for o in occ if o.get("page")}),
                "occurrence_count": len(occ),
                "occurrences": occ,
            }
        )

    cover = (profile or {}).get("cover_page") or {}
    if cover.get("detected"):
        issues = cover_layout_results(profile, info)
        if issues:
            rows.extend(issues)
        else:
            rows.append(
                {
                    "rule_id": "GUIDE-COVER-PASS",
                    "name": "Front/Cover page structure"
                    if cover.get("geometry_mode") == "structure_only"
                    else "Front/Cover page layout",
                    "category": "Formatting Compliance",
                    "severity": "Mandatory",
                    "status": "PASS",
                    "expected": "Follow the approved cover/title-page template",
                    "detected": "Required cover-page structure/layout satisfied",
                    "message": "The submitted cover page satisfies the approved cover/title-page template check.",
                    "correction": "No correction required.",
                    "source_page": str(cover.get("guideline_page") or ""),
                    "source_section": "Cover/Title Page",
                    "source_text": "Cover/title-page template in the approved guideline",
                    "affected_pages": [1],
                    "occurrence_count": 1,
                    "occurrences": [occurrence(1, "Front/Cover page", "Compliant")],
                }
            )
    return rows
