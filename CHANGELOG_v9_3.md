# UniSubmit AI v9.3 - Parser Hardening

- Upgraded AI service to 0.6.3-qwen35-35b-parser-hardened.
- Added one JSON repair retry when Qwen returns malformed structured output.
- Expanded the standard-text fallback parser to extract atomic rules for margins, chapter/heading styles, body formatting, line spacing, abstract word range, required sections, and prohibited abstract elements.
- Added cover/title-page template inference from page-content anchors even without a literal "Cover Page" label.
- Added parser coverage audit to flag guideline categories that appear in text but are missing from extracted rules.
- Added program-guideline consistency check and activation blocking for obvious degree-level mismatch (for example M.Sc. configuration with B.Sc. guideline text).
- Added new deterministic rule types: heading_case, body_alignment, body_regular_required, figure_caption_position, table_caption_position, and section_forbidden_element.
- Extended validation support for heading capitalization, regular body-text style, and prohibited abstract elements.
- Admin UI now shows coverage audit and program-guideline mismatch diagnostics.
