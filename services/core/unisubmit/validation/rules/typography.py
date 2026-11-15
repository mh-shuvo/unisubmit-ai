"""Fonts, sizes, bold and heading case."""

import re
from typing import Any

from .layout import heading_alignment, normalize_alignment
from .model import Info, ProfileRule, Result
from .results import consolidate, not_verified, occurrence
from .sections import heading_candidates
from .values import num_ok, parse_num_op


def norm_font(x: str | None) -> str:
    n = re.sub(r"[^a-z0-9]", "", (x or "").lower())
    aliases = {
        "timesnewroman": "timesnewroman",
        "timesroman": "timesnewroman",
        "arial": "arial",
        "calibri": "calibri",
        "cambria": "cambria",
    }
    for k, v in aliases.items():
        if k in n:
            return v
    return n


def same_font(a: str | None, b: str | None) -> bool:
    aa, bb = norm_font(a), norm_font(b)
    return bool(aa and bb and (aa == bb or aa in bb or bb in aa))


def span_is_bold(span: dict[str, Any]) -> bool:
    """True when a span is bold.

    ``pdf_utils`` normalises font names to the family, so the weight no longer
    survives in the name. Prefer the flag it now records and fall back to the
    name for span dictionaries built elsewhere.
    """
    flag = span.get("bold")
    if flag is not None:
        return bool(flag)
    return "bold" in str(span.get("font", "")).lower()


def span_is_italic(span: dict[str, Any]) -> bool:
    """True when a span is italic. See :func:`span_is_bold`."""
    flag = span.get("italic")
    if flag is not None:
        return bool(flag)
    return "italic" in str(span.get("font", "")).lower()


SMALL_WORDS = {
    # articles
    "a",
    "an",
    "the",
    # coordinating conjunctions
    "and",
    "but",
    "or",
    "nor",
    "for",
    "so",
    "yet",
    # short prepositions (≤4 letters)
    "as",
    "at",
    "by",
    "in",
    "of",
    "off",
    "on",
    "per",
    "to",
    "up",
    "via",
    "from",
    "into",
    "like",
    "near",
    "over",
    "past",
    "than",
    "upon",
    "with",
}


def to_title_case(s: str) -> str:
    words = s.split(" ")
    out: list[str] = []
    n = len(words)
    for i, w in enumerate(words):
        if not w:
            out.append(w)
            continue
        if w.isupper():
            out.append(w)
            continue
        lower = w.lower()
        # first or last word always capitalized
        if i == 0 or i == n - 1:
            out.append(w[:1].upper() + w[1:].lower())
        elif lower in SMALL_WORDS:
            out.append(lower)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def needs_title_case_fix(s: str) -> bool:
    return to_title_case(s) != s


