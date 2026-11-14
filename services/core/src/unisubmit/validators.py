import re
from collections import Counter

A4_PTS = (595.0, 842.0)

def _normalize_heading(text):
    t=re.sub(r'\s+',' ',(text or '').strip())
    t=re.sub(r'^chapter\s+(?:[0-9ivxlcdm]+|[a-z]+)\s*[:.\-–—]?\s*','',t,flags=re.I)
    t=re.sub(r'^\d+(?:\.\d+)*\s*[:.\-–—]?\s*','',t)
    return re.sub(r'[^a-z0-9]+',' ',t.lower()).strip()


def _section_heading_exists(info,target):
    wanted=_normalize_heading(target)
    for pg in info.get('pages',[]):
        text='\n'.join(x.get('text','') for x in pg.get('lines',[]))
        if re.search(r'(?im)^\s*table\s+of\s+contents\s*$',text):
            continue
        for ln in pg.get('lines',[]):
            txt=(ln.get('text') or '').strip()
            if len(txt)<=180 and _normalize_heading(txt)==wanted:
                return True
    return False

def _result(rule, status, detected, message):
    if status == "FAIL" and rule.severity in {"Warning", "Recommendation"}:
        status = "WARNING"
    return {
        "rule_id": rule.id,
        "name": rule.name,
        "category": rule.category,
        "severity": rule.severity,
        "status": status,
        "expected": rule.expected,
        "detected": detected,
        "message": message,
        "source_page": rule.source_page,
        "source_text": rule.source_text,
    }

def validate_rule(rule, info):
    text = info["text"]
    v = rule.validator
    target = (rule.target or "").strip()
    expected = (rule.expected or "").strip()

    if v == "searchable_pdf":
        ok = info["searchable"]
        return _result(rule, "PASS" if ok else "FAIL", str(ok), "PDF contains machine-readable text." if ok else "PDF appears to be image-only or non-searchable.")

    if v == "max_file_mb":
        max_mb = float(expected or 20)
        actual = info["file_size"] / (1024 * 1024)
        ok = actual <= max_mb
        return _result(rule, "PASS" if ok else "FAIL", f"{actual:.2f} MB", f"Maximum allowed file size is {max_mb:g} MB.")

    if v == "max_pages":
        limit = int(float(expected))
        ok = info["page_count"] <= limit
        return _result(rule, "PASS" if ok else "FAIL", str(info["page_count"]), f"Maximum allowed page count is {limit}.")

    if v == "min_pages":
        limit = int(float(expected))
        ok = info["page_count"] >= limit
        return _result(rule, "PASS" if ok else "FAIL", str(info["page_count"]), f"Minimum required page count is {limit}.")

    if v == "a4_page_size":
        bad = 0
        for w, h in info["page_sizes"]:
            portrait = abs(w - A4_PTS[0]) < 12 and abs(h - A4_PTS[1]) < 12
            landscape = abs(h - A4_PTS[0]) < 12 and abs(w - A4_PTS[1]) < 12
            if not (portrait or landscape):
                bad += 1
        ok = bad == 0
        return _result(rule, "PASS" if ok else "FAIL", f"{bad} non-A4 page(s)", "All pages should be A4 size.")

    if v == "section_present":
        found = _section_heading_exists(info, target)
        return _result(rule, "PASS" if found else "FAIL", "Found" if found else "Not found", f"Required section '{target}' must be present as an actual heading.")

    if v in ("section_max_words", "max_words"):
        # Section-scoped maximum word count
        # Best-effort extraction from target heading to next all-caps/numbered heading.
        pattern = rf"(?is){re.escape(target)}\s*\n(.*?)(?=\n\s*(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Z\s&:-]{{3,}}\n|\Z)"
        m = re.search(pattern, text)
        if not m:
            return _result(rule, "FAIL", "Section not found", f"Could not locate '{target}' to measure word count.")
        words = re.findall(r"\b\w+[\w'-]*\b", m.group(1))
        limit = int(float(expected))
        ok = len(words) <= limit
        return _result(rule, "PASS" if ok else "FAIL", f"{len(words)} words", f"'{target}' must not exceed {limit} words.")

    if v == "dominant_font_size":
        if not info["font_sizes"]:
            return _result(rule, "WARNING", "Unknown", "Font size could not be reliably extracted from the PDF.")
        dominant = Counter(info["font_sizes"]).most_common(1)[0][0]
        exp = float(expected)
        ok = abs(dominant - exp) <= 0.6
        return _result(rule, "PASS" if ok else "FAIL", f"{dominant} pt", f"Dominant body text font size should be approximately {exp:g} pt.")

    if v == "contains_text":
        ok = expected.lower() in text.lower()
        return _result(rule, "PASS" if ok else "FAIL", "Found" if ok else "Not found", f"Document must contain: {expected}")

    return _result(rule, "WARNING", "Not evaluated", f"Validator '{v}' is not implemented yet.")

def run_rules(rules, info):
    results = [validate_rule(r, info) for r in rules if r.enabled]
    mandatory_failed = any(r["severity"] == "Mandatory" and r["status"] == "FAIL" for r in results)
    return results, ("REVISION REQUIRED" if mandatory_failed else "PASSED")
