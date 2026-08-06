import re
from datetime import datetime
from pathlib import Path

from ptw.domain.models import Target
from ptw.constants import OUTPUT_DIR
from ptw.text import normalize_digits


def dated_output_files(
    today: datetime | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    today = today or datetime.now()
    date_name = f"{today.month}.{today.day}"
    return (
        output_dir / f"{date_name}平特尾.txt",
        output_dir / f"{date_name}平特尾失败.txt",
    )


def dated_multi_issue_files(
    issue: str,
    today: datetime | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    today = today or datetime.now()
    date_name = f"{today.month}.{today.day}"
    return (
        output_dir / f"{date_name}平特尾{issue}期.txt",
        output_dir / f"{date_name}平特尾{issue}期失败.txt",
    )


def dated_multi_summary_file(
    issues: list[str],
    today: datetime | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> Path:
    today = today or datetime.now()
    issue_name = "-".join(issues)
    return output_dir / f"{today.month}.{today.day}平特尾{issue_name}期多期失败汇总.txt"


def format_progress_line(
    done: int,
    total: int,
    success: int,
    failed: int,
    elapsed: float,
    current: str,
) -> str:
    percent = int(done * 100 / total) if total else 100
    return (
        f"[进度 {done}/{total} {percent}% 成功 {success} "
        f"失败 {failed} 用时 {elapsed:.1f}s] 当前: {current}"
    )


def value_from_result_line(line: str) -> tuple[str, str] | None:
    line = normalize_digits(line)
    match = re.match(r"^([0-9]尾(?:\s+[0-9]尾)?)\s+(.+?)\s*$", line.strip())
    if not match:
        return None
    return match.group(2), match.group(1)


def format_success_output(results: list[str]) -> str:
    counts: dict[str, int] = {}
    results = [normalize_digits(result) for result in results]
    for result in results:
        parsed = value_from_result_line(result)
        if parsed:
            _, value = parsed
            for tail in re.findall(r"([0-9])尾", value):
                counts[tail] = counts.get(tail, 0) + 1

    content = "\n".join(results)
    if counts:
        ranking = "\n".join(
            f"{tail}尾：{count}条"
            for tail, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
        content = f"{content}\n\n尾数排行榜\n{ranking}"
    return content + ("\n" if content else "")


def _display_position(position: str) -> str:
    if position in {"尾部", "tail", "bottom", "下"}:
        return "bottom"
    if position in {"顶部", "top", "上"}:
        return "top"
    return "-"


def _failure_parts(
    error: str,
    targets: list[Target],
) -> tuple[Target | None, str, str, bool]:
    for target in targets:
        for prefix, is_not_found in (
            (f"未找到：{target.label} {target.url} - ", True),
            (f"失败：{target.label} {target.url} - ", False),
        ):
            if error.startswith(prefix):
                return target, error[len(prefix) :], target.label, is_not_found

    prefix = "失败：" if error.startswith("失败：") else "未找到："
    if error.startswith(prefix):
        payload = error[len(prefix) :]
        subject, separator, reason = payload.partition(" - ")
        return None, reason if separator else payload, subject, False
    return None, error, error, False


def _failure_stage(reason: str, subject: str, is_not_found: bool) -> str:
    if "缓存" in subject or "cache" in subject.lower():
        return "缓存更新"
    if is_not_found:
        return "指定期数校验"
    if "解析" in reason or "校验" in reason:
        return "指定期数校验"
    return "抓取"


def _failure_category(reason: str, stage: str) -> str:
    if stage == "缓存更新":
        return "缓存更新失败"
    if stage == "抓取":
        return "抓取失败"
    if "方向最近3组" in reason or "方向范围" in reason:
        return "方向范围外"
    if "未命中专属关键字" in reason or "关键字" in reason:
        return "关键字缺失"
    if "未找到指定" in reason:
        return "指定期数缺失"
    if any(marker in reason for marker in ("不同原始文档", "来源不唯一", "不同显式尾数", "冲突")):
        return "同期冲突"
    if "没有符合专属格式" in reason or "有效尾数" in reason:
        return "格式无效"
    if any(marker in reason for marker in ("记录ID", "作者", "标题", "栏目", "字段", "身份")):
        return "身份字段校验失败"
    return "指定期数校验失败"


def format_failure_output(
    errors: list[str],
    targets: list[Target],
    issue: str,
) -> str:
    if not errors:
        return ""

    issue = normalize_digits(issue)
    entries: list[str] = []
    category_counts: dict[str, int] = {}
    for error in errors:
        target, reason, subject, is_not_found = _failure_parts(error, targets)
        label = target.label if target else subject
        url = target.url if target else ""
        position = _display_position(target.position) if target else "-"
        stage = _failure_stage(reason, subject, is_not_found)
        category = _failure_category(reason, stage)
        category_counts[category] = category_counts.get(category, 0) + 1
        url_part = f" {url}" if url else ""
        entries.append(
            "\n".join(
                (
                    f"失败 {label}{url_part} 方向: {position} 期数: {issue}",
                    f"阶段: {stage} 原因: {reason}",
                )
            )
        )

    summary = "失败分类统计\n" + "\n".join(
        f"{category} {count}条" for category, count in category_counts.items()
    )
    return "\n\n".join((*entries, summary)) + "\n"