def check_body_font(rule: ProfileRule, info: Info) -> list[Result]:
    """`body_font`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected):
        return out
    for pg in info.get("pages", []):
        det = pg.get("dominant_font")
        if det and not same_font(det, str(expected)):
            sample = next(
                (
                    s
                    for s in pg.get("spans", [])
                    if same_font(s.get("font"), det) and len(s.get("text", "")) > 15
                ),
                None,
            )
            occ.append(
                occurrence(
                    pg["page"],
                    "Dominant body text",
                    det,
                    (sample or {}).get("text", ""),
                    (sample or {}).get("bbox"),
                )
            )
    if occ:
        out.append(
            consolidate(
                "BODY_FONT",
                "Body font",
                str(expected),
                source,
                occ,
                "The dominant body font differs from the guideline requirement.",
                "Change the affected body text to the required font.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_body_font_size(rule: ProfileRule, info: Info) -> list[Result]:
    """`body_font_size_pt`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected is not None):
        return out
    exp, op = parse_num_op(expected)
    if exp is None:
        out.append(
            not_verified(
                "FONT_SIZE",
                "Body font size",
                str(expected),
                source,
                f'Could not interpret the required size "{expected}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    label = {">=": f"at least {exp:g} pt", "<=": f"at most {exp:g} pt"}.get(op, f"{exp:g} pt")
    for pg in info.get("pages", []):
        det = pg.get("dominant_font_size")
        if det is not None and not num_ok(float(det), exp, op, 0.6):
            sample = next(
                (
                    s
                    for s in pg.get("spans", [])
                    if abs(float(s.get("size", 0)) - float(det)) < 0.2
                    and len(s.get("text", "")) > 15
                ),
                None,
            )
            occ.append(
                occurrence(
                    pg["page"],
                    "Dominant body text",
                    f"{det} pt",
                    (sample or {}).get("text", ""),
                    (sample or {}).get("bbox"),
                )
            )
    if occ:
        out.append(
            consolidate(
                "FONT_SIZE",
                "Body font size",
                label,
                source,
                occ,
                "The dominant body-text size differs from the guideline requirement.",
                "Set the affected body text to the required size.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_body_regular(rule: ProfileRule, info: Info) -> list[Result]:
    """`body_regular_required`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    occ: list[Result] = []
    out: list[Result] = []
    if not (bool(expected)):
        return out
    for pg in info.get("pages", []):
        bad = [
            sp
            for sp in pg.get("spans", [])
            if len(sp.get("text", "")) > 20 and (span_is_bold(sp) or span_is_italic(sp))
        ]
        # Only fail when styled long spans dominate enough to look like body text, not headings/emphasis.
        if len(bad) >= 3:
            for sp in bad[:8]:
                occ.append(
                    occurrence(
                        pg["page"],
                        "Body text style",
                        sp.get("font", ""),
                        sp.get("text", ""),
                        sp.get("bbox"),
                    )
                )
    if occ:
        out.append(
            consolidate(
                "BODY_REGULAR",
                "Body text style",
                "Regular",
                source,
                occ,
                "Substantial body-text spans appear bold/italic where regular text is required.",
                "Change the affected body paragraphs to regular style.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_heading_case(rule: ProfileRule, info: Info) -> list[Result]:
    """`heading_case`."""
    idx = rule.idx
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    occ: list[Result] = []
    out: list[Result] = []
    if not (target and expected):
        return out
    candidates = heading_candidates(info, target)
    if not candidates:
        # No line matched the target, so no heading was inspected. PASS here
        # would assure the student of a check that never ran.
        out.append(
            not_verified(
                f"HEADING_CASE_{idx}",
                f"{target}: Heading Case",
                str(expected),
                source,
                f'No document heading matched the target "{target}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    for pg, ln in candidates:
        txt = (ln.get("text") or "").strip()
        letters = "".join(ch for ch in txt if ch.isalpha())
        if (
            (
                str(expected).lower() == "all caps"
                or str(expected).lower() == "upper case"
                or str(expected).lower() == "uppercase"
            )
            and letters
            and letters != letters.upper()
        ):
            occ.append(occurrence(pg["page"], txt, f"Not {expected}", txt, ln.get("bbox")))
        elif str(expected).lower() == "title case" and txt and needs_title_case_fix(txt):
            occ.append(occurrence(pg["page"], txt, "Not Title Case", txt, ln.get("bbox")))
    if occ:
        out.append(
            consolidate(
                f"HEADING_CASE_{idx}",
                f"{target}: Heading Case",
                str(expected),
                source,
                occ,
                "One or more headings do not match the required capitalization.",
                "Correct the capitalization of the listed headings.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_heading_format(rule: ProfileRule, info: Info) -> list[Result]:
    """`heading_alignment`, `heading_bold_required`, `heading_font_size_pt`."""
    idx = rule.idx
    severity = rule.severity
    kind = rule.kind
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    occ: list[Result] = []
    out: list[Result] = []
    if not (target):
        return out
    candidates = heading_candidates(info, target)
    if not candidates:
        out.append(
            not_verified(
                f"HEADING_{kind}_{idx}",
                f"{target}: {kind.replace('_', ' ').title()}",
                str(expected),
                source,
                f'No document heading matched the target "{target}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    for pg, ln in candidates:
        txt = (ln.get("text") or "").strip()
        bbox = ln.get("bbox", (0, 0, 0, 0))
        if kind == "heading_alignment":
            det = heading_alignment(pg, info, bbox)
            exp_align = normalize_alignment(expected)
            if exp_align and det and det != exp_align:
                occ.append(occurrence(pg["page"], txt, det, txt, bbox))
        elif kind == "heading_font_size_pt":
            size = float(ln.get("font_size") or 0)
            exp, op = parse_num_op(expected)
            if exp is not None and size and not num_ok(size, exp, op, 0.7):
                occ.append(occurrence(pg["page"], txt, f"{size:g} pt", txt, bbox))
        elif kind == "heading_bold_required" and bool(expected):
            # line metadata lacks font flags, infer from spans intersecting bbox
            bold = any(
                span_is_bold(sp) and abs(sp.get("bbox", (0, 0, 0, 0))[1] - bbox[1]) < 3
                for sp in pg.get("spans", [])
            )
            if not bold:
                occ.append(occurrence(pg["page"], txt, "Not bold", txt, bbox))
    if occ:
        exp = expected
        out.append(
            consolidate(
                f"HEADING_{kind}_{idx}",
                f"{target}: {kind.replace('_', ' ').title()}",
                str(exp),
                source,
                occ,
                "One or more headings do not match the explicit guideline style.",
                "Correct the listed headings to match the guideline.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out
