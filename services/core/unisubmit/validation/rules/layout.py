"""Paper size, margins, line spacing, page counts, numbering and alignment."""

import re
from typing import Any

from .model import Info, ProfileRule, Result
from .results import consolidate, not_verified, occurrence
from .sections import section_page_span
from .values import is_generic_target, parse_num


def normalize_alignment(value: object) -> str | None:
    """Map a guideline's alignment wording to 'center', 'left' or 'right'.

    Lenient substring match, not an exact lookup, so phrasings like
    'flush left', 'left-aligned' or 'center-aligned' all resolve the same
    as the bare word. Returns None for wording this can't classify, so the
    caller skips the check rather than guessing which side was meant.
    """
    v = str(value or "").strip().lower()
    if not v:
        return None
    if "center" in v or "centre" in v:
        return "center"
    if "left" in v:
        return "left"
    if "right" in v:
        return "right"
    return None


PT_TO_MM = 25.4 / 72.0


def document_margins_mm(info: Info) -> tuple[float | None, float | None]:
    """The first usable (left_mm, right_mm) pair found across the document.

    A heading-only page often has no body-text evidence of its own, so its
    own ``margins`` dict can be empty. Margins are consistent throughout a
    thesis, so borrowing another page's measurement is a safe fallback.
    Returns (None, None) if no page has margin evidence at all.
    """
    for pg in info.get("pages", []):
        m = pg.get("margins") or {}
        left, right = m.get("left_mm"), m.get("right_mm")
        if left is not None and right is not None:
            return left, right
    return None, None


def heading_alignment(pg: dict[str, Any], info: Info, bbox: Any) -> str | None:
    """Classify a heading line as 'center', 'left', 'right' or None (unclear).

    Checks the margin anchors before the page-center tolerance, not after:
    the center tolerance is deliberately wide (headings of different widths
    must all read as centered), and on a page with asymmetric margins a
    long flush-left heading's midpoint can drift inside that wide center
    band even though its left edge sits exactly on the left margin. A tight
    margin match is the more specific signal, so it must win first.
    """
    page_w = pg.get("width_pt") or 595
    m = pg.get("margins") or {}
    left_mm, right_mm = m.get("left_mm"), m.get("right_mm")
    if left_mm is None or right_mm is None:
        left_mm, right_mm = document_margins_mm(info)
    tol = 10.0
    if left_mm is not None and right_mm is not None:
        left_margin_pt = left_mm / PT_TO_MM
        right_margin_pt = page_w - right_mm / PT_TO_MM
        if abs(bbox[0] - left_margin_pt) < tol:
            return "left"
        if abs(bbox[2] - right_margin_pt) < tol:
            return "right"
        mid = (bbox[0] + bbox[2]) / 2
        return "center" if abs(mid - page_w / 2) < page_w * 0.08 else None
    # No margin evidence anywhere in the document: fall back to a best-effort
    # page-edge comparison rather than refusing to classify at all.
    mid = (bbox[0] + bbox[2]) / 2
    if abs(mid - page_w / 2) < page_w * 0.08:
        return "center"
    return "left" if bbox[0] <= page_w - bbox[2] else "right"


CAPTION_LINE_RE = re.compile(r"^(?:figure|table)\s+\d", re.I)


NUMBERED_HEADING_LINE_RE = re.compile(r"^(?:chapter\s+\w+|\d+(?:\.\d+)+)\b", re.I)


def is_body_text_line(text: str, font_size: float) -> bool:
    """True for an ordinary paragraph line, not a heading, caption or TOC entry.

    Mirrors the body-line filter ``pdf_utils.inspect_pdf`` uses for its own
    margin/line-spacing evidence (long, body-sized, not all-caps/caption/
    numbered-heading); duplicated here because that filtered list isn't
    part of the ``info`` structure handed to this module.
    """
    stripped = (text or "").strip()
    if len(stripped) < 35:
        return False
    if not (9.0 <= (font_size or 0) <= 14.5):
        return False
    letters = "".join(ch for ch in stripped if ch.isalpha())
    if letters and letters == letters.upper():
        return False
    if "...." in stripped:
        return False
    if CAPTION_LINE_RE.match(stripped):
        return False
    if NUMBERED_HEADING_LINE_RE.match(stripped):
        return False
    return True


