# Unsatisfiable guideline rules: defects and fixes

Three defects in the deterministic measurement layer made guideline rules
impossible to satisfy. Not hard to satisfy, and not usually-failing: no document
could pass them, because the measurement contradicted itself.

Found while rebuilding submission #3 ("AI Tools for Improving Hospital Patient
Care") to full compliance against guideline version 2.1. Each defect was
reproduced against the live engine before and after the change.

A narrative walkthrough with diagrams is in `validator_unsatisfiable_rules.html`
in this directory. One defect found during verification is **not** fixed here and
is recorded separately in `open_defects.md`.

---

## Summary

| # | Defect | File | Function | Effect before |
|---|--------|------|----------|---------------|
| 1 | Double spacing could not be measured | `app/pdf_utils.py` | `inspect_pdf` | `line_spacing` rule had no PASS path; every compliant thesis carried a permanent WARNING |
| 2 | Chapter and section rules shared one line list | `app/format_validation.py` | `_heading_candidates` | Any numbered heading took two mandatory FAILs no student could clear |
| 3 | Font weight erased before the bold check | `app/pdf_utils.py`, `app/format_validation.py` | `inspect_pdf`, `heading_bold_required` | A genuinely bold heading reported "Not bold" |

Effect on submission #3 after the fixes: **39 PASS / 1 WARNING** became
**40 PASS / 0 WARNING**, compliance rate 97.9% to 100%.

---

## Defect 1: double spacing could not be measured

### What the rule asked for

Guideline section 3 requires *"Double-spacing throughout main body text"*. The
parser stores `line_spacing = 2.0`; the checker tolerates `±0.28`, so a PASS
needed a measured ratio between **1.72 and 2.28**.

### What went wrong

`inspect_pdf` collected spacing samples only from pairs of lines that MuPDF had
placed in the **same block**:

```python
for block in d.get('blocks', []):
    block_lines = []                                  # reset for every block
    ...
    for a, b in zip(block_lines, block_lines[1:]):    # pairs only within one block
        baseline_gap = b['bbox'][1] - a['bbox'][1]
        fs = (a['font_size'] + b['font_size']) / 2
        if 0.8 * fs <= baseline_gap <= 3.0 * fs:
            line_spacings.append(baseline_gap / fs)
```

MuPDF starts a new block as soon as the baseline gap passes roughly 1.5 em.
Measured by sweeping line-height in 0.02 steps across nine font sizes, the
threshold sits between **1.41 and 1.63 x the font size**. At 12 pt the last gap
that still groups is 18.75 pt (1.562 em), while the passing range does not begin
until 20.64 pt (1.72 em).

So the two conditions excluded each other. To be *sampled* the gap had to stay
under about 1.5 em; to *pass* it had to reach 1.72 em. A sweep of all 31 steps
found no gap that was both grouped and inside the passing range.

The sample window on the last line already accepted 2.0. The requirement was
never rejected, it was never reached: at double spacing every block holds exactly
one line, `zip([x], [])` is empty, and `estimated_line_spacing` came back `None`
for every page. The rule then took its "could not estimate" path and emitted a
WARNING, so a correctly formatted thesis was never told it had passed.

Note the outer loop did visit every block. The fault was that `block_lines` was
reset per block, so the last line of one block was never paired with the first
line of the next.

### Fix

`app/pdf_utils.py` - measure over the page's body lines in reading order instead
of within a block. Lines in different columns do not overlap horizontally, so an
overlap test replaces the block boundary as the "same column of text" signal. The
existing `0.8 - 3.0` sanity window is unchanged.

```python
ordered = sorted(body_lines, key=lambda x: x['bbox'][1])
for a, b in zip(ordered, ordered[1:]):
    if min(a['bbox'][2], b['bbox'][2]) <= max(a['bbox'][0], b['bbox'][0]):
        continue
    ...
```

