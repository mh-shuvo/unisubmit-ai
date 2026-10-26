import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz

class PDFError(Exception):
    pass

PT_TO_MM = 25.4 / 72.0


def _norm_font(name: str) -> str:
    n = (name or '').split('+')[-1].lower().replace('-', ' ').replace('_', ' ')
    n = re.sub(r'\s+', ' ', n).strip()
    if 'timesnewroman' in n.replace(' ', '') or ('times' in n and 'roman' in n):
        return 'Times New Roman'
    if 'arial' in n:
        return 'Arial'
    if 'calibri' in n:
        return 'Calibri'
    if 'cambria' in n:
        return 'Cambria'
    return (name or '').split('+')[-1]


def _weighted_mode(items):
    if not items:
        return None
    c = Counter()
    for value, weight in items:
        c[value] += max(1, weight)
    return c.most_common(1)[0][0]


def _percentile(values, p):
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals)-1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c-k) + vals[c] * (k-f)


def inspect_pdf(path: str):
    p = Path(path)
    if p.suffix.lower() != '.pdf':
        raise PDFError('Only PDF files are allowed.')
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise PDFError(f'Invalid or corrupted PDF: {e}')
    if doc.needs_pass:
        doc.close(); raise PDFError('Password-protected PDFs are not allowed.')

    texts, page_sizes, font_sizes, page_details = [], [], [], []
    all_font_weighted, all_size_weighted = [], []

    for page_idx, page in enumerate(doc):
        rect = page.rect
        page_sizes.append((round(rect.width,1), round(rect.height,1)))
        page_text = page.get_text('text')
        texts.append(page_text)
        d = page.get_text('dict')
        spans, lines_meta = [], []
        page_font_weighted, page_size_weighted = [], []
        content_x0, content_x1, content_y0, content_y1 = [], [], [], []
        body_lines = []
        line_spacings = []

        for block in d.get('blocks', []):
            for line in block.get('lines', []):
                bbox = line.get('bbox') or (0,0,0,0)
                line_text_parts=[]; line_sizes=[]
                for span in line.get('spans', []):
                    text = (span.get('text') or '').strip()
                    if not text:
                        continue
                    size = round(float(span.get('size') or 0),1)
                    raw_font = span.get('font') or ''
                    font = _norm_font(raw_font)
                    sb = tuple(round(float(x),1) for x in (span.get('bbox') or (0,0,0,0)))
                    # _norm_font collapses every weight to the family name, so record
                    # the style here or 'TimesNewRomanPS-BoldMT' is indistinguishable
                    # from the regular face and no bold check can ever succeed.
                    flags = int(span.get('flags') or 0)
                    low_font = raw_font.lower()
                    item={'text':text,'font':font,'size':size,'bbox':sb,
                          'bold':bool(flags & 16) or 'bold' in low_font,
                          'italic':bool(flags & 2) or 'italic' in low_font or 'oblique' in low_font}
                    spans.append(item)
                    w=len(text)
                    font_sizes.append(size); all_font_weighted.append((font,w)); all_size_weighted.append((size,w))
                    page_font_weighted.append((font,w)); page_size_weighted.append((size,w)); line_sizes.append(size); line_text_parts.append(text)
                if line_text_parts:
                    lb=tuple(round(float(x),1) for x in bbox)
                    line_text=' '.join(line_text_parts)
                    avg_size=round(sum(line_sizes)/len(line_sizes),1) if line_sizes else 0
                    lines_meta.append({'text':line_text,'bbox':lb,'font_size':avg_size})
                    # Generic content area retained for diagnostics only.
                    ymid=(lb[1]+lb[3])/2
                    if rect.height*0.07 <= ymid <= rect.height*0.93:
                        content_x0.append(lb[0]); content_x1.append(lb[2]); content_y0.append(lb[1]); content_y1.append(lb[3])

                    # Robust margin evidence should come from ordinary body-like lines,
                    # not centered titles, TOC dot leaders, tables/captions, or short labels.
                    stripped=line_text.strip()
                    letters=''.join(ch for ch in stripped if ch.isalpha())
                    is_all_caps=bool(letters) and letters == letters.upper()
                    is_dot_leader='....' in stripped
                    is_caption=bool(re.match(r'^(?:figure|table)\s+\d', stripped, re.I))
                    is_numbered_heading=bool(re.match(r'^(?:chapter\s+\w+|\d+(?:\.\d+)+)\b', stripped, re.I))
                    long_enough=len(stripped) >= 35
                    # Most thesis body lines are 10-14 pt. This keeps headings out while
                    # remaining tolerant across departmental templates.
                    body_size=9.0 <= avg_size <= 14.5
                    if long_enough and body_size and not is_all_caps and not is_dot_leader and not is_caption and not is_numbered_heading:
                        body_lines.append({'text': stripped, 'bbox': lb, 'font_size': avg_size})

        # Line spacing is measured over the page's body lines in reading order
        # rather than within a MuPDF block. MuPDF starts a new block as soon as the
        # baseline gap passes roughly 1.5 em, so a genuinely double-spaced page puts
        # every line in a block of its own and a per-block pairing can never observe
        # the 2.0 the guideline asks for.
        ordered = sorted(body_lines, key=lambda x: x['bbox'][1])
        for a, b in zip(ordered, ordered[1:]):
            if not (a['font_size'] and b['font_size']):
                continue
            # Neighbouring lines of one text column overlap horizontally; lines in
            # different columns do not, and their gap is not a line spacing.
            if min(a['bbox'][2], b['bbox'][2]) <= max(a['bbox'][0], b['bbox'][0]):
                continue
            baseline_gap = b['bbox'][1] - a['bbox'][1]
            fs = (a['font_size'] + b['font_size']) / 2
            if 0.8*fs <= baseline_gap <= 3.0*fs:
                line_spacings.append(baseline_gap/fs)

        margins={}
        # For PDF validation we cannot recover the original Word margin settings.
        # What we can verify reliably is the body-text frame and whether body content
        # intrudes into a required margin. Sparse pages must never look like a huge
        # bottom-margin failure simply because the page has little text.
        margin_evidence = body_lines if body_lines else []
        if margin_evidence:
            bx0=[x['bbox'][0] for x in margin_evidence]
            bx1=[x['bbox'][2] for x in margin_evidence]
            by0=[x['bbox'][1] for x in margin_evidence]
            by1=[x['bbox'][3] for x in margin_evidence]
            left=_percentile(bx0,0.10)
            # Right edge uses the longest/fullest body lines only. The 90th percentile
            # avoids short lines making the right margin appear too large.
            right=_percentile(bx1,0.90)
            # Top/bottom are intrusion boundaries, not estimates of paragraph extent.
            top=min(by0)
            bottom=max(by1)
            margins={
                'left_mm':round(left*PT_TO_MM,1),
                'right_mm':round((rect.width-right)*PT_TO_MM,1),
                'top_mm':round(top*PT_TO_MM,1),
                'bottom_mm':round((rect.height-bottom)*PT_TO_MM,1),
                'method':'body_text_frame',
                'evidence_lines':len(margin_evidence),
            }
        page_details.append({
            'page':page_idx+1,
            'width_pt':round(rect.width,1),'height_pt':round(rect.height,1),
            'width_mm':round(rect.width*PT_TO_MM,1),'height_mm':round(rect.height*PT_TO_MM,1),
            'dominant_font':_weighted_mode(page_font_weighted),
            'dominant_font_size':_weighted_mode(page_size_weighted),
            'estimated_line_spacing':round(_percentile(line_spacings,0.5),2) if line_spacings else None,
            'margins':margins,
            'spans':spans,
            'lines':lines_meta,
        })

    doc.close()
    text='\n'.join(texts).strip()
    return {
        'page_count':len(page_sizes),'text':text,'searchable':len(text)>50,
        'page_sizes':page_sizes,'font_sizes':font_sizes,'file_size':p.stat().st_size,
        'dominant_font':_weighted_mode(all_font_weighted),
        'dominant_font_size':_weighted_mode(all_size_weighted),
        'pages':page_details,
    }
