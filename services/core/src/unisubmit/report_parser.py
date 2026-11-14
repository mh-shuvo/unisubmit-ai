import re
from html import escape

SECTION_ALIASES = {
    'COMPLIANCE ASSESSMENT': 'compliance',
    'REQUIRED CORRECTIONS': 'corrections',
    'IMPROVEMENT SUGGESTIONS': 'suggestions',
    'GROUNDING NOTE': 'grounding',
}


def _clean_heading(line: str) -> str:
    line = line.strip()
    line = re.sub(r'^#{1,6}\s*', '', line)
    line = re.sub(r'^\*\*(.*?)\*\*$', r'\1', line)
    return line.strip().rstrip(':').upper()


def _status_from_text(text: str) -> str:
    m = re.search(r'(?im)^\s*[-*]?\s*(?:\*\*)?status(?:\*\*)?\s*:\s*(PASS|FAIL|PARTIAL|NOT VERIFIABLE|REVISION REQUIRED|PASSED)', text or '')
    if m:
        value = m.group(1).upper()
        if value == 'PASS':
            return 'PASSED'
        return value
    upper = (text or '').upper()
    for candidate in ('REVISION REQUIRED', 'NOT VERIFIABLE', 'PARTIAL', 'FAIL', 'PASSED', 'PASS'):
        if candidate in upper:
            return 'PASSED' if candidate == 'PASS' else candidate
    return 'NOT VERIFIABLE'


def parse_ai_report(text: str) -> dict:
    """Parse the constrained Markdown-like response returned by the RAG reviewer.

    Keeps raw text for audit while exposing predictable sections for the UI/PDF.
    """
    text = (text or '').replace('\r\n', '\n').strip()
    sections = {k: [] for k in SECTION_ALIASES.values()}
    current = None

    for raw_line in text.split('\n'):
        heading = _clean_heading(raw_line)
        if heading in SECTION_ALIASES:
            current = SECTION_ALIASES[heading]
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())

    # Fallback for older/unstructured output.
    if not any(any(line.strip() for line in lines) for lines in sections.values()):
        sections['compliance'] = text.split('\n') if text else []

    normalized = {}
    for name, lines in sections.items():
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'^[-*•]\s*', '', line)
            if name == 'compliance' and re.match(r'(?i)^(?:\*\*)?status(?:\*\*)?\s*:', line):
                continue
            cleaned.append(line)
        normalized[name] = cleaned

    return {
        'status': _status_from_text('\n'.join(normalized['compliance']) or text),
        'compliance': normalized['compliance'],
        'corrections': normalized['corrections'],
        'suggestions': normalized['suggestions'],
        'grounding': normalized['grounding'],
        'raw': text,
    }


def inline_markdown_html(value: str) -> str:
    """Tiny safe Markdown renderer for AI output: emphasis, code, and source markers."""
    value = escape(value or '')
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    value = re.sub(r'\[(SOURCE\s+\d+)(?:\s*\|\s*Page\s*(\d+))?\]',
                   lambda m: f'<span class="source-ref">{m.group(1)}' + (f' · p.{m.group(2)}' if m.group(2) else '') + '</span>',
                   value, flags=re.I)
    return value
