import json
import re
from collections import Counter


_OPERATOR_PREFIX_RE = re.compile(r'^\s*(>=|<=|=>|=<|≥|≤|>|<|~|=)\s*')
_OPERATOR_WORDS_RE = re.compile(
    r'^\s*(?:at\s+least|minimum(?:\s+of)?|min(?:\.|imum)?|no\s+less\s+than|not\s+less\s+than)\s+'
    r'|^\s*(?:at\s+most|maximum(?:\s+of)?|max(?:\.|imum)?|no\s+more\s+than|not\s+more\s+than|'
    r'up\s+to|not\s+exceeding|must\s+not\s+exceed)\s+',
    re.I,
)
# Trailing spacing nouns: 'single space', 'double spacing', '1.5 lines'.
_SPACING_SUFFIX_RE = re.compile(
    r'\s*(?:line\s+)?(?:spaces?|spacing|spaced|lines?|pts?|points?|mm|cm|inch(?:es)?|words?|pages?)\.?$',
    re.I,
)

_GTE_WORDS = ('at least', 'minimum', 'min', 'no less than', 'not less than')
_LTE_WORDS = ('at most', 'maximum', 'max', 'no more than', 'not more than',
              'up to', 'not exceeding', 'must not exceed')

# Qwen sometimes writes spacing/size requirements as English words.
_WORD_NUMBERS = {
    'single': 1.0, 'one': 1.0,
    'one and a half': 1.5, 'one-and-a-half': 1.5, '1.5': 1.5,
    'double': 2.0, 'two': 2.0,
    'triple': 3.0, 'three': 3.0,
}

# Rule targets that mean "the whole document" rather than one named section.
_GENERIC_TARGETS = {
    '', 'none', 'null', 'n/a', 'na', 'all', 'all pages', 'whole document', 'document',
    'entire document', 'body', 'body text', 'general text', 'main text', 'text',
}


def _parse_num_op(value):
    """Parse a rule value into ``(number, operator)``.

    The operator is ``'>='``, ``'<='`` or ``'=='`` and records the comparison the
    guideline actually asked for, so '>=12' is not silently validated as '==12'.
    Returns ``(None, '==')`` when no number can be recovered, letting the call
    site report NOT VERIFIED instead of guessing.

    Handles operator symbols ('>=12', '≥ 25'), operator words ('at least 12',
    'no more than 150'), spacing nouns ('single space', 'double spacing') and
    word numbers ('double', 'one and a half').
    """
    s = str(value).strip()
    low = s.lower()
    op = '=='
    if any(low.startswith(w) for w in _GTE_WORDS):
        op = '>='
    elif any(low.startswith(w) for w in _LTE_WORDS):
        op = '<='
    s = _OPERATOR_WORDS_RE.sub('', s).strip()
    m = _OPERATOR_PREFIX_RE.match(s)
    if m:
        sym = m.group(1)
        if sym in ('>=', '=>', '≥', '>'):
            op = '>='
        elif sym in ('<=', '=<', '≤', '<'):
            op = '<='
        s = s[m.end():].strip()
    s = _SPACING_SUFFIX_RE.sub('', s).strip().lower()
    word_val = _WORD_NUMBERS.get(s)
    if word_val is not None:
        return word_val, op
    try:
        return float(s), op
    except ValueError:
        return None, op


def _parse_num(value):
    """Return just the numeric part of a rule value, or None when unparseable."""
    return _parse_num_op(value)[0]


def _num_ok(detected, exp, op, tol):
    """Compare a detected value against the requirement, honouring the operator."""
    if op == '>=':
        return detected >= exp - tol
    if op == '<=':
        return detected <= exp + tol
    return abs(detected - exp) <= tol


def _is_generic_target(target):
    """True when a rule applies document-wide rather than to one named section."""
    return str(target or '').strip().lower() in _GENERIC_TARGETS


def _strip_qualifier(target):
    """Drop parenthetical qualifiers Qwen appends to section targets.

    'Abstract (M.S. thesis)' → 'Abstract'. Without this the literal target is
    never matchable as a heading, so the rule fails on every submission.
    """
    t = re.sub(r'\s*[\(\[][^)\]]*[\)\]]\s*', ' ', str(target or ''))
    return re.sub(r'\s+', ' ', t).strip(' -:–—')


def _not_verified(code, name, expected, source_page, reason, severity='Mandatory',
                  source_text='', source_section=''):
    """Row for a rule the engine cannot decide, so it is never a silent PASS."""
    return {
        'rule_id': f'GUIDE-{code}', 'name': name, 'category': 'Formatting Compliance',
        'severity': severity, 'status': 'NOT VERIFIED', 'expected': expected,
        'detected': reason,
        'message': f'This guideline rule could not be checked automatically: {reason}',
        'correction': 'Review this rule manually against the submitted document.',
        'source_page': str(source_page or ''), 'source_section': source_section or '',
        'source_text': source_text or 'Explicit requirement parsed from the approved guideline',
        'affected_pages': [], 'occurrence_count': 0, 'occurrences': [],
    }


