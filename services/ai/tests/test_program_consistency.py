"""Offline regression tests for the program-guideline consistency gate.

DEF-023: a guideline whose text names several degree levels must not be
blocked for any program of one of those levels. Only a guideline naming
exclusively other levels is a mismatch. The vocabulary covers formal
(B.Sc./M.Sc./Doctor of Philosophy) and loose (Bachelor's/Masters/PhD)
wording.
"""
import unittest

from unisubmit_ai import main as service

DUAL_LEVEL = (
    "This template applies to Bachelor of Science (B.Sc.) and "
    "Master of Science (M.Sc.) theses."
)
DUAL_LEVEL_LOOSE = (
    "This template applies to the Bachelor of Science and the "
    "Masters programme alike."
)
BACHELOR_ONLY = "Guideline for the Bachelor of Science thesis."
MASTER_ONLY = "Guideline for the Master of Science thesis."
DOCTORAL_ONLY = "Guideline for the Doctor of Philosophy (PhD) dissertation."


def check(program, text):
    return service._program_guideline_consistency(program, [{"text": text}])


class ProgramGuidelineConsistencyTests(unittest.TestCase):
    def test_dual_level_guideline_allows_master_program(self):
        """DEF-023: the defect's exact shape — MSc program, dual-level text."""
        self.assertFalse(check("M.Sc. in CSE", DUAL_LEVEL)["mismatch"])

    def test_dual_level_guideline_allows_bachelor_program(self):
        """DEF-023: the mirror branch blocked BSc programs the same way."""
        self.assertFalse(check("B.Sc. in CSE", DUAL_LEVEL)["mismatch"])

    def test_bachelor_only_guideline_still_blocks_master_program(self):
        self.assertTrue(check("M.Sc. in CSE", BACHELOR_ONLY)["mismatch"])

    def test_master_only_guideline_still_blocks_bachelor_program(self):
        self.assertTrue(check("B.Sc. in CSE", MASTER_ONLY)["mismatch"])

    def test_matching_level_guideline_passes(self):
        self.assertFalse(check("M.Sc. in CSE", MASTER_ONLY)["mismatch"])

    def test_loose_masters_wording_counts_as_master_level(self):
        """'Masters programme' with no M.Sc. must still clear an MSc program."""
        self.assertFalse(check("M.Sc. in CSE", DUAL_LEVEL_LOOSE)["mismatch"])

    def test_phd_program_matches_doctoral_guideline(self):
        self.assertFalse(check("PhD in CSE", DOCTORAL_ONLY)["mismatch"])

    def test_phd_program_blocked_by_bachelor_only_guideline(self):
        self.assertTrue(check("PhD in CSE", BACHELOR_ONLY)["mismatch"])

    def test_bachelor_program_blocked_by_doctoral_only_guideline(self):
        self.assertTrue(check("B.Sc. in CSE", DOCTORAL_ONLY)["mismatch"])

    def test_guideline_without_degree_statement_passes(self):
        self.assertFalse(
            check("M.Sc. in CSE", "Use A4 paper with 25 mm margins.")["mismatch"]
        )


if __name__ == "__main__":
    unittest.main()