`app/format_validation.py` - a cover page, a blank leaf or an index carries no
running prose, so it has no line spacing to measure. Warning on those pages made
the warning permanent for any compliant thesis. Doubt is now reported only when
no page in the document could be measured at all, which keeps the "never silently
pass" guarantee: a document the engine genuinely cannot assess still produces a
WARNING rather than a PASS.

### Verified

`REVISED5_source/repro_spacing.py` - `line-height: 2` now measures `2.0` and
passes. `probe_threshold.py` records where the MuPDF threshold sits.

---

## Defect 2: chapter and section rules shared one line list

### What the rules asked for

The guideline sets a heading hierarchy: chapter titles 14 pt, secondary headings
12 pt. The parser stores two rules with two different targets, which is correct:

```python
{'type': 'heading_font_size_pt', 'target': 'Chapter Heading', 'value': 14}   # rule 25
{'type': 'heading_font_size_pt', 'target': 'Section Heading', 'value': 12}   # rule 29
```

### What went wrong

`_heading_candidates` discarded the target. Whenever it was one of three
recognised phrases, the branch that ran never consulted it again and fell back to
a single shared pattern:

```python
if low in {'chapter heading', 'section heading', 'heading'}:
    if re.match(r'^(chapter\s+\w+|\d+(?:\.\d+)*\s+\S+)', txt, re.I):
        found.append((pg, ln))          # 'low' is never used again
```

Both targets therefore returned the *identical* list of lines, and each rule
demanded a different size of it. With a tolerance of 0.7 the two acceptance
windows are 13.3-14.7 and 11.3-12.7, which do not overlap. Measured on a one-page
document with a single heading:

```
heading at 12 pt   FAIL chapter (wants 14)   PASS section
heading at 13 pt   FAIL chapter              FAIL section
heading at 14 pt   PASS chapter              FAIL section (wants 12)
```

The only clean document was one whose headings the resolver could not see at all,
which is why submission #3 had to ship with unnumbered headings and 8 heading
rules reading NOT VERIFIED.

A second fault in the same function: unlike `_find_actual_heading`, it had no
contents-page guard, so every contents entry beginning `Chapter N` or `1.1` was
collected as a heading too. In version 5 of the thesis this is visible as
candidates such as `CHAPTER 1: INTRODUCTION1` and `1.1 Background1`, where the
trailing digit is the contents page number. Contents entries are left-aligned, so
the "chapter headings must be centred" rule failed on them even though every real
chapter heading was centred.

This was an aggravation, not the cause. A document with one heading and no
contents page still failed, which rules the contents listing out as the cause.
Adding a contents page turned one failure into two.

### Fix

`app/format_validation.py` - one pattern per heading level, and contents pages
skipped for level matches:

```python
_HEADING_LEVEL_PATTERNS = {
    'chapter heading': re.compile(r'^chapter\s+\w+', re.I),
    'section heading': re.compile(r'^\d+\.\d+(?!\.\d)\s+\S+'),
    'third level heading': re.compile(r'^\d+\.\d+\.\d+\s+\S+'),
    'heading': re.compile(r'^(?:chapter\s+\w+|\d+(?:\.\d+)*\s+\S+)', re.I),
}
```

The negative lookahead keeps `1.1.1 Reporting practice` out of the section-level
set. `Third Level Heading` previously matched nothing at all, because it fell
through to the substring branch and no line literally contains the words "third
level heading"; that is why its four rules read NOT VERIFIED.

### Verified

`REVISED5_source/repro_headings.py` and `probe_isolate.py`. A properly numbered
document now passes both size rules at once.

---

## Defect 3: font weight erased before the bold check

### What went wrong

Chrome embeds the bold cut as its own face and sets the bold flag bit, so the
weight is unambiguously present in the file:

```
text   : CHAPTER 1: INTRODUCTION
font   : TimesNewRomanPS-BoldMT
flags  : 20  (bold bit set: True)
```

`_norm_font()` exists to make font comparison tolerant, so that a dozen spellings
all match the guideline's "Times New Roman". It does that job correctly. The side
effect is that every weight and style collapses to the same string:

