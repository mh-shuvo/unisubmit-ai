"""Parsed guideline profiles: merging duplicate semantic rules and normalising severities.

Pure functions on the profile document; no database, no I/O. The AI service
keeps its own copy of the merge until both import one shared package (P6.1).
"""

import re
import uuid
from typing import Any

Profile = dict[str, Any]
Rule = dict[str, Any]
SEVERITIES = {"Mandatory", "Warning", "Recommendation", "Ignore"}


def semantic_norm(text: object) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Normalize soft modal wording so the same intent is not duplicated.
    s = re.sub(r"\b(should|shall|must|required to|needs to|need to)\b", "must", s)
    return re.sub(r"\s+", " ", s).strip()


def _pages(rule: Rule) -> set[int]:
    """Every page a rule cites. The model may give `source_pages` as a list, a
    single number or a string; a string is one page, not one per digit (DEF-044)."""
    cited = rule.get("source_pages")
    values = list(cited) if isinstance(cited, (list, tuple, set)) else [cited]
    values.append(rule.get("source_page"))
    pages = set()
    for value in values:
        if value is None:
            continue
        try:
            pages.add(int(value))
        except (TypeError, ValueError):
            continue
    return pages


def dedupe_semantic_requirements(profile: Any) -> Any:
    """Merge duplicate semantic requirements while preserving every guideline source.

    The canonical rule is keyed by normalized requirement + target/source section.
    This intentionally merges page-boundary duplicates such as the same Abstract
    requirement found on pages 1 and 2. Manual rules are also deduplicated only
    when their normalized requirement and target section are identical.
    """
    if not isinstance(profile, dict):
        return profile
    merged: list[Rule] = []
    by_key: dict[tuple[str, str], Rule] = {}
    for raw in profile.get("semantic_requirements") or []:
        if not isinstance(raw, dict) or not str(raw.get("requirement") or "").strip():
            continue
        rule = dict(raw)
        section = str(rule.get("target_section") or rule.get("source_section") or "").strip()
        key = (semantic_norm(rule["requirement"]), semantic_norm(section))
        base = by_key.get(key)
        if base is None:
            rule["source_pages"] = sorted(_pages(rule))
            if rule["source_pages"] and not rule.get("source_page"):
                rule["source_page"] = rule["source_pages"][0]
            rule["target_section"] = section
            rule.setdefault(
                "source_type", "Manual Semantic Rule" if rule.get("manual") else "Guideline-derived"
            )
            rule.setdefault(
                "semantic_id",
                str(rule.get("rule_id") or ("SEM-" + uuid.uuid4().hex[:10].upper())),
            )
            by_key[key] = rule
            merged.append(rule)
            continue
        base["source_pages"] = sorted(set(base.get("source_pages") or []) | _pages(rule))
        if base["source_pages"]:
            base["source_page"] = base["source_pages"][0]
        # Preserve evidence from both pages without repeating identical text.
        evidence: list[str] = []
        for text in (
            str(base.get("evidence") or "").strip(),
            str(rule.get("evidence") or "").strip(),
        ):
            if text and text not in evidence:
                evidence.append(text)
        if evidence:
            base["evidence"] = " | ".join(evidence)[:1600]
        base["merged_duplicate_count"] = int(base.get("merged_duplicate_count") or 0) + 1
    profile["semantic_requirements"] = merged
    return profile


def normalize_profile_severities(profile: Any) -> Any:
    """Add reviewable severity metadata without inventing new academic rules."""
    if not isinstance(profile, dict):
        return profile
    profile = dedupe_semantic_requirements(profile)
    for key in ("deterministic_rules", "semantic_requirements"):
        for rule in profile.get(key, []) or []:
            suggested = rule.get("suggested_severity")
            if suggested not in SEVERITIES:
                suggested = "Mandatory" if bool(rule.get("mandatory", True)) else "Warning"
            final = rule.get("final_severity")
            if final not in SEVERITIES:
                final = suggested
            rule["suggested_severity"] = suggested
            rule["final_severity"] = final
            rule["mandatory"] = final == "Mandatory"
            rule["enabled"] = final != "Ignore"
    profile.setdefault("review_audit", [])
    return profile
