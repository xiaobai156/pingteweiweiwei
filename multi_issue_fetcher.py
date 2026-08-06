import argparse
import sys
from datetime import datetime
from pathlib import Path

import tail_fetcher
from ptw.logging import finish_progress
from ptw.services.multi_issue import (
    MultiIssueService,
    parse_issues,
)
from ptw.storage.output import (
    dated_multi_issue_files as _dated_issue_files,
    dated_multi_summary_file as _dated_summary_file,
)


def dated_multi_issue_files(
    issue: str,
    today: datetime | None = None,
) -> tuple[Path, Path, Path]:
    success, failure = _dated_issue_files(
        issue,
        today,
        output_dir=tail_fetcher.OUTPUT_DIR,
    )
    audit = failure.with_name(failure.name.replace("失败.txt", "审计.txt"))
    return success, failure, audit


def dated_multi_summary_file(
    issues: list[str],
    today: datetime | None = None,
) -> Path:
    return _dated_summary_file(
        issues,
        today,
        output_dir=tail_fetcher.OUTPUT_DIR,
    )


def run_multi(issues: list[str]) -> Path:
    service = MultiIssueService(tail_fetcher, tail_fetcher.OUTPUT_DIR)
    return service.run(issues)


def main() -> int:
    parser = argparse.ArgumentParser(description="按多个指定期数抓取平特一肖/平尾结果")
    parser.add_argument("issues", nargs="*", help="期数，例如 187 188 189 190")
    args = parser.parse_args()
    values = args.issues or [input("请输入多个期数，例如 187 188 189 190：").strip()]
    issues = parse_issues(values)
    if not issues:
        print("期数不能为空")
        return 1
    summary_path = run_multi(issues)
    finish_progress()
    print(f"完成：多期 {len(issues)} 个")
    print(f"多期失败汇总：{summary_path.resolve()}")
    print("注意：多期模式不更新 recent_10_cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
