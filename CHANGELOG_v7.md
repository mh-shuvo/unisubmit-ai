# UniSubmit AI v7 – Rich Validation Reports

## Student report experience
- Added a dedicated responsive HTML report page for each validated submission version.
- Parses the AI service's constrained Markdown output into separate sections:
  - Guideline Compliance Assessment
  - Required Corrections
  - AI Improvement Suggestions
  - Grounding & Evidence Note
- Additional deterministic restrictions are shown separately from AI/RAG findings.
- Source markers such as `[SOURCE 1 | Page 3]` are rendered as readable evidence chips.
- Added `View full report` and `Download PDF` actions to each submission version.
- PDF reports now use the same structured sections and are regenerated on download, so older validation runs also receive the updated PDF layout.

## AI response contract
- RAG validation now asks Qwen for clean Markdown with fixed headings, one finding per bullet, and source/page evidence for compliance claims.
- Raw AI output is still retained for auditability; parsing is presentation-only.

## Responsive design
- Rich report layout includes status hero, metadata cards, compliance/correction cards, improvement suggestion cards, evidence panel, and an academic-responsibility note.
- Desktop uses a report + sticky summary layout; tablet/mobile collapse cleanly into a single column.
