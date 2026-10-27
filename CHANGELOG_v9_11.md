# UniSubmit AI v9.11

## Format Validation Engine Fixes (Deterministic Rules)

- Robust text and operator parsing via `_parse_num_op`: handles operator symbols (`>=12`, `≥ 25`), textual phrases (`at least 12`, `no more than 150`), spacing nouns (`single space`, `double spacing`), and word numbers (`double`, `one and a half`), preventing `ValueError` crashes from unparsed human text.
- Relational operator comparison via `_num_ok`: correctly honors `>=` and `<=` conditions (for example, `>= 12 pt` accepts 14 pt body text instead of failing for not being exactly 12 pt).
- Explicit `NOT VERIFIED` status via `_not_verified`: unreadable or unscopeable rules return explicit `NOT VERIFIED` rows instead of silently defaulting to `PASS` or throwing false `FAIL`. Excluded from the compliance-rate denominator to avoid score distortion.
- Target-scoped section page spans via `_section_page_span`: `max_pages` and `min_pages` scoped to a target section (for example, "Abstract must be confined to a single page") measure only the target section span rather than the entire document.
- Target-scoped margin handling: sub-area margin rules that cannot be isolated at the whole-page level report `NOT VERIFIED` with explanatory rationale rather than failing compliant pages.
- Parenthetical qualifier stripping via `_strip_qualifier`: removes parser-generated labels such as `Abstract (M.S. thesis)` and `Abstract (Ph.D. thesis)` so the engine can locate actual document headings.
- Fallback heading candidates via `_heading_candidates`: heading branches retry stripped targets; unmatchable targets report `NOT VERIFIED` rather than passing without inspecting any document lines.

## Semantic Layer & Context Window Optimization

- Eliminated full-document prompt duplication: `_extract_target_section()` returns empty string when no target section is specified instead of copying the entire document text per rule, reducing prompt requirements payload from ~914k characters to ~15.5k characters.
- Batched semantic validation: requests are chunked via `SEMANTIC_BATCH_SIZE = 12`, ensuring prompt and output tokens remain strictly within Ollama's `num_ctx` (16,384) and `num_predict` (4,096) budgets to prevent silent prompt truncation.
- Bounded evidence size: per-rule student evidence capped at `SECTION_EVIDENCE_CHARS = 3000` with shared full-text fallback.
- Streamlined output schema: removed redundant request-mirrored fields (`requirement`, `severity`, source pages) from the LLM response schema to conserve generation tokens.
- Isolated batch failures: an error in one batch leaves only its specific rules as `NOT VERIFIED` without discarding successful batches.
- Silent truncation logging: added prompt-size warnings in `call_ollama()` when input tokens approach the context limit.

## Concurrency, Timeouts & Client Resilience

- Configurable Ollama timeout: introduced `OLLAMA_TIMEOUT` (900s) in `ai_service` with env-override support for batched inference workloads.
- Extended client timeout: increased `RAG_VALIDATE_TIMEOUT` (1800s) in `app/ai_service_client.py` to accommodate multi-batch generation without premature client timeouts.
- Detailed 503 diagnostics: service error responses now include failing batch numbers and exception types rather than blank messages.
- Queue serialization safeguards: documented concurrency constraints between `queue_worker` and inline validation runners (`test_job.py`) to prevent Ollama queue saturation.

## Verification & Regression Suite

- Added 26 unit tests in `tests/test_format_validation_rules.py` covering formatting defect fixes D1-D6 and heading candidate matching.
- Verified end-to-end against live student UI submission: 88 total rules evaluated (45 deterministic, 42 semantic, 1 system restriction) with 0 crashes, 0 timeouts, and 24 validated semantic passes (up from 0).
