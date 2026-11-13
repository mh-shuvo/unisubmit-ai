# Defect register

Every code defect and standing limitation found in UniSubmit AI, in one
namespace, with one metadata format. This is the register the thesis draws on
and the bucket new findings go into.

Read [`CONVENTIONS.md`](CONVENTIONS.md) before adding an entry.
[`TEMPLATE.md`](TEMPLATE.md) is the skeleton.

**A validation result is not a defect.** A student document failing rule 31 is
the system working, and belongs in a validation report. This register records
faults in the project's own code (`DEF-nnn`) and understood constraints that are
not faults (`LIM-nnn`).

30 entries: 23 defects and 7 limitations. 5 open, 25 closed.

---

## Open bucket

The working queue. Fixed one at a time, newest constraint last.

| ID | Title | Kind | Class | Severity | Blocks | Added |
|----|-------|------|-------|----------|--------|-------|
| [LIM-002](LIM-002.md) | Existing `ai_profiles` rows still hold the stale model name | limitation | `config-resolution` | major | Any installation predating `0fc7e9c` still reproduces the 404 | 2026-10-18 |
| [LIM-003](LIM-003.md) | One Ollama instance is a shared serial resource with no admission control | limitation | `resource-contention` | major | Any throughput claim; running `test_job.py` beside the live worker | 2026-10-27 |
| [LIM-005](LIM-005.md) | Reproduction artefacts the defect documents cite no longer exist | limitation | `observability-gap` | blocking | Replaying any quantitative claim in the episode documents | 2026-11-02 |
| [DEF-021](DEF-021.md) | An unavailable repair service is reported as invalid model output | defect | `observability-gap` | major | Nothing; misdirects diagnosis of a model-service outage | 2026-11-02 |
| [LIM-006](LIM-006.md) | The test suite runs only when invoked by hand | limitation | `observability-gap` | minor | Nothing; weakens the standing regression guarantee | 2026-11-02 |

---

## Closed

In discovery order. `Fixed in` names the release where one exists, and the
commit otherwise; `DEF-001` to `DEF-006` predate this repository and are
recorded only in `VALIDATION_FIX_v9_10_1.md`.

