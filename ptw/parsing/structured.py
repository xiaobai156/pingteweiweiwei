
import json
import re

from ptw.config import render_keyword
from ptw.constants import STRUCTURED_JSON_BEGIN, STRUCTURED_JSON_END
from ptw.domain.models import AuditRecord, Target
from ptw.fetching.documents import _json_script_payloads
from ptw.fetching.dynamic_api import _canonical_record_id, record_id_from_url
from ptw.parsing.candidates import (
    ParsedCandidate,
    collect_candidates,
    direction_candidate_occurrences,
    direction_candidates,
)
from ptw.parsing.evidence import make_candidate_evidence
from ptw.parsing.primitives import (
    audit_flags,
    issue_windows,
    issue_windows_with_issues,
    is_foreign_heading,
    normalize_digit_tail,
    pick_blocks,
)
from ptw.text import html_to_text, normalize_digits, normalize_search


JSON_ID_FIELDS = {
    "id",
    "articleid",
    "article_id",
    "topicid",
    "topic_id",
    "recordid",
    "record_id",
    "postid",
    "post_id",
    "contentid",
    "content_id",
}


JSON_AUTHOR_FIELDS = (
    "author",
    "authorName",
    "author_name",
    "authorNickname",
    "author_nickname",
    "username",
    "userName",
    "user_name",
)


JSON_TITLE_FIELDS = ("title", "articleTitle", "article_title", "subject", "name")


JSON_CATEGORY_FIELDS = ("category", "column", "channel", "section", "module")


JSON_DIRECTION_FIELDS = ("direction", "position", "pick", "side")


JSON_CONTENT_FIELDS = ("content", "body", "articleContent", "article_content", "text", "html", "detail")


def _json_value_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_json_value_text(item) for item in value if _json_value_text(item))
    if isinstance(value, dict):
        for key in ("name", "title", "text", "value", "label"):
            if key in value:
                return _json_value_text(value[key])
    return ""


def _json_direct_field(record: dict, fields: tuple[str, ...]) -> tuple[str, str]:
    wanted = {re.sub(r"[^a-z0-9]", "", field.lower()) for field in fields}
    for key, value in record.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if normalized_key in wanted:
            return str(key), _json_value_text(value)
    return "", ""


