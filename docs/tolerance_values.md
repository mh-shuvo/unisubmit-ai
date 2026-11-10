# Tolerance values

Every fuzzy-match threshold, percentile cutoff, and measurement tolerance used
by the deterministic format-validation engine, in one place, with the file,
the line, and the reasoning behind the chosen number. Budget/timeout values
from the semantic (LLM) layer are listed separately at the end for
completeness, since they are configuration ceilings rather than measurement
tolerances.

Written for a new contributor who needs to know *why* a number is what it is
before changing it, not just where it lives.

---

## Measurement layer: `app/pdf_utils.py`

This module turns a PDF into the `info` structure the rule checker consumes.
Its tolerances decide what counts as "the same line", "a body paragraph", or
"the margin", before any guideline rule is even evaluated.

| Value | Location | What it gates | Why this number |
|---|---|---|---|
| `0.07` / `0.93` (fraction of page height) | `inspect_pdf`, line 105 | Excludes the top/bottom 7% of the page from the generic content-area diagnostic | Headers and footers (page numbers, running titles) live in that band; keeping them out stops them from skewing the page's content bounding box |
| `len(stripped) >= 35` | `inspect_pdf`, line 116 | Minimum character count for a line to count as a "body line" | Short lines are usually labels, running heads, or fragments, not prose; 35 characters is long enough to exclude those but short enough not to exclude a short final sentence |
| `9.0 <= avg_size <= 14.5` (pt) | `inspect_pdf`, line 119 | Font-size band accepted as "body text" | Spans the common 10-12pt body sizes used across different department templates with headroom on both sides, while staying below typical heading sizes (14pt+) |
| `0.8*fs <= baseline_gap <= 3.0*fs` | `inspect_pdf`, line 138 | Accepted ratio of baseline gap to font size when pairing two lines for line-spacing measurement | Single spacing is ~1.0-1.2x the font size and double is ~2.0x; the 0.8-3.0 band covers single through double-plus while rejecting gaps between unrelated lines (a new paragraph, a different column) |
| `_percentile(bx0, 0.10)` | `inspect_pdf`, line 152 | Left-margin estimate: 10th percentile of body-line left edges | A handful of lines starting further right (list items, block quotes) would pull a minimum too far right; the 10th percentile is robust to those without needing to classify them first |
| `_percentile(bx1, 0.90)` | `inspect_pdf`, line 155 | Right-margin estimate: 90th percentile of body-line right edges | Every paragraph's last line is short; a maximum would be dragged left by outliers in the other direction, so the 90th percentile is used instead, deliberately matching the 10th-percentile choice for the left edge |
| `_percentile(line_spacings, 0.5)` | `inspect_pdf`, line 173 | Reported line spacing for a page | The median of all measured gaps on the page, robust to the occasional larger gap between paragraphs |
| `PT_TO_MM = 25.4 / 72.0` | `pdf_utils.py`, line 11 | Unit conversion, not a tolerance | Exact: 72 points per inch, 25.4mm per inch. Duplicated (not imported) in `format_validation.py` as `_PT_TO_MM` to keep the two modules decoupled |

---

## Rule-checking layer: `app/format_validation.py`

These decide whether a *measured* value (already extracted by `pdf_utils.py`)
satisfies a *guideline* rule.

### Numeric comparisons (`_num_ok`)

`_num_ok(detected, exp, op, tol)` is the shared `>=`/`<=`/`==` comparison used
by every size-like rule; only the `tol` argument varies by call site:

| `tol` | Call site | Why this number |
|---|---|---|
| `0.6` pt | `body_font_size_pt`, line 644 | The detected value is the page's dominant (mode) font size over many spans, so it is already a stable, averaged number; a small tolerance absorbs rounding only |
| `0.7` pt | `heading_font_size_pt`, line 812 | The detected value here comes from one matched heading line rather than a page-wide mode, so slightly more slack is given to absorb per-line measurement noise |
| `max(2.5, exp*0.08)` mm | margin rules, line 660 | A flat floor of 2.5mm so very small required margins still get a sane minimum tolerance, plus 8% of the required value so large margins scale proportionally. Comment in code: "A margin fails only when content is closer to the edge than allowed" — the tolerance is one-sided by design |

### Heading alignment (`_heading_alignment`)

| Value | Line | Why this number |
|---|---|---|
| `tol = 10.0` pt | 532 | Distance from a heading's edge to the page's actual left/right margin position (converted from the measured `left_mm`/`right_mm`), within which the heading counts as flush to that margin. Checked **before** the center tolerance below, deliberately: it is the more specific signal and must win first, or a long flush-left heading on a page with asymmetric margins can be misread as centered (see `DEF-022`) |
| `page_w * 0.08` | 539, 543 | A heading's midpoint within 8% of page width from true center counts as "centered". Deliberately wide: headings of very different lengths must all still read as centered, since a short heading's midpoint is naturally less stable than a long one's |

