"""The types every rule module shares."""

from dataclasses import dataclass
from typing import Any

Info = dict[str, Any]  # what `pdf_utils.inspect_pdf` returns
Result = dict[str, Any]  # one row of a validation report


@dataclass(frozen=True, slots=True)
class ProfileRule:
    """One enabled rule of a parsed guideline profile, as a check receives it."""

    raw: dict[str, Any]
    idx: int
    severity: str
    mandatory: bool
    kind: str
    expected: Any
    source: Any
    section: str
    evidence: str
    target: str
