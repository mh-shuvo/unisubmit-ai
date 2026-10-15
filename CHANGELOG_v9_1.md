# UniSubmit AI v9.1 - Guideline Parser Reliability Fix

## Fixed
- Guideline parsing now happens before RAG indexing.
- Ollama/Qwen requirement extraction uses JSON mode and temperature 0.
- Qwen reasoning/code-fence wrappers are stripped before JSON parsing.
- Added conservative standard-text fallback extraction for page size, body font, font size, line spacing, margins, page limits, abstract word limit, and required sections.
- Qwen-extracted and fallback rules are merged without duplicate rule types/targets.
- Parser diagnostics are stored in the parsed profile and shown in the Admin UI.
- Parsing errors are surfaced to the Admin instead of silently resulting in zero rules.

## Upgrade
Restart both Core and AI Service after replacing the files, then use **Re-parse & Re-index Guideline** for each existing guideline version.