def _norm_font(x):
    n=re.sub(r'[^a-z0-9]','',(x or '').lower())
    aliases={'timesnewroman':'timesnewroman','timesroman':'timesnewroman','arial':'arial','calibri':'calibri','cambria':'cambria'}
    for k,v in aliases.items():
        if k in n: return v
    return n


def _same_font(a,b):
    aa,bb=_norm_font(a),_norm_font(b)
    return bool(aa and bb and (aa==bb or aa in bb or bb in aa))


def _occ(page, location, detected, snippet='', bbox=None):
    return {'page':page,'location':location,'detected':detected,'snippet':(snippet or '')[:220],'bbox':bbox}


def _consolidate(code,name,expected,source_page,occurrences,message,correction,status='FAIL',severity='Mandatory',source_text='',source_section=''):
    pages=sorted({o['page'] for o in occurrences if o.get('page')})
    detected_values=[]
    for o in occurrences:
        d=str(o.get('detected',''))
        if d and d not in detected_values: detected_values.append(d)
    return {
        'rule_id':f'GUIDE-{code}','name':name,'category':'Formatting Compliance','severity':severity,
        'status':status,'expected':expected,'detected':', '.join(detected_values)[:260],
        'message':message,'correction':correction,'source_page':str(source_page or ''),
        'source_section':source_section or '',
        'source_text':source_text or 'Explicit requirement parsed from the approved guideline',
        'affected_pages':pages,'occurrence_count':len(occurrences),'occurrences':occurrences,
    }


def _rules(profile):
    if not profile: return []
    if isinstance(profile,str):
        try: profile=json.loads(profile)
        except Exception: return []
    if not isinstance(profile,dict): return []
    # v9 profile first; retain v8 compatibility.
    return profile.get('deterministic_rules') or profile.get('rules') or []


def _find_section(text, target):
    if not target: return None
    # Qwen qualifies targets ('Abstract (M.S. thesis)'); the bare heading is what
    # appears in the document, so retry without the parenthetical qualifier.
    candidates=[str(target).strip()]
    stripped=_strip_qualifier(target)
    if stripped and stripped.lower()!=candidates[0].lower(): candidates.append(stripped)
    for cand in candidates:
        if not cand: continue
        # Capture from heading to next likely heading. Best-effort for searchable PDFs.
        pat=rf'(?is)(?:^|\n)\s*(?:\d+(?:\.\d+)*\s+)?{re.escape(cand)}\s*\n(.*?)(?=\n\s*(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Z\s&:/-]{{3,}}\s*\n|\Z)'
        m=re.search(pat,text)
        if m: return m.group(1)
    return None


def _heading_candidates(info, target):
    """Lines that plausibly are the heading a ``heading_*`` rule targets.

    Tries the target as written, then without its parenthetical qualifier, so
    'Chapter Heading (implied by context)' still resolves to chapter headings.
    Returns ``[]`` when nothing matches, so the call site can report
    NOT VERIFIED instead of passing a rule that examined no line at all.
    """
    names=[str(target or '').strip()]
    stripped=_strip_qualifier(target)
    if stripped and stripped.lower()!=names[0].lower(): names.append(stripped)
    for name in names:
        low=name.lower()
        if not low: continue
        found=[]
        for pg in info.get('pages',[]):
            for ln in pg.get('lines',[]):
                txt=(ln.get('text') or '').strip()
                if not txt: continue
                if low in {'chapter heading','section heading','heading'}:
                    if re.match(r'^(chapter\s+\w+|\d+(?:\.\d+)*\s+\S+)',txt,re.I): found.append((pg,ln))
                elif low in txt.lower(): found.append((pg,ln))
        if found: return found
    return []


def _section_page_span(info, text, target):
    """Pages a named section occupies, as ``(first, last)``, or None if unknown.

    Used by page-count rules scoped to a section, such as "the abstract must be
    confined to a single page", which must not be measured against the whole
    document.
    """
    start=_find_actual_heading(info,target)[0]
    if start is None: return None
    body=_find_section(text,target)
    if not body: return (start,start)
    # The section ends on the last page still carrying one of its longer lines.
    anchors=[ln.strip() for ln in body.splitlines() if len(ln.strip())>=40]
    last=start
    for pg in info.get('pages',[]):
        if pg.get('page',0)<start: continue
        txt=' '.join(x.get('text','') for x in pg.get('lines',[]))
        if any(a in txt for a in anchors[-40:]): last=max(last,pg.get('page',start))
    return (start,last)


def _page_for_text(info, needle):
    if not needle: return None
    n=needle.lower()
    for pg in info.get('pages',[]):
        txt=' '.join(x.get('text','') for x in pg.get('lines',[])).lower()
        if n in txt: return pg.get('page')
    return None


