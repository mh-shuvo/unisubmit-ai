# UniSubmit AI v9.10

## Semantic Rule Management and Deduplication

- Duplicate semantic requirements are canonicalized by normalized requirement + target section.
- Duplicate guideline source pages are merged into one rule (for example, Pages 1, 2) instead of creating repeated report cards.
- Existing duplicated profiles are normalized at runtime; re-parsing persists the deduplicated profile.
- Manual semantic rule CRUD added for Department/Institute Admins.
- Manual rules support target section, severity, expected evidence, edit, delete, and activation workflow.
- Manual semantic rules survive guideline re-parse/re-index operations.
- Manual and guideline-derived semantic rules use the same RAG + Qwen validation pipeline and report status model.
- Semantic evaluator now receives targeted student-section text and is instructed to use NOT VERIFIED only for genuine missing/ambiguous evidence.
- HTML and PDF reports show merged guideline page sources and distinguish manual semantic rules from guideline-derived rules.