def _json_path_value(record: dict, path: str) -> object:
    current: object = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _iter_json_objects(value: object, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield from _iter_json_objects(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_json_objects(child, f"{path}[{index}]")


def _json_object_id(record: dict) -> str:
    for key, value in record.items():
        normalized_key = re.sub(r"[^a-z0-9_]", "", str(key).lower())
        if normalized_key in JSON_ID_FIELDS:
            record_id = _canonical_record_id(value)
            if record_id:
                return record_id
    return ""


def _structured_json_payloads(text: str) -> list[object]:
    raw_payloads: list[str] = []
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        raw_payloads.append(stripped)
    raw_payloads.extend(
        match.group(1).strip()
        for match in re.finditer(
            rf"{re.escape(STRUCTURED_JSON_BEGIN)}\s*\n(.*?){re.escape(STRUCTURED_JSON_END)}",
            text,
            flags=re.S,
        )
    )
    raw_payloads.extend(_json_script_payloads(text))

    payloads: list[object] = []
    seen: set[str] = set()
    for raw in raw_payloads:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, (dict, list)):
            continue
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if fingerprint not in seen:
            seen.add(fingerprint)
            payloads.append(payload)
    return payloads


def _direction_pick(value: str) -> str:
    normalized = normalize_search(value).lower()
    if normalized in {"top", "顶部", "上", "first", "首条"}:
        return "top"
    if normalized in {"tail", "bottom", "尾部", "下", "last", "末条"}:
        return "bottom"
    return ""


def _matches_expected(actual: str, expected: str) -> bool:
    actual_key = normalize_search(actual)
    expected_key = normalize_search(expected)
    return bool(actual_key and expected_key and actual_key == expected_key)


def _structured_record_matches(
    payloads: list[object],
    target_id: str,
) -> tuple[list[tuple[str, dict]], int]:
    matches: list[tuple[str, dict]] = []
    article_count = 0
    for payload in payloads:
        for path, record in _iter_json_objects(payload):
            record_id = _json_object_id(record)
            if not record_id:
                continue
            _, author = _json_direct_field(record, JSON_AUTHOR_FIELDS)
            _, title = _json_direct_field(record, JSON_TITLE_FIELDS)
            _, category = _json_direct_field(record, JSON_CATEGORY_FIELDS)
            _, content = _json_direct_field(record, JSON_CONTENT_FIELDS)
            if author or content or (title and category):
                article_count += 1
            if record_id == target_id:
                matches.append((path, record))
    return matches, article_count


def _structured_record_content(record: dict, target: Target) -> tuple[str, str]:
    if target.record_content_field:
        value = _json_path_value(record, target.record_content_field)
        text = _json_value_text(value)
        return target.record_content_field, html_to_text(text.encode("utf-8")) if text else ""
    field, value = _json_direct_field(record, JSON_CONTENT_FIELDS)
    return field, html_to_text(value.encode("utf-8")) if value else ""


def _manager_article_title_text(value: str) -> str:
    return html_to_text(value.encode("utf-8")) if value else ""


def _manager_article_candidates(content: str, target: Target) -> list[ParsedCandidate]:
    candidates: list[ParsedCandidate] = []
    position = 0
    for candidate_issue, window in issue_windows_with_issues(content):
        keyword = render_keyword(target.keyword, candidate_issue)
        keyword_key = normalize_search(keyword)
        if not keyword_key or keyword_key not in normalize_search(window):
            continue
        issue_seen = False
        if any(
            re.search(rf"(?<![0-9]){re.escape(candidate_issue)}期", line.replace(" ", ""))
            for line in window.splitlines()
        ):
            for line in window.splitlines():
                compact_line = line.replace(" ", "")
                if re.search(
                    rf"(?<![0-9]){re.escape(candidate_issue)}期", compact_line
                ):
                    issue_seen = True
                    continue
                if issue_seen and is_foreign_heading(line, (keyword,)):
                    break
            else:
                issue_seen = False
        if issue_seen:
            continue
        compact_keyword = re.sub(r"\s+", "", keyword)
        pattern = re.compile(
            rf"{re.escape(normalize_digits(candidate_issue))}期.*?{re.escape(compact_keyword)}"
            r"[【\[\(（《〈＜〖{]([0-9]{1,4})尾?[】\]\)）》〉＞〗}]"
        )
        compact = re.sub(r"\s+", "", window)
        for match in pattern.finditer(compact):
            digit = normalize_digit_tail(match.group(1))
            if digit:
                candidates.append(
                    ParsedCandidate(
                        candidate_issue,
                        f"{digit}尾",
                        window,
                        content,
                        0,
                        position,
                    )
                )
                position += 1
    return candidates


def audit_result_from_manager_article(
    matches: list[tuple[str, dict]], article_count: int, issue: str, target: Target, target_id: str
) -> AuditRecord:
    keyword = render_keyword(target.keyword, issue)
    if len(matches) != 1:
        flag = "JSON_RECORD_NOT_FOUND" if not matches else "JSON_RECORD_MULTIPLE_MATCHES"
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            keyword,
            target.position,
            target.mode,
            "",
            (flag,),
            record_id=target_id,
            record_count=article_count,
            record_match_count=len(matches),
            source_kind="json",
        )

    record_path, record = matches[0]
    _, author = _json_direct_field(record, JSON_AUTHOR_FIELDS)
    _, raw_title = _json_direct_field(record, JSON_TITLE_FIELDS)
    title = _manager_article_title_text(raw_title)
    content_field, content = _structured_record_content(record, target)
    flags: list[str] = ["JSON_RECORD_UNIQUE_MATCH", "MANAGER_ARTICLE_ID_MATCH"]
    if not target.record_author:
        flags.append("JSON_EXPECTED_AUTHOR_MISSING")
    elif not author or not _matches_expected(author, target.record_author):
        flags.append("JSON_AUTHOR_MISMATCH")
    if not title:
        flags.append("JSON_TITLE_MISSING")
    elif not target.record_title:
        flags.append("JSON_EXPECTED_TITLE_MISSING")
    elif normalize_search(target.record_title) not in normalize_search(title):
        flags.append("JSON_TITLE_MISMATCH")
    if not content_field or not content:
        flags.append("JSON_TARGET_FIELD_MISSING")

    all_candidates = _manager_article_candidates(content, target) if content else []
    direction = direction_candidates(all_candidates, target.position)
    window_candidates = direction_candidate_occurrences(all_candidates, direction)
    candidates = [candidate for candidate in window_candidates if candidate.issue == issue]
    all_target_candidates = [candidate for candidate in all_candidates if candidate.issue == issue]
    issue_windows_for_target = issue_windows(content, issue) if content else []
    keyword_windows = [
        window
        for candidate_issue, window in issue_windows_with_issues(content)
        if candidate_issue == issue
        and normalize_search(render_keyword(target.keyword, issue)) in normalize_search(window)
    ] if content else []
    selected = (
        candidates[-1]
        if candidates and target.position in {"尾部", "tail", "bottom", "下"}
        else candidates[0]
        if candidates
        else None
    )
    value = selected.value if selected else ""
    matched_window = selected.window if selected else ""
    if not keyword_windows:
        flags.append("JSON_TARGET_KEYWORD_MISSING" if issue_windows_for_target else "NO_ISSUE_WINDOW")
    elif not candidates:
        flags.append("DIRECTION_WINDOW_EXCLUDED" if all_target_candidates else "NO_RESULT")
    if len({candidate.value for candidate in candidates}) > 1:
        flags.append("MULTIPLE_TAIL_CANDIDATES")
    if any(
        flag.startswith("JSON_")
        and flag not in {"JSON_RECORD_UNIQUE_MATCH"}
        for flag in flags
    ):
        value = ""
        matched_window = ""
    result = f"{value} {target.label}" if value else ""
    flags.extend(
        audit_flags(
            content,
            [candidate.window for candidate in candidates] or keyword_windows,
            matched_window,
            keyword,
            target.mode,
        )
    )
    if "MULTIPLE_TAIL_CANDIDATES" in flags:
        result = ""
    candidate_evidence = tuple(
        make_candidate_evidence(
            issue,
            candidate.value,
            keyword,
            candidate.window,
            content,
            0,
            content,
            candidate_position,
            record_id=target_id,
            record_path=record_path,
        )
        for candidate_position, candidate in enumerate(candidates)
    )
    return AuditRecord(
        target.label,
        target.url,
        issue,
        result,
        keyword,
        target.position,
        target.mode,
        matched_window,
        tuple(dict.fromkeys(flags)),
        record_id=target_id,
        record_path=record_path,
        record_count=article_count,
        record_match_count=1,
        source_kind="json",
        candidate_evidence=candidate_evidence,
    )


