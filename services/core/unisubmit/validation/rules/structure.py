"""Required sections, section lengths and forbidden elements."""

import re

from .model import Info, ProfileRule, Result
from .results import consolidate, not_verified, occurrence
from .sections import find_actual_heading, find_section, page_for_text
from .values import parse_num


def check_required_section(rule: ProfileRule, info: Info) -> list[Result]:
    """`required_section`."""
    idx = rule.idx
    severity = rule.severity
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    occ: list[Result] = []
    out: list[Result] = []
    if not (target):
        return out
    page_no, heading_line = find_actual_heading(info, target)
    if page_no is None:
        occ = [occurrence(None, "Document structure", "Not found")]
        out.append(
            consolidate(
                f"REQUIRED_SECTION_{idx}",
                f"Required section: {target}",
                "Present",
                source,
                occ,
                f'The required section "{target}" was not found as an actual document heading.',
                "Add the required section using the guideline-prescribed heading.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_section_words(rule: ProfileRule, info: Info) -> list[Result]:
    """`section_max_words`, `section_min_words`."""
    idx = rule.idx
    severity = rule.severity
    kind = rule.kind
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    text = info.get("text", "")
    occ: list[Result] = []
    out: list[Result] = []
    if not (target and expected is not None):
        return out
    exp = parse_num(expected)
    if exp is None:
        out.append(
            not_verified(
                f"SECTION_WORDS_{idx}",
                f"{target} word count",
                str(expected),
                source,
                f'Could not interpret the required word count "{expected}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    sec = find_section(text, target)
    limit = int(exp)
    if sec is None:
        # A missing section is reported by its own required_section rule. Not
        # finding it here is no evidence that the word limit was breached.
        out.append(
            not_verified(
                f"SECTION_WORDS_{idx}",
                f"{target} word count",
                str(limit),
                source,
                f'Could not locate the section "{target}" to measure its word count',
                severity,
                evidence,
                section,
            )
        )
    else:
        count = len(re.findall(r"\b\w+[\w'-]*\b", sec))
        ok = count <= limit if kind == "section_max_words" else count >= limit
        if not ok:
            pg = page_for_text(info, target)
            occ = [occurrence(pg, f"Section: {target}", f"{count} words")]
            direction = "not exceed" if kind == "section_max_words" else "contain at least"
            out.append(
                consolidate(
                    f"SECTION_WORDS_{idx}",
                    f"{target} word count",
                    f"{direction} {limit} words",
                    source,
                    occ,
                    f"The {target} section contains {count} words.",
                    "Revise the section to satisfy the required word limit.",
                    severity=severity,
                    source_text=evidence,
                    source_section=section,
                )
            )
    return out


def check_forbidden_element(rule: ProfileRule, info: Info) -> list[Result]:
    """`section_forbidden_element`."""
    idx = rule.idx
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    text = info.get("text", "")
    occ: list[Result] = []
    out: list[Result] = []
    if not (target and expected):
        return out
    sec = find_section(text, target)
    if sec is not None:
        elem = str(expected).lower()
        patterns = {
            "figures": r"(?im)^\s*(?:figure|fig\.)\s*\d+",
            "tables": r"(?im)^\s*table\s*\d+",
            "citations": r"\[[0-9,; \-]+\]|\([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?,?\s+\d{4}\)",
        }
        pat = patterns.get(elem)
        if pat and re.search(pat, sec):
            pg = page_for_text(info, target)
            occ = [occurrence(pg, f"Section: {target}", f"{elem} detected")]
            out.append(
                consolidate(
                    f"FORBIDDEN_{idx}",
                    f"{target}: prohibited {elem}",
                    f"No {elem}",
                    source,
                    occ,
                    f"The guideline prohibits {elem} in the {target} section.",
                    "Remove the prohibited element(s) from the section.",
                    severity=severity,
                    source_text=evidence,
                    source_section=section,
                )
            )
    return out
