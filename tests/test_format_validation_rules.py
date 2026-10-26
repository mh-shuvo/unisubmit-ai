"""Regression tests for the deterministic format validator.

Covers the defects that made job #1/#2 fail or report false results against
storage/submissions/submission_1_v2_*.pdf:

- rule values the guideline parser writes as text ('>=12', 'double') crashed
  the whole validation job on float() conversion
- a value the parser could not express as a number was reported as PASS
- section-scoped page/spacing rules were measured against the whole document
- targets carrying a parenthetical qualifier never matched a real heading
- '>=' was validated as '==', so a compliant larger value still failed

And the three defects that made correctly formatted theses fail:

- line spacing was sampled only within a MuPDF block, which splits once the gap
  passes ~1.5 em, so double spacing could never be observed
- the chapter and section heading rules resolved to one shared list of lines and
  demanded two different sizes of it, which no document can satisfy
- font-name normalisation erased the weight before the bold check read it
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.format_validation import (  # noqa: E402
    _find_section,
    _heading_candidates,
    _is_generic_target,
    _num_ok,
    _parse_num,
    _parse_num_op,
    _span_is_bold,
    _strip_qualifier,
    validate_all_profile_rules,
)
from app.pdf_utils import inspect_pdf  # noqa: E402


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


class HeadingLevelSeparation(unittest.TestCase):
    """The chapter and section rules were handed one shared list of lines.

    Both targets resolved through the same branch, so 'chapter headings are 14 pt'
    and 'section headings are 12 pt' judged the same lines. No size satisfies both,
    so every document with a recognisable heading took at least one failure.
    """

    @staticmethod
    def _doc():
        pg = _page(1)
        pg['lines'] = [
            {'text': 'CHAPTER 1: INTRODUCTION',
             'bbox': (200.0, 70.0, 395.0, 88.0), 'font_size': 14.0},
            {'text': '1.1 Background',
             'bbox': (99.0, 130.0, 220.0, 144.0), 'font_size': 12.0},
            {'text': '1.1.1 Reporting practice',
             'bbox': (99.0, 190.0, 260.0, 204.0), 'font_size': 12.0},
        ]
        return _info([pg])

    def test_chapter_target_matches_only_the_chapter_line(self):
        found = _heading_candidates(self._doc(), 'Chapter Heading')
        self.assertEqual([ln['text'] for _, ln in found], ['CHAPTER 1: INTRODUCTION'])

    def test_section_target_matches_only_the_second_level_line(self):
        found = _heading_candidates(self._doc(), 'Section Heading')
        self.assertEqual([ln['text'] for _, ln in found], ['1.1 Background'])

    def test_third_level_target_matches_only_the_third_level_line(self):
        found = _heading_candidates(self._doc(), 'Third Level Heading')
        self.assertEqual([ln['text'] for _, ln in found], ['1.1.1 Reporting practice'])

    def test_both_size_rules_pass_on_one_compliant_document(self):
        rows = validate_all_profile_rules({'deterministic_rules': [
            _rule(type='heading_font_size_pt', target='Chapter Heading', value=14),
            _rule(type='heading_font_size_pt', target='Section Heading', value=12),
        ]}, self._doc())
        self.assertEqual([r['status'] for r in rows], ['PASS', 'PASS'])

    def test_wrong_chapter_size_still_fails(self):
        info = self._doc()
        info['pages'][0]['lines'][0]['font_size'] = 12.0
        row = _run(_rule(type='heading_font_size_pt', target='Chapter Heading', value=14),
                   info)
        self.assertEqual(row['status'], 'FAIL')

    def test_contents_entries_are_not_headings(self):
        toc = _page(1)
        toc['lines'] = [
            {'text': 'TABLE OF CONTENTS',
             'bbox': (200.0, 70.0, 395.0, 88.0), 'font_size': 14.0},
            {'text': 'CHAPTER 1: INTRODUCTION 1',
             'bbox': (99.0, 130.0, 400.0, 144.0), 'font_size': 12.0},
        ]
        self.assertEqual(_heading_candidates(_info([toc]), 'Chapter Heading'), [])

    def test_qualified_chapter_target_still_resolves(self):
        found = _heading_candidates(self._doc(), 'Chapter Heading (implied by context)')
        self.assertEqual([ln['text'] for _, ln in found], ['CHAPTER 1: INTRODUCTION'])


class FontWeightDetection(unittest.TestCase):
    """A genuinely bold heading was reported as 'Not bold'.

    pdf_utils rewrites 'TimesNewRomanPS-BoldMT' to the family name 'Times New
    Roman', and the bold check searched that name for the word 'bold'.
    """

    @staticmethod
    def _heading(**span):
        pg = _page(1)
        bbox = (200.0, 70.0, 395.0, 88.0)
        pg['lines'] = [{'text': 'CHAPTER 1: INTRODUCTION', 'bbox': bbox,
                        'font_size': 14.0}]
        base = {'text': 'CHAPTER 1: INTRODUCTION', 'font': 'Times New Roman',
                'size': 14.0, 'bbox': bbox}
        base.update(span)
        pg['spans'] = [base]
        return _info([pg])

    def test_recorded_flag_is_trusted_over_the_normalised_name(self):
        self.assertTrue(_span_is_bold({'font': 'Times New Roman', 'bold': True}))

    def test_font_name_is_the_fallback_when_no_flag_was_recorded(self):
        self.assertTrue(_span_is_bold({'font': 'TimesNewRomanPS-BoldMT'}))

    def test_regular_span_is_not_bold(self):
        self.assertFalse(_span_is_bold({'font': 'Times New Roman', 'bold': False}))

    def test_bold_heading_passes(self):
        row = _run(_rule(type='heading_bold_required', target='Chapter Heading',
                         value=True), self._heading(bold=True))
        self.assertEqual(row['status'], 'PASS')

    def test_regular_heading_still_fails(self):
        row = _run(_rule(type='heading_bold_required', target='Chapter Heading',
                         value=True), self._heading(bold=False))
        self.assertEqual(row['status'], 'FAIL')


class LineSpacingReporting(unittest.TestCase):
    """A page with no running prose is not a spacing breach.

    Warning on every cover page and blank leaf made the warning permanent for any
    compliant thesis, so doubt is now reported only when nothing anywhere in the
    document could be measured.
    """

    def test_double_spacing_passes(self):
        row = _run(_rule(type='line_spacing', value='double'),
                   _info([_page(1, estimated_line_spacing=2.0)]))
        self.assertEqual(row['status'], 'PASS')

    def test_unmeasurable_page_does_not_warn_when_prose_pages_comply(self):
        row = _run(_rule(type='line_spacing', value='double'),
                   _info([_page(1, estimated_line_spacing=None),
                          _page(2, estimated_line_spacing=2.0)]))
        self.assertEqual(row['status'], 'PASS')

    def test_wrong_spacing_still_fails(self):
        row = _run(_rule(type='line_spacing', value='double'),
                   _info([_page(1, estimated_line_spacing=None),
                          _page(2, estimated_line_spacing=1.15)]))
        self.assertEqual(row['status'], 'FAIL')

    def test_document_with_nothing_measurable_is_still_flagged(self):
        row = _run(_rule(type='line_spacing', value='double'),
                   _info([_page(1, estimated_line_spacing=None)]))
        self.assertEqual(row['status'], 'WARNING')


class PdfMeasurement(unittest.TestCase):
    """inspect_pdf must observe double spacing and record the font weight.

    MuPDF starts a new block once the baseline gap passes roughly 1.5 em, so
    pairing lines only inside a block made every double-spaced page unmeasurable.
    """

    PROSE = 'hospital information systems accumulate continuous measurements daily'

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pdf(self, pitch, bold_heading=False):
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        for i in range(4):
            page.insert_text((99.2, 120 + pitch * i), self.PROSE,
                             fontname='tiro', fontsize=12)
        if bold_heading:
            page.insert_text((200.0, 400.0), 'CHAPTER 1: INTRODUCTION',
                             fontname='tibo', fontsize=14)
        path = str(Path(self.tmp) / f'p{pitch}{bold_heading}.pdf')
        doc.save(path)
        doc.close()
        return path

    def test_double_spacing_is_measured(self):
        measured = inspect_pdf(self._pdf(24.0))['pages'][0]['estimated_line_spacing']
        self.assertAlmostEqual(measured if measured is not None else 0.0, 2.0,
                               places=1)

    def test_single_spacing_is_still_measured(self):
        info = inspect_pdf(self._pdf(14.0))
        self.assertLess(info['pages'][0]['estimated_line_spacing'], 1.3)

    def test_span_weight_is_recorded(self):
        info = inspect_pdf(self._pdf(24.0, bold_heading=True))
        heading = next(s for s in info['pages'][0]['spans']
                       if s['text'].startswith('CHAPTER'))
        self.assertTrue(heading.get('bold'))

    def test_body_span_is_not_marked_bold(self):
        info = inspect_pdf(self._pdf(24.0, bold_heading=True))
        body = next(s for s in info['pages'][0]['spans']
                    if s['text'].startswith('hospital'))
        self.assertIs(body.get('bold'), False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
