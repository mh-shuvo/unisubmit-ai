"""Building result rows: occurrences, consolidated failures, NOT VERIFIED."""

from .model import Result


def not_verified(
    code: str,
    name: str,
    expected: object,
    source_page: object,
    reason: str,
    severity: str = "Mandatory",
    source_text: str = "",
    source_section: str = "",
) -> Result:
    """Row for a rule the engine cannot decide, so it is never a silent PASS."""
    return {
        "rule_id": f"GUIDE-{code}",
        "name": name,
        "category": "Formatting Compliance",
        "severity": severity,
        "status": "NOT VERIFIED",
        "expected": expected,
        "detected": reason,
        "message": f"This guideline rule could not be checked automatically: {reason}",
        "correction": "Review this rule manually against the submitted document.",
        "source_page": str(source_page or ""),
        "source_section": source_section or "",
        "source_text": source_text or "Explicit requirement parsed from the approved guideline",
        "affected_pages": [],
        "occurrence_count": 0,
        "occurrences": [],
    }


def occurrence(
    page: object,
    location: str,
    detected: object,
    snippet: str = "",
    bbox: object = None,
) -> Result:
    return {
        "page": page,
        "location": location,
        "detected": detected,
        "snippet": (snippet or "")[:220],
        "bbox": bbox,
    }


def consolidate(
    code: str,
    name: str,
    expected: object,
    source_page: object,
    occurrences: list[Result],
    message: str,
    correction: str,
    status: str = "FAIL",
    severity: str = "Mandatory",
    source_text: str = "",
    source_section: str = "",
) -> Result:
    pages = sorted({o["page"] for o in occurrences if o.get("page")})
    detected_values = []
    for o in occurrences:
        d = str(o.get("detected", ""))
        if d and d not in detected_values:
            detected_values.append(d)
    return {
        "rule_id": f"GUIDE-{code}",
        "name": name,
        "category": "Formatting Compliance",
        "severity": severity,
        "status": status,
        "expected": expected,
        "detected": ", ".join(detected_values)[:260],
        "message": message,
        "correction": correction,
        "source_page": str(source_page or ""),
        "source_section": source_section or "",
        "source_text": source_text or "Explicit requirement parsed from the approved guideline",
        "affected_pages": pages,
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
    }
