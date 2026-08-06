import re
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Protocol

from ptw.domain.models import AuditRecord, Target
from ptw.domain.validation import is_publishable
from ptw.storage.output import (
    dated_multi_issue_files,
    dated_multi_summary_file,
    format_failure_output,
    value_from_result_line,
)
from ptw.text import normalize_digits


FETCH_EXCEPTIONS = (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError)


class MultiGateway(Protocol):
    def load_targets(self) -> list[Target]: ...
    def fetch_text(self, url: str) -> str: ...
    def audit_result_from_text(self, text: str, issue: str, target: Target): ...
    def atomic_write_pair_with_backup(
        self, first_path: Path, first_content: str, second_path: Path, second_content: str
    ) -> None: ...


class MultiIssueService:
    cache_read_only = True

    def __init__(self, gateway: MultiGateway, output_dir: Path):
        self.gateway = gateway
        self.output_dir = output_dir

    def fetch_snapshots(self, urls: list[str]) -> tuple[dict[str, str], dict[str, str]]:
        fetched: dict[str, str] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_map = {executor.submit(self.gateway.fetch_text, url): url for url in urls}
            done_count = 0
            for future in as_completed(future_map):
                url = future_map[future]
                done_count += 1
                try:
                    fetched[url] = future.result()
                except FETCH_EXCEPTIONS as exc:
                    fetched[url] = ""
                    errors[url] = str(exc)
                    self.gateway.log(f"[FETCH FAIL] {done_count}/{len(urls)} {url}")
        return fetched, errors

    def audit_target(
        self,
        text: str,
        issue: str,
        target: Target,
    ) -> AuditRecord | None:
        if not text:
            return None
        return self.gateway.audit_result_from_text(text, issue, target)

    def run(self, issues: list[str]) -> Path:
        targets = self.gateway.load_targets()
        urls = sorted({target.url for target in targets})
        success_by_issue: dict[str, set[str]] = {}
        error_by_issue: dict[str, dict[str, str]] = {}
        start_time = time.monotonic()

        self.gateway.log(f"Total targets: {len(targets)}, urls: {len(urls)}")
        self.gateway.log("Fetching pages...")
        fetched, fetch_errors = self.fetch_snapshots(urls)

        initial_records: dict[tuple[str, int], AuditRecord | None] = {}
        unmatched_urls: set[str] = set()
        for issue in issues:
            for index, target in enumerate(targets):
                try:
                    record = self.audit_target(
                        fetched.get(target.url, ""),
                        issue,
                        target,
                    )
                except FETCH_EXCEPTIONS:
                    record = None
                initial_records[(issue, index)] = record
                if not is_publishable(record):
                    unmatched_urls.add(target.url)

        retry_fetched: dict[str, str] = {}
        retry_errors: dict[str, str] = {}
        if unmatched_urls:
            label_by_url = {target.url: target.label for target in targets}
            for url in sorted(unmatched_urls):
                self.gateway.log(f"[RETRY FETCH] {label_by_url[url]}")
            retry_fetched, retry_errors = self.fetch_snapshots(sorted(unmatched_urls))

        self.gateway.log("Extracting results...")
        for issue in issues:
            results: list[str] = []
            errors: list[str] = []
            success_path, fail_path = dated_multi_issue_files(
                issue,
                output_dir=self.output_dir,
            )
            self.gateway.log(f"========== ISSUE {issue} ==========")
            for index, target in enumerate(targets, start=1):
                record = initial_records[(issue, index - 1)]
                result = record.result if is_publishable(record) else ""
                result_text = fetched.get(target.url, "")
                extraction_error = ""
                if not result:
                    retry_text = retry_fetched.get(target.url, "")
                    if retry_text:
                        try:
                            record = self.audit_target(retry_text, issue, target)
                            result = record.result if is_publishable(record) else ""
                            result_text = retry_text
                        except FETCH_EXCEPTIONS as exc:
                            extraction_error = str(exc)

                if result:
                    results.append(result)
                elif extraction_error:
                    errors.append(f"失败：{target.label} {target.url} - {extraction_error}")
                elif not result_text and not retry_fetched.get(target.url, ""):
                    reason = retry_errors.get(target.url) or fetch_errors.get(target.url) or "抓取失败"
                    errors.append(f"失败：{target.label} {target.url} - {reason}")
                elif record is not None:
                    errors.append(self.gateway.format_not_found_error(record))
                else:
                    errors.append(f"失败：{target.label} {target.url} - 专属解析异常")

                self.gateway.log(
                    self.gateway.format_progress_line(
                        index,
                        len(targets),
                        len(results),
                        len(errors),
                        time.monotonic() - start_time,
                        target.label,
                    )
                )

            self.gateway.atomic_write_pair_with_backup(
                success_path,
                self.gateway.format_success_output(results),
                fail_path,
                format_failure_output(errors, targets, issue),
            )
            self.gateway.log("Recent 10 cache: skipped")
            if errors:
                self.gateway.log(f"Failure file: {fail_path.resolve()}")
            success_by_issue[issue] = labels_from_results(results)
            error_by_issue[issue] = error_labels(errors)
            self.gateway.log(f"Issue {issue}: success {len(results)}, fail {len(errors)}")

        summary_path = dated_multi_summary_file(issues, output_dir=self.output_dir)
        self.gateway.atomic_write_with_backup(
            summary_path,
            format_multi_failure_report(
                issues,
                targets,
                success_by_issue,
                error_by_issue,
            ),
        )
        return summary_path


def parse_issues(values: list[str]) -> list[str]:
    issues: list[str] = []
    for value in values:
        for item in re.split(r"[\s,，、]+", value.strip()):
            issue = re.sub(r"\D", "", normalize_digits(item))
            if issue and issue not in issues:
                issues.append(issue)
    return issues


def labels_from_results(results: list[str]) -> set[str]:
    return {
        parsed[0]
        for line in results
        if (parsed := value_from_result_line(line)) is not None
    }


def error_labels(errors: list[str]) -> dict[str, str]:
    by_label: dict[str, str] = {}
    for error in errors:
        match = re.match(r"^(?:未找到|失败)：(.+?)\s+https?://", error)
        if match:
            by_label[match.group(1).strip()] = error
    return by_label


def format_multi_failure_report(
    issues: list[str],
    targets: list[Target],
    success_by_issue: dict[str, set[str]],
    error_by_issue: dict[str, dict[str, str]],
) -> str:
    lines = [
        "平特尾多期失败汇总",
        f"期数：{' '.join(issues)}",
        "说明：任意一期成功即通过；本报告只列所有输入期数都失败的目录。",
        "",
    ]
    failed_count = 0
    for target in targets:
        if any(target.label in success_by_issue.get(issue, set()) for issue in issues):
            continue
        failed_count += 1
        lines.extend(
            [
                f"{failed_count}. {target.label}",
                f"网址：{target.url}",
                f"位置：{target.position}",
                f"模式：{target.mode}",
            ]
        )
        for issue in issues:
            reason = error_by_issue.get(issue, {}).get(target.label)
            if not reason:
                reason = f"未找到：{target.label} {target.url}"
            lines.append(f"{issue}期：{reason}")
        lines.append("")
    if failed_count == 0:
        lines.append("全部目录至少命中任意一期。")
    return "\n".join(lines).rstrip() + "\n"
