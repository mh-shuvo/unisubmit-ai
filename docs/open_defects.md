# Open defects

Known defects that were understood and reproduced here. All entries on this
page are now resolved; this document is kept for its original reproduction
detail rather than rewritten, per the register's own convention that a
superseding document keeps its starting framing. The register at
[`defects/README.md`](defects/README.md) is the current source of truth for
status.

Fixed defects are recorded in `unsatisfiable_rules_defects_and_fixes.md` and
`validation_defects_and_fixes.md`.

Every defect and limitation now carries an identifier in the register at
[`defects/README.md`](defects/README.md), which is the place to add a new
finding. `OPEN-1` below is [DEF-020](defects/DEF-020.md) there; the two
"Related vocabulary gaps" this page noted alongside it (`heading_alignment`
and `body_alignment`) are now [DEF-022](defects/DEF-022.md) and
[LIM-007](defects/LIM-007.md) respectively.

| ID | Register | Title | Severity | Blocks | Status |
|----|----------|-------|----------|--------|--------|
| OPEN-1 | [DEF-020](defects/DEF-020.md) | `heading_case` rejects correct title case | Mandatory rule, always fails | Restoring chapter and section numbering in submissions | **Resolved** (`a44f94f`) |

---

## OPEN-1: `heading_case` rejects correct title case

**File:** `app/format_validation.py`
**Function:** `validate_format_profile`, the `kind == 'heading_case'` branch
**Rule affected:** rule 31, `heading_case` / `Section Heading` / `Title Case`
**Found:** while verifying the fixes for defects 1-3, by testing whether a
numbered thesis would validate
**Latent until:** defect 2 is fixed, which it now is. This defect is therefore
live for any submission that numbers its sections.

### The defect

Title case is tested by comparing the heading with Python's `str.title()`:

```python
elif str(expected).lower() == 'title case' and txt and txt != txt.title():
    occ.append(_occ(pg['page'], txt, 'Not Title Case', txt, ln.get('bbox')))
```

`str.title()` capitalises the first letter of **every** word, including articles,
conjunctions and prepositions. Correct title case does not. So the check demands
a capitalisation that is wrong, and rejects the one that is right:

```
'1.1 Background'.title()               == '1.1 Background'               -> passes
'1.5 Scope and Significance'.title()   == '1.5 Scope And Significance'   -> FAILS
'2.1 Categories of Reported Evidence'  -> wants 'Categories Of Reported' -> FAILS
```

Any section heading containing `and`, `of`, `the`, `in`, `for`, `with`, `to`, `a`
or `an` fails. In practice that is most section headings in any thesis.

### Reproduction

```sh
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, '/opt/homebrew/var/www/pvs/unisubmit_ai')
from app.format_validation import validate_all_profile_rules
pg = {'page': 1, 'width_pt': 595.0, 'height_pt': 842.0, 'width_mm': 210.0,
      'height_mm': 297.0, 'dominant_font': 'Times New Roman',
      'dominant_font_size': 12.0, 'estimated_line_spacing': 2.0,
      'margins': {}, 'spans': [], 'lines': [
          {'text': '1.1 Background', 'bbox': (99.0, 70.0, 220.0, 88.0), 'font_size': 12.0},
          {'text': '1.5 Scope and Significance', 'bbox': (99.0, 130.0, 300.0, 148.0), 'font_size': 12.0},
      ]}
info = {'page_count': 1, 'pages': [pg], 'text': '', 'searchable': True,
        'page_sizes': [(595.0, 842.0)]}
rule = {'type': 'heading_case', 'target': 'Section Heading', 'value': 'Title Case',
        'final_severity': 'Mandatory', 'enabled': True, 'source_page': 2}
for r in validate_all_profile_rules({'deterministic_rules': [rule]}, info):
    print(r['status'], '|', r['detected'])
    for o in r.get('occurrences') or []:
        print('   ', o['location'], '->', o['detected'])
PY
```

Current output:

```
FAIL | Not Title Case
     1.5 Scope and Significance -> Not Title Case
```

### Why it matters

With defects 1-3 fixed, a properly numbered, bold, correctly cased thesis now
passes **11 of the 12 heading rules**. This is the only one left:

```
PASS  Chapter Heading: heading alignment            expected=centered
PASS  Chapter Heading: heading font size            expected=14 pt
PASS  Chapter Heading: heading bold                 expected=Bold
PASS  Chapter Heading: heading case                 expected=UPPERCASE
PASS  Section Heading: heading alignment            expected=flush left
PASS  Section Heading: heading font size            expected=12 pt
PASS  Section Heading: heading bold                 expected=Bold
FAIL  Section Heading: Heading Case                 expected=Title Case
PASS  Third Level Heading: heading alignment        expected=flush left
PASS  Third Level Heading: heading font size        expected=12 pt
PASS  Third Level Heading: heading bold             expected=Bold
PASS  Third Level Heading: heading case             expected=Sentence Case
```

Submission #3 currently ships with unnumbered headings, which was the only way to
reach zero failures while defect 2 stood. Numbering can be restored as soon as
OPEN-1 is fixed; until then, restoring it would introduce one mandatory failure.

### Suggested fix

**Resolved in commit `a44f94f`.** Implemented essentially as suggested below,
with one adjustment: no explicit step was needed to "ignore a leading section
number" — a number has no letters to capitalise, so Python's own
`word[:1].upper()` leaves it untouched without special-casing. Full narrative
in [`defects/DEF-020.md`](defects/DEF-020.md).

Replace the `str.title()` comparison with a real title-case test:

- every word must start with a capital, **except** recognised minor words
  (`a an and as at but by for in nor of on or the to up via`)
- the first and last word are always capitalised regardless
- ignore a leading section number, and accept words that are already all-caps
  (acronyms such as `EHR`, `AI`)

### Related vocabulary gaps

Three rules currently report a status without examining anything, because the
checker recognises only part of the vocabulary the guideline parser emits. These
are not in OPEN-1's scope but sit in the same branch and are worth fixing in the
same pass:

| Rule | Stored value | What the checker tests | Result |
|------|--------------|------------------------|--------|
| `heading_case` / Chapter Heading | `UPPERCASE` | only `'all caps'` or `'title case'` | PASS without looking |
| `heading_alignment` / Section Heading | `flush left` | only values starting `'center'` | PASS without looking |
| `body_alignment` | `justified` | no validator exists | NOT VERIFIED |

The first two are the riskier pair: a student whose chapter headings are
lowercase, or whose section headings are centred, is told the rule is satisfied.
Matching `'uppercase'` and `'flush left'` to the existing branches is a few lines
of work. `heading_case` for `Sentence Case` is also unhandled, though it happens
to pass correctly cased third-level headings by doing nothing.

**All three resolved in commits `a44f94f` and `fc2b81c`:**
`heading_case`/`UPPERCASE` now matches alongside `'all caps'`; `heading_alignment`
now recognises left/right wording, anchored to the page's actual margins rather
than a naive page-edge comparison (full narrative:
[`defects/DEF-022.md`](defects/DEF-022.md)); `body_alignment` now has a
validator for `justified`/`left`/`right` (full narrative:
[`defects/LIM-007.md`](defects/LIM-007.md)). `heading_case` for `Sentence Case`
remains unhandled — out of scope for this pass, and still safe only by
accident, the same way `UPPERCASE` was before this fix. Worth its own entry if
picked up.