### Body-text alignment (`_page_body_alignment`, `_is_body_text_line`)

| Value | Line | Why this number |
|---|---|---|
| `len(lines) < 4` → `None` | 588 | Fewer than 4 qualifying lines is not enough evidence to trust a percentile-based margin estimate; reports "can't tell" rather than guessing from too little data |
| `int(0.1*(len-1))` / `int(0.9*(len-1))` | 591-592 | 10th/90th percentile left/right edge, the same trick as `pdf_utils.py`'s own margin estimate and for the same reason: robust to each paragraph's short last line |
| `<= 6.0` pt | 593-594 | How close a line's edge must sit to the percentile reference to count as "flush". Tighter than the 10pt heading tolerance because body-text alignment is judged from many lines at once, so a tighter band is still statistically safe and more precise |
| `>= 0.6` (ratio) | 595-597 | Fraction of lines that must be "flush" before the page is called justified/left/right rather than ambiguous. Set below 1.0 deliberately so one or two atypical lines (a quoted block, a short heading-adjacent line that slipped through the filter) do not flip the whole page's verdict, while still requiring a clear majority |
| `len(stripped) < 35`, `9.0 <= font_size <= 14.5` | 560-561 | Mirrors `pdf_utils.py`'s own body-line filter exactly (see that table above); duplicated rather than imported because the filtered list isn't part of the `info` structure handed to this module |

### Body text style (`body_regular_required`)

| Value | Line | Why this number |
|---|---|---|
| `len(sp.get('text','')) > 20` | 779 | Only spans long enough to plausibly be a body sentence are counted, excluding short heading fragments or caption labels |
| `len(bad) >= 3` | 781 | At least 3 offending (bold/italic) spans must appear on a page before it is flagged, so a single incidentally emphasised word does not fail the whole rule |

### Heading bold (`heading_bold_required`)

| Value | Line | Why this number |
|---|---|---|
| `abs(span_bbox_y0 - line_bbox_y0) < 3` pt | 815 | A span is treated as belonging to the heading line if its top y-coordinate is within 3pt of the heading line's own bbox top, absorbing the sub-point rounding difference between how MuPDF reports a line's bbox and a span's bbox for the same visual line |

### Cover-page layout (`_cover_layout_results`)

| Value | Line | Why this number |
|---|---|---|
| `len(common) < 4` → skip | 400 | At least 4 matched anchor elements (present in both the approved sample and the student's actual cover page) are required before attempting a relative-position comparison; fewer points make "position" too noisy to judge |
| `abs(sr - ar) > 0.15` | 423 | An element's relative vertical position (0 = topmost matched anchor, 1 = bottommost) may drift up to 15% from the sample's position before being flagged. Loose on purpose: exact spacing varies page to page even on a compliant cover page |
| `abs(sx - 0.5) <= 0.14`, `abs(ax - 0.5) <= 0.14` | 425, 427 | An element counts as "centered" if its horizontal midpoint sits within 14% of page width from true center. Wide enough to absorb measurement noise and differing element widths, tight enough to still separate centered from left/right-justified blocks |

---

## Semantic/LLM layer: budgets, not measurement tolerances

These bound how much text and time the semantic validator spends talking to
Ollama. Documented here for completeness since they are also "tolerance"
values in the loose sense (how much slack the system allows), but they gate
cost and context size, not a geometric match.

| Value | Location | Why this number |
|---|---|---|
| `SECTION_EVIDENCE_CHARS = 3000` | `ai_service/main.py:39` | Per-rule student evidence is capped at 3000 characters so one long section cannot dominate a batch's prompt budget |
| `SEMANTIC_BATCH_SIZE = 12` | `ai_service/main.py:43` | Rules are evaluated in batches of 12 so prompt and output tokens stay within Ollama's context and generation budgets (see next two rows) without needing one request per rule |
| `OLLAMA_NUM_CTX = 16384`, `OLLAMA_NUM_PREDICT = 4096` | `ai_service/main.py:397-398` | Ollama's context window and generation budget; the usable prompt budget is `num_ctx - num_predict`, logged as a warning when a prompt approaches it so truncation is never silent |
| `OLLAMA_TIMEOUT = 900` s | `ai_service/main.py:52` | Client-side timeout for a single Ollama call, sized for a full batch of 12 rules' worth of generation |
| `RAG_VALIDATE_TIMEOUT = 1800` s | `app/ai_service_client.py:9` | Timeout for the whole multi-batch semantic validation request from the core app to `ai_service`; set above `OLLAMA_TIMEOUT` so the caller does not give up before the batches it is waiting on can finish (see `DEF-007`) |

All four are overridable by environment variable (`SEMANTIC_SECTION_CHARS`,
`SEMANTIC_BATCH_SIZE`, `OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT`, `OLLAMA_TIMEOUT`,
`RAG_VALIDATE_TIMEOUT`), so a deployment with a faster model or a larger
context window can raise them without a code change.
