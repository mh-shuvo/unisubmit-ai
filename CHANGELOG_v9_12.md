# UniSubmit AI v9.12

## Heading and body-text style checks (`DEF-020`, `DEF-022`, `LIM-007`)

- Fixed `heading_case` / `Title Case`: replaced the `str.title()` comparison,
  which capitalised every word including minor words like `and`/`of`/`the`, with
  `_to_title_case` / `_needs_title_case_fix`, a stop-word-aware check
  (`_SMALL_WORDS`) that lowercases minor words unless they open or close the
  heading, leaves a leading section number untouched, and leaves an existing
  all-caps acronym (`AI`, `EHR`) as-is. Previously, any correctly cased heading
  containing a minor word (`Scope and Significance`) failed the rule; only the
  incorrectly over-capitalised form passed.
- `heading_case` now also recognises `'UPPERCASE'`/`'upper case'` as synonyms
  for `'ALL CAPS'`. Previously an unrecognised value silently passed the rule
  without checking anything.
- Fixed `heading_alignment`: previously only wording starting with `'center'`
  was checked at all; `'flush left'`, `'left-aligned'`, `'right-aligned'`
  guideline wording passed silently regardless of the heading's actual
  position. Added `_normalize_alignment` (lenient wording match) and
  `_heading_alignment` (detection anchored to the page's actual measured
  margins rather than raw page-edge distance), since a long flush-left heading
  on a page with asymmetric margins can otherwise read as centered.
- Added a `body_alignment` validator (`_page_body_alignment`,
  `_is_body_text_line`): a rule type the guideline parser could already emit
  (`'justified'`) but the deterministic engine had no checker for, so it always
  reported `NOT VERIFIED` regardless of the document. Classifies a page's body
  paragraphs as `justified`, `left`, or `right` from how tightly the lines'
  edges sit against the page's measured margins; still reports `NOT VERIFIED`
  when there isn't enough body text on a page, or when the guideline's wording
  can't be classified, rather than guessing.

## Documentation

- Added `docs/tolerance_values.md`: every fuzzy-match tolerance, percentile
  cutoff, and measurement band used by the format-validation engine, with its
  file, line, and the reasoning behind the chosen number.
- Closed `DEF-020` in the defect register, and registered two new entries
  found and fixed in the same pass: `DEF-022` (`heading_alignment`'s
  center-only vocabulary) and `LIM-007` (`body_alignment`'s missing
  validator). `docs/defects/README.md` counts, theme groupings and bucket
  tables updated accordingly (29 entries: 22 defects, 7 limitations; 5 open,
  24 closed).
- Updated `docs/open_defects.md` to mark `OPEN-1` and its noted vocabulary
  gaps as resolved, pointing at the register entries that now carry the full
  narrative, without rewriting the page's original reproduction detail.

## Verification & Regression Suite

- Added 20 unit tests in `tests/test_format_validation_rules.py`: `TitleCase`
  (title-case conversion and the UPPERCASE synonym), `HeadingAlignment`
  (left/right/center recognition, and the margin-asymmetry false positive),
  and `BodyAlignment` (justified/left/right classification, including the
  right-aligned-misread-as-justified case and both `NOT VERIFIED` guards).
  Full suite: 66 tests passing, up from 46.
