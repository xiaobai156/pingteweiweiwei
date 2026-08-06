import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch

from multi_issue_fetcher import (
    dated_multi_issue_files,
    dated_multi_summary_file,
    run_multi,
)
from ptw.domain.models import Target
from ptw.services.multi_issue import (
    format_multi_failure_report,
    labels_from_results,
    parse_issues,
)
from ptw.storage.atomic import atomic_write_with_backup


class MultiIssueFetcherTests(unittest.TestCase):
    def test_parse_issues_accepts_spaces_and_commas(self):
        self.assertEqual(parse_issues(["187 188,189，190", "188"]), ["187", "188", "189", "190"])

    def test_parse_issues_normalizes_fullwidth_digits_before_deduplication(self):
        self.assertEqual(parse_issues(["８４,84"]), ["84"])

    def test_labels_from_results_handles_fullwidth_tail_digits(self):
        self.assertEqual(labels_from_results(["２尾 步步登高", "1尾 熊出没"]), {"步步登高", "熊出没"})

    def test_labels_from_results_handles_two_tail_result(self):
        self.assertEqual(labels_from_results(["3尾 5尾 胁肩谄笑"]), {"胁肩谄笑"})

    def test_report_only_lists_targets_failed_in_all_issues(self):
        targets = [
            Target("https://example.test/a", "顶部", "甲", "甲", "site_dedicated_tail"),
            Target("https://example.test/b", "尾部", "乙", "乙", "site_dedicated_tail"),
        ]
        report = format_multi_failure_report(
            ["187", "188"],
            targets,
            {"187": {"甲"}, "188": set()},
            {
                "187": {"乙": "未找到：乙 https://example.test/b"},
                "188": {"乙": "失败：乙 https://example.test/b - timeout"},
            },
        )

        self.assertNotIn("1. 甲", report)
        self.assertIn("1. 乙", report)
        self.assertIn("187期：未找到：乙 https://example.test/b", report)
        self.assertIn("188期：失败：乙 https://example.test/b - timeout", report)

    def test_summary_filename_contains_issue_combination(self):
        with patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path("output")):
            path = dated_multi_summary_file(["187", "188"], datetime(2026, 7, 11))

        self.assertEqual(path, Path("output/7.11平特尾187-188期多期失败汇总.txt"))

    def test_atomic_write_preserves_existing_file_as_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.txt"
            path.write_text("old", encoding="utf-8")

            atomic_write_with_backup(path, "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            backups = list(path.parent.glob("result.txt.bak*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old")

    def test_run_multi_fetches_each_unique_url_once_and_shares_snapshots(self):
        targets = [
            Target("https://example.test/shared", "顶部", "甲", "甲", "site_dedicated_tail"),
            Target("https://example.test/shared", "尾部", "乙", "乙", "site_dedicated_tail"),
            Target("https://example.test/other", "顶部", "丙", "丙", "site_dedicated_tail"),
        ]

        def extract(text, issue, label, keyword, position, mode):
            return f"{issue[-1]}尾 {label}" if text == f"snapshot:{keyword if keyword == '丙' else 'shared'}" else ""

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=targets), \
                patch(
                    "multi_issue_fetcher.tail_fetcher.fetch_text",
                    side_effect=lambda url: "snapshot:shared" if url.endswith("shared") else "snapshot:丙",
                ) as fetch_text, \
                patch("multi_issue_fetcher.tail_fetcher.audit_result_from_text") as audit_result, \
                patch("multi_issue_fetcher.tail_fetcher.update_recent_10_cache") as cache_update:
            audit_result.side_effect = lambda text, issue, target: unittest.mock.Mock(
                label=target.label,
                url=target.url,
                issue=issue,
                result=extract(text, issue, target.label, target.keyword, target.position, target.mode),
                keyword=target.keyword,
                position=target.position,
                mode=target.mode,
                matched_window=text,
                risk_flags=(),
            )

            summary_path = run_multi(["187", "188"])

            self.assertEqual(fetch_text.call_count, 2)
            self.assertCountEqual(
                fetch_text.call_args_list,
                [call("https://example.test/shared"), call("https://example.test/other")],
            )
            cache_update.assert_not_called()
            self.assertTrue(summary_path.exists())
            for issue in ("187", "188"):
                success_path, fail_path, audit_path = dated_multi_issue_files(issue)
                self.assertTrue(success_path.exists())
                self.assertTrue(fail_path.exists())
                self.assertFalse(audit_path.exists())

    def test_run_multi_retries_each_unmatched_url_only_once_for_all_issues(self):
        target = Target("https://example.test/retry", "顶部", "甲", "甲", "site_dedicated_tail")
        snapshots = iter(["first", "second"])

        def extract(text, issue, label, keyword, position, mode):
            return f"1尾 {label}" if text == "second" and issue == "188" else ""

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", side_effect=lambda url: next(snapshots)) as fetch_text, \
                patch("multi_issue_fetcher.tail_fetcher.audit_result_from_text") as audit_result:
            audit_result.side_effect = lambda text, issue, current: unittest.mock.Mock(
                label=current.label,
                url=current.url,
                issue=issue,
                result=extract(text, issue, current.label, current.keyword, current.position, current.mode),
                keyword=current.keyword,
                position=current.position,
                mode=current.mode,
                matched_window=text,
                risk_flags=(),
            )

            summary_path = run_multi(["187", "188"])

            self.assertEqual(fetch_text.call_count, 2)
            self.assertNotIn("1. 甲", summary_path.read_text(encoding="utf-8"))

    def test_run_multi_keeps_progress_line_format(self):
        target = Target("https://example.test/a", "顶部", "甲", "甲", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", return_value="snapshot"), \
                patch("multi_issue_fetcher.tail_fetcher.audit_result_from_text") as audit_result, \
                patch("multi_issue_fetcher.tail_fetcher.log") as log:
            audit_result.return_value = unittest.mock.Mock(
                label="甲", url=target.url, issue="187", result="1尾 甲", keyword="甲",
                position="顶部", mode="site_dedicated_tail", matched_window="snapshot", risk_flags=(),
            )

            run_multi(["187"])

            progress_lines = [args[0] for args, _ in log.call_args_list if args and args[0].startswith("[进度 ")]
            self.assertEqual(len(progress_lines), 1)
            self.assertRegex(
                progress_lines[0],
                r"^\[进度 1/1 100% 成功 1 失败 0 用时 \d+\.\d+s\] 当前: 甲$",
            )

    def test_run_multi_writes_detailed_not_found_reason(self):
        target = Target("https://example.test/a", "顶部", "甲", "专属模块", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", return_value="专属模块\n85期 平特一尾【9]"):
            run_multi(["84"])

            fail_path = dated_multi_issue_files("84")[1]
            self.assertIn("专属模块中未找到指定84期", fail_path.read_text(encoding="utf-8"))

    def test_run_multi_preserves_target_specific_value_policy(self):
        target = Target(
            "https://example.test/a",
            "顶部",
            "甲",
            "专属模块",
            "site_dedicated_tail",
            value_policy="explicit_tail_only",
        )
        text = "专属模块\n210期 40-37-15-17-42-48 T49 +9=9尾√"
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", return_value=text):
            run_multi(["210"])
            success_path, fail_path, _ = dated_multi_issue_files("210")
            self.assertEqual("", success_path.read_text(encoding="utf-8"))
            self.assertIn(
                "没有符合专属格式的有效尾数",
                fail_path.read_text(encoding="utf-8"),
            )

    def test_run_multi_records_fetch_failed_in_audit(self):
        target = Target("https://example.test/a", "顶部", "甲", "甲", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", side_effect=urllib.error.URLError("offline")):
            run_multi(["187"])

            audit_path = dated_multi_issue_files("187")[2]
            self.assertFalse(audit_path.exists())

    def test_run_multi_records_runtime_fetch_failure_without_aborting(self):
        target = Target("https://example.test/a", "顶部", "甲", "甲", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)), \
                patch("multi_issue_fetcher.tail_fetcher.load_targets", return_value=[target]), \
                patch("multi_issue_fetcher.tail_fetcher.fetch_text", side_effect=RuntimeError("iframe抓取失败：frame")):
            summary_path = run_multi(["187"])

            success_path, fail_path, _ = dated_multi_issue_files("187")
            self.assertTrue(summary_path.exists())
            self.assertTrue(success_path.exists())
            self.assertIn("iframe抓取失败：frame", fail_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
