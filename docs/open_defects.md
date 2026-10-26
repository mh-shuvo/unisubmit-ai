# Open defects

Known defects that are understood, reproduced, and deliberately **not** fixed
yet. Each entry carries enough detail to be picked up without re-investigating.

Fixed defects are recorded in `unsatisfiable_rules_defects_and_fixes.md` and
`validation_defects_and_fixes.md`.

| ID | Title | Severity | Blocks | Status |
|----|-------|----------|--------|--------|
| OPEN-1 | `heading_case` rejects correct title case | Mandatory rule, always fails | Restoring chapter and section numbering in submissions | Open |

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
