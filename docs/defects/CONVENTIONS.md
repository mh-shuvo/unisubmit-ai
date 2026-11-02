# Defect register conventions

How entries in this register are written, so that any two of them can be
compared and counted. Read this before adding an entry.

The register itself is [`README.md`](README.md). The skeleton for a new entry is
[`TEMPLATE.md`](TEMPLATE.md).

---

## What the register records, and what it does not

A **validation result is not a defect.** A student document that fails rule 31
is the system working as designed, and belongs in a validation report, not here.
Verification output of that kind is evidence about documents; this register is
evidence about the software.

Two kinds of entry:

| Kind | Prefix | Meaning |
|------|--------|---------|
| Defect | `DEF-nnn` | A fault in this project's own code: a crash, a wrong result, a rule no document could satisfy, or a configured value that never reached the code that needed it |
| Limitation | `LIM-nnn` | An understood constraint that is not a fault and may never be removed, such as the model context ceiling or the absence of continuous integration |

A limitation can be closed by work that removes it, as `LIM-001` was. A
limitation that is deliberately accepted is closed as `wontfix` and keeps its
entry, because an accepted constraint is a result the thesis has to state.

---

## Identifiers

- `DEF-nnn` and `LIM-nnn`, zero padded to three digits.
- Numbered **in order of discovery**, not of severity or of fixing, so that
  reading the register in ID order reads the project's history in order.
- Permanent. An ID is never reused, renumbered, or retired, even if the entry
  turns out to be a duplicate or a mistake. Withdraw it with status `wontfix`
  and say why.
- Three earlier schemes are preserved as the `Legacy` field: `D1`-`D8` in
  `validation_defects_and_fixes.md`, `Defect 1`-`Defect 3` in
  `unsatisfiable_rules_defects_and_fixes.md`, and `OPEN-1` in
  `open_defects.md`. Every legacy identifier maps to exactly one entry here.

---

## The metadata block

Every entry opens with this block, in this field order. No field is optional;
write `none` or `n/a` rather than leaving one out, because a missing field and
an empty field are not the same claim.

| Field | Meaning |
|-------|---------|
| `ID` | This entry's identifier |
| `Legacy` | The earlier identifier and the document it came from, or `none` |
| `Kind` | `defect` or `limitation` |
| `Status` | See below |
| `Severity` | See below |
| `Class` | One value from the primary taxonomy below |
| `ODC` | Defect type, qualifier and trigger. `n/a (limitation)` for a `LIM` entry |
| `Component` | File and function, as `path :: function` |
| `Found` | How it surfaced, concretely enough to be repeated |
| `Added` | Date the entry was registered, `YYYY-MM-DD` |
| `Fixed in` | Release and commit, or `not fixed` |
| `Test` | The regression test that fails if the defect returns, or `none` |
| `Theme` | One thesis theme, `T1`-`T6` |

### Status

| Status | Meaning |
|--------|---------|
| `open` | Registered, understood well enough to be picked up, not being worked on |
| `investigating` | Being diagnosed now; the root cause in the entry is provisional |
| `fixed` | Fixed, verified, and guarded by a named test |
| `deferred` | Real, reproduced, and consciously postponed; says what it blocks |
| `wontfix` | Will not be changed; says why, and what the consequence is |

### Severity

| Severity | Meaning |
|----------|---------|
| `blocking` | No usable result is produced, or a mandatory rule no document can pass |
| `major` | A wrong result, or a failure under a condition that occurs in normal use |
| `minor` | Correct results, but diagnosis, performance or clarity suffers |

Severity describes the effect on a user of the system, not the effort to fix it.

---

## Primary taxonomy: defect class

This axis is specific to this project. The classes were derived after the fact
from the defects actually found, not chosen in advance from a published list,
which is the point of having a secondary axis as well.

| Class | Meaning |
|-------|---------|
| `nl-to-rule-translation` | Human guideline text was turned into a formal rule incorrectly: a value, an operator, or a target name lost its meaning on the way in |
| `scope-leakage` | A measurement was correct but taken at the wrong granularity, so a rule about one section or area was decided from the whole document |
| `fail-open` | An undecidable or unmeasurable rule was reported as compliant, so the absence of evidence read as evidence of compliance |
| `unsatisfiable-constraint` | The measurement contradicted the rule, so no input could satisfy it. Distinct from a strict rule: there was no passing path at all |
| `lossy-normalisation` | Information was destroyed by a normalising step before the consumer that needed it ran |
| `llm-context-budget` | Prompt or output exceeded the model's budget and was truncated, silently |
| `llm-output-contract` | Model output was not validated before use, or was parsed too strictly to accept output the model legitimately produces |
| `config-resolution` | A configured value never reached the code that reads it, so a default was used in its place |
| `timeout-misalignment` | A client budget was shorter than the server-side budget it waits on, so the caller gave up on work still in progress |
| `resource-contention` | A shared serial dependency was saturated by a second caller |
| `dependency-api-drift` | A library or server API was used without regard to the version actually installed |
| `observability-gap` | The failure was invisible, unattributable, or not replayable afterwards |

Choose exactly one class, the one that describes the **cause**, not the symptom.
A context overflow that produced unparseable output is `llm-context-budget`, not
`llm-output-contract`. When two classes fit equally, say so in the entry's
`## For the thesis` section rather than inventing a thirteenth class.

---

## Secondary axis: orthogonal defect classification

