# UniSubmit AI v9.4 - Validation Hardening

- Replaced page-extent margin estimation with robust body-text-frame intrusion checking.
- Sparse pages no longer create false bottom/right margin failures.
- Added tolerance-aware mandatory margin validation.
- Added normalized CHAPTER/section heading matching.
- TOC entries are ignored when proving actual section presence.
- Reworked cover-page validation to compare semantic anchors and relative layout instead of absolute guideline-page coordinates.
- Added per-element cover-page diagnostics.
- AI service version 0.6.4-qwen35-35b-validator-hardened.

## Regression check
Tested against the supplied `Irtiza.pdf` fixture behavior:
- A4/page body margins no longer generate false failures when body text respects the required boundary.
- `CHAPTER 1: INTRODUCTION` through `CHAPTER 6: CONCLUSION AND FUTURE WORK` satisfy normalized required-section rules.
- TOC entries are not used as proof of section existence.
- An inline cover template in a guideline is treated as structure-only, not as an absolute visual-coordinate sample.
- Abstract minimum-word rule still correctly fails when the abstract contains only 68 words.
