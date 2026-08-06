import io
import unittest
from unittest.mock import patch

from ptw.logging import finish_progress, log


class ProgressLoggingTests(unittest.TestCase):
    def test_progress_messages_share_one_terminal_line(self):
        first = "[进度 1/2 50% 成功 1 失败 0 用时 1.0s] 当前: 甲"
        second = "[进度 2/2 100% 成功 2 失败 0 用时 2.0s] 当前: 乙"
        output = io.StringIO()

        with patch("ptw.logging.sys.stdout", output):
            log(first)
            log(second)
            finish_progress()

        self.assertEqual(output.getvalue(), f"\r{first}\r{second}\n")

    def test_normal_log_finishes_progress_before_printing_next_message(self):
        progress = "[进度 1/1 100% 成功 1 失败 0 用时 1.0s] 当前: 甲"
        output = io.StringIO()

        with patch("ptw.logging.sys.stdout", output):
            log(progress)
            log("完成：成功 1 条，失败 0 条")

        self.assertEqual(
            output.getvalue(),
            f"\r{progress}\n完成：成功 1 条，失败 0 条\n",
        )

    def test_shorter_progress_clears_previous_line_tail(self):
        first = "[进度 1/2 50% 成功 1 失败 0 用时 1.0s] 当前: 较长的目录名"
        second = "[进度 2/2 100% 成功 2 失败 0 用时 2.0s] 当前: 甲"
        output = io.StringIO()

        with patch("ptw.logging.sys.stdout", output):
            log(first)
            log(second)
            finish_progress()

        padding = " " * (len(first) - len(second))
        self.assertEqual(output.getvalue(), f"\r{first}\r{second}{padding}\n")


if __name__ == "__main__":
    unittest.main()
