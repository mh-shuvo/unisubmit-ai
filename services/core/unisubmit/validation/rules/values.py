"""Reading rule values: numbers with operators and units, and rule targets."""

import json
import re
from typing import Any

OPERATOR_PREFIX_RE = re.compile(r"^\s*(>=|<=|=>|=<|≥|≤|>|<|~|=)\s*")


OPERATOR_WORDS_RE = re.compile(
    r"^\s*(?:at\s+least|minimum(?:\s+of)?|min(?:\.|imum)?|no\s+less\s+than|not\s+less\s+than)\s+"
    r"|^\s*(?:at\s+most|maximum(?:\s+of)?|max(?:\.|imum)?|no\s+more\s+than|not\s+more\s+than|"
    r"up\s+to|not\s+exceeding|must\s+not\s+exceed)\s+",
    re.I,
)


SPACING_SUFFIX_RE = re.compile(
    r"\s*(?:line\s+)?(?:spaces?|spacing|spaced|lines?|pts?|points?|mm|cm|inch(?:es)?|words?|pages?)\.?$",
    re.I,
)


GTE_WORDS = ("at least", "minimum", "min", "no less than", "not less than")


LTE_WORDS = (
    "at most",
    "maximum",
    "max",
    "no more than",
    "not more than",
    "up to",
    "not exceeding",
    "must not exceed",
)


WORD_NUMBERS = {
    "single": 1.0,
    "one": 1.0,
    "one and a half": 1.5,
    "one-and-a-half": 1.5,
    "1.5": 1.5,
    "double": 2.0,
    "two": 2.0,
    "triple": 3.0,
    "three": 3.0,
}


GENERIC_TARGETS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "all",
    "all pages",
    "whole document",
    "document",
    "entire document",
    "body",
    "body text",
    "general text",
    "main text",
    "text",
}


def parse_num_op(value: object) -> tuple[float | None, str]:
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
    op = "=="
    if any(low.startswith(w) for w in GTE_WORDS):
        op = ">="
    elif any(low.startswith(w) for w in LTE_WORDS):
        op = "<="
    s = OPERATOR_WORDS_RE.sub("", s).strip()
    m = OPERATOR_PREFIX_RE.match(s)
    if m:
        sym = m.group(1)
        if sym in (">=", "=>", "≥", ">"):
            op = ">="
        elif sym in ("<=", "=<", "≤", "<"):
            op = "<="
        s = s[m.end() :].strip()
    s = SPACING_SUFFIX_RE.sub("", s).strip().lower()
    word_val = WORD_NUMBERS.get(s)
    if word_val is not None:
        return word_val, op
    try:
        return float(s), op
    except ValueError:
        return None, op


def parse_num(value: object) -> float | None:
    """Return just the numeric part of a rule value, or None when unparseable."""
    return parse_num_op(value)[0]


def num_ok(detected: float, exp: float, op: str, tol: float) -> bool:
    """Compare a detected value against the requirement, honouring the operator."""
    if op == ">=":
        return detected >= exp - tol
    if op == "<=":
        return detected <= exp + tol
    return abs(detected - exp) <= tol


def is_generic_target(target: object) -> bool:
    """True when a rule applies document-wide rather than to one named section."""
    return str(target or "").strip().lower() in GENERIC_TARGETS


def strip_qualifier(target: object) -> str:
    """Drop parenthetical qualifiers Qwen appends to section targets.

    'Abstract (M.S. thesis)' → 'Abstract'. Without this the literal target is
    never matchable as a heading, so the rule fails on every submission.
    """
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*", " ", str(target or ""))
    return re.sub(r"\s+", " ", t).strip(" -:–—")


def profile_rules(profile: Any) -> list[dict[str, Any]]:
    if not profile:
        return []
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError:
            return []
    if not isinstance(profile, dict):
        return []
    # v9 profile first; retain v8 compatibility.
    return profile.get("deterministic_rules") or profile.get("rules") or []