def _normalize_heading(text):
    t=(text or '').strip()
    t=re.sub(r'\s+',' ',t)
    # Drop parenthetical qualifiers so a parsed target such as
    # 'Abstract (M.S. thesis)' still matches the document's 'ABSTRACT' heading.
    t=_strip_qualifier(t) or t
    # Remove common chapter prefixes while preserving the actual semantic heading.
    t=re.sub(r'^chapter\s+(?:[0-9ivxlcdm]+|[a-z]+)\s*[:.\-–—]?\s*','',t,flags=re.I)
    # Remove numbered section prefixes such as 1, 1.1, 1.1.1.
    t=re.sub(r'^\d+(?:\.\d+)*\s*[:.\-–—]?\s*','',t)
    t=re.sub(r'[^a-z0-9]+',' ',t.lower()).strip()
    return t


def _is_toc_page(pg):
    lines=pg.get('lines',[])
    text='\n'.join(x.get('text','') for x in lines)
    if re.search(r'(?im)^\s*table\s+of\s+contents\s*$', text):
        return True
    dotted=sum(1 for x in lines if re.search(r'\.{4,}\s*\d+\s*$', x.get('text','')))
    return dotted >= 3


def _find_actual_heading(info, target):
    """Find a real document heading while avoiding false matches from TOC entries.

    Front-matter headings such as ``TABLE OF CONTENTS`` are themselves valid
    headings even though their page is, by definition, a TOC-like page. Main
    chapters must still be found outside the TOC region. Matching tolerates
    chapter-number prefixes, punctuation, capitalization, and multiple spaces.
    """
    wanted=_normalize_heading(target)
    if not wanted:
        return None,None

    toc_page_headings={
        'table of contents', 'list of figures', 'list of tables',
        'list of abbreviations', 'list of symbols'
    }

    for pg in info.get('pages',[]):
        is_toc=_is_toc_page(pg)
        for ln in pg.get('lines',[]):
            txt=(ln.get('text') or '').strip()
            if not txt or len(txt)>180:
                continue
            norm=_normalize_heading(txt)

            # A front-matter index heading is valid on its own index page.
            if wanted in toc_page_headings and norm == wanted:
                return pg.get('page'),ln

            # For all other required sections, do not count TOC/index entries as
            # proof that the real section exists later in the document.
            if is_toc:
                continue

            if norm == wanted:
                return pg.get('page'),ln
            if wanted and norm.startswith(wanted+' ') and len(norm)-len(wanted) <= 18:
                return pg.get('page'),ln
    return None,None


def _cover_layout_results(profile, info):
    cover=(profile or {}).get('cover_page') or {}
    if not cover.get('detected') or not cover.get('blocks') or not info.get('pages'):
        return []
    student=info['pages'][0]
    lines=student.get('lines',[])
    if not lines:
        return []

    sample_blocks=cover.get('blocks',[])
    roles=[
        ('Title', r'title of the thesis|thesis title'),
        ('Degree statement', r'partial fulfillment|requirements for the degree'),
        ('Prepared-by block', r'prepared by|submitted by'),
        ('Student ID', r'student id|registration no'),
        ('Supervisor block', r'supervised by|supervisor'),
        ('Department line', r'department of|institute of'),
        ('University line', r'\buniversity\b'),
        ('Submission date', r'month[, ]+year|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b.*\b20\d{2}\b'),
    ]

    def first_match(items, pat, key='text'):
        return next((x for x in items if re.search(pat, x.get(key,'') or '', re.I)), None)

    sample=[]; actual=[]; missing=[]
    for label,pat in roles:
        sb=first_match(sample_blocks,pat)
        if not sb:
            continue
        ln=first_match(lines,pat)
        sample.append((label,sb))
        if ln:
            actual.append((label,ln))
        else:
            missing.append(_occ(1,label,'Missing'))

    # If the sample is embedded among guideline instructions, coordinates are not a
    # true cover-page layout. Validate required fields and their order only.
    if cover.get('geometry_mode') == 'structure_only':
        occ=list(missing)
        if not occ and len(actual) >= 3:
            order={label:i for i,(label,_) in enumerate(sample)}
            last=-1
            for label,ln in actual:
                idx=order.get(label,-1)
                if idx < last:
                    occ.append(_occ(1,label,'Out of prescribed order',ln.get('text',''),ln.get('bbox')))
                last=max(last,idx)
        if not occ:
            return []
        return [_consolidate(
            'COVER_STRUCTURE','Front/Cover page structure','Include the cover elements shown in the approved template',
            cover.get('guideline_page'),occ,
            'One or more required cover-page elements are missing or out of the prescribed order.',
            'Add/reorder only the listed cover-page elements to follow the approved template.',
            source_text='Cover/title-page template embedded in the approved guideline'
        )]

    # Standalone visual samples can support relative layout comparison. Use semantic
    # anchors and relative positions, not absolute pixel coordinates.
    common=[]
    for label,sb in sample:
        ln=next((x for l,x in actual if l==label),None)
        if ln:
            common.append((label,sb,ln))
    if len(common) < 4:
        if not missing:
            return []
        return [_consolidate(
            'COVER_STRUCTURE','Front/Cover page structure','Include the cover elements shown in the approved sample',
            cover.get('guideline_page'),missing,
            'One or more required cover-page elements are missing.',
            'Add the listed cover-page elements.',
            source_text='Visual cover/title-page sample in the approved guideline'
        )]

    def sample_y(x): return (float(x.get('y0',0))+float(x.get('y1',0)))/2
    def actual_y(x):
        b=x.get('bbox',(0,0,0,0)); return (float(b[1])+float(b[3]))/2
    sy=[sample_y(sb) for _,sb,_ in common]; ay=[actual_y(ln) for _,_,ln in common]
    smin,smax=min(sy),max(sy); amin,amax=min(ay),max(ay)
    sspan=max(0.001,smax-smin); aspan=max(1.0,amax-amin)
    page_w=float(student.get('width_pt') or 595)
    occ=list(missing)
    for label,sb,ln in common:
        sr=(sample_y(sb)-smin)/sspan
        ar=(actual_y(ln)-amin)/aspan
        bbox=ln.get('bbox',(0,0,0,0))
        vertical_bad=abs(sr-ar)>0.15
        sx=(float(sb.get('x0',0))+float(sb.get('x1',0)))/2
        sample_centered=abs(sx-0.5) <= 0.14
        ax=(float(bbox[0])+float(bbox[2]))/2/page_w
        actual_centered=abs(ax-0.5) <= 0.14
        alignment_bad=sample_centered != actual_centered
        if vertical_bad or alignment_bad:
            reason=[]
            if vertical_bad: reason.append(f'relative vertical position {ar:.2f} (expected about {sr:.2f})')
            if alignment_bad: reason.append('alignment differs from sample')
            occ.append(_occ(1,label,'; '.join(reason),ln.get('text',''),bbox))

    if not occ:
        return []
    return [_consolidate(
        'COVER_LAYOUT','Front/Cover page layout','Follow the approved cover-page sample',
        cover.get('guideline_page'),occ,
        'One or more major cover-page elements differ materially from the approved sample.',
        'Reposition only the listed cover-page elements to match the approved sample.',
        source_text='Visual cover/title-page sample in the approved guideline'
    )]


