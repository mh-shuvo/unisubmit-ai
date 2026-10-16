# UniSubmit AI — Validation Defects & Fixes

Diagnosis of the validation errors students saw on submission. Found by running
the full AI job against `submission_1_v2_80120022.pdf` (AI job #2, template
version 2, profile status `Active`), and confirmed end to end by a real UI
submission — see [Result](#result).

Eight defects, D1-D8. D1-D6 are rule-evaluation bugs in
`app/format_validation.py`, D7 is a context-window overflow in
`ai_service/main.py`, and D8 is a concurrency gap between the queue worker and
any other caller of `/rag/validate`.

> **Reproducing with `test_job.py`:** that harness calls `process_job()` inline
> and bypasses `queue_worker`, so running it while the app is live creates
> exactly the Ollama contention described in D8 and both jobs time out. Stop the
> app's worker first, or submit through the UI instead. The sample PDFs
> referenced here were deleted after testing; re-upload one to replay.

---

## Starting state

Both AI jobs in the database were `FAILED` and had produced **no report at all**:

| Job | `error_message` | Triggering rule |
|---|---|---|
| 1 | `could not convert string to float: '>=12'` | `body_font_size_pt`, value `">=12"` |
| 2 | `could not convert string to float: 'double'` | `line_spacing`, value `"double"` |

`validation_json` was `[]`, `report_path` was empty, `status` was
`Validation Failed`.

---

## Defect 1 (D1) — `float()` on guideline-parser text values

**Where:** `app/format_validation.py`, `validate_format_profile()`

Qwen writes rule values as human text, not bare numbers. `float()` raised and
killed the whole job, so a single odd value lost all 88 rule results.

```python
exp = float(expected)        # 'double'  -> ValueError -> job FAILED
```

**Example:** guideline says *"The print size should be at least 12 points"* →
parser stores `value: ">=12"` → `float(">=12")` raises → student gets no report.

**Fix:** `_parse_num_op()` understands operator symbols (`>=12`, `≥ 25`),
operator words (`at least 12`, `no more than 150`), spacing nouns
(`single space`, `double spacing`) and word numbers (`double`, `one and a half`).

---

## Defect 2 (D2) — unparseable value silently became PASS

**Where:** `app/format_validation.py`, `validate_all_profile_rules()`

The first crash patch used `continue` to skip values it could not read. But the
wrapper treats "rule produced no issue" as **PASS**, so skipped rules were
reported to the student as compliant.

**Example:** rule `line_spacing` target `Abstract` value `"single space"` →
value unreadable → skipped → report shows **✅ PASS Line spacing**. The abstract
spacing was never checked, and the student was told it complied.

**Fix:** every unreadable or unscopeable rule now returns an explicit
`NOT VERIFIED` row via `_not_verified()`, carrying the reason. `NOT VERIFIED` is
excluded from the compliance-rate denominator in `_profile_rule_stats()`, so it
cannot inflate or deflate the score.

---

## Defect 3 (D3) — `max_pages` ignored the rule's `target`

**Where:** `app/format_validation.py`, `min_pages`/`max_pages` branch

A page limit scoped to one section was measured against the whole document.

**Example:** guideline says *"The abstract must be confined in a single page"* →
parser stores `{type: max_pages, target: "Abstract", value: 1}` → engine compared
`1` against `page_count = 30` → **Mandatory FAIL: "Expected 1, Detected 30
pages"**. Every thesis longer than one page failed this rule.

**Fix:** when `target` names a section, the span is measured with
`_section_page_span()` (heading page → last page carrying the section's text).
When the section cannot be located the rule is `NOT VERIFIED`, never `FAIL`.
Document-wide limits (`target` empty) behave exactly as before.

---

## Defect 4 (D4) — `margin_*_mm` ignored the rule's `target`

**Where:** `app/format_validation.py`, margin branch

A margin requirement for one named area was applied to every page.

**Example:** guideline says *"Heading of each chapter should be centered leaving
4 spaces from the top"* → parser stores `{type: margin_top_mm, target: "Chapter
Heading area", value: 101.6}` → engine demanded a 101.6 mm top margin on all 30
pages → **Mandatory FAIL** listing 11 pages.

**Fix:** page-level margin measurement cannot isolate a sub-area, so a
targeted margin rule is reported `NOT VERIFIED` with that reason. Generic
margin rules (`target` empty or `"None"`) still fail on real intrusion.

---

## Defect 5 (D5) — parenthetical targets could never match a heading

**Where:** `app/format_validation.py`, `_find_section()` / `_normalize_heading()`

### Where the qualifier comes from

The guideline has **one sentence** covering **two** limits:

> "The abstract of a M.S. thesis must not exceed 150 words and Ph.D. thesis 350
> words."

Qwen split it into two rules. Both measure the *same* section — the Abstract —
so to distinguish them it appended the degree to the target:

```
deterministic_rules[25]  target='Abstract (M.S. thesis)'   value=150  Mandatory
deterministic_rules[26]  target='Abstract (Ph.D. thesis)'  value=350  Mandatory
```

`(M.S. thesis)` is **Qwen's own label**. It never appears in a student's
document.

### Why that broke the rule

`target` was serving two incompatible purposes:

| What `target` must answer | What was stored there |
|---|---|
| "Which section do I measure?" → `Abstract` | "Which degree does this limit apply to?" → `Abstract (M.S. thesis)` |

`_find_section()` uses `target` as a **literal search key**. `re.escape()` turns
it into `Abstract\ \(M\.S\.\ thesis\)`, so the engine hunted for a heading line
reading exactly `Abstract (M.S. thesis)`:

```python
>>> _find_section("ABSTRACT\nBody text...\nINTRODUCTION\n", "Abstract")
'Body text...'
>>> _find_section("ABSTRACT\nBody text...\nINTRODUCTION\n", "Abstract (M.S. thesis)")
None          # before fix — unmatchable in ANY document
```

The search failed, and the old code treated "not found" as a failure at the
rule's own severity:

```python
if sec is None:
    occ=[_occ(None,f'Section: {target}','Section not found')]
    out.append(_consolidate(..., severity=severity, ...))   # Mandatory -> FAIL
```

Net effect: 2 Mandatory FAILs on every submission for a word limit that was
**never counted**, and Mandatory FAIL forces `REVISION REQUIRED`.

### Two stacked problems, two fixes

**Fix 1 — make the target matchable (C4/C6/C8).** `_strip_qualifier()` removes
any parenthetical or bracketed qualifier. Generic, no degree or section names
hardcoded:

```python
re.sub(r'\s*[\(\[][^)\]]*[\)\]]\s*', ' ', target)
```

`_find_section()` tries the literal target, then the stripped one;
`_normalize_heading()` applies the same strip, so `required_section` and
`_find_actual_heading()` benefit too.

**Fix 2 — "not found" is not a breach (C13).** Failing to locate a section is no
evidence its word limit was exceeded, so that case is now `NOT VERIFIED`. The
section's absence is already reported once by its own `required_section` rule.

The two fixes do different work:

| Document | Before | After | Fix responsible |
|---|---|---|---|
| Well-formatted thesis, heading `ABSTRACT` | FAIL (never measured) | **FOUND — 150-word limit actually checked** | Fix 1 |
| `submission_1_v2_80120022.pdf` | FAIL (never measured) | **NOT VERIFIED** | Fix 2 |

This sample PDF is still "not found" after Fix 1, and correctly so — its heading
reads `AN ABSTRACT OF THE DISSERTATION OF`, not `ABSTRACT`, so there is no bare
abstract heading to locate. "Could not check" is the honest result; "you
exceeded 150 words" was not. Fix 1 is what makes the rule do real work on a
correctly formatted paper instead of failing it.

> Still open (see Open items): both the M.S. and Ph.D. rules fire for every
> submission even though a paper is one degree. That is a profile-review
> decision, not an engine defect.

### The same matching bug has a second, opposite symptom

**This is not a word-limit problem.** `150` and `350` are plain integers that
parsed without trouble. The word count was simply **never computed**, because
the section lookup failed first. `section_max_words` is only where the matching
failure happened to surface.

The engine resolves a rule's `target` through **two different matchers**, and a
qualifier breaks both — with opposite consequences:

| Matcher | Used by | On no match | Symptom |
|---|---|---|---|
| Exact heading match (`_find_section`, `_normalize_heading`) | `section_max_words`, `section_min_words`, `required_section` | "section not found" → `_consolidate(...)` at rule severity | **false FAIL** |
| Substring match (`target.lower() in txt.lower()`) | `heading_case`, `heading_alignment`, `heading_font_size_pt`, `heading_bold_required` | zero lines examined → zero occurrences → wrapper sees "no issue" | **false PASS** |

Eight rules in this profile carry a parenthetical target — two word-count rules
and six heading rules:

```
[25] section_max_words  Mandatory  'Abstract (M.S. thesis)'
[26] section_max_words  Mandatory  'Abstract (Ph.D. thesis)'
[30] heading_case       Warning    'Chapter Heading (implied by context of ABSTRACT)'
[31] heading_alignment  Warning    'Chapter Heading (implied by context)'
[32] heading_case       Warning    'ABSTRACT Heading (specific)'
[41] heading_case       Warning    'Cover page Left corner text (M.S. THESIS or Ph.D. THESIS)'
[42] heading_case       Warning    'Cover page Middle text (Name of student)'
[43] heading_case       Warning    'Cover page Right corner text (Month and Year)'
```

Measured against the real PDF before the fix — no line matched, yet every rule
reported PASS:

```
Cover page Middle text (Name of student)          lines matched = 0  -> PASS
Chapter Heading (implied by context of ABSTRACT)  lines matched = 0  -> PASS
ABSTRACT Heading (specific)                       lines matched = 0  -> PASS
```

**Fix 3 (C29) — `_heading_candidates()`.** Both heading branches now resolve the
target through one helper that retries without the qualifier, and an empty
result is reported as `NOT VERIFIED` rather than passing a rule that inspected
nothing:

```python
candidates=_heading_candidates(info,target)
if not candidates:
    out.append(_not_verified(..., f'No document heading matched the target "{target}"', ...)); continue
```

Effect on this PDF: 6 vacuous PASSes became `NOT VERIFIED`, and 2 rules whose
qualifier hid a usable target (`Chapter Heading (implied by context…)` →
`Chapter Heading`) now evaluate for real and report genuine WARNINGs.

---

## Defect 6 (D6) — `>=` was validated as `==`

**Where:** `app/format_validation.py`, `body_font_size_pt` / `heading_font_size_pt`

The operator was stripped and the value compared for equality.

**Example:** guideline requires *at least* 12 pt. A thesis typeset at 14 pt
satisfies that, but `abs(14 - 12) > 0.6` → **FAIL**. Oversized-but-compliant
text was penalised.

**Fix:** `_parse_num_op()` returns the operator and `_num_ok()` applies it. The
report label follows (`at least 12 pt` instead of `12 pt`).

---

## Defect 7 (D7) — semantic layer returned NOT VERIFIED for everything

**Where:** `ai_service/main.py`, `_extract_target_section()` and `rag_validate()`

All 42 semantic requirements came back `NOT VERIFIED`, including facts plainly
present in the document.

Root cause chain:

1. All 42 requirements have an empty `target_section`.
2. `_extract_target_section()` returned `text[:30000]` — the **whole document** —
   whenever the target was empty.
3. That full document was attached to each of the 42 requirements, so the
   requirements JSON blob alone was **914,306 characters**.
4. Total prompt ≈ **972,306 chars (~243,000 tokens)** against `num_ctx = 16384`.
5. Ollama truncates an oversized prompt **from the front, silently**. The
   instructions and the JSON schema were discarded; only a tail of student text
   survived. The model never saw the task.
6. The merge keys on `semantic_id`, so zero returned ids meant every rule fell
   through to the `NOT VERIFIED` default.

**Example:** *"The cover page and title page must include the Student's Name."*
The document contains `James Smith` on page 1. Result was `NOT VERIFIED —
Insufficient evidence`, because the model was never given the requirement.

**Fixes:**

| Change | Effect |
|---|---|
| `_extract_target_section()` returns `""` when there is no target section | Document no longer duplicated per rule; blob 914,306 → 15,548 chars |
| Empty `student_section_text` key omitted; prompt tells the model to fall back to `FULL STUDENT CONTENT` | No loss of evidence |
| `SECTION_EVIDENCE_CHARS` (3000) caps per-rule evidence | Bounded as the rule set grows |
| Semantic validation **batched** at `SEMANTIC_BATCH_SIZE = 12` | Prompt *and* output stay inside `num_ctx`/`num_predict` regardless of rule count |
| Output schema slimmed to fields the server consumes | Server already holds `requirement`/`severity`/source fields |
| Prompt-size warning in `call_ollama()` | Silent truncation is now logged, not invisible |
| One unusable batch no longer discards the others | Its rules stay `NOT VERIFIED`; gap stated in the grounding note |
| `RAG_VALIDATE_TIMEOUT` (1800 s, env-overridable) in `app/ai_service_client.py` | Multi-batch run no longer hits the old 660 s client timeout |

> **Config note:** `ai_service/.env` sets `OLLAMA_NUM_CTX=16384` and
> `OLLAMA_NUM_PREDICT=4096`, which **override the code defaults**. Tune budgets
> there, not only in `ai_service/main.py`. Batch size 12 is chosen for 16384.

---

## Defect 8 (D8) — concurrent Ollama consumers time each other out

**Where:** not a single file — an architectural gap between `app/main.py`'s
`queue_worker` and any other process that calls `/rag/validate`.

After the batching fix, UI submissions still failed with
`AI validation HTTP 503: Local LLM unavailable: `. The AI service log settled
what was actually happening:

```
INFO:  127.0.0.1:57592 - "POST /rag/validate HTTP/1.1" 200 OK   <- full 4-batch run SUCCEEDED
...
ERROR: Ollama semantic generation failed ... batch=3/4
ERROR: Ollama semantic generation failed ... batch=2/4
```

Two facts decide it:

1. A complete 4-batch run returned **200 OK under the old 300 s cap**, so 300 s
   was sufficient for an uncontended run.
2. `batch=3/4` failing *before* `batch=2/4` is impossible in a sequential loop.
   Those are **two concurrent `/rag/validate` requests**, one on its 3rd batch
   and one on its 2nd.

**The two consumers were** `queue_worker` (processing the UI submission) and a
standalone `test_job.py` run. `test_job.py` calls `process_job()` inline, so it
reaches `/rag/validate` directly and **bypasses `queue_worker`'s
one-job-at-a-time serialization** entirely.

**Mechanism.** `OLLAMA_NUM_PARALLEL` is unset, so Ollama auto-picks a small
value and queues concurrent generations for the same model. The httpx
read-timeout clock starts at `client.post()`, **not** when generation begins, so
the second request burns its whole budget waiting in Ollama's queue and times
out having produced nothing. Both requests lose, though neither generation was
abnormally slow.

**Correction to an earlier conclusion in this document's history:** the failures
were first attributed to "a 12-rule batch takes longer than 300 s on this
hardware". The `200 OK` above refutes that. Batch duration also tracks output
token count far more than rule count — measured 112 s for 4 requirements versus
307 s for 3 — so 300 s sat inside the variance band rather than below it.

**Fix:** there is no code fix for the contention itself; the rule is
operational. C30's 900 s raises headroom to absorb queue wait and output
variance, but it is **not** the cause and does not prevent a recurrence.

| Measure | Status |
|---|---|
| Never run `test_job.py` while the app worker is live | operational, prevents every failure in that log |
| Set `OLLAMA_NUM_PARALLEL=1` explicitly so queuing is deliberate | not applied |
| Make `test_job.py` refuse to start when a job is already `PROCESSING` | not applied |

---

---

## Code changes mapped to defects

Every change, what it replaced, and which defect it exists to solve.
`git diff --numstat`: `app/format_validation.py` +211/-23, `ai_service/main.py`
+123/-56, `app/ai_service_client.py` +6/-1. Defect IDs D1-D7 refer to the
sections above.

### Summary table

| # | File | Symbol / location | Change | Solves |
|---|---|---|---|---|
| C1 | `app/format_validation.py` | new `_parse_num_op()`, `_parse_num()` | text value → `(number, operator)` | D1, D6 |
| C2 | `app/format_validation.py` | new `_num_ok()` | compare honouring `>=` / `<=` / `==` | D6 |
| C3 | `app/format_validation.py` | new `_is_generic_target()`, `_GENERIC_TARGETS` | tell document-wide rules from section-scoped | D3, D4 |
| C4 | `app/format_validation.py` | new `_strip_qualifier()` | drop `(M.S. thesis)`-style qualifiers | D5 |
| C5 | `app/format_validation.py` | new `_not_verified()` | explicit undecidable row instead of silence | D2 |
| C6 | `app/format_validation.py` | `_find_section()` | retry with stripped target | D5 |
| C7 | `app/format_validation.py` | new `_section_page_span()` | measure a section's page span | D3 |
| C8 | `app/format_validation.py` | `_normalize_heading()` | strip qualifier for heading matching | D5 |
| C9 | `app/format_validation.py` | `body_font_size_pt` branch | `float()` → parse + operator + label | D1, D2, D6 |
| C10 | `app/format_validation.py` | `margin_*_mm` branch | `float()` → parse, + target scoping | D1, D2, D4 |
| C11 | `app/format_validation.py` | `line_spacing` branch | `float()` → parse, + target scoping | D1, D2 |
| C12 | `app/format_validation.py` | `min_pages`/`max_pages` branch | `int(float())` → parse, + section span | D1, D2, D3 |
| C13 | `app/format_validation.py` | `section_*_words` branch | parse; section-not-found no longer FAIL | D1, D2, D5 |
| C14 | `app/format_validation.py` | `heading_font_size_pt` branch | parse + operator | D1, D6 |
| C15 | `app/format_validation.py` | `_rule_display_name()` | no more blank `": heading case"` names | cosmetic |
| C16 | `app/format_validation.py` | `validate_all_profile_rules()` | null-value guard before validation | D2 |
| C17 | `ai_service/main.py` | `_extract_target_section()` | empty target returns `""`, not whole doc | **D7 root cause** |
| C18 | `ai_service/main.py` | `_extract_target_section()` | per-rule cap 18000 → 3000 | D7 |
| C19 | `ai_service/main.py` | `rag_validate()` | omit empty `student_section_text` key | D7 |
| C20 | `ai_service/main.py` | `rag_validate()` | context chunks 40 → 12 | D7 |
| C21 | `ai_service/main.py` | prompt | student text 18000 → 14000 chars | D7 |
| C22 | `ai_service/main.py` | `rag_validate()` | single prompt → **batched loop** | D7 |
| C23 | `ai_service/main.py` | prompt schema | slimmed output fields | D7 (output overflow) |
| C24 | `ai_service/main.py` | prompt | "echo semantic_id EXACTLY" | D7 (id mismatch) |
| C25 | `ai_service/main.py` | `rag_validate()` | one bad batch no longer kills the rest | robustness |
| C26 | `ai_service/main.py` | `call_ollama()` | warn when prompt exceeds budget | observability |
| C27 | `ai_service/main.py` | response | `semantic_batches`, `semantic_batches_failed` | observability |
| C28 | `app/ai_service_client.py` | `rag_validate()` | timeout 660 s → `RAG_VALIDATE_TIMEOUT` 1800 s | consequence of C22 |
| C29 | `app/format_validation.py` | new `_heading_candidates()`, both `heading_*` branches | qualifier-aware matching; no match → NOT VERIFIED | D5 (false PASS half) |
| C30 | `ai_service/main.py` | `call_ollama()`, new `OLLAMA_TIMEOUT` | hardcoded 300 s → env-configurable 900 s | D8 (headroom) |
| C31 | `ai_service/main.py` | semantic 503 handler | name the failing batch and exception type | D8 (diagnosability) |

---

### C1 / C2 — parse the value, keep the operator

**Solves D1** (crash) and **D6** (`>=` treated as `==`).

Before — any non-numeric text killed the job, and the operator was discarded:

```python
exp = float(expected)                      # '>=12', 'double' -> ValueError
if det is not None and abs(float(det) - exp) > 0.6:
```

After:

```python
exp, op = _parse_num_op(expected)          # '>=12' -> (12.0, '>=')
...
if det is not None and not _num_ok(float(det), exp, op, 0.6):
```

`_parse_num_op()` strips operator symbols, operator words, and spacing nouns,
then maps word numbers. `_num_ok()` applies the operator instead of assuming
equality. `_parse_num()` is the thin wrapper for call sites that only need the
number.

**Why not just `try/except`:** swallowing the error returns to D2 — the rule
would be skipped and reported as PASS. The value has to be *understood*, and
when it cannot be, said so explicitly.

---

### C5 / C16 — undecidable must never read as PASS

**Solves D2.**

`validate_all_profile_rules()` treats "the rule produced no issue row" as PASS.
So `continue` is not a safe way to skip a rule.

Before (the first crash patch) — skipped rule silently became PASS:

```python
exp = _parse_num(expected)
if exp is None: continue                   # -> wrapper reports PASS
```

After — the rule reports itself as undecided, with the reason:

```python
exp = _parse_num(expected)
if exp is None:
    out.append(_not_verified('LINE_SPACING', 'Line spacing', str(expected), source,
                             f'Could not interpret the required spacing "{expected}"',
                             severity, evidence, section)); continue
```

C16 catches the related case where the parser produced **no value at all**
(`value: null`), which previously fell through to a vacuous PASS with a blank
name:

```python
if r.get('value') is None or (isinstance(r.get('value'), str) and not r['value'].strip()):
    rows.append(_not_verified(f'NO_VALUE_{idx}', name, expected or '(not parsed)', source,
                              'The guideline parser extracted no required value for this rule',
                              severity, evidence, section))
    continue
```

`NOT VERIFIED` is already excluded from the compliance-rate denominator in
`_profile_rule_stats()` (`app/main.py`), so these rows cannot distort the score,
and both the PDF (`app/reporting.py`) and HTML
(`app/templates/validation_report.html`) already render the status.

---

### C3 / C7 / C12 — a section page limit is not a document page limit

**Solves D3.**

Before — `target` was ignored entirely:

```python
val = int(float(expected)); actual = int(info.get('page_count', 0))
ok = actual >= val if kind == 'min_pages' else actual <= val
```

With `{max_pages, target: "Abstract", value: 1}` this compared `1` against the
document's 30 pages.

After — scope is chosen from `target`:

```python
if _is_generic_target(target):
    scope = 'Whole document'; actual = int(info.get('page_count', 0))
else:
    name = f'{name} ({target})'; scope = f'Section: {target}'
    span = _section_page_span(info, text, target)
    if span is None:
        out.append(_not_verified(..., f'Could not locate the section "{target}" to measure its length', ...)); continue
    actual = span[1] - span[0] + 1
```

`_section_page_span()` (C7) finds the heading page, then the last page still
carrying one of the section's longer lines. `_is_generic_target()` (C3) treats
`''`, `'None'`, `'whole document'`, `'body text'` etc. as document-wide — the
literal string `"None"` matters, because the parser emits it as a target.

**Why `NOT VERIFIED` when the section is missing:** a section that cannot be
found is not evidence that its page limit was exceeded. The absence is already
reported once by the matching `required_section` rule.

---

### C10 — a margin for one area is not a margin for every page

**Solves D4.**

`inspect_pdf()` measures one body-text boundary per page. That number cannot
describe "the chapter heading block", so the rule is not decidable from it.

Added before the existing per-page loop:

```python
if not _is_generic_target(target):
    out.append(_not_verified(kind.upper(), f'{side.title()} margin ({target})',
                             f'at least {exp:g} mm', source,
                             f'Rule applies to "{target}" only, which the page-level '
                             f'margin measurement cannot isolate',
                             severity, evidence, section)); continue
```

Generic margin rules (the four real ones in this profile) are untouched and
still PASS/FAIL on actual text intrusion. C11 applies the same reasoning to
section-scoped `line_spacing`, since per-page spacing is also a whole-page
median.

---

### C4 / C6 / C8 / C13 — qualified targets must match real headings

**Solves D5.**

Before — `re.escape()` put the qualifier into the heading pattern, making the
target unmatchable against any document:

```python
pat = rf'(?is)(?:^|\n)\s*(?:\d+(?:\.\d+)*\s+)?{re.escape(target)}\s*\n(.*?)...'
m = re.search(pat, text)
return m.group(1) if m else None
```

After — try the literal target, then the de-qualified one (C6):

```python
candidates = [str(target).strip()]
stripped = _strip_qualifier(target)
if stripped and stripped.lower() != candidates[0].lower(): candidates.append(stripped)
for cand in candidates:
    ...
    if m: return m.group(1)
return None
```

`_strip_qualifier()` (C4) is deliberately generic — no degree names, no section
names:

```python
t = re.sub(r'\s*[\(\[][^)\]]*[\)\]]\s*', ' ', str(target or ''))
return re.sub(r'\s+', ' ', t).strip(' -:–—')
```

C8 applies the same strip inside `_normalize_heading()`, so `required_section`
and `_find_actual_heading()` get the benefit too. C13 additionally downgrades
"section not found" from `FAIL` to `NOT VERIFIED` for word-count rules.

---

### C17 / C18 / C19 — stop duplicating the document per rule

**Solves D7, root cause.**

Before — an empty target returned the entire document, and this ran once per
requirement:

```python
if not target:
    return text[:30000]
...
return "\n".join(out).strip()[:18000]
```

All 42 requirements had an empty `target_section`, so the requirements blob
carried 42 copies of the document: **914,306 characters**.

After:

```python
if not target:
    return ""                              # full doc is already in the prompt once
...
return "\n".join(out).strip()[:SECTION_EVIDENCE_CHARS]   # 3000
```

C19 drops the key entirely when empty, and the prompt gained the matching
instruction so no evidence is lost:

> When a rule has no student_section_text field, use FULL STUDENT CONTENT below
> as its evidence and still make a decision.

Blob: 914,306 → **15,548 chars**.

---

### C20 / C21 / C22 / C23 — keep prompt *and* output inside the budget

**Solves D7, remaining overflow.**

Even after C17, one prompt holding all 42 rules plus 40 context chunks still
exceeded `num_ctx`, and 42 result objects exceeded `num_predict` — the service
raised `Ollama response was incomplete`.

C22 is the structural fix — one call per batch of rules:

```python
batches = [explicit[i:i + SEMANTIC_BATCH_SIZE]
           for i in range(0, len(explicit), SEMANTIC_BATCH_SIZE)] or [[]]
for batch_no, batch in enumerate(batches, start=1):
    prompt = f"""...Return exactly {len(batch)} objects...
    PARSED SEMANTIC REQUIREMENTS...:\n{json.dumps(batch, ensure_ascii=False)}..."""
```

This bounds both sides regardless of how many rules a guideline yields, and
mirrors the chunking the guideline parser already uses. C20 trims shared context
(40 → 12 chunks) and C21 trims student text (18000 → 14000 chars), since each
batch re-sends both. C23 removes fields the server never reads from the model's
output — `requirement`, `severity`, `source_*` all come from the stored rule —
which roughly halves output tokens.

New knobs, all env-overridable:

```python
SEMANTIC_BATCH_SIZE     = int(os.getenv("SEMANTIC_BATCH_SIZE", "12"))
SEMANTIC_CONTEXT_CHUNKS = int(os.getenv("SEMANTIC_CONTEXT_CHUNKS", "12"))
SEMANTIC_STUDENT_CHARS  = int(os.getenv("SEMANTIC_STUDENT_CHARS", "14000"))
SECTION_EVIDENCE_CHARS  = int(os.getenv("SEMANTIC_SECTION_CHARS", "3000"))
```

**`num_ctx` / `num_predict` were left at their original 16384 / 4096.** Raising
them was tried and did nothing, because `ai_service/.env` overrides the code
defaults. Batch size 12 is sized for 16384.

---

### C24 / C25 / C26 / C27 — make the failure mode visible next time

The reason D7 went unnoticed is that every layer failed silently.

C26 — `call_ollama()` now warns before Ollama truncates:

```python
budget = num_ctx - num_predict
estimated_tokens = int(len(prompt) / CHARS_PER_TOKEN)
if estimated_tokens > budget:
    logger.warning("Prompt likely exceeds context budget model=%s prompt_chars=%s "
                   "estimated_tokens=%s num_ctx=%s usable_budget=%s; Ollama will truncate", ...)
```

C24 pins the merge key, since the server joins model output to rules on
`semantic_id` and a renumbered id silently becomes `NOT VERIFIED`:

> Return one object per rule, echoing its semantic_id EXACTLY as given. Do not
> invent or renumber semantic_id values.

C25 stops one unusable batch from discarding the batches that succeeded — those
rules stay `NOT VERIFIED` and the gap is stated in the grounding note; only an
all-batch failure raises. C27 returns `semantic_batches` and
`semantic_batches_failed` for diagnosis.

---

### C28 — client timeout had to follow the batching

**Consequence of C22**, not a defect of its own. Four sequential LLM calls
exceeded the old fixed 660 s and surfaced as `ReadTimeout`:

```python
-        async with httpx.AsyncClient(timeout=660) as c:
+        async with httpx.AsyncClient(timeout=RAG_VALIDATE_TIMEOUT) as c:
```

```python
RAG_VALIDATE_TIMEOUT = float(os.getenv("RAG_VALIDATE_TIMEOUT", "1800"))
```

Matches the 1500 s already used for `parse_guideline_profile`.

---

### C30 / C31 — per-call Ollama timeout and a usable error message

**Solves D8** (see below).

Batching changed the shape of an Ollama call: one request now covers 12 rules
instead of one. The 300 s cap was sized for single-shot calls and was never
revisited, and a batch that overran surfaced with no diagnosable reason at all:

```python
-    async with httpx.AsyncClient(timeout=300) as client:
+    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
```

```python
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "900"))
```

Unlike the context budgets, this one was **hardcoded**, so it could not be tuned
from `ai_service/.env`.

C31 fixes the error text. `str()` on a timeout is empty, so the student-facing
message read `Local LLM unavailable: ` with no reason — which cost real
debugging time:

```python
raise HTTPException(
    status_code=503,
    detail=f"Local LLM unavailable on semantic batch {batch_no}/{len(batches)}: "
           f"{type(e).__name__}: {e}".rstrip(': '),
) from e
```

Now reads `Local LLM unavailable on semantic batch 2/4: ReadTimeout`, recording
both the failing batch and the exception type into `ai_jobs.error_message`.

---

### Not part of this work

`app/main.py` carries a one-line uncommitted change (`"p": p` in
`student_options`) that predates this investigation and is unrelated to
validation. `test_job.py` was repaired to import `app.db` rather than the
non-existent `app.database`, and takes the job id as an argument.

---

## Result

### End-to-end verification — real UI submission

The authoritative run: a paper submitted through the student UI, processed by
`queue_worker` with nothing else using Ollama.

| | Value |
|---|---|
| Job status | `COMPLETED` |
| Attempts | **1** — no retries |
| `error_message` | empty |
| Duration | 8m58s (19:03:57 → 19:12:55) |
| Report | written, 63,145 bytes, present on disk |
| AI narrative | 12,047 chars |
| Run id | `RAG-2F8542C8BB` |

All 88 rules evaluated: 45 deterministic, 42 semantic, 1 system restriction.
No crash, no timeout, no failed batch.

| Category | Result |
|---|---|
| Formatting Compliance (45) | 10 PASS / 4 FAIL / 14 WARNING / 17 NOT VERIFIED |
| Semantic Compliance (42) | **24 PASS / 2 FAIL / 16 NOT VERIFIED** |
| System Restriction (1) | 1 PASS |
| Overall | 88 checked, 35 passed, 6 failed, 14 warnings, 33 not verified, **63.6 %** |

The semantic layer doing real work — 24 PASS rather than 42 NOT VERIFIED — is
the clearest evidence the D7 context-overflow fix landed.

> **Attribution caveat:** this run changed two variables at once — C30's 900 s
> timeout *and* the absence of contention. It therefore does not isolate which
> mattered. The log evidence in D8 (a `200 OK` under the old 300 s cap, and
> out-of-order batch failures) is what identifies contention as the cause; C30
> is headroom.

### Earlier run on `submission_1_v2_80120022.pdf`

Measured against the old sample thesis (20,470 chars of text) during
development, before C29:

| | Before (crash patched only) | After |
|---|---|---|
| Job outcome | `FAILED`, no report | `REVISION REQUIRED`, report written |
| Formatting Mandatory FAIL | 8 | **4** |
| Semantic results | 42 NOT VERIFIED / 0 PASS | 24 PASS / 2 FAIL / 16 NOT VERIFIED |
| Semantic prompt | ~972,306 chars (~243k tokens) | ~73,548 chars (~21k tokens) |

Its 4 remaining Mandatory formatting failures were all genuine:

| Rule | Expected | Detected |
|---|---|---|
| Page size | A4 (210 × 297 mm) | 215.9 × 279.4 mm (US Letter) |
| Body font size | at least 12 pt | 8.0 pt (page 25, landscape table) |
| Line spacing | 1.5 | 1.16, 1.19 |
| Chapter Heading alignment | centered | left/right |

After C29 the deterministic split on that file moved from 17 PASS / 12 NOT
VERIFIED to 10 PASS / 17 NOT VERIFIED — six vacuous PASSes became honest
NOT VERIFIED, and two qualifier-hidden rules began reporting real WARNINGs.
Mandatory FAILs stayed at 4.

---

## Regression tests

`tests/test_format_validation_rules.py` — 26 tests covering defects D1-D6 and the
heading-matching half of D5 (C29). D7 and D8 are not unit-testable here: both
need a live Ollama, and D8 is a concurrency condition rather than a code path.

```bash
.venv/bin/python -m unittest discover -s tests -p "test_format_validation_rules.py" -v
```

Verified red against the pre-fix code: defects 1 and 2 reproduce as
`ValueError` crashes, defects 3-5 reproduce as false `FAIL` rows.

---

## Open items

Items 1-4 are guideline/profile quality, decided in profile review.
Items 5-6 are engine robustness gaps that remain unfixed.

1. **Contradictory spacing rules.** The guideline says *"spaced at one and a
   half"* (p4) and *"maximum double spacing"* (p5). Both were parsed, so a
   student sees a Mandatory FAIL against 1.5 **and** a Warning against 2.0 for
   the same text. Needs resolution in profile review.
2. **Both abstract word limits fire.** `Abstract (M.S. thesis)` 150 and
   `Abstract (Ph.D. thesis)` 350 are separate Mandatory rules, but a submission
   is one degree. `consistency_check.detected_degree_markers` already reports
   `["Master of Science"]`, so the other variant could be auto-disabled — a
   product decision.
3. **Garbage targets.** `required_section: "Cover page"` was derived from
   *"9. Final binding and Colour"*; a cover page is not a heading, so it warns
   on every submission.
4. **`FAIL` with "Insufficient evidence".** The 2 semantic FAILs report
   `detected: "Insufficient evidence"` because the model returned `FAIL` without
   `detected_evidence`. A FAIL asserting missing evidence is contradictory —
   consider mapping that combination to `NOT VERIFIED`.
5. **Orphaned `PROCESSING` jobs are never reclaimed** (engine gap, not profile
   quality). `queue_worker` only ever selects
   `AIJob.status.in_(["QUEUED", "RETRYING"])`, so a job interrupted mid-flight —
   by an app restart, a `--reload`, or a crash — stays `PROCESSING` forever and
   is never retried. The student's submission then shows `QUEUED` indefinitely
   with no error and no report. Observed twice during this work. A startup sweep
   resetting `PROCESSING` to `RETRYING`, or a stale-`started_at` check, would
   close it.
6. **A late AI failure discards completed deterministic work.** In
   `process_job()` the 45 deterministic results are computed at lines 1798-1810
   but `sv.validation_json` is not written until 1859 and committed at 1871 —
   *after* the AI call at 1841. A transient LLM failure therefore throws away
   formatting results that were already correct, giving the student nothing
   instead of "formatting checked, semantic unavailable". Committing the
   deterministic half before the AI call would degrade gracefully; the
   no-AI-profile path already behaves this way.
5. **Body font size on table pages.** The 8 pt finding comes from one landscape
   table page. Dense data tables below the body size may warrant exclusion from
   the body-font rule.