```
TimesNewRomanPSMT          -> 'Times New Roman'
TimesNewRomanPS-BoldMT     -> 'Times New Roman'
TimesNewRomanPS-ItalicMT   -> 'Times New Roman'
```

`heading_bold_required` then tested for weight by searching that normalised name
for the substring `'bold'`, which had just been removed. The `flags` field that
still held the answer was never carried into the span record.

`body_regular_required` had the inverse fault for the same reason: it flags body
text by looking for `'bold'` or `'italic'` in the font name, so it could never
fire. Body text set entirely in bold italic would have passed it. It is not in
the active profile, but it would have been wrong if switched on.

### Fix

`app/pdf_utils.py` - record the style from the PyMuPDF flag bits before
normalisation discards it, keeping a font-name fallback:

```python
flags = int(span.get('flags') or 0)
item = {..., 'bold': bool(flags & 16) or 'bold' in low_font,
             'italic': bool(flags & 2) or 'italic' in low_font or 'oblique' in low_font}
```

`app/format_validation.py` - new `_span_is_bold()` and `_span_is_italic()`
helpers prefer the recorded flag and fall back to the font name for span
dictionaries built elsewhere, so callers that do not go through `inspect_pdf`
keep working. Both `heading_bold_required` and `body_regular_required` now use
them. `_norm_font()` itself is unchanged, so family matching is unaffected.

### Verified

`REVISED5_source/repro_bold.py` - the bold heading from step 1 now passes.

---

## Tests

`tests/test_format_validation_rules.py`, 46 tests, 20 of them new in four
classes:

| Class | Covers |
|-------|--------|
| `HeadingLevelSeparation` | each level resolves to its own lines; both size rules pass together; contents entries excluded; wrong size still fails |
| `FontWeightDetection` | flag preferred over the normalised name; name used as fallback; bold heading passes; regular heading still fails |
| `LineSpacingReporting` | double spacing passes; an unmeasurable page does not warn when prose pages comply; wrong spacing still fails; a wholly unmeasurable document still warns |
| `PdfMeasurement` | `inspect_pdf` measures 2.0 at a 24 pt pitch, still measures single spacing, and records the span weight |

Each defect was confirmed red by reverting only its own fix:

| Fix reverted | Tests that fail |
|--------------|-----------------|
| `pdf_utils` spacing and flags | `test_double_spacing_is_measured`, `test_span_weight_is_recorded`, `test_body_span_is_not_marked_bold` |
| `_heading_candidates` | 6 of 7 in `HeadingLevelSeparation` |
| bold rule and spacing reporting | `test_bold_heading_passes`, `test_unmeasurable_page_does_not_warn_when_prose_pages_comply` |

Five tests stay green in both states, so they are guards against the fixes
overreaching rather than restatements of them:
`test_single_spacing_is_still_measured`, `test_wrong_chapter_size_still_fails`,
`test_regular_heading_still_fails`, `test_wrong_spacing_still_fails`,
`test_document_with_nothing_measurable_is_still_flagged`.

Run with:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

---

## Reproduction scripts

In `resources/sample-thesis/AI Tools for Improving Hospital Patient
Care/REVISED5_source/`. They import the live engine, so their output always
reflects the current code.

| Script | Shows |
|--------|-------|
| `repro_spacing.py` | measured spacing at eight line heights |
| `probe_threshold.py` | where the MuPDF grouping threshold sits, across nine font sizes |
| `probe_isolate.py` | the size conflict with and without a contents page |
| `repro_headings.py` | the shared candidate list and the impossible sizes |
| `repro_bold.py` | a bold heading reported as not bold |
| `check.py` | every parsed rule against any candidate PDF |

---

## Still open

A fourth defect of the same family was found while verifying these fixes and is
**not** addressed here. `heading_case` rejects correct title case, which blocks
restoring chapter and section numbering in the thesis. See `open_defects.md`.
