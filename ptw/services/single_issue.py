import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ptw.domain.models import AuditRecord, Target
from ptw.domain.validation import is_publishable


FETCH_EXCEPTIONS = (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError)


@dataclass(frozen=True)
class SingleIssueDependencies:
    load_targets: Callable[[], list[Target]]
    fetch_text: Callable[[str], str]
    audit_result: Callable[[str, str, Target], AuditRecord]
    format_not_found: Callable[[AuditRecord], str]
    format_failure: Callable[[list[str], list[Target], str], str]
    atomic_write: Callable[[Path, str], None]
    format_success: Callable[[list[str]], str]
    update_cache: Callable[[list[Target], list[str], str], bool]
    dated_files: Callable[[], tuple[Path, Path]]
    format_progress: Callable[[int, int, int, int, float, str], str]
    log: Callable[[str], None]
    atomic_write_pair: Callable[[Path, str, Path, str], None] | None = None
    commit: Callable[[list[Target], list[str], str, Path, str, Path, str], bool] | None = None


def run_single_issue(
    issue: str,
    output_path: Path,
    fail_path: Path | None,
    update_cache: bool,
    dependencies: SingleIssueDependencies,
) -> tuple[list[str], list[str]]:
    results: list[str] = []
    errors: list[str] = []
    targets = dependencies.load_targets()
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
            dependencies.log(f"[RETRY FETCH] {label}")
            retry_fetched[url] = dependencies.fetch_text(url)
            return retry_fetched[url]
        except FETCH_EXCEPTIONS as exc:
            retry_errors[url] = str(exc)
            return ""

    dependencies.log(f"Total targets: {len(targets)}, urls: {len(urls)}")
    dependencies.log("Fetching pages...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(dependencies.fetch_text, url): url for url in urls}
        done_count = 0
        for future in as_completed(future_map):
            url = future_map[future]
            done_count += 1
            try:
                fetched[url] = future.result()
            except FETCH_EXCEPTIONS as exc:
                fetched[url] = ""
                fetch_errors[url] = str(exc)
                dependencies.log(f"[FETCH FAIL] {done_count}/{len(urls)} {url}")

    dependencies.log("Extracting results...")
    total_targets = len(targets)
    for index, target in enumerate(targets, start=1):
        text = fetched.get(target.url, "")
        if not text:
            text = retry_url(target.url, target.label)
            if not text:
                reason = retry_errors.get(target.url) or fetch_errors.get(target.url) or "抓取失败"
                errors.append(f"失败：{target.label} {target.url} - {reason}")
                dependencies.log(
                    dependencies.format_progress(
                        index,
                        total_targets,
                        len(results),
                        len(errors),
                        time.monotonic() - start_time,
                        target.label,
                    )
                )
                continue
        try:
            record = dependencies.audit_result(text, issue, target)
            if is_publishable(record):
                results.append(record.result)
            else:
                retry_text = retry_url(target.url, target.label)
                if retry_text:
                    retry_record = dependencies.audit_result(retry_text, issue, target)
                    if is_publishable(retry_record):
                        results.append(retry_record.result)
                        record = retry_record
                    else:
                        record = retry_record
                        errors.append(dependencies.format_not_found(record))
                else:
                    errors.append(dependencies.format_not_found(record))
        except FETCH_EXCEPTIONS as exc:
            errors.append(f"失败：{target.label} {target.url} - {exc}")
        dependencies.log(
            dependencies.format_progress(
                index,
                total_targets,
                len(results),
                len(errors),
                time.monotonic() - start_time,
                target.label,
            )
        )

    resolved_fail_path = fail_path or dependencies.dated_files()[1]
    committed = False
    if update_cache:
        success_content = dependencies.format_success(results)
        failure_content = dependencies.format_failure(errors, targets, issue)
        if dependencies.commit:
            committed = dependencies.commit(
                targets,
                results,
                issue,
                output_path,
                success_content,
                resolved_fail_path,
                failure_content,
            )
            if committed:
                dependencies.log("Recent 10 cache: updated")
            else:
                results = []
                errors.append(
                    "失败：缓存 recent_10_cache.json - "
                    "缓存更新被拒绝（旧值冲突或缓存结构无效）"
                )
        elif dependencies.update_cache(targets, results, issue):
            dependencies.log("Recent 10 cache: updated")
        else:
            results = []
            errors.append(
                "失败：缓存 recent_10_cache.json - "
                "缓存更新被拒绝（旧值冲突或缓存结构无效）"
            )
    else:
        dependencies.log("Recent 10 cache: skipped")
    if not committed:
        success_content = dependencies.format_success(results)
        failure_content = dependencies.format_failure(errors, targets, issue)
        if dependencies.atomic_write_pair:
            dependencies.atomic_write_pair(
                output_path,
                success_content,
                resolved_fail_path,
                failure_content,
            )
        else:
            dependencies.atomic_write(output_path, success_content)
            dependencies.atomic_write(resolved_fail_path, failure_content)
    if errors:
        dependencies.log(f"Failure file: {resolved_fail_path.resolve()}")
    return results, errors
