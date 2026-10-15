# UniSubmit AI v9 - Guideline-First Validation

## Major changes
- Added `/guideline/profile` to parse the approved guideline PDF directly before validation.
- Qwen extracts only explicit guideline requirements; unsupported/inferred defaults are not created.
- Rules are split into deterministic rules and semantic requirements.
- Admin can review and activate the parsed rule profile without manually retyping the guideline.
- Deterministic validator now supports A4/page size, body font, body font size, margins, estimated line spacing, min/max page count, required sections, section word limits, page numbering and selected heading rules.
- Semantic requirements drive targeted BGE-M3/Qdrant retrieval before Qwen compliance review.
- Labelled cover/title-page sample pages are captured as normalized layout profiles; major first-page anchors are compared with tolerance.
- Same-type issues remain consolidated while preserving every affected page, occurrence, snippet, bounding box, expected/detected value and guideline source.
- Guideline profile status is now Parsed - Review Required -> Active. Mandatory parsed rules affect PASS/REVISION REQUIRED only after activation.

## Upgrade
Re-index each guideline in the Admin portal, review the parsed rules, then click **Activate Parsed Rules**.
