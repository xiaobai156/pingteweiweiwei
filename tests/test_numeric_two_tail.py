import unittest

from config_fixtures import all_targets
from ptw.config import load_targets
from ptw.domain.models import Target
from ptw.parsing.pipeline import extract_result_from_text


class NumericTwoTailParserTests(unittest.TestCase):
    def _target(self) -> Target:
        return Target(
            "https://example.test/topic/1.html",
            "顶部",
            "目标",
            "目标",
            "numeric_two_tail",
        )

    def test_reads_numeric_two_tail_marker_and_preserves_order(self):
        cases = [
            ("216期:平特２尾《4尾3尾》开00准", "4尾 3尾"),
            ("216期☆平特⒉尾☆〖≤1尾3尾≥〗开：00准", "1尾 3尾"),
            ("216期:平特２尾《5尾7尾》开:00准", "5尾 7尾"),
        ]

        for line, expected in cases:
            with self.subTest(line=line):
                result = extract_result_from_text(
                    f"目标\n{line}",
                    "216",
                    "目标",
                    keyword="目标",
                    mode="numeric_two_tail",
                )
                self.assertEqual(f"{expected} 目标", result)

    def test_rejects_incomplete_or_unmarked_two_tail_values(self):
        cases = [
            "216期:平特２尾《4尾》开00准",
            "216期:平特２尾《4尾3尾5尾》开00准",
            "216期:平特一尾《4尾3尾》开00准",
        ]

        for line in cases:
            with self.subTest(line=line):
                result = extract_result_from_text(
                    f"目标\n{line}",
                    "216",
                    "目标",
                    keyword="目标",
                    mode="numeric_two_tail",
                )
                self.assertEqual("", result)

    def test_rejects_conflicting_same_issue_numeric_two_tail_candidates(self):
        text = (
            "目标\n"
            "216期:平特２尾《4尾3尾》开00准\n"
            "目标\n"
            "216期:平特２尾《5尾7尾》开00准"
        )

        result = extract_result_from_text(
            text,
            "216",
            "目标",
            keyword="目标",
            mode="numeric_two_tail",
        )

        self.assertEqual("", result)

    def test_verified_targets_use_numeric_two_tail_mode(self):
        labels = {"步步登高", "裁长补短", "无肠可断"}
        targets = {target.label: target for target in all_targets() if target.label in labels}

        self.assertEqual(labels, set(targets))
        self.assertTrue(all(target.mode == "numeric_two_tail" for target in targets.values()))


if __name__ == "__main__":
    unittest.main()