Each `DEF` entry also carries an ODC triple, so the register can be compared
with published defect data instead of only with itself. ODC needs a citation in
the thesis; decide between Chillarege's ODC and IEEE 1044 before any count from
this register is quoted in the text.

**Defect type**, what kind of change the fix was:

`Assignment/Initialisation`, `Checking`, `Algorithm/Method`, `Function`,
`Interface`, `Timing/Serialisation`, `Relationship`, `Build/Package`.

**Qualifier**, what was wrong with the original code: `Missing`, `Incorrect`,
or `Extraneous`.

**Trigger**, the condition that exposed it. This doubles as the detection
method, and is the axis that supports a claim about *how* a class of defect gets
found. Assign it from how the defect actually surfaced, not from how it could
have been found:

| Trigger | Used here for |
|---------|---------------|
| `Normal Mode` | Surfaced by ordinary use, a real submission through the UI |
| `Workload/Stress` | Surfaced by size: a long document, a large prompt, a slow run |
| `Concurrency` | Surfaced by two callers at once |
| `Hardware/Software Configuration` | Surfaced by the environment, not the code path |
| `Lateral Compatibility` | Surfaced by another component's version |
| `Design Conformance` | Surfaced by comparing behaviour against the guideline's intent |
| `Logic/Flow` | Surfaced by reading the code |
| `Rare Situation` | Surfaced by an unusual but legitimate input shape |
| `Complex Path` | Surfaced by an existing unit test exercising an error or recovery path |

### How the two axes relate

| Class | Usual ODC defect type |
|-------|----------------------|
| `nl-to-rule-translation` | `Algorithm/Method` or `Checking` |
| `scope-leakage` | `Algorithm/Method` |
| `fail-open` | `Checking`, qualifier `Missing` |
| `unsatisfiable-constraint` | `Algorithm/Method` |
| `lossy-normalisation` | `Algorithm/Method` |
| `llm-context-budget` | `Algorithm/Method` |
| `llm-output-contract` | `Checking` |
| `config-resolution` | `Assignment/Initialisation` |
| `timeout-misalignment` | `Assignment/Initialisation` or `Timing/Serialisation` |
| `resource-contention` | `Timing/Serialisation` |
| `dependency-api-drift` | `Interface` |
| `observability-gap` | `Checking` or `Function` |

The table is guidance, not a constraint. Where an entry departs from it, the
entry says why.

---

## Thesis themes

Each entry belongs to one theme. The themes are the chapter skeleton: a section
can be written from one theme's group of entries without rereading the rest.

| Theme | Title |
|-------|-------|
| `T1` | Translating natural-language guidelines into executable rules |
| `T2` | Measurement validity: scope, satisfiability and normalisation |
| `T3` | Safe failure semantics: fail-open against an explicit NOT VERIFIED, and what belongs in the compliance-rate denominator |
| `T4` | Operating a local LLM: context budget and output contract |
| `T5` | Integration brittleness: configuration, timeouts, dependency drift and contention |
| `T6` | Observability and reproducibility |

---

## Workflow

The register is a bucket first and a record second. A finding goes in before it
is understood, not after it is fixed.

**On finding something**

1. Take the next free ID.
2. Copy [`TEMPLATE.md`](TEMPLATE.md) to `docs/defects/DEF-nnn.md`.
3. Fill in `## Symptom` and `## Reproduction` the same day, while the
   circumstances are still known. `## Root cause` may say `not yet diagnosed`.
4. Set `Status: open`, add the row to the **Open bucket** table in
   [`README.md`](README.md), and update the counts.

Reproduction notes are the part that decays fastest. Record the file, the
submission, the guideline version and the job number, and keep any probe script
in the repository rather than in a scratch directory. See `LIM-005` for what
happens otherwise.

**On fixing one**

Fixes happen one entry at a time, by hand or by naming the ID in a request to an
agent. Closing an entry means all of:

1. Fill in `## Fix` and `## Verified`, with the before and after numbers.
2. Add a regression test that fails without the fix. Confirm it is red by
   reverting only that fix, then green again with it.
3. Set `Status: fixed`, and fill in `Fixed in` and `Test`.
4. Move the row from the Open bucket to the Closed table, and update the counts.
5. Add a bullet to the release changelog, `CHANGELOG_v*.md`.

**Conventions for new work**

- Put the ID in the commit subject or body, for example
  `fix(validation): scope page limits to the target section (DEF-010)`.
  The eight commits that predate this register are linked by SHA from the
  entries instead; their messages were not rewritten.
- Name a regression test after what it guarantees, and cite the ID in its
  docstring, so the chain from entry to commit to test can be checked
  mechanically later.

---

## Where the narrative lives

Entries registered before this register existed are summaries that link into the
long-form account in the document that first recorded them:

- [`../validation_defects_and_fixes.md`](../validation_defects_and_fixes.md),
  `DEF-008`-`DEF-015`, with the `C1`-`C31` map of code changes
- [`../unsatisfiable_rules_defects_and_fixes.md`](../unsatisfiable_rules_defects_and_fixes.md),
  `DEF-017`-`DEF-019`
- [`../open_defects.md`](../open_defects.md), `DEF-020`
- [`../../VALIDATION_FIX_v9_10_1.md`](../../VALIDATION_FIX_v9_10_1.md),
  `DEF-001`-`DEF-006`

Those documents keep their own framing, their starting state, their verification
results and their change maps, and are not duplicated here.

Entries registered from now on carry their full narrative in their own file,
because there is no episode document for them to point at.
