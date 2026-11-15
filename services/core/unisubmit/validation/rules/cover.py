"""The cover or title page compared with the guideline's sample."""

import re
from typing import Any

from .model import Info, Result
from .results import consolidate, occurrence


def cover_layout_results(profile: dict[str, Any], info: Info) -> list[Result]:
    cover = (profile or {}).get("cover_page") or {}
    if not cover.get("detected") or not cover.get("blocks") or not info.get("pages"):
        return []
    student = info["pages"][0]
    lines = student.get("lines", [])
    if not lines:
        return []

    sample_blocks = cover.get("blocks", [])
    roles = [
        ("Title", r"title of the thesis|thesis title"),
        ("Degree statement", r"partial fulfillment|requirements for the degree"),
        ("Prepared-by block", r"prepared by|submitted by"),
        ("Student ID", r"student id|registration no"),
        ("Supervisor block", r"supervised by|supervisor"),
        ("Department line", r"department of|institute of"),
        ("University line", r"\buniversity\b"),
        (
            "Submission date",
            r"month[, ]+year|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b.*\b20\d{2}\b",
        ),
    ]

    def first_match(
        items: list[dict[str, Any]], pat: str, key: str = "text"
    ) -> dict[str, Any] | None:
        return next((x for x in items if re.search(pat, x.get(key, "") or "", re.I)), None)

    sample = []
    actual = []
    missing = []
    for label, pat in roles:
        sb = first_match(sample_blocks, pat)
        if not sb:
            continue
        ln = first_match(lines, pat)
        sample.append((label, sb))
        if ln:
            actual.append((label, ln))
        else:
            missing.append(occurrence(1, label, "Missing"))

    # If the sample is embedded among guideline instructions, coordinates are not a
    # true cover-page layout. Validate required fields and their order only.
    if cover.get("geometry_mode") == "structure_only":
        occ = list(missing)
        if not occ and len(actual) >= 3:
            order = {label: i for i, (label, _) in enumerate(sample)}
            last = -1
            for label, ln in actual:
                idx = order.get(label, -1)
                if idx < last:
                    occ.append(
                        occurrence(
                            1, label, "Out of prescribed order", ln.get("text", ""), ln.get("bbox")
                        )
                    )
                last = max(last, idx)
        if not occ:
            return []
        return [
            consolidate(
                "COVER_STRUCTURE",
                "Front/Cover page structure",
                "Include the cover elements shown in the approved template",
                cover.get("guideline_page"),
                occ,
                "One or more required cover-page elements are missing or out of the prescribed order.",
                "Add/reorder only the listed cover-page elements to follow the approved template.",
                source_text="Cover/title-page template embedded in the approved guideline",
            )
        ]

    # Standalone visual samples can support relative layout comparison. Use semantic
    # anchors and relative positions, not absolute pixel coordinates.
    common = []
    for label, sb in sample:
        ln = next((x for l, x in actual if l == label), None)
        if ln:
            common.append((label, sb, ln))
    if len(common) < 4:
        if not missing:
            return []
        return [
            consolidate(
                "COVER_STRUCTURE",
                "Front/Cover page structure",
                "Include the cover elements shown in the approved sample",
                cover.get("guideline_page"),
                missing,
                "One or more required cover-page elements are missing.",
                "Add the listed cover-page elements.",
                source_text="Visual cover/title-page sample in the approved guideline",
            )
        ]

    def sample_y(x: dict[str, Any]) -> float:
        return (float(x.get("y0", 0)) + float(x.get("y1", 0))) / 2

    def actual_y(x: dict[str, Any]) -> float:
        b = x.get("bbox", (0, 0, 0, 0))
        return (float(b[1]) + float(b[3])) / 2

    sy = [sample_y(sb) for _, sb, _ in common]
    ay = [actual_y(ln) for _, _, ln in common]
    smin, smax = min(sy), max(sy)
    amin, amax = min(ay), max(ay)
    sspan = max(0.001, smax - smin)
    aspan = max(1.0, amax - amin)
    page_w = float(student.get("width_pt") or 595)
    occ = list(missing)
    for label, sb, ln in common:
        sr = (sample_y(sb) - smin) / sspan
        ar = (actual_y(ln) - amin) / aspan
        bbox = ln.get("bbox", (0, 0, 0, 0))
        vertical_bad = abs(sr - ar) > 0.15
        sx = (float(sb.get("x0", 0)) + float(sb.get("x1", 0))) / 2
        sample_centered = abs(sx - 0.5) <= 0.14
        ax = (float(bbox[0]) + float(bbox[2])) / 2 / page_w
        actual_centered = abs(ax - 0.5) <= 0.14
        alignment_bad = sample_centered != actual_centered
        if vertical_bad or alignment_bad:
            reason = []
            if vertical_bad:
                reason.append(f"relative vertical position {ar:.2f} (expected about {sr:.2f})")
            if alignment_bad:
                reason.append("alignment differs from sample")
            occ.append(occurrence(1, label, "; ".join(reason), ln.get("text", ""), bbox))

    if not occ:
        return []
    return [
        consolidate(
            "COVER_LAYOUT",
            "Front/Cover page layout",
            "Follow the approved cover-page sample",
            cover.get("guideline_page"),
            occ,
            "One or more major cover-page elements differ materially from the approved sample.",
            "Reposition only the listed cover-page elements to match the approved sample.",
            source_text="Visual cover/title-page sample in the approved guideline",
        )
    ]