def validate_format_profile(profile, info):
    if not profile: return []
    if isinstance(profile,str):
        try: profile=json.loads(profile)
        except Exception: return []
    rules=_rules(profile)
    out=[]
    text=info.get('text','')

    for idx,r in enumerate(rules, start=1):
        severity=r.get('final_severity') or r.get('severity') or ('Mandatory' if bool(r.get('mandatory',True)) else 'Warning')
        if severity=='Ignore' or r.get('enabled') is False:
            continue
        mandatory=(severity=='Mandatory')
        kind=r.get('type'); expected=r.get('value'); source=r.get('source_page')
        section=r.get('source_section',''); evidence=r.get('evidence',''); target=(r.get('target') or '').strip()
        occ=[]

        if kind=='page_size' and expected:
            exp=str(expected).upper()
            if exp=='A4':
                for pg in info.get('pages',[]):
                    w,h=pg['width_mm'],pg['height_mm']
                    portrait=abs(w-210)<=3 and abs(h-297)<=3
                    landscape=abs(h-210)<=3 and abs(w-297)<=3
                    if not (portrait or landscape): occ.append(_occ(pg['page'],'Whole page',f'{w} x {h} mm'))
                if occ: out.append(_consolidate('PAGE_SIZE','Page size','A4 (210 x 297 mm)',source,occ,'One or more pages do not use the required paper size.','Set all listed pages to A4.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='body_font' and expected:
            for pg in info.get('pages',[]):
                det=pg.get('dominant_font')
                if det and not _same_font(det,str(expected)):
                    sample=next((s for s in pg.get('spans',[]) if _same_font(s.get('font'),det) and len(s.get('text',''))>15),None)
                    occ.append(_occ(pg['page'],'Dominant body text',det,(sample or {}).get('text',''),(sample or {}).get('bbox')))
            if occ: out.append(_consolidate('BODY_FONT','Body font',str(expected),source,occ,'The dominant body font differs from the guideline requirement.','Change the affected body text to the required font.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='body_font_size_pt' and expected is not None:
            exp,op=_parse_num_op(expected)
            if exp is None:
                out.append(_not_verified('FONT_SIZE','Body font size',str(expected),source,f'Could not interpret the required size "{expected}"',severity,evidence,section)); continue
            label={'>=':f'at least {exp:g} pt','<=':f'at most {exp:g} pt'}.get(op,f'{exp:g} pt')
            for pg in info.get('pages',[]):
                det=pg.get('dominant_font_size')
                if det is not None and not _num_ok(float(det),exp,op,0.6):
                    sample=next((s for s in pg.get('spans',[]) if abs(float(s.get('size',0))-float(det))<0.2 and len(s.get('text',''))>15),None)
                    occ.append(_occ(pg['page'],'Dominant body text',f'{det} pt',(sample or {}).get('text',''),(sample or {}).get('bbox')))
            if occ: out.append(_consolidate('FONT_SIZE','Body font size',label,source,occ,'The dominant body-text size differs from the guideline requirement.','Set the affected body text to the required size.',severity=severity,source_text=evidence,source_section=section))

        elif kind in {'margin_left_mm','margin_right_mm','margin_top_mm','margin_bottom_mm'} and expected is not None:
            side=kind.replace('margin_','').replace('_mm',''); exp=_parse_num(expected); key=f'{side}_mm'
            if exp is None:
                out.append(_not_verified(kind.upper(),f'{side.title()} margin',str(expected),source,f'Could not interpret the required margin "{expected}"',severity,evidence,section)); continue
            if not _is_generic_target(target):
                # The page-wide margin measurement does not describe a named sub-area
                # such as a chapter-heading block, so do not fail the whole document.
                out.append(_not_verified(kind.upper(),f'{side.title()} margin ({target})',f'at least {exp:g} mm',source,f'Rule applies to "{target}" only, which the page-level margin measurement cannot isolate',severity,evidence,section)); continue
            # PDF does not preserve Word margin settings. Validate that ordinary body
            # text does not intrude into the required margin. Extra whitespace on a
            # sparse page is never a failure, especially for bottom/right margins.
            tolerance_mm=max(2.5, exp*0.08)
            evaluated=[]
            for pg in info.get('pages',[]):
                m=pg.get('margins') or {}
                det=m.get(key)
                if det is None or int(m.get('evidence_lines') or 0) < 1:
                    continue
                evaluated.append((pg, float(det)))
                # A margin fails only when content is closer to the edge than allowed.
                if float(det) < exp - tolerance_mm:
                    occ.append(_occ(pg['page'],f'{side.title()} body-text boundary',f'{det:g} mm'))
            if occ:
                out.append(_consolidate(
                    kind.upper(),f'{side.title()} margin',f'at least {exp:g} mm',source,occ,
                    f'Body text intrudes into the required {side} margin on one or more pages.',
                    f'Move the affected body content inward so the {side} margin is at least {exp:g} mm.',
                    severity=severity,source_text=evidence,source_section=section
                ))

        elif kind=='line_spacing' and expected is not None:
            exp=_parse_num(expected); low=[]
            if exp is None:
                out.append(_not_verified('LINE_SPACING','Line spacing',str(expected),source,f'Could not interpret the required spacing "{expected}"',severity,evidence,section)); continue
            if not _is_generic_target(target):
                # Per-page spacing is a whole-document measurement; a section-scoped
                # spacing rule cannot be decided from it.
                out.append(_not_verified('LINE_SPACING',f'Line spacing ({target})',f'{exp:g}',source,f'Rule applies to "{target}" only, which the per-page spacing measurement cannot isolate',severity,evidence,section)); continue
            for pg in info.get('pages',[]):
                det=pg.get('estimated_line_spacing')
                if det is None: low.append(_occ(pg['page'],'Body paragraphs','Could not estimate'))
                elif abs(float(det)-exp)>0.28: occ.append(_occ(pg['page'],'Estimated body line spacing',f'{det}'))
            if occ: out.append(_consolidate('LINE_SPACING','Line spacing',f'{exp:g}',source,occ,'Estimated line spacing differs from the guideline requirement.','Adjust the listed paragraphs/pages to the required line spacing.',severity=severity,source_text=evidence,source_section=section))
            elif low and mandatory: out.append(_consolidate('LINE_SPACING_VERIFY','Line spacing',f'{exp:g}',source,low,'Line spacing could not be measured reliably on some pages.','Review the listed pages manually.',status='WARNING',severity='Mandatory',source_text=evidence,source_section=section))

        elif kind in {'min_pages','max_pages'} and expected is not None:
            exp=_parse_num(expected)
            name='Minimum page count' if kind=='min_pages' else 'Maximum page count'
            if exp is None:
                out.append(_not_verified(kind.upper(),name,str(expected),source,f'Could not interpret the required page count "{expected}"',severity,evidence,section)); continue
            val=int(exp)
            # A page limit may be scoped to one section ("the abstract must be
            # confined to a single page"); measuring it against the whole document
            # would fail every submission.
            if _is_generic_target(target):
                scope='Whole document'; actual=int(info.get('page_count',0))
            else:
                name=f'{name} ({target})'; scope=f'Section: {target}'
                span=_section_page_span(info,text,target)
                if span is None:
                    out.append(_not_verified(kind.upper(),name,str(val),source,f'Could not locate the section "{target}" to measure its length',severity,evidence,section)); continue
                actual=span[1]-span[0]+1
            ok=actual>=val if kind=='min_pages' else actual<=val
            if not ok:
                occ=[_occ(None,scope,f'{actual} pages')]
                msg=f'{scope} spans {actual} pages, which does not satisfy the guideline limit.'
                corr=f'Adjust {scope.lower()} so it satisfies the required {"minimum" if kind=="min_pages" else "maximum"} of {val} pages.'
                out.append(_consolidate(kind.upper(),name,str(val),source,occ,msg,corr,severity=severity,source_text=evidence,source_section=section))

        elif kind=='required_section' and target:
            page_no, heading_line=_find_actual_heading(info,target)
            if page_no is None:
                occ=[_occ(None,'Document structure','Not found')]
                out.append(_consolidate(f'REQUIRED_SECTION_{idx}',f'Required section: {target}','Present',source,occ,f'The required section "{target}" was not found as an actual document heading.','Add the required section using the guideline-prescribed heading.',severity=severity,source_text=evidence,source_section=section))

        elif kind in {'section_max_words','section_min_words'} and target and expected is not None:
            exp=_parse_num(expected)
            if exp is None:
                out.append(_not_verified(f'SECTION_WORDS_{idx}',f'{target} word count',str(expected),source,f'Could not interpret the required word count "{expected}"',severity,evidence,section)); continue
            sec=_find_section(text,target); limit=int(exp)
            if sec is None:
                # A missing section is reported by its own required_section rule. Not
                # finding it here is no evidence that the word limit was breached.
                out.append(_not_verified(f'SECTION_WORDS_{idx}',f'{target} word count',str(limit),source,f'Could not locate the section "{target}" to measure its word count',severity,evidence,section))
            else:
                count=len(re.findall(r"\b\w+[\w'-]*\b",sec))
                ok=count<=limit if kind=='section_max_words' else count>=limit
                if not ok:
                    pg=_page_for_text(info,target)
                    occ=[_occ(pg,f'Section: {target}',f'{count} words')]
                    direction='not exceed' if kind=='section_max_words' else 'contain at least'
                    out.append(_consolidate(f'SECTION_WORDS_{idx}',f'{target} word count',f'{direction} {limit} words',source,occ,f'The {target} section contains {count} words.','Revise the section to satisfy the required word limit.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='page_numbering_required' and bool(expected):
            missing=[]
            for pg in info.get('pages',[]):
                # Search footer/header edge lines for a standalone arabic/roman page number.
                lines=pg.get('lines',[]); h=pg.get('height_pt') or 842
                found=False
                for ln in lines:
                    t=ln.get('text','').strip(); y0=ln.get('bbox',(0,0,0,0))[1]
                    if (y0 < h*0.08 or y0 > h*0.90) and re.fullmatch(r'(?:\d+|[ivxlcdm]+)',t,re.I): found=True; break
                if not found: missing.append(_occ(pg['page'],'Header/footer page-number area','No page number detected'))
            if missing: out.append(_consolidate('PAGE_NUMBERING','Page numbering','Required',source,missing,'Page numbering was not detected on one or more pages.','Add page numbers according to the guideline.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='section_forbidden_element' and target and expected:
            sec=_find_section(text,target)
            if sec is not None:
                elem=str(expected).lower()
                patterns={
                    'figures':r'(?im)^\s*(?:figure|fig\.)\s*\d+',
                    'tables':r'(?im)^\s*table\s*\d+',
                    'citations':r'\[[0-9,; \-]+\]|\([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?,?\s+\d{4}\)',
                }
                pat=patterns.get(elem)
                if pat and re.search(pat,sec):
                    pg=_page_for_text(info,target)
                    occ=[_occ(pg,f'Section: {target}',f'{elem} detected')]
                    out.append(_consolidate(f'FORBIDDEN_{idx}',f'{target}: prohibited {elem}',f'No {elem}',source,occ,f'The guideline prohibits {elem} in the {target} section.','Remove the prohibited element(s) from the section.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='body_regular_required' and bool(expected):
            for pg in info.get('pages',[]):
                bad=[sp for sp in pg.get('spans',[]) if len(sp.get('text',''))>20 and ('bold' in str(sp.get('font','')).lower() or 'italic' in str(sp.get('font','')).lower())]
                # Only fail when styled long spans dominate enough to look like body text, not headings/emphasis.
                if len(bad)>=3:
                    for sp in bad[:8]: occ.append(_occ(pg['page'],'Body text style',sp.get('font',''),sp.get('text',''),sp.get('bbox')))
            if occ: out.append(_consolidate('BODY_REGULAR','Body text style','Regular',source,occ,'Substantial body-text spans appear bold/italic where regular text is required.','Change the affected body paragraphs to regular style.',severity=severity,source_text=evidence,source_section=section))

        elif kind=='heading_case' and target and expected:
            candidates=_heading_candidates(info,target)
            if not candidates:
                # No line matched the target, so no heading was inspected. PASS here
                # would assure the student of a check that never ran.
                out.append(_not_verified(f'HEADING_CASE_{idx}',f'{target}: Heading Case',str(expected),source,f'No document heading matched the target "{target}"',severity,evidence,section)); continue
            for pg,ln in candidates:
                txt=(ln.get('text') or '').strip()
                letters=''.join(ch for ch in txt if ch.isalpha())
                if str(expected).lower()=='all caps' and letters and letters != letters.upper(): occ.append(_occ(pg['page'],txt,'Not ALL CAPS',txt,ln.get('bbox')))
                elif str(expected).lower()=='title case' and txt and txt != txt.title(): occ.append(_occ(pg['page'],txt,'Not Title Case',txt,ln.get('bbox')))
            if occ: out.append(_consolidate(f'HEADING_CASE_{idx}',f'{target}: Heading Case',str(expected),source,occ,'One or more headings do not match the required capitalization.','Correct the capitalization of the listed headings.',severity=severity,source_text=evidence,source_section=section))

        elif kind in {'heading_alignment','heading_font_size_pt','heading_bold_required'} and target:
            # Match likely heading lines containing the target phrase or numbered headings.
            candidates=_heading_candidates(info,target)
            if not candidates:
                out.append(_not_verified(f'HEADING_{kind}_{idx}',f'{target}: {kind.replace("_"," ").title()}',str(expected),source,f'No document heading matched the target "{target}"',severity,evidence,section)); continue
            for pg,ln in candidates:
                txt=(ln.get('text') or '').strip()
                bbox=ln.get('bbox',(0,0,0,0)); page_w=pg.get('width_pt') or 595
                if kind=='heading_alignment':
                    det='center' if abs(((bbox[0]+bbox[2])/2)-page_w/2) < page_w*0.08 else 'left/right'
                    if str(expected).lower().startswith('center') and det!='center': occ.append(_occ(pg['page'],txt,det,txt,bbox))
                elif kind=='heading_font_size_pt':
                    det=float(ln.get('font_size') or 0); exp,op=_parse_num_op(expected)
                    if exp is not None and det and not _num_ok(det,exp,op,0.7): occ.append(_occ(pg['page'],txt,f'{det:g} pt',txt,bbox))
                elif kind=='heading_bold_required' and bool(expected):
                    # line metadata lacks font flags, infer from spans intersecting bbox
                    bold=any('bold' in str(sp.get('font','')).lower() and abs(sp.get('bbox',(0,0,0,0))[1]-bbox[1])<3 for sp in pg.get('spans',[]))
                    if not bold: occ.append(_occ(pg['page'],txt,'Not bold',txt,bbox))
            if occ:
                exp = expected
                out.append(_consolidate(f'HEADING_{kind}_{idx}',f'{target}: {kind.replace("_"," ").title()}',str(exp),source,occ,'One or more headings do not match the explicit guideline style.','Correct the listed headings to match the guideline.',severity=severity,source_text=evidence,source_section=section))

    out.extend(_cover_layout_results(profile,info))
    # Advisory rules never block a submission. Represent their mismatches as warnings.
    for item in out:
        if item.get('severity') in {'Warning','Recommendation'} and item.get('status')=='FAIL':
            item['status']='WARNING'
    return out


_SUPPORTED_RULE_KINDS = {
    'page_size','body_font','body_font_size_pt','margin_left_mm','margin_right_mm',
    'margin_top_mm','margin_bottom_mm','line_spacing','min_pages','max_pages',
    'required_section','section_max_words','section_min_words','page_numbering_required',
    'section_forbidden_element','body_regular_required','heading_case','heading_alignment',
    'heading_font_size_pt','heading_bold_required'
}


def _rule_display_name(rule, index):
    kind=(rule.get('type') or '').strip()
    target=(rule.get('target') or '').strip()
    labels={
        'page_size':'Page size','body_font':'Body font','body_font_size_pt':'Body font size',
        'margin_left_mm':'Left margin','margin_right_mm':'Right margin','margin_top_mm':'Top margin',
        'margin_bottom_mm':'Bottom margin','line_spacing':'Line spacing','min_pages':'Minimum page count',
        'max_pages':'Maximum page count','page_numbering_required':'Page numbering',
        'body_regular_required':'Body text style'
    }
    if kind=='required_section': return f'Required section: {target}'
    if kind=='section_max_words': return f'{target} maximum word count'
    if kind=='section_min_words': return f'{target} minimum word count'
    if kind=='section_forbidden_element': return f'{target}: prohibited {rule.get("value")}'
    # An untargeted heading rule must still read as a name, not a bare ': ...'.
    heading_labels={'heading_case':'heading case','heading_alignment':'heading alignment',
                    'heading_font_size_pt':'heading font size','heading_bold_required':'heading bold'}
    if kind in heading_labels:
        label=heading_labels[kind]
        return f'{target}: {label}' if target else label.capitalize()
    return labels.get(kind, kind.replace('_',' ').title() or f'Rule {index}')


def _expected_label(rule):
    kind=rule.get('type'); value=rule.get('value'); target=rule.get('target') or ''
    if kind=='page_size': return str(value or '')
    if kind=='body_font_size_pt' or kind=='heading_font_size_pt': return f'{value} pt'
    if kind in {'margin_left_mm','margin_right_mm','margin_top_mm','margin_bottom_mm'}: return f'at least {value} mm'
    if kind=='required_section': return 'Present'
    if kind=='section_max_words': return f'not exceed {value} words'
    if kind=='section_min_words': return f'at least {value} words'
    if kind=='page_numbering_required': return 'Required' if value else 'Not required'
    if kind=='body_regular_required': return 'Regular' if value else str(value)
    if kind=='heading_bold_required': return 'Bold' if value else 'Not bold'
    if kind=='section_forbidden_element': return f'No {value}'
    return str(value if value is not None else target)


def validate_all_profile_rules(profile, info):
    """Return one result row for every active deterministic guideline rule.

    Existing validation logic emits exceptions only. This wrapper evaluates each
    parsed rule independently, preserving the detailed exception result when one
    exists and creating an explicit PASS row when no exception is produced.
    Unsupported rule types are surfaced as NOT VERIFIED rather than falsely PASSing.
    The cover-page profile is also represented as an explicit rule result.
    """
    if not profile: return []
    if isinstance(profile,str):
        try: profile=json.loads(profile)
        except Exception: return []
    rules=_rules(profile)
    rows=[]
    for idx,r in enumerate(rules, start=1):
        severity=r.get('final_severity') or r.get('severity') or ('Mandatory' if bool(r.get('mandatory',True)) else 'Warning')
        if severity=='Ignore' or r.get('enabled') is False:
            continue
        kind=(r.get('type') or '').strip()
        name=_rule_display_name(r,idx)
        expected=_expected_label(r)
        source=str(r.get('source_page') or '')
        section=r.get('source_section','') or ''
        evidence=r.get('evidence','') or 'Explicit requirement parsed from the approved guideline'
        if kind not in _SUPPORTED_RULE_KINDS:
            rows.append({
                'rule_id':f'GUIDE-UNVERIFIED-{idx}','name':name,'category':'Formatting Compliance',
                'severity':severity,'status':'NOT VERIFIED','expected':expected,'detected':'Not automatically verifiable',
                'message':'This parsed guideline rule is listed in the report, but the current deterministic engine does not yet have a reliable validator for it.',
                'correction':'No automatic correction is prescribed. Review this rule manually.',
                'source_page':source,'source_section':section,'source_text':evidence,
                'affected_pages':[],'occurrence_count':0,'occurrences':[]
            })
            continue
        if r.get('value') is None or (isinstance(r.get('value'),str) and not r['value'].strip()):
            # The parser produced no requirement value, so there is nothing to check.
            # Reporting PASS here would wrongly assure the student they comply.
            rows.append(_not_verified(f'NO_VALUE_{idx}',name,expected or '(not parsed)',source,
                                      'The guideline parser extracted no required value for this rule',
                                      severity,evidence,section))
            continue
        mini={'deterministic_rules':[r]}
        issues=validate_format_profile(mini,info)
        if issues:
            row=issues[0]
            row['profile_rule_index']=idx
            rows.append(row)
            continue
        detected='Requirement satisfied'
        occ=[]
        if kind=='required_section':
            pg,ln=_find_actual_heading(info,(r.get('target') or '').strip())
            if pg is not None:
                detected=f'Found on page {pg}'
                occ=[_occ(pg,'Document heading','Present',(ln or {}).get('text',''),(ln or {}).get('bbox'))]
        elif kind=='page_size':
            detected='All checked pages use the required size'
        elif kind.startswith('margin_'):
            detected='No body text intrudes into the required margin'
        elif kind=='body_font':
            vals=[]
            for pg in info.get('pages',[]):
                v=pg.get('dominant_font')
                if v and v not in vals: vals.append(v)
            detected=', '.join(vals) if vals else 'Compliant'
        elif kind=='body_font_size_pt':
            vals=[]
            for pg in info.get('pages',[]):
                v=pg.get('dominant_font_size')
                if v is not None and v not in vals: vals.append(v)
            detected=', '.join(f'{v} pt' for v in vals[:6]) if vals else 'Compliant'
        rows.append({
            'rule_id':f'GUIDE-PASS-{idx}','profile_rule_index':idx,'name':name,
            'category':'Formatting Compliance','severity':severity,'status':'PASS',
            'expected':expected,'detected':detected,'message':'The submitted PDF satisfies this parsed guideline rule.',
            'correction':'No correction required.','source_page':source,'source_section':section,'source_text':evidence,
            'affected_pages':sorted({o.get('page') for o in occ if o.get('page')}),
            'occurrence_count':len(occ),'occurrences':occ,
        })

    cover=(profile or {}).get('cover_page') or {}
    if cover.get('detected'):
        issues=_cover_layout_results(profile,info)
        if issues:
            rows.extend(issues)
        else:
            rows.append({
                'rule_id':'GUIDE-COVER-PASS','name':'Front/Cover page structure' if cover.get('geometry_mode')=='structure_only' else 'Front/Cover page layout',
                'category':'Formatting Compliance','severity':'Mandatory','status':'PASS',
                'expected':'Follow the approved cover/title-page template','detected':'Required cover-page structure/layout satisfied',
                'message':'The submitted cover page satisfies the approved cover/title-page template check.',
                'correction':'No correction required.','source_page':str(cover.get('guideline_page') or ''),
                'source_section':'Cover/Title Page','source_text':'Cover/title-page template in the approved guideline',
                'affected_pages':[1],'occurrence_count':1,
                'occurrences':[_occ(1,'Front/Cover page','Compliant')]
            })
    return rows
