import argparse
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Error

from ptw.config import load_targets
from ptw.constants import OUTPUT_DIR, RECENT_10_CACHE_FILE
from ptw.domain.validation import is_publishable
from ptw.fetching.browser import BrowserFetcher
from ptw.logging import finish_progress, log
from ptw.parsing.pipeline import audit_result_from_text, format_not_found_error
from ptw.storage.atomic import atomic_restore_bytes, atomic_write_success_optional_failure
from ptw.storage.cache import update_recent_10_cache_partial
from ptw.storage.output import (
    dated_output_files,
    format_failure_output,
    format_progress_line,
    format_success_output,
    value_from_result_line,
)

FAILURE_LINE = re.compile(
    r"^失败 (.+?) (https?://\S+) 方向: (top|bottom) 期数: (\d+)$"
)
POSITION_BY_DIRECTION = {"top": "顶部", "bottom": "尾部"}
FETCH_EXCEPTIONS = (Error, OSError, RuntimeError, TimeoutError, ValueError)


def read_failure_targets(path: Path, issue: str) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = FAILURE_LINE.fullmatch(line)
        if match and match.group(4) == issue:
            records.append((match.group(1), match.group(2), POSITION_BY_DIRECTION[match.group(3)]))
    if not records:
        raise ValueError("失败清单没有匹配的指定期")
    keys = {(label, url) for label, url, _ in records}
    if len(keys) != len(records):
        raise ValueError("失败清单存在重复站点")
    return records


def select_targets(records: list[tuple[str, str, str]]):
    targets = load_targets()
    selected = []
    for label, url, position in records:
        matches = [
            target for target in targets
            if target.label == label and target.url == url and target.position == position
        ]
        if len(matches) != 1:
            raise ValueError(f"目标配置无法唯一匹配：{label} {url}")
        selected.append(matches[0])
    return selected


def run_selected(issue: str, targets, fetch) -> tuple[list[str], list[str]]:
    results: list[str] = []
    errors: list[str] = []
    urls = sorted({target.url for target in targets})
    fetched: dict[str, str] = {}
    fetch_errors: dict[str, str] = {}
    retry_fetched: dict[str, str] = {}
    retry_errors: dict[str, str] = {}
    start_time = time.monotonic()

    def retry_url(url: str, label: str) -> str:
        if url in retry_fetched:
            return retry_fetched[url]
        if url in retry_errors:
            return ""
        try:
            log(f"[RETRY FETCH] {label}")
            retry_fetched[url] = fetch(url)
            return retry_fetched[url]
        except FETCH_EXCEPTIONS as exc:
            retry_errors[url] = str(exc)
            return ""

    log(f"Total targets: {len(targets)}, urls: {len(urls)}")
    log("Fetching pages...")
    for url in urls:
        try:
            fetched[url] = fetch(url)
        except FETCH_EXCEPTIONS as exc:
            fetched[url] = ""
            fetch_errors[url] = str(exc)
            log(f"[FETCH FAIL] {url}")

    log("Extracting results...")
    for index, target in enumerate(targets, start=1):
        text = fetched.get(target.url, "")
        if not text:
            text = retry_url(target.url, target.label)
        if not text:
            reason = retry_errors.get(target.url) or fetch_errors.get(target.url) or "抓取失败"
            errors.append(f"失败：{target.label} {target.url} - {reason}")
        else:
            try:
                record = audit_result_from_text(text, issue, target)
                if not is_publishable(record):
                    retry_text = retry_url(target.url, target.label)
                    if retry_text:
                        record = audit_result_from_text(retry_text, issue, target)
                if is_publishable(record):
                    results.append(record.result)
                else:
                    errors.append(format_not_found_error(record))
            except (ValueError, RuntimeError) as exc:
                errors.append(f"失败：{target.label} {target.url} - {exc}")
        log(
            format_progress_line(
                index,
                len(targets),
                len(results),
                len(errors),
                time.monotonic() - start_time,
                target.label,
            )
        )
    return results, errors


def merge_success_content(path: Path, results: list[str]) -> str:
    existing: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if value_from_result_line(line):
                existing.append(line.strip())
    by_label = {value_from_result_line(line)[0]: line for line in existing}
    merged = list(existing)
    for result in results:
        parsed = value_from_result_line(result)
        if parsed is None:
            raise ValueError(f"成功结果格式无效：{result}")
        label, value = parsed
        old = by_label.get(label)
        if old is not None:
            old_value = value_from_result_line(old)[1]
            if old_value != value:
                raise ValueError(f"成功文件已有冲突结果：{label}")
            continue
        by_label[label] = result
        merged.append(result)
    return format_success_output(merged)


def run_failed_issue(
    issue: str,
    failure_file: Path,
    output_path: Path,
    fail_path: Path,
    cache_path: Path = RECENT_10_CACHE_FILE,
) -> tuple[int, int]:
    records = read_failure_targets(failure_file, issue)
    targets = select_targets(records)
    browser = BrowserFetcher()
    try:
        results, errors = run_selected(issue, targets, browser.fetch)
    finally:
        browser.close()
    success_content = merge_success_content(output_path, results)
    failure_content = format_failure_output(errors, targets, issue)
    result_by_label = {value_from_result_line(result)[0]: result for result in results}
    successful_targets = [target for target in targets if target.label in result_by_label]
    original_cache = cache_path.read_bytes() if cache_path.exists() else None
    if not update_recent_10_cache_partial(successful_targets, results, issue, cache_path):
        raise RuntimeError("当前期缓存更新被拒绝")
    try:
        atomic_write_success_optional_failure(
            output_path,
            success_content,
            fail_path,
            failure_content,
            backup=False,
        )
    except Exception:
        atomic_restore_bytes(cache_path, original_cache)
        raise
    return len(results), len(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="仅处理失败清单中的指定期站点")
    parser.add_argument("issue")
    parser.add_argument("--failure-file", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-output", type=Path)
    parser.add_argument("--cache", type=Path, default=RECENT_10_CACHE_FILE)
    args = parser.parse_args()
    success_path, failure_path = dated_output_files(output_dir=OUTPUT_DIR)
    results, errors = run_failed_issue(
        args.issue,
        args.failure_file,
        args.output or success_path,
        args.fail_output or failure_path,
        args.cache,
    )
    finish_progress()
    print(f"完成：指定目标成功 {results} 条，失败 {errors} 条")
    print(f"成功文件：{(args.output or success_path).resolve()}")
    print(f"失败文件：{(args.fail_output or failure_path).resolve()}")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