def page_body_alignment(pg: dict[str, Any]) -> str | None:
    """Classify one page's body paragraphs as 'justified', 'left', 'right',
    or None when there isn't enough body text (or no edge is consistent
    enough to call) on the page.

    Checking only the right edge cannot tell justified from right-aligned:
    both have (almost) every line's right edge flush against the same x,
    the difference is whether the *left* edge is also consistent or ragged.
    So this checks both edges against their own percentile-based reference
    (90th for the right margin, 10th for the left margin -- the same trick
    ``pdf_utils`` uses for its own margin measurement) rather than trying to
    segment paragraphs to drop each one's short last line: one short line
    per paragraph barely moves either percentile or either tight-fraction.
    Centered body text is not handled -- essentially unheard of for thesis
    paragraphs, and would need a different signal (line midpoints, not
    edges) to detect.
    """
    lines = [
        ln for ln in pg.get("lines", []) if is_body_text_line(ln.get("text"), ln.get("font_size"))
    ]
    if len(lines) < 4:
        return None
    lefts = sorted(ln["bbox"][0] for ln in lines)
    rights = sorted(ln["bbox"][2] for ln in lines)
    left_margin = lefts[int(0.1 * (len(lefts) - 1))]
    right_margin = rights[int(0.9 * (len(rights) - 1))]
    left_tight = sum(1 for x in lefts if x - left_margin <= 6.0) / len(lefts)
    right_tight = sum(1 for x in rights if right_margin - x <= 6.0) / len(rights)
    if right_tight >= 0.6 and left_tight >= 0.6:
        return "justified"
    if right_tight >= 0.6:
        return "right"
    if left_tight >= 0.6:
        return "left"
    return None


