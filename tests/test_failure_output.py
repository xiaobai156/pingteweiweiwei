import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multi_issue_fetcher import dated_multi_issue_files, run_multi
from ptw.domain.models import Target
from ptw.storage.atomic import atomic_write_success_optional_failure
from ptw.storage.output import format_failure_output
from tail_fetcher import run


class FailureOutputTests(unittest.TestCase):
    def setUp(self):
        self.target = Target(
            "https://example.test/a",
            "尾部",
            "甲",
            "甲",
            "site_dedicated_tail",
        )

    def test_formats_not_found_failure_with_context_and_category_summary(self):
        errors = [
            "未找到：甲 https://example.test/a - 指定216期不在尾部方向最近3组有效候选内"
        ]

        self.assertEqual(
            format_failure_output(errors, [self.target], "216"),
            "失败 甲 https://example.test/a 方向: bottom 期数: 216\n"
            "阶段: 指定期数校验 原因: 指定216期不在尾部方向最近3组有效候选内\n\n"
            "失败分类统计\n"
            "方向范围外 1条\n",
        )

    def test_formats_fetch_and_cache_failures_with_distinct_stages(self):
        errors = [
            "失败：甲 https://example.test/a - 脚本抓取失败：missing.js",
            "失败：缓存 recent_10_cache.json - 缓存更新未完成",
        ]

        output = format_failure_output(errors, [self.target], "216")

        self.assertIn(
            "失败 甲 https://example.test/a 方向: bottom 期数: 216\n"
            "阶段: 抓取 原因: 脚本抓取失败：missing.js",
            output,
        )
        self.assertIn(
            "失败 缓存 recent_10_cache.json 方向: - 期数: 216\n"
            "阶段: 缓存更新 原因: 缓存更新未完成",
            output,
        )
        self.assertIn("抓取失败 1条", output)
        self.assertIn("缓存更新失败 1条", output)

    def test_single_issue_failure_file_uses_new_format(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "tail_fetcher.load_targets", return_value=[self.target]
        ), patch(
            "tail_fetcher.fetch_text", return_value="甲\n215期 平特一尾【9】"
        ):
            fail_path = Path(temp_dir) / "fail.txt"
            run(
                "216",
                Path(temp_dir) / "success.txt",
                fail_path,
                update_cache=False,
            )

            self.assertEqual(
                fail_path.read_text(encoding="utf-8"),
                "失败 甲 https://example.test/a 方向: bottom 期数: 216\n"
                "阶段: 指定期数校验 原因: 专属模块中未找到指定216期\n\n"
                "失败分类统计\n"
                "指定期数缺失 1条\n",
            )

    def test_multi_issue_failure_file_uses_new_format(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "multi_issue_fetcher.tail_fetcher.OUTPUT_DIR", Path(temp_dir)
        ), patch(
            "multi_issue_fetcher.tail_fetcher.load_targets", return_value=[self.target]
        ), patch(
            "multi_issue_fetcher.tail_fetcher.fetch_text",
            return_value="甲\n215期 平特一尾【9】",
        ):
            run_multi(["216"])

            _, fail_path, _ = dated_multi_issue_files("216")
            self.assertEqual(
                fail_path.read_text(encoding="utf-8"),
                "失败 甲 https://example.test/a 方向: bottom 期数: 216\n"
                "阶段: 指定期数校验 原因: 专属模块中未找到指定216期\n\n"
                "失败分类统计\n"
                "指定期数缺失 1条\n",
            )


class SingleIssueFailureFileCreationTests(unittest.TestCase):
    def test_blank_failure_content_does_not_create_failure_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            success = Path(tmp) / "success.txt"
            failure = Path(tmp) / "failure.txt"

            atomic_write_success_optional_failure(success, "3尾 甲\n", failure, "")

            self.assertEqual("3尾 甲\n", success.read_text(encoding="utf-8"))
            self.assertFalse(failure.exists())

    def test_blank_failure_content_removes_stale_failure_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            success = Path(tmp) / "success.txt"
            failure = Path(tmp) / "failure.txt"
            failure.write_text("旧的失败内容\n", encoding="utf-8")

            atomic_write_success_optional_failure(success, "3尾 甲\n", failure, "  \n")

            self.assertEqual("3尾 甲\n", success.read_text(encoding="utf-8"))
            self.assertFalse(failure.exists())

    def test_non_blank_failure_content_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            success = Path(tmp) / "success.txt"
            failure = Path(tmp) / "failure.txt"

            atomic_write_success_optional_failure(success, "3尾 甲\n", failure, "失败 乙\n")

            self.assertEqual("3尾 甲\n", success.read_text(encoding="utf-8"))
            self.assertEqual("失败 乙\n", failure.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()