import copy
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from ptw.constants import RECENT_10_WINDOW
from ptw.domain.models import Target
from ptw.storage.atomic import acquire_lock, release_lock
from ptw.storage.output import value_from_result_line
from ptw.text import normalize_digits


VALUE_PATTERN = re.compile(r"^[0-9]尾(?:\s+[0-9]尾)?$")


def cache_pick(position: str) -> str:
    return "bottom" if position in {"尾部", "tail", "bottom", "下"} else "top"


def _rule_matches(site: dict, target: Target) -> bool:
    return (
        site.get("target_id") == target.target_id
        and site.get("name") == target.label
        and site.get("url") == target.url
        and site.get("pick") == cache_pick(target.position)
        and site.get("keyword") == target.keyword
        and site.get("mode") == target.mode
        and site.get("parser_id") == target.parser_id
        and site.get("rule_version") == target.rule_version
        and site.get("record_author", "") == target.record_author
        and site.get("record_title", "") == target.record_title
        and site.get("record_category", "") == target.record_category
        and site.get("record_direction", "") == target.record_direction
        and site.get("record_content_field", "") == target.record_content_field
        and site.get("record_id", "") == target.record_id
        and site.get("value_policy", "mode_default") == target.value_policy
    )


def _new_site(target: Target) -> dict:
    return {
        "target_id": target.target_id,
        "name": target.label,
        "url": target.url,
        "pick": cache_pick(target.position),
        "keyword": target.keyword,
        "mode": target.mode,
        "parser_id": target.parser_id,
        "rule_version": target.rule_version,
        "record_author": target.record_author,
        "record_title": target.record_title,
        "record_category": target.record_category,
        "record_direction": target.record_direction,
        "record_content_field": target.record_content_field,
        "record_id": target.record_id,
        "value_policy": target.value_policy,
        "values": {},
    }


