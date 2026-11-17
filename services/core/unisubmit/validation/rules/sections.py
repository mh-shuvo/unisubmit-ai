"""Finding sections and headings in the extracted document."""

import re
from typing import Any

from .model import Info
from .values import strip_qualifier


def find_section(text: str, target: str) -> str | None:
    if not target:
        return None
    # Qwen qualifies targets ('Abstract (M.S. thesis)'); the bare heading is what
    # appears in the document, so retry without the parenthetical qualifier.
    candidates = [str(target).strip()]
    stripped = strip_qualifier(target)
    if stripped and stripped.lower() != candidates[0].lower():
        candidates.append(stripped)
    for cand in candidates:
        if not cand:
            continue
        # Capture from heading to next likely heading. Best-effort for searchable PDFs.
        pat = rf"(?is)(?:^|\n)\s*(?:\d+(?:\.\d+)*\s+)?{re.escape(cand)}\s*\n(.*?)(?=\n\s*(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Z\s&:/-]{{3,}}\s*\n|\Z)"
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


HEADING_LEVEL_PATTERNS = {
    "chapter heading": re.compile(r"^chapter\s+\w+", re.I),
    "section heading": re.compile(r"^\d+\.\d+(?!\.\d)\s+\S+"),
    "third level heading": re.compile(r"^\d+\.\d+\.\d+\s+\S+"),
    "heading": re.compile(r"^(?:chapter\s+\w+|\d+(?:\.\d+)*\s+\S+)", re.I),
}


def heading_candidates(info: Info, target: str) -> list[Any]:
    """Lines that plausibly are the heading a ``heading_*`` rule targets.

    Tries the target as written, then without its parenthetical qualifier, so
    'Chapter Heading (implied by context)' still resolves to chapter headings.
    Returns ``[]`` when nothing matches, so the call site can report
    NOT VERIFIED instead of passing a rule that examined no line at all.
    """
    names = [str(target or "").strip()]
    stripped = strip_qualifier(target)
    if stripped and stripped.lower() != names[0].lower():
        names.append(stripped)
    for name in names:
        low = name.lower()
        if not low:
            continue
        pattern = HEADING_LEVEL_PATTERNS.get(low)
        found = []
        for pg in info.get("pages", []):
            # Contents entries repeat the text of every heading in the document, so
            # matching them would hand each level's rule the other level's lines.
            if pattern is not None and is_toc_page(pg):
                continue
            for ln in pg.get("lines", []):
                txt = (ln.get("text") or "").strip()
                if not txt:
                    continue
                if pattern is not None:
                    if pattern.match(txt):
                        found.append((pg, ln))
                elif low in txt.lower():
                    found.append((pg, ln))
        if found:
            return found
    return []


def section_page_span(info: Info, text: str, target: str) -> tuple[int, int] | None:
    """Pages a named section occupies, as ``(first, last)``, or None if unknown.

    Used by page-count rules scoped to a section, such as "the abstract must be
    confined to a single page", which must not be measured against the whole
    document.
    """
    start = find_actual_heading(info, target)[0]
    if start is None:
        return None
    body = find_section(text, target)
    if not body:
        return (start, start)
    # The section ends on the last page still carrying one of its longer lines.
    anchors = [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 40]
    last = start
    for pg in info.get("pages", []):
        if pg.get("page", 0) < start:
            continue
        txt = " ".join(x.get("text", "") for x in pg.get("lines", []))
        if any(a in txt for a in anchors[-40:]):
            last = max(last, pg.get("page", start))
    return (start, last)


def page_for_text(info: Info, needle: str) -> int | None:
    if not needle:
        return None
    n = needle.lower()
    for pg in info.get("pages", []):
        txt = " ".join(x.get("text", "") for x in pg.get("lines", [])).lower()
        if n in txt:
            return int(pg["page"])
    return None


def normalize_heading(text: str | None) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    # Drop parenthetical qualifiers so a parsed target such as
    # 'Abstract (M.S. thesis)' still matches the document's 'ABSTRACT' heading.
    t = strip_qualifier(t) or t
    # Remove common chapter prefixes while preserving the actual semantic heading.
    t = re.sub(r"^chapter\s+(?:[0-9ivxlcdm]+|[a-z]+)\s*[:.\-–—]?\s*", "", t, flags=re.I)
    # Remove numbered section prefixes such as 1, 1.1, 1.1.1.
    t = re.sub(r"^\d+(?:\.\d+)*\s*[:.\-–—]?\s*", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    return t


def is_toc_page(pg: dict[str, Any]) -> bool:
    lines = pg.get("lines", [])
    text = "\n".join(x.get("text", "") for x in lines)
    if re.search(r"(?im)^\s*table\s+of\s+contents\s*$", text):
        return True
    dotted = sum(1 for x in lines if re.search(r"\.{4,}\s*\d+\s*$", x.get("text", "")))
    return dotted >= 3


def find_actual_heading(info: Info, target: str) -> tuple[int | None, dict[str, Any] | None]:
    """Find a real document heading while avoiding false matches from TOC entries.

    Front-matter headings such as ``TABLE OF CONTENTS`` are themselves valid
    headings even though their page is, by definition, a TOC-like page. Main
    chapters must still be found outside the TOC region. Matching tolerates
    chapter-number prefixes, punctuation, capitalization, and multiple spaces.
    """
    wanted = normalize_heading(target)
    if not wanted:
        return None, None

    toc_page_headings = {
        "table of contents",
        "list of figures",
        "list of tables",
        "list of abbreviations",
        "list of symbols",
    }

    for pg in info.get("pages", []):
        is_toc = is_toc_page(pg)
        for ln in pg.get("lines", []):
            txt = (ln.get("text") or "").strip()
            if not txt or len(txt) > 180:
                continue
            norm = normalize_heading(txt)

            # A front-matter index heading is valid on its own index page.
            if wanted in toc_page_headings and norm == wanted:
                return pg.get("page"), ln

            # For all other required sections, do not count TOC/index entries as
            # proof that the real section exists later in the document.
            if is_toc:
                continue

            if norm == wanted:
                return pg.get("page"), ln
            if wanted and norm.startswith(wanted + " ") and len(norm) - len(wanted) <= 18:
                return pg.get("page"), ln
    return None, None
