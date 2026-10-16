"""Regression tests for the deterministic format validator.

Covers the defects that made job #1/#2 fail or report false results against
storage/submissions/submission_1_v2_*.pdf:

- rule values the guideline parser writes as text ('>=12', 'double') crashed
  the whole validation job on float() conversion
- a value the parser could not express as a number was reported as PASS
- section-scoped page/spacing rules were measured against the whole document
- targets carrying a parenthetical qualifier never matched a real heading
- '>=' was validated as '==', so a compliant larger value still failed
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.format_validation import (  # noqa: E402
    _find_section,
    _is_generic_target,
    _num_ok,
    _parse_num,
    _parse_num_op,
    _strip_qualifier,
    validate_all_profile_rules,
)


def _page(page, **kw):
    base = {
        'page': page, 'width_pt': 595.0, 'height_pt': 842.0,
        'width_mm': 210.0, 'height_mm': 297.0,
        'dominant_font': 'Times New Roman', 'dominant_font_size': 12.0,
        'estimated_line_spacing': 1.5,
        'margins': {'left_mm': 38.1, 'right_mm': 25.4, 'top_mm': 25.4,
                    'bottom_mm': 25.4, 'evidence_lines': 20},
        'spans': [], 'lines': [],
    }
    base.update(kw)
    return base


def _info(pages, text=''):
    return {'page_count': len(pages), 'pages': pages, 'text': text,
            'searchable': True, 'page_sizes': [(595.0, 842.0)] * len(pages)}


def _run(rule, info):
    rows = validate_all_profile_rules({'deterministic_rules': [rule]}, info)
    assert len(rows) == 1, rows
    return rows[0]


def _rule(**kw):
    base = {'type': '', 'target': '', 'value': None, 'mandatory': True,
            'final_severity': 'Mandatory', 'enabled': True, 'source_page': 4}
    base.update(kw)
    return base


class ParseRuleValues(unittest.TestCase):
    def test_operator_prefix_is_not_a_crash(self):
        self.assertEqual(_parse_num('>=12'), 12.0)

    def test_word_spacing_value(self):
        self.assertEqual(_parse_num('double'), 2.0)

    def test_spacing_noun_suffix_is_stripped(self):
        self.assertEqual(_parse_num('single space'), 1.0)

    def test_word_operator_is_recognised(self):
        self.assertEqual(_parse_num_op('no more than 150 words'), (150.0, '<='))

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(_parse_num('A4'))

    def test_at_least_allows_larger_value(self):
        self.assertTrue(_num_ok(14.0, 12.0, '>=', 0.6))

    def test_at_least_rejects_smaller_value(self):
        self.assertFalse(_num_ok(8.0, 12.0, '>=', 0.6))

    def test_exact_match_rejects_larger_value(self):
        self.assertFalse(_num_ok(14.0, 12.0, '==', 0.6))


class TargetHandling(unittest.TestCase):
    def test_parenthetical_qualifier_is_stripped(self):
        self.assertEqual(_strip_qualifier('Abstract (M.S. thesis)'), 'Abstract')

    def test_literal_none_target_is_document_wide(self):
        self.assertTrue(_is_generic_target('None'))

    def test_named_section_is_not_document_wide(self):
        self.assertFalse(_is_generic_target('Abstract'))

    def test_qualified_target_still_finds_section(self):
        text = 'ABSTRACT\nSome abstract body text that is long enough.\nINTRODUCTION\nBody.\n'
        self.assertIsNotNone(_find_section(text, 'Abstract (M.S. thesis)'))


class NoSilentPass(unittest.TestCase):
    def test_unparseable_value_is_not_verified(self):
        row = _run(_rule(type='line_spacing', value='as per department'),
                   _info([_page(1)]))
        self.assertEqual(row['status'], 'NOT VERIFIED')

    def test_missing_parsed_value_is_not_verified(self):
        row = _run(_rule(type='heading_case', target='', value=None),
                   _info([_page(1)]))
        self.assertEqual(row['status'], 'NOT VERIFIED')

    def test_untargeted_heading_rule_has_a_readable_name(self):
        row = _run(_rule(type='heading_case', target='', value=None),
                   _info([_page(1)]))
        self.assertFalse(row['name'].startswith(':'))


class SectionScoping(unittest.TestCase):
    """'The abstract must be confined to a single page' is not a 30-page limit."""

    def test_section_page_limit_is_not_measured_on_whole_document(self):
        info = _info([_page(i) for i in range(1, 31)])
        row = _run(_rule(type='max_pages', target='Abstract', value=1), info)
        self.assertNotEqual(row['status'], 'FAIL')

    def test_document_page_limit_still_fails(self):
        info = _info([_page(i) for i in range(1, 31)])
        row = _run(_rule(type='max_pages', target='', value=10), info)
        self.assertEqual(row['status'], 'FAIL')

    def test_section_scoped_spacing_is_not_verified_from_page_medians(self):
        row = _run(_rule(type='line_spacing', target='Abstract', value='single space'),
                   _info([_page(1, estimated_line_spacing=1.16)]))
        self.assertEqual(row['status'], 'NOT VERIFIED')

    def test_named_area_margin_is_not_a_document_failure(self):
        row = _run(_rule(type='margin_top_mm', target='Chapter Heading area', value=101.6),
                   _info([_page(1)]))
        self.assertEqual(row['status'], 'NOT VERIFIED')

    def test_document_margin_still_fails_on_intrusion(self):
        intruding = _page(1)
        intruding['margins'] = dict(intruding['margins'], left_mm=10.0)
        row = _run(_rule(type='margin_left_mm', target='', value=38.1),
                   _info([intruding]))
        self.assertEqual(row['status'], 'FAIL')

    def test_missing_section_word_count_is_not_a_failure(self):
        row = _run(_rule(type='section_max_words', target='Abstract (M.S. thesis)', value=150),
                   _info([_page(1)], text='INTRODUCTION\nNo abstract heading here.\n'))
        self.assertEqual(row['status'], 'NOT VERIFIED')


class HeadingTargetMatching(unittest.TestCase):
    """A heading rule that matched no line was reported as PASS."""

    @staticmethod
    def _info_with_heading(text):
        pg = _page(1)
        pg['lines'] = [{'text': text, 'bbox': (100.0, 50.0, 300.0, 64.0), 'font_size': 14.0}]
        return _info([pg], text=text + '\n')

    def test_unmatchable_target_is_not_verified(self):
        row = _run(_rule(type='heading_case', target='Cover page Middle text (Name of student)',
                         value='ALL CAPS', final_severity='Warning'),
                   self._info_with_heading('CHAPTER 1'))
        self.assertEqual(row['status'], 'NOT VERIFIED')

    def test_qualified_generic_target_still_evaluates(self):
        # 'Chapter Heading (implied by context)' must resolve to chapter headings.
        row = _run(_rule(type='heading_alignment', target='Chapter Heading (implied by context)',
                         value='centered', final_severity='Warning'),
                   self._info_with_heading('CHAPTER 1'))
        self.assertNotEqual(row['status'], 'NOT VERIFIED')

    def test_matching_target_still_passes_when_compliant(self):
        row = _run(_rule(type='heading_case', target='ABSTRACT', value='ALL CAPS',
                         final_severity='Warning'),
                   self._info_with_heading('ABSTRACT'))
        self.assertEqual(row['status'], 'PASS')


class OperatorSemantics(unittest.TestCase):
    def test_at_least_12pt_accepts_14pt_body(self):
        row = _run(_rule(type='body_font_size_pt', value='>=12'),
                   _info([_page(1, dominant_font_size=14.0)]))
        self.assertEqual(row['status'], 'PASS')

    def test_at_least_12pt_rejects_8pt_body(self):
        row = _run(_rule(type='body_font_size_pt', value='>=12'),
                   _info([_page(1, dominant_font_size=8.0)]))
        self.assertEqual(row['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
