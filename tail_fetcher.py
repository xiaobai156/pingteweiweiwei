import argparse
import re
import sys
from functools import partial
from pathlib import Path

from ptw.config import load_targets
from ptw.constants import OUTPUT_DIR, RECENT_10_CACHE_FILE
from ptw.domain.models import Target
from ptw.fetching.http import fetch_text
from ptw.logging import finish_progress, log
from ptw.parsing.pipeline import (
    audit_result_from_text,
    format_not_found_error,
)
from ptw.services.single_issue import SingleIssueDependencies, run_single_issue
from ptw.storage.atomic import (
    atomic_write_pair_with_backup,
    atomic_write_with_backup,
)
from ptw.storage.cache import update_recent_10_cache as _cache_update
from ptw.storage.output import (
    dated_output_files,
    format_failure_output,
    format_progress_line,
    format_success_output,
)
from ptw.storage.transaction import commit_single_issue as _commit_single_issue


SUCCESS_FILE, FAIL_FILE = dated_output_files()


def update_recent_10_cache(
    targets: list[Target],
    results: list[str],
    issue: str,
    cache_path: Path = RECENT_10_CACHE_FILE,
) -> bool:
    return _cache_update(targets, results, issue, cache_path)


def run(
    issue: str,
    output_path: Path = SUCCESS_FILE,
    fail_path: Path | None = None,
    update_cache: bool = True,
    audit_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    dependencies = SingleIssueDependencies(
        load_targets=load_targets,
        fetch_text=fetch_text,
        audit_result=audit_result_from_text,
        format_not_found=format_not_found_error,
        format_failure=format_failure_output,
        atomic_write=atomic_write_with_backup,
        format_success=format_success_output,
        update_cache=update_recent_10_cache,
        dated_files=lambda: dated_output_files(output_dir=OUTPUT_DIR),
        format_progress=format_progress_line,
        log=log,
        atomic_write_pair=atomic_write_pair_with_backup,
        commit=partial(_commit_single_issue, update_recent_10_cache),
    )
    return run_single_issue(issue, output_path, fail_path, update_cache, dependencies)


def main() -> int:
    parser = argparse.ArgumentParser(description="按指定期数抓取平特一肖/平尾结果")
    parser.add_argument("issue", nargs="?", help="期数，例如 167")
    parser.add_argument("-o", "--output", default=str(SUCCESS_FILE), help="成功 txt 路径")
    args = parser.parse_args()
    issue = re.sub(r"\D", "", args.issue or input("请输入期数，例如 167：").strip())
    if not issue:
        print("期数不能为空")
        return 1
    results, errors = run(issue, Path(args.output))
    finish_progress()
    print(f"完成：成功 {len(results)} 条，失败 {len(errors)} 条")
    print(f"成功文件：{Path(args.output).resolve()}")
    print(f"失败文件：{dated_output_files(output_dir=OUTPUT_DIR)[1].resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
