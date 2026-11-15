"""Merging parsed semantic rules (`guidelines/profile.py`).

DEF-044: the web app's copy misread `source_pages` given as a number or a string.
"""

from unisubmit.guidelines.profile import dedupe_semantic_requirements


def pages(source_pages: object) -> list[int]:
    profile = {"semantic_requirements": [{"requirement": "r", "source_pages": source_pages}]}
    return dedupe_semantic_requirements(profile)["semantic_requirements"][0]["source_pages"]


def test_a_number_is_one_page() -> None:
    assert pages(12) == [12]


def test_a_string_is_one_page_not_one_per_digit() -> None:
    assert pages("12") == [12]


def test_duplicates_on_two_pages_merge_and_cite_both() -> None:
    profile = {
        "semantic_requirements": [
            {"requirement": "The abstract must be one page", "source_pages": [1]},
            {"requirement": "The abstract shall be one page.", "source_page": 2},
        ]
    }

    merged = dedupe_semantic_requirements(profile)["semantic_requirements"]

    assert [(r["source_pages"], r["merged_duplicate_count"]) for r in merged] == [([1, 2], 1)]