| ID | Title | Kind | Class | ODC type | Fixed in | Test |
|----|-------|------|-------|----------|----------|------|
| [DEF-001](DEF-001.md) | Semantic validation parsed model output with strict `json.loads` | defect | `llm-output-contract` | Checking | v9.10.1 | `test_qwen_thinking_fence_and_preamble` |
| [DEF-002](DEF-002.md) | Valid JSON with the wrong root or field types crashed result processing | defect | `llm-output-contract` | Checking | v9.10.1 | `test_invalid_shape_repaired_once` |
| [DEF-003](DEF-003.md) | Every retrieval exception became a 500 with no traceback | defect | `observability-gap` | Checking | v9.10.1 | `test_retrieval_failure_is_logged_and_503` |
| [DEF-004](DEF-004.md) | Qdrant `query_points` was called unconditionally | defect | `dependency-api-drift` | Interface | v9.10.1 | `test_legacy_rest_with_new_client` |
| [DEF-005](DEF-005.md) | The core client timed out before the service it was waiting on | defect | `timeout-misalignment` | Assignment/Initialisation | v9.10.1 | none |
| [DEF-006](DEF-006.md) | Malformed `source_pages` and duplicate semantic IDs misassociated results | defect | `llm-output-contract` | Algorithm/Method | v9.10.1 | `test_duplicate_ids_assigned_unique_ids` |
| [DEF-007](DEF-007.md) | Guideline parse client and server budgets were misaligned | defect | `timeout-misalignment` | Timing/Serialisation | `98cb791` | none |
| [DEF-008](DEF-008.md) | `float()` was applied to guideline-parser text values | defect | `nl-to-rule-translation` | Algorithm/Method | v9.11, `6e394f9` | `ParseRuleValues` |
| [DEF-009](DEF-009.md) | An unparseable value silently became PASS | defect | `fail-open` | Checking | v9.11, `6e394f9` | `NoSilentPass` |
| [DEF-010](DEF-010.md) | `max_pages` ignored the rule's `target` | defect | `scope-leakage` | Algorithm/Method | v9.11, `6e394f9` | `SectionScoping` |
| [DEF-011](DEF-011.md) | `margin_*_mm` and `line_spacing` ignored the rule's `target` | defect | `scope-leakage` | Algorithm/Method | v9.11, `6e394f9` | `SectionScoping` |
| [DEF-012](DEF-012.md) | Parenthetical targets could never match a heading | defect | `nl-to-rule-translation` | Algorithm/Method | v9.11, `6e394f9` | `TargetHandling` |
| [DEF-013](DEF-013.md) | `>=` was validated as `==` | defect | `nl-to-rule-translation` | Checking | v9.11, `6e394f9` | `OperatorSemantics` |
| [DEF-014](DEF-014.md) | The semantic layer returned NOT VERIFIED for every requirement | defect | `llm-context-budget` | Algorithm/Method | v9.11, `6e394f9` | none |
| [DEF-015](DEF-015.md) | Concurrent Ollama consumers time each other out | defect | `resource-contention` | Timing/Serialisation | v9.11, `6e394f9` | none |
| [DEF-016](DEF-016.md) | The configured Ollama model never reached the code that used it | defect | `config-resolution` | Assignment/Initialisation | `0fc7e9c` | none |
| [DEF-017](DEF-017.md) | Double spacing could not be measured, so the rule had no passing path | defect | `unsatisfiable-constraint` | Algorithm/Method | `ce8f86a` | `PdfMeasurement`, `LineSpacingReporting` |
| [DEF-018](DEF-018.md) | Chapter and section rules shared one line list | defect | `unsatisfiable-constraint` | Algorithm/Method | `ce8f86a` | `HeadingLevelSeparation` |
| [DEF-019](DEF-019.md) | Font weight was erased before the bold check ran | defect | `lossy-normalisation` | Algorithm/Method | `ce8f86a` | `FontWeightDetection` |
| [LIM-001](LIM-001.md) | The services did not verify their dependencies at startup | limitation | `observability-gap` | n/a | `b38c6d1` | none |
| [LIM-004](LIM-004.md) | The context window bounds batch size and evidence budget | limitation | `llm-context-budget` | n/a | accepted, `wontfix` | none |
| [DEF-020](DEF-020.md) | `heading_case` rejects correct title case | defect | `nl-to-rule-translation` | Algorithm/Method | `a44f94f` | `TitleCase` |
| [DEF-022](DEF-022.md) | `heading_alignment` only ever checked for centering | defect | `nl-to-rule-translation` | Checking | `fc2b81c` | `HeadingAlignment` |
| [LIM-007](LIM-007.md) | `body_alignment` had no deterministic validator | limitation | `nl-to-rule-translation` | n/a | `fc2b81c` | `BodyAlignment` |
| [DEF-023](DEF-023.md) | A dual-level guideline is blocked for every program | defect | `nl-to-rule-translation` | Checking | `4f49fc3` | `ProgramGuidelineConsistencyTests` |

`DEF-007`, `DEF-016`, `DEF-017` to `DEF-019` and `LIM-001` are fixed but are
not yet described in any `CHANGELOG_v*.md`; `DEF-023` is described in
`CHANGELOG_v9_13.md`. `DEF-017` to `DEF-019` are on branch
`unsatisfiable-heading-and-spacing-rules`.

---

## By theme

The chapter skeleton. Each group can be written as one section.

**T1 — Translating natural-language guidelines into executable rules** (7)
[DEF-008](DEF-008.md) · [DEF-012](DEF-012.md) · [DEF-013](DEF-013.md) · [DEF-020](DEF-020.md) · [DEF-022](DEF-022.md) · [LIM-007](LIM-007.md) · [DEF-023](DEF-023.md)

The guideline is written for a human, a language model turns it into a rule,
and the engine must then accept whatever vocabulary the model chose. A
requirement is a predicate, and its number, its operator and its target were
each lost at a different point in the pipeline. `DEF-020`, `DEF-022` and
`LIM-007` are one more shape of the same problem: a styling concept
(`Title Case`, `flush left`, `justified`) the guideline could state plainly,
that the checker's own vocabulary did not yet cover. `DEF-023` is the
fail-closed counterpart: a guideline stating it serves both degree levels,
rejected by the very gate meant to catch wrong uploads.

**T2 — Measurement validity: scope, satisfiability and normalisation** (5)
[DEF-010](DEF-010.md) · [DEF-011](DEF-011.md) · [DEF-017](DEF-017.md) · [DEF-018](DEF-018.md) · [DEF-019](DEF-019.md)

Every number the engine computed was correct and the results were still wrong,
because the unit of measurement did not match the unit the rule was about. Two
of these were unsatisfiable: no document could pass them.

**T3 — Safe failure semantics** (1)
[DEF-009](DEF-009.md)

Fail-open against an explicit `NOT VERIFIED`, and what belongs in the
compliance-rate denominator. One entry, and the principle the rest of the
system rests on; [DEF-002](DEF-002.md) had to establish it again in the
semantic layer.

