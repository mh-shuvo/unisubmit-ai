# UniSubmit AI v8

## Mandatory guideline-derived formatting compliance

- AI service now extracts an explicit formatting profile from the indexed guideline using Qwen + RAG.
- Only requirements explicitly supported by the approved guideline are added to the profile.
- Supported deterministic checks include A4 page size, dominant body font, dominant body font size, left/right/top/bottom margins, and estimated line spacing.
- Formatting rules are mandatory and participate in PASS/REVISION REQUIRED decisions.
- Every formatting issue records affected page numbers, detected value, location, representative text/snippet, and PDF bounding box where available.
- Same-type issues across multiple pages are consolidated into one report issue while preserving individual occurrences.
- Rich HTML report includes a dedicated Mandatory Formatting Compliance section with expandable occurrence details.
- Downloadable PDF report also includes consolidated formatting findings and affected pages.

## Important

After upgrading an existing guideline version, re-index the guideline once. Re-indexing regenerates the RAG collection and extracts the formatting profile used by the deterministic validator.