def check_page_size(rule: ProfileRule, info: Info) -> list[Result]:
    """`page_size`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected):
        return out
    exp = str(expected).upper()
    if exp == "A4":
        for pg in info.get("pages", []):
            w, h = pg["width_mm"], pg["height_mm"]
            portrait = abs(w - 210) <= 3 and abs(h - 297) <= 3
            landscape = abs(h - 210) <= 3 and abs(w - 297) <= 3
            if not (portrait or landscape):
                occ.append(occurrence(pg["page"], "Whole page", f"{w} x {h} mm"))
        if occ:
            out.append(
                consolidate(
                    "PAGE_SIZE",
                    "Page size",
                    "A4 (210 x 297 mm)",
                    source,
                    occ,
                    "One or more pages do not use the required paper size.",
                    "Set all listed pages to A4.",
                    severity=severity,
                    source_text=evidence,
                    source_section=section,
                )
            )
    return out


def check_margin(rule: ProfileRule, info: Info) -> list[Result]:
    """`margin_bottom_mm`, `margin_left_mm`, `margin_right_mm`, `margin_top_mm`."""
    severity = rule.severity
    kind = rule.kind
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected is not None):
        return out
    side = kind.replace("margin_", "").replace("_mm", "")
    exp = parse_num(expected)
    key = f"{side}_mm"
    if exp is None:
        out.append(
            not_verified(
                kind.upper(),
                f"{side.title()} margin",
                str(expected),
                source,
                f'Could not interpret the required margin "{expected}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    if not is_generic_target(target):
        # The page-wide margin measurement does not describe a named sub-area
        # such as a chapter-heading block, so do not fail the whole document.
        out.append(
            not_verified(
                kind.upper(),
                f"{side.title()} margin ({target})",
                f"at least {exp:g} mm",
                source,
                f'Rule applies to "{target}" only, which the page-level margin measurement cannot isolate',
                severity,
                evidence,
                section,
            )
        )
        return out
    # PDF does not preserve Word margin settings. Validate that ordinary body
    # text does not intrude into the required margin. Extra whitespace on a
    # sparse page is never a failure, especially for bottom/right margins.
    tolerance_mm = max(2.5, exp * 0.08)
    evaluated = []
    for pg in info.get("pages", []):
        m = pg.get("margins") or {}
        det = m.get(key)
        if det is None or int(m.get("evidence_lines") or 0) < 1:
            continue
        evaluated.append((pg, float(det)))
        # A margin fails only when content is closer to the edge than allowed.
        if float(det) < exp - tolerance_mm:
            occ.append(occurrence(pg["page"], f"{side.title()} body-text boundary", f"{det:g} mm"))
    if occ:
        out.append(
            consolidate(
                kind.upper(),
                f"{side.title()} margin",
                f"at least {exp:g} mm",
                source,
                occ,
                f"Body text intrudes into the required {side} margin on one or more pages.",
                f"Move the affected body content inward so the {side} margin is at least {exp:g} mm.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_line_spacing(rule: ProfileRule, info: Info) -> list[Result]:
    """`line_spacing`."""
    severity = rule.severity
    mandatory = rule.mandatory
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    target = rule.target
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected is not None):
        return out
    exp = parse_num(expected)
    low = []
    if exp is None:
        out.append(
            not_verified(
                "LINE_SPACING",
                "Line spacing",
                str(expected),
                source,
                f'Could not interpret the required spacing "{expected}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    if not is_generic_target(target):
        # Per-page spacing is a whole-document measurement; a section-scoped
        # spacing rule cannot be decided from it.
        out.append(
            not_verified(
                "LINE_SPACING",
                f"Line spacing ({target})",
                f"{exp:g}",
                source,
                f'Rule applies to "{target}" only, which the per-page spacing measurement cannot isolate',
                severity,
                evidence,
                section,
            )
        )
        return out
    # A cover page, a blank leaf or an index carries no running prose, so it
    # has no line spacing to measure. That is not evidence of a breach, and
    # warning on it would make every compliant thesis carry a warning. Only
    # report doubt when no page in the document could be measured at all.
    measured = 0
    for pg in info.get("pages", []):
        det = pg.get("estimated_line_spacing")
        if det is None:
            low.append(occurrence(pg["page"], "Body paragraphs", "No measurable body paragraph"))
            continue
        measured += 1
        if abs(float(det) - exp) > 0.28:
            occ.append(occurrence(pg["page"], "Estimated body line spacing", f"{det}"))
    if occ:
        out.append(
            consolidate(
                "LINE_SPACING",
                "Line spacing",
                f"{exp:g}",
                source,
                occ,
                "Estimated line spacing differs from the guideline requirement.",
                "Adjust the listed paragraphs/pages to the required line spacing.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    elif not measured and low and mandatory:
        out.append(
            consolidate(
                "LINE_SPACING_VERIFY",
                "Line spacing",
                f"{exp:g}",
                source,
                low,
                "Line spacing could not be measured on any page of the document.",
                "Review the listed pages manually.",
                status="WARNING",
                severity="Mandatory",
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_page_count(rule: ProfileRule, info: Info) -> list[Result]:
    """`max_pages`, `min_pages`."""
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
    if not (expected is not None):
        return out
    exp = parse_num(expected)
    name = "Minimum page count" if kind == "min_pages" else "Maximum page count"
    if exp is None:
        out.append(
            not_verified(
                kind.upper(),
                name,
                str(expected),
                source,
                f'Could not interpret the required page count "{expected}"',
                severity,
                evidence,
                section,
            )
        )
        return out
    val = int(exp)
    # A page limit may be scoped to one section ("the abstract must be
    # confined to a single page"); measuring it against the whole document
    # would fail every submission.
    if is_generic_target(target):
        scope = "Whole document"
        actual = int(info.get("page_count", 0))
    else:
        name = f"{name} ({target})"
        scope = f"Section: {target}"
        span = section_page_span(info, text, target)
        if span is None:
            out.append(
                not_verified(
                    kind.upper(),
                    name,
                    str(val),
                    source,
                    f'Could not locate the section "{target}" to measure its length',
                    severity,
                    evidence,
                    section,
                )
            )
            return out
        actual = span[1] - span[0] + 1
    ok = actual >= val if kind == "min_pages" else actual <= val
    if not ok:
        occ = [occurrence(None, scope, f"{actual} pages")]
        msg = f"{scope} spans {actual} pages, which does not satisfy the guideline limit."
        corr = f"Adjust {scope.lower()} so it satisfies the required {'minimum' if kind == 'min_pages' else 'maximum'} of {val} pages."
        out.append(
            consolidate(
                kind.upper(),
                name,
                str(val),
                source,
                occ,
                msg,
                corr,
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_page_numbering(rule: ProfileRule, info: Info) -> list[Result]:
    """`page_numbering_required`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    out: list[Result] = []
    if not (bool(expected)):
        return out
    missing = []
    for pg in info.get("pages", []):
        # Search footer/header edge lines for a standalone arabic/roman page number.
        lines = pg.get("lines", [])
        h = pg.get("height_pt") or 842
        found = False
        for ln in lines:
            t = ln.get("text", "").strip()
            y0 = ln.get("bbox", (0, 0, 0, 0))[1]
            if (y0 < h * 0.08 or y0 > h * 0.90) and re.fullmatch(r"(?:\d+|[ivxlcdm]+)", t, re.I):
                found = True
                break
        if not found:
            missing.append(
                occurrence(pg["page"], "Header/footer page-number area", "No page number detected")
            )
    if missing:
        out.append(
            consolidate(
                "PAGE_NUMBERING",
                "Page numbering",
                "Required",
                source,
                missing,
                "Page numbering was not detected on one or more pages.",
                "Add page numbers according to the guideline.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out


def check_body_alignment(rule: ProfileRule, info: Info) -> list[Result]:
    """`body_alignment`."""
    severity = rule.severity
    expected = rule.expected
    source = rule.source
    section = rule.section
    evidence = rule.evidence
    occ: list[Result] = []
    out: list[Result] = []
    if not (expected):
        return out
    exp_low = str(expected).strip().lower()
    if "justif" in exp_low:
        exp_align = "justified"
    elif exp_low in {"left", "flush left", "left-aligned", "left aligned"}:
        exp_align = "left"
    elif exp_low in {"right", "flush right", "right-aligned", "right aligned"}:
        exp_align = "right"
    else:
        out.append(
            not_verified(
                "BODY_ALIGNMENT",
                "Body text alignment",
                str(expected),
                source,
                f'Alignment "{expected}" is not one this checker can classify (only justified, left or right are supported)',
                severity,
                evidence,
                section,
            )
        )
        return out
    measured = [(pg["page"], page_body_alignment(pg)) for pg in info.get("pages", [])]
    measured = [(pg_num, det) for pg_num, det in measured if det is not None]
    if not measured:
        out.append(
            not_verified(
                "BODY_ALIGNMENT",
                "Body text alignment",
                str(expected),
                source,
                "No page had enough ordinary body-text lines to measure alignment",
                severity,
                evidence,
                section,
            )
        )
        return out
    for pg_num, det in measured:
        if det != exp_align:
            occ.append(occurrence(pg_num, "Body text alignment", det))
    if occ:
        out.append(
            consolidate(
                "BODY_ALIGNMENT",
                "Body text alignment",
                str(expected),
                source,
                occ,
                "Body paragraphs do not match the required text alignment.",
                "Set the affected paragraphs to the required alignment.",
                severity=severity,
                source_text=evidence,
                source_section=section,
            )
        )
    return out