**T4 — Operating a local LLM: context budget and output contract** (5)
[DEF-001](DEF-001.md) · [DEF-002](DEF-002.md) · [DEF-006](DEF-006.md) · [DEF-014](DEF-014.md) · [LIM-004](LIM-004.md)

Output that is only partly specified, and a context window that truncates from
the front without saying so. [DEF-014](DEF-014.md) is the case worth building
the section around.

**T5 — Integration brittleness** (7)
[DEF-004](DEF-004.md) · [DEF-005](DEF-005.md) · [DEF-007](DEF-007.md) · [DEF-015](DEF-015.md) · [DEF-016](DEF-016.md) · [LIM-002](LIM-002.md) · [LIM-003](LIM-003.md)

Configuration, timeouts, dependency drift and contention. The timeout
misalignment appears three times on three call paths, which is the argument for
treating client and server budgets as one declared pair.

**T6 — Observability and reproducibility** (5)
[DEF-003](DEF-003.md) · [DEF-021](DEF-021.md) · [LIM-001](LIM-001.md) · [LIM-005](LIM-005.md) · [LIM-006](LIM-006.md)

The lowest-severity group and the one with the largest effect on the cost of
everything else. [LIM-005](LIM-005.md) is the item with direct bearing on the
thesis submission, and [DEF-021](DEF-021.md) is [LIM-006](LIM-006.md)
demonstrated: a regression its own test would have caught, undetected for
sixteen days because nothing runs that test.

---

## Counts

Maintained by hand. Update them in the same edit that adds or closes an entry.

### By class, all 30 entries

| Class | Count |
|-------|-------|
| `nl-to-rule-translation` | 7 |
| `observability-gap` | 5 |
| `llm-output-contract` | 3 |
| `scope-leakage` | 2 |
| `unsatisfiable-constraint` | 2 |
| `llm-context-budget` | 2 |
| `config-resolution` | 2 |
| `timeout-misalignment` | 2 |
| `resource-contention` | 2 |
| `fail-open` | 1 |
| `lossy-normalisation` | 1 |
| `dependency-api-drift` | 1 |
| **Total** | **30** |

### By ODC defect type, the 23 `DEF` entries

Limitations carry no ODC triple, so these sum to 23 rather than 30.

| Defect type | Count |
|-------------|-------|
| Algorithm/Method | 10 |
| Checking | 8 |
| Assignment/Initialisation | 2 |
| Timing/Serialisation | 2 |
| Interface | 1 |
| **Total** | **23** |

| Qualifier | Count |
|-----------|-------|
| Incorrect | 14 |
| Missing | 9 |
| Extraneous | 0 |

### By trigger, the 23 `DEF` entries

The trigger is also the detection method: how the defect actually surfaced.

| Trigger | Count |
|---------|-------|
| Design Conformance | 8 |
| Normal Mode | 5 |
| Logic/Flow | 3 |
| Workload/Stress | 2 |
| Concurrency | 1 |
| Hardware/Software Configuration | 1 |
| Lateral Compatibility | 1 |
| Complex Path | 1 |
| Rare Situation | 1 |
| **Total** | **23** |

Worth noting when this table is quoted: 8 of 23 were found by comparing
behaviour against the guideline's intent, and all 8 of those are in T1 and T2.
Building a document that ought to pass found defects that examining failing
results did not, because an unsatisfiable rule and a widely failed rule look
identical in aggregate statistics.

### By severity, all 30 entries

| Severity | Count |
|----------|-------|
| blocking | 14 |
| major | 12 |
| minor | 4 |
| **Total** | **30** |

---

## Where the long-form narrative lives

Entries that predate this register summarise and link into the document that
first recorded them. Those documents keep their own framing and are not
duplicated here.

| Document | Entries |
|----------|---------|
| [`../../VALIDATION_FIX_v9_10_1.md`](../../VALIDATION_FIX_v9_10_1.md) | `DEF-001`-`DEF-006` |
| [`../validation_defects_and_fixes.md`](../validation_defects_and_fixes.md) | `DEF-008`-`DEF-015`, with the `C1`-`C31` map of code changes |
| [`../unsatisfiable_rules_defects_and_fixes.md`](../unsatisfiable_rules_defects_and_fixes.md) | `DEF-017`-`DEF-019`, with the walkthrough in `validator_unsatisfiable_rules.html` |
| [`../open_defects.md`](../open_defects.md) | `DEF-020` |
| Commit messages | `DEF-007` (`98cb791`), `DEF-016` (`0fc7e9c`), `LIM-001` (`b38c6d1`) |

`DEF-021` was found while building this register and carries its full narrative
in its own entry, as every entry registered from now on does.

Entries registered from now on carry their full narrative in their own file.