def _read_cache(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("schema") != 2:
        raise ValueError("缓存结构或版本无效")
    if not isinstance(value.get("sites"), list):
        raise ValueError("缓存sites格式无效")
    return value


def _write_cache_unlocked(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bak")
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8-sig", newline="") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copy2(path, backup)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _apply_one(cache: dict, target: Target, issue: str, value: str) -> bool:
    issue_digits = re.sub(r"\D", "", normalize_digits(issue))
    normalized_value = normalize_digits(value).strip()
    if not issue_digits or not VALUE_PATTERN.fullmatch(normalized_value):
        return False

    sites = cache["sites"]
    matching_indexes = [
        index for index, site in enumerate(sites)
        if isinstance(site, dict) and site.get("target_id") == target.target_id
    ]
    if len(matching_indexes) > 1:
        return False
    if matching_indexes:
        site = sites[matching_indexes[0]]
        if not _rule_matches(site, target):
            return False
    else:
        site = _new_site(target)
        sites.append(site)

    values = site.get("values")
    if not isinstance(values, dict):
        return False
    old_value = normalize_digits(str(values.get(issue_digits, ""))).strip()
    if old_value and old_value != normalized_value:
        return False
    values[issue_digits] = normalized_value

    issue_numbers = {
        int(key)
        for item in sites
        if isinstance(item, dict) and isinstance(item.get("values"), dict)
        for key in item["values"]
        if str(key).isdigit()
    }
    keep = sorted(issue_numbers, reverse=True)[: int(cache.get("window_size", RECENT_10_WINDOW))]
    keep_keys = {str(item) for item in keep}
    for item in sites:
        item_values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(item_values, dict):
            return False
        item["values"] = {
            key: item_values[key]
            for key in sorted(item_values, key=int, reverse=True)
            if key in keep_keys
        }
    cache["issues"] = keep
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return True


def update_one(path: Path, target: Target, issue: str, value: str) -> bool:
    lock_fd, lock_path = acquire_lock(path)
    try:
        try:
            cache = _read_cache(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        updated = copy.deepcopy(cache)
        if not _apply_one(updated, target, issue, value):
            return False
        _write_cache_unlocked(path, updated)
        return True
    finally:
        release_lock(lock_fd, lock_path)


def result_from_recent_10_cache(
    target: Target,
    issue: str,
    cache_path: Path,
) -> str:
    issue_digits = re.sub(r"\D", "", normalize_digits(issue))
    if not issue_digits or not cache_path.exists():
        return ""
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    sites = cache.get("sites") if isinstance(cache, dict) else None
    if not isinstance(sites, list):
        return ""
    for site in sites:
        if not isinstance(site, dict):
            continue
        if target.target_id and site.get("target_id") != target.target_id:
            continue
        if site.get("name") != target.label or site.get("url") != target.url:
            continue
        if site.get("pick") != cache_pick(target.position):
            continue
        if site.get("mode") != target.mode or site.get("keyword") != target.keyword:
            continue
        if target.parser_id and site.get("parser_id") != target.parser_id:
            continue
        if target.parser_id and site.get("rule_version") != target.rule_version:
            continue
        metadata = (
            "record_author",
            "record_title",
            "record_category",
            "record_direction",
            "record_content_field",
            "record_id",
        )
        if any(site.get(field, "") != getattr(target, field) for field in metadata):
            continue
        if site.get("value_policy", "mode_default") != target.value_policy:
            continue
        values = site.get("values")
        if not isinstance(values, dict):
            continue
        value = normalize_digits(str(values.get(issue_digits, "")).strip())
        if VALUE_PATTERN.fullmatch(value):
            return f"{value} {target.label}"
    return ""


def _target_cache_key(target: Target) -> tuple[str, str]:
    return (
        target.target_id or target.label,
        target.url,
    )


def _site_cache_key(site: dict) -> tuple[str, str]:
    return (
        str(site.get("target_id") or site.get("name", "")),
        str(site.get("url", "")),
    )


def _same_rule(site: dict, target: Target) -> bool:
    if target.target_id and site.get("target_id") != target.target_id:
        return False
    if target.parser_id and site.get("parser_id") != target.parser_id:
        return False
    if target.parser_id and site.get("rule_version") != target.rule_version:
        return False
    return (
        site.get("pick") == cache_pick(target.position)
        and site.get("mode") == target.mode
        and site.get("keyword") == target.keyword
        and site.get("record_author", "") == target.record_author
        and site.get("record_title", "") == target.record_title
        and site.get("record_category", "") == target.record_category
        and site.get("record_direction", "") == target.record_direction
        and site.get("record_content_field", "") == target.record_content_field
        and site.get("record_id", "") == target.record_id
        and site.get("value_policy", "mode_default") == target.value_policy
    )


def update_recent_10_cache(
    targets: list[Target],
    results: list[str],
    issue: str,
    cache_path: Path,
) -> bool:
    if not any(value_from_result_line(line) is not None for line in results):
        return True
    lock_fd, lock_path = acquire_lock(cache_path)
    try:
        issue_digits = re.sub(r"\D", "", normalize_digits(issue))
        if not issue_digits:
            return False
        issue_num = int(issue_digits)
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError):
                return False
            if not isinstance(cache, dict):
                return False
            if cache.get("schema") not in {1, 2}:
                return False
            if not isinstance(cache.get("sites"), list):
                return False
            if not isinstance(cache.get("issues", []), list):
                return False
        else:
            cache = {}

        old_sites = cache.get("sites")
        if not isinstance(old_sites, list):
            old_sites = []
        old_by_key = {
            _site_cache_key(item): item
            for item in old_sites
            if isinstance(item, dict)
        }
        old_order = [
            _site_cache_key(item)
            for item in old_sites
            if isinstance(item, dict)
        ]
        result_values = dict(
            parsed for line in results if (parsed := value_from_result_line(line)) is not None
        )

        known_issues: set[int] = {issue_num}
        sites_by_key: dict[tuple[str, str], dict] = {}
        target_order: list[tuple[str, str]] = []
        for target in targets:
            key = _target_cache_key(target)
            target_order.append(key)
            old_entry = old_by_key.get(key, {})
            old_values = old_entry.get("values") if _same_rule(old_entry, target) else {}
            values = {
                str(value_issue): str(value).strip()
                for value_issue, value in (old_values or {}).items()
                if str(value_issue).isdigit() and str(value).strip()
            }
            current_value = result_values.get(target.label)
            if current_value:
                current_value = normalize_digits(current_value)
                if not VALUE_PATTERN.fullmatch(current_value):
                    return False
                old_value = normalize_digits(values.get(str(issue_num), ""))
                if old_value and old_value != current_value:
                    return False
                values[str(issue_num)] = current_value
            known_issues.update(int(value_issue) for value_issue in values)
            site = _new_site(target)
            if not target.target_id:
                site.pop("target_id")
                site.pop("parser_id")
                site.pop("rule_version")
            site["values"] = values
            sites_by_key[key] = site

        ordered_keys: list[tuple[str, str]] = []
        target_keys = set(target_order)
        for key in old_order:
            if key in target_keys and key not in ordered_keys:
                ordered_keys.append(key)
        for key in target_order:
            if key not in ordered_keys:
                ordered_keys.append(key)
        sites = [sites_by_key[key] for key in ordered_keys]

        keep_issues = sorted(known_issues, reverse=True)[:RECENT_10_WINDOW]
        keep_issue_keys = {str(item) for item in keep_issues}
        for site in sites:
            site["values"] = {
                key: value
                for key, value in sorted(
                    site["values"].items(),
                    key=lambda item: int(item[0]),
                    reverse=True,
                )
                if key in keep_issue_keys
            }

        cache_data = {
            "schema": 2 if all(target.target_id for target in targets) else 1,
            "description": "平特尾重复检测最近10期基准数据；由每日指定期抓取自动覆盖更新。",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "window_size": RECENT_10_WINDOW,
            "issues": keep_issues,
            "sites": sites,
        }
        _write_cache_unlocked(cache_path, cache_data)
        return True
    finally:
        release_lock(lock_fd, lock_path)
