from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

from .report_parser import parse_ai_report


def _esc(text):
    return str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _p(text, style):
    return Paragraph(_esc(text), style)


def _page_label(page):
    return f'p.{page}' if page not in (None, '', 'None') else 'Document-level'


def _source_label(r):
    if r.get('source_type') == 'Manual Semantic Rule':
        page = 'Manual Semantic Rule'
    elif r.get('source_pages'):
        pages=', '.join(str(x) for x in r.get('source_pages') if x not in (None,'','None'))
        page=f'Pages {pages}' if pages else 'Approved guideline'
    else:
        page = f"Page {r.get('source_page')}" if r.get('source_page') else 'Approved guideline'
    section = (r.get('source_section') or '').strip()
    return f'{page} - {section}' if section else page


def build_validation_report(path, submission, version, results, suggestions):
    """Generate a PDF with content parity to the full HTML validation report.

    The PDF intentionally uses the same source data and section order as the HTML
    report: status/metadata, statistics, summary, formatting issues with every
    occurrence, compliance, corrections, suggestions, additional restrictions,
    grounding/evidence, source context, and academic-responsibility note.
    """
    parsed = parse_ai_report(suggestions)
    styles = getSampleStyleSheet()

    title = ParagraphStyle('ReportTitle', parent=styles['Title'], fontSize=20, leading=24,
                           textColor=colors.HexColor('#172033'), spaceAfter=4)
    subtitle = ParagraphStyle('Subtitle', parent=styles['BodyText'], fontSize=8.5, leading=12,
                              textColor=colors.HexColor('#667085'))
    heading = ParagraphStyle('SectionHeading', parent=styles['Heading2'], fontSize=12.5, leading=15,
                             textColor=colors.HexColor('#312E81'), spaceBefore=9, spaceAfter=6)
    subheading = ParagraphStyle('SubHeading', parent=styles['Heading3'], fontSize=10.5, leading=13,
                                textColor=colors.HexColor('#172033'), spaceBefore=5, spaceAfter=3)
    body = ParagraphStyle('ReportBody', parent=styles['BodyText'], fontSize=8.9, leading=13,
                          textColor=colors.HexColor('#344054'))
    small = ParagraphStyle('Small', parent=body, fontSize=7.8, leading=11,
                           textColor=colors.HexColor('#667085'))
    tiny = ParagraphStyle('Tiny', parent=body, fontSize=7.1, leading=9.6,
                          textColor=colors.HexColor('#667085'))
    status_style = ParagraphStyle('Status', parent=body, fontSize=11, leading=14,
                                  alignment=TA_CENTER, textColor=colors.white)

    doc = SimpleDocTemplate(path, pagesize=A4, rightMargin=30, leftMargin=30,
                            topMargin=30, bottomMargin=30,
                            title='UniSubmit AI Validation Report')
    story = [
        _p('Guideline Compliance & Improvement Report', title),
        _p(f'Submission #{submission.id} - Version {version.version_no} - {submission.program.name}', subtitle),
        Spacer(1, 10),
    ]

    display_status = version.status or parsed.get('status') or 'UNKNOWN'
    upper = str(display_status).upper()
    status_color = (colors.HexColor('#16A34A') if upper == 'PASSED' else
                    colors.HexColor('#DC2626') if ('REVISION' in upper or 'FAIL' in upper) else
                    colors.HexColor('#D97706'))
    status_table = Table([[_p(f'Overall status: {display_status}', status_style)]], colWidths=[535])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), status_color),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story += [status_table, Spacer(1, 10)]

    submitted = ''
    if getattr(version, 'created_at', None):
        try:
            submitted = version.created_at.strftime('%d %b %Y, %I:%M %p')
        except Exception:
            submitted = str(version.created_at)

    meta = [
        ['Student', submission.student.name],
        ['Student ID', submission.student.student_id],
        ['Program', submission.program.name],
        ['Submission type', submission.template_version.template.submission_type],
        ['Title', submission.title],
        ['Supervisor', f'{submission.teacher.name}, {submission.teacher.designation}'],
        ['Submission version', str(version.version_no)],
        ['Guideline version', submission.template_version.version],
        ['Submitted', submitted or 'N/A'],
        ['AI run', version.ai_run_reference or 'N/A'],
    ]
    mt = Table([[_p(a, small), _p(b, body)] for a, b in meta], colWidths=[125, 410])
    mt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F5F3FF')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#E4E7EC')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story += [mt, Spacer(1, 12)]

    stats_entry = next((r for r in results if r.get('category') == 'Validation Statistics'), None)
    stats = (stats_entry or {}).get('statistics') or {}
    visible_results = [r for r in results if r.get('category') != 'Validation Statistics']
    format_results = [r for r in visible_results if r.get('category') == 'Formatting Compliance']
    semantic_results = [r for r in visible_results if r.get('category') == 'Semantic Compliance']
    other_results = [r for r in visible_results if r.get('category') not in {'Formatting Compliance','Semantic Compliance'}]
    failed_format = [r for r in format_results if r.get('status') == 'FAIL']
    affected_pages = sorted({p for r in failed_format for p in (r.get('affected_pages') or [])})
    warnings = sum(1 for r in visible_results if r.get('status') == 'WARNING')
    mandatory_failures = sum(1 for r in visible_results if r.get('severity') == 'Mandatory' and r.get('status') == 'FAIL')

    # Validation Statistics - same values shown in the HTML report.
    story.append(_p('Validation Statistics', heading))
    if stats:
        rate = stats.get('compliance_rate')
        stat_rows = [
            [_p('Total checked', small), _p(str(stats.get('total_checked', 0)), body),
             _p('Passed', small), _p(str(stats.get('passed', 0)), body),
             _p('Failed', small), _p(str(stats.get('failed', 0)), body)],
            [_p('Warnings', small), _p(str(stats.get('warnings', 0)), body),
             _p('Not verified', small), _p(str(stats.get('not_verified', 0)), body),
             _p('Compliance rate', small), _p((str(rate) + '%') if rate is not None else 'N/A', body)],
        ]
        st = Table(stat_rows, colWidths=[82, 58, 70, 58, 70, 70])
        st.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#E4E7EC')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FAFAFF')),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(st)
        cats = stats.get('categories') or {}
        if cats:
            rows = [[_p('Category', small), _p('Total', small), _p('Pass', small),
                     _p('Fail', small), _p('Warning', small), _p('Not verified', small)]]
            for name, cs in cats.items():
                rows.append([_p(name, body), _p(str(cs.get('total', 0)), small),
                             _p(str(cs.get('passed', 0)), small), _p(str(cs.get('failed', 0)), small),
                             _p(str(cs.get('warnings', 0)), small), _p(str(cs.get('not_verified', 0)), small)])
            ct = Table(rows, colWidths=[195, 65, 65, 65, 70, 75], repeatRows=1)
            ct.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEF2FF')),
                ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#E4E7EC')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story += [Spacer(1, 6), ct]
        if stats.get('note'):
            story += [Spacer(1, 5), _p(stats.get('note'), small)]
    else:
        story.append(_p('Statistics were not captured for this validation run.', small))

    story += [Spacer(1, 7)]
    summary_rows = [[
        _p('Mandatory issues', small), _p(str(mandatory_failures), body),
        _p('Formatting failures', small), _p(str(len(failed_format)), body),
        _p('Warnings', small), _p(str(warnings), body),
        _p('Affected pages', small), _p(', '.join(str(p) for p in affected_pages) if affected_pages else 'None', body),
    ]]
    sm = Table(summary_rows, colWidths=[75, 35, 90, 35, 55, 35, 75, 135])
    sm.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 0.45, colors.HexColor('#E4E7EC')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story += [sm, Spacer(1, 10)]

    # Parsed deterministic guideline rules - every rule, including PASS rows.
    story.append(_p('Parsed Guideline Rule Results', heading))
    story.append(_p('Every active deterministic guideline rule is listed with an explicit status. Failed same-type occurrences remain consolidated.', small))
    if not format_results:
        story += [Spacer(1, 4), _p('No active deterministic guideline rules were available for this validation run.', small)]
    else:
        for r in format_results:
            status = r.get('status', '')
            pages = ', '.join(str(x) for x in (r.get('affected_pages') or [])) or 'None'
            header = Table([[ _p(status, small), _p(r.get('name', ''), subheading), _p(f"Affected pages: {pages}", small) ]],
                           colWidths=[55, 300, 180])
            bg = colors.HexColor('#FFF1F2') if status == 'FAIL' else colors.HexColor('#FFFBEB') if status == 'WARNING' else colors.HexColor('#ECFDF3')
            header.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), bg),
                ('BOX', (0, 0), (-1, -1), 0.45, colors.HexColor('#E4E7EC')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story += [Spacer(1, 6), header]

            details = [
                ['Expected', r.get('expected', '')],
                ['Detected', r.get('detected', '') or 'Not found'],
                ['Occurrences', str(r.get('occurrence_count') or len(r.get('occurrences') or []))],
                ['Problem', r.get('message', '')],
                ['Required correction', r.get('correction', '') or 'Correct the affected formatting to match the guideline.'],
                ['Guideline source', _source_label(r)],
            ]
            if r.get('source_text'):
                details.append(['Source evidence', r.get('source_text')])
            dt = Table([[_p(a, small), _p(b, body)] for a, b in details], colWidths=[115, 420])
            dt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F8FAFC')),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#E4E7EC')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(dt)

            occs = r.get('occurrences') or []
            if occs:
                rows = [[_p('Page', tiny), _p('Location', tiny), _p('Detected', tiny), _p('Example', tiny)]]
                for o in occs:
                    rows.append([
                        _p(_page_label(o.get('page')), tiny),
                        _p(o.get('location', ''), tiny),
                        _p(o.get('detected', ''), tiny),
                        _p(o.get('snippet') or '-', tiny),
                    ])
                ot = Table(rows, colWidths=[70, 145, 120, 200], repeatRows=1)
                ot.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEF2FF')),
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#E4E7EC')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))
                story += [Spacer(1, 4), ot]

    # Semantic guideline rules - every active semantic rule with explicit status.
    story += [Spacer(1, 10), _p('Semantic Guideline Rule Results', heading)]
    story.append(_p('Every active semantic rule is evaluated with RAG grounding and listed with PASS, FAIL, WARNING, or NOT VERIFIED status.', small))
    if not semantic_results:
        story.append(_p('No active semantic guideline rules were available for this validation run.', small))
    else:
        for r in semantic_results:
            status=r.get('status','')
            header=Table([[_p(status, small), _p(r.get('name',''), subheading), _p('Severity: '+str(r.get('severity','')), small)]], colWidths=[70, 330, 135])
            bg = colors.HexColor('#FFF1F2') if status == 'FAIL' else colors.HexColor('#FFFBEB') if status in {'WARNING','NOT VERIFIED'} else colors.HexColor('#ECFDF3')
            header.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),0.45,colors.HexColor('#E4E7EC')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story += [Spacer(1, 6), header]
            details=[['Assessment',r.get('message','')],['Student evidence',r.get('detected','') or 'Not verified'],['Correction',r.get('correction','') or ('No correction required.' if status=='PASS' else 'No mandatory correction was generated.')],['Guideline source',_source_label(r)]]
            if r.get('source_text'): details.append(['Source evidence',r.get('source_text')])
            dt=Table([[_p(a,small),_p(b,body)] for a,b in details],colWidths=[115,420])
            dt.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#F8FAFC')),('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#E4E7EC')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            story.append(dt)

    # AI sections - same order and items as the HTML report.
    section_specs = [
        ('Guideline Compliance Assessment', parsed.get('compliance') or [], 'No guideline compliance findings were reported.'),
        ('Required Corrections', parsed.get('corrections') or [], 'No guideline-supported mandatory correction was reported.'),
        ('AI Improvement Suggestions', parsed.get('suggestions') or [], 'No additional improvement suggestions were generated.'),
    ]
    for section_title, items, empty in section_specs:
        story += [Spacer(1, 10), _p(section_title, heading)]
        if not items:
            story.append(_p(empty, small))
        else:
            for i, item in enumerate(items, 1):
                story.append(KeepTogether([_p(f'{i}. {item}', body), Spacer(1, 4)]))

    story += [Spacer(1, 10), _p('Additional Restriction Checks', heading)]
    if other_results:
        rows = [[_p('Rule', small), _p('Status', small), _p('Severity', small), _p('Message', small)]]
        for r in other_results:
            rows.append([
                _p(r.get('name', ''), body), _p(r.get('status', ''), small),
                _p(r.get('severity', ''), small), _p(r.get('message') or r.get('detected') or '', small),
            ])
        rt = Table(rows, repeatRows=1, colWidths=[175, 65, 80, 215])
        rt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEF2FF')),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#E4E7EC')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(rt)
    else:
        story.append(_p('No additional structured restrictions were configured.', small))

    story += [Spacer(1, 10), _p('Grounding & Evidence Note', heading)]
    grounding = parsed.get('grounding') or []
    if grounding:
        for item in grounding:
            story.append(_p(item, body))
            story.append(Spacer(1, 3))
    else:
        story.append(_p('No grounding note was returned for this validation run.', small))

    story += [Spacer(1, 10), _p('Source Context', heading)]
    source_rows = [
        ['Program', submission.program.name],
        ['Guideline', f'v{submission.template_version.version}'],
        ['Submission', submission.template_version.template.submission_type],
        ['AI run', version.ai_run_reference or 'N/A'],
    ]
    sc = Table([[_p(a, small), _p(b, body)] for a, b in source_rows], colWidths=[110, 425])
    sc.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F8FAFC')),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#E4E7EC')),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [sc, Spacer(1, 10)]
    story.append(_p('Academic responsibility', subheading))
    story.append(_p('This is a pre-submission assistance report. Final academic judgment remains with the supervisor/teacher.', body))

    doc.build(story)
