# DEF-nnn: one line, what is wrong, in the present tense

Copy this file to `DEF-nnn.md` or `LIM-nnn.md` and fill it in. Field meanings
and allowed values are in [`CONVENTIONS.md`](CONVENTIONS.md). Delete this
paragraph and any section that genuinely does not apply, but say `none` rather
than dropping a metadata field.

**ID:** DEF-nnn
**Legacy:** none
**Kind:** defect
**Status:** open
**Severity:** major
**Class:** nl-to-rule-translation
**ODC:** Algorithm/Method, Incorrect; trigger: Normal Mode
**Component:** `path/to/file.py` :: `function_name`
**Found:** how it surfaced, concretely
**Added:** YYYY-MM-DD
**Fixed in:** not fixed
**Test:** none
**Theme:** T1

---

## Symptom

What a user or an operator sees. No diagnosis here, only the observation, so
that the entry still makes sense if the diagnosis turns out to be wrong.

## Root cause

Why it happens, at the level of the specific line or decision. Name the file
and the function. If this is not yet known, write `not yet diagnosed` and say
what has been ruled out.

## Reproduction

Enough to replay it: the input file, the guideline version, the job number, the
command. Commit any probe script to the repository rather than leaving it in a
scratch directory.

```
command or snippet
```

## Fix

What changed and why that is the right place for the change. Written only once
the fix exists.

## Verified

The evidence that it is fixed, with before and after numbers rather than an
assertion. For example, rule counts, compliance rate, prompt size, elapsed time.

## Tests

The regression test, by file and name, and confirmation that it was seen red by
reverting only this fix and green again with it.

## For the thesis

Why this one is interesting beyond the repair: what it says about building a
compliance checker on top of a language model, which theme it supports, and any
classification judgement that was not obvious.