def audit_result_from_structured_json(
    payloads: list[object], issue: str, target: Target
) -> AuditRecord:
    url_record_id = record_id_from_url(target.url)
    keyword = render_keyword(target.keyword, issue)
    if target.record_id and target.record_id != url_record_id:
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            keyword,
            target.position,
            target.mode,
            "",
            ("JSON_CONFIG_RECORD_ID_MISMATCH",),
            record_id=url_record_id,
            source_kind="json",
        )
    target_id = target.record_id or url_record_id
    if not target_id:
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            keyword,
            target.position,
            target.mode,
            "",
            ("JSON_URL_RECORD_ID_MISSING",),
            source_kind="json",
        )

    matches, article_count = _structured_record_matches(payloads, target_id)
    if target.mode == "manager_article_tail":
        return audit_result_from_manager_article(matches, article_count, issue, target, target_id)
    if not matches:
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            keyword,
            target.position,
            target.mode,
            "",
            ("JSON_RECORD_NOT_FOUND",),
            record_id=target_id,
            record_count=article_count,
            source_kind="json",
        )
    if len(matches) != 1:
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            keyword,
            target.position,
            target.mode,
            "",
            ("JSON_RECORD_MULTIPLE_MATCHES",),
            record_id=target_id,
            record_count=article_count,
            record_match_count=len(matches),
            source_kind="json",
        )

    record_path, record = matches[0]
    _, author = _json_direct_field(record, JSON_AUTHOR_FIELDS)
    _, title = _json_direct_field(record, JSON_TITLE_FIELDS)
    _, category = _json_direct_field(record, JSON_CATEGORY_FIELDS)
    _, direction = _json_direct_field(record, JSON_DIRECTION_FIELDS)
    content_field, content = _structured_record_content(record, target)
    flags: list[str] = ["JSON_RECORD_UNIQUE_MATCH"]
    if not target.record_author:
        flags.append("JSON_EXPECTED_AUTHOR_MISSING")
    elif not author or not _matches_expected(author, target.record_author):
        flags.append("JSON_AUTHOR_MISMATCH")
    if not title:
        flags.append("JSON_TITLE_MISSING")
    elif not target.record_title:
        flags.append("JSON_EXPECTED_TITLE_MISSING")
    elif target.record_title and not _matches_expected(title, target.record_title):
        flags.append("JSON_TITLE_MISMATCH")
    if not category:
        flags.append("JSON_CATEGORY_MISSING")
    elif not target.record_category:
        flags.append("JSON_EXPECTED_CATEGORY_MISSING")
    elif target.record_category and not _matches_expected(category, target.record_category):
        flags.append("JSON_CATEGORY_MISMATCH")
    expected_direction = _direction_pick(target.record_direction or target.position)
    if not direction or _direction_pick(direction) != expected_direction:
        flags.append("JSON_DIRECTION_MISMATCH")
    if not content_field or not content:
        flags.append("JSON_TARGET_FIELD_MISSING")

    peer_keywords = tuple(render_keyword(item, issue) for item in target.peer_keywords)
    metadata_keyword_match = any(
        _matches_expected(field, keyword)
        for field in (author, title, category)
        if field
    )
    if content and metadata_keyword_match:
        blocks = [content]
    else:
        blocks = pick_blocks(content, keyword, peer_keywords, target.mode) if content else []
    all_candidates = collect_candidates(blocks, target)
    direction = direction_candidates(all_candidates, target.position)
    window_candidates = direction_candidate_occurrences(all_candidates, direction)
    matched = [candidate for candidate in window_candidates if candidate.issue == issue]
    all_target_candidates = [candidate for candidate in all_candidates if candidate.issue == issue]
    target_windows = [window for block in blocks for window in issue_windows(block, issue)]
    selected = (
        matched[-1]
        if matched and target.position in {"尾部", "tail", "bottom", "下"}
        else matched[0]
        if matched
        else None
    )
    value = selected.value if selected else ""
    matched_window = selected.window if selected else ""
    selected_block = selected.block if selected else (
        direction[-1].block
        if direction
        else blocks[-1]
        if blocks and target.position in {"尾部", "tail", "bottom", "下"}
        else blocks[0]
        if blocks
        else ""
    )
    if content and not blocks:
        flags.append("JSON_TARGET_KEYWORD_MISSING")
    elif not target_windows:
        flags.append("NO_ISSUE_WINDOW")
    elif not matched:
        flags.append("DIRECTION_WINDOW_EXCLUDED" if all_target_candidates else "NO_RESULT")
    if len({candidate.value for candidate in matched}) > 1:
        flags.append("MULTIPLE_TAIL_CANDIDATES")
        value = ""
        matched_window = matched[-1].window if matched else ""
    if any(flag.startswith("JSON_") and flag not in {"JSON_RECORD_UNIQUE_MATCH"} for flag in flags):
        value = ""
        matched_window = ""
    result = f"{value} {target.label}" if value else ""
    flags.extend(
        audit_flags(
            selected_block,
            [candidate.window for candidate in matched] or target_windows,
            matched_window,
            keyword,
            target.mode,
        )
    )
    if "MULTIPLE_TAIL_CANDIDATES" in flags:
        result = ""
    candidate_evidence = tuple(
        make_candidate_evidence(
            issue,
            candidate.value,
            keyword,
            candidate.window,
            candidate.block,
            candidate.block_index,
            content,
            candidate_position,
            record_id=target_id,
            record_path=record_path,
        )
        for candidate_position, candidate in enumerate(matched)
    )
    return AuditRecord(
        target.label,
        target.url,
        issue,
        result,
        keyword,
        target.position,
        target.mode,
        matched_window,
        tuple(dict.fromkeys(flags)),
        record_id=target_id,
        record_path=record_path,
        record_count=article_count,
        record_match_count=1,
        source_kind="json",
        candidate_evidence=candidate_evidence,
    )
