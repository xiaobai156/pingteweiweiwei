
from dataclasses import replace

from ptw.config import render_keyword
from ptw.domain.models import AuditRecord, Target
from ptw.fetching.documents import split_documents
from ptw.parsing.candidates import (
    collect_candidates,
    direction_candidate_occurrences,
    direction_candidates,
)
from ptw.parsing.evidence import make_candidate_evidence
from ptw.parsing.plugins.hongyun import collect_hongyun_candidates
from ptw.parsing.primitives import (
    audit_flags,
    issue_windows,
    pick_blocks,
)
from ptw.parsing.structured import (
    _structured_json_payloads,
    _structured_record_matches,
    audit_result_from_structured_json,
)
from ptw.text import normalize_digits


def format_not_found_error(record: AuditRecord) -> str:
    flags = set(record.risk_flags)
    if "KEYWORD_NOT_FOUND" in flags:
        reason = f"未命中专属关键字：{record.keyword}"
    elif "JSON_URL_RECORD_ID_MISSING" in flags:
        reason = "结构化接口页面 URL 不含可验证的记录ID"
    elif "JSON_CONFIG_RECORD_ID_MISMATCH" in flags:
        reason = "配置记录ID与详情页URL记录ID不一致"
    elif "JSON_RECORD_NOT_FOUND" in flags:
        reason = f"接口中未找到 URL 对应记录ID：{record.record_id}"
    elif "JSON_RECORD_MULTIPLE_MATCHES" in flags:
        reason = f"接口中发现多个同记录ID：{record.record_id}，已拒绝"
    elif "JSON_AUTHOR_MISMATCH" in flags:
        reason = "URL记录ID命中，但作者字段校验不一致"
    elif "JSON_EXPECTED_AUTHOR_MISSING" in flags:
        reason = "结构化记录缺少专属作者校验配置，已拒绝"
    elif "JSON_EXPECTED_TITLE_MISSING" in flags:
        reason = "结构化记录缺少专属标题校验配置，已拒绝"
    elif "JSON_TITLE_MISSING" in flags or "JSON_TITLE_MISMATCH" in flags:
        reason = "URL记录ID命中，但标题字段校验失败"
    elif "JSON_EXPECTED_CATEGORY_MISSING" in flags:
        reason = "结构化记录缺少专属栏目校验配置，已拒绝"
    elif "JSON_CATEGORY_MISSING" in flags or "JSON_CATEGORY_MISMATCH" in flags:
        reason = "URL记录ID命中，但栏目字段校验失败"
    elif "JSON_DIRECTION_MISMATCH" in flags:
        reason = "URL记录ID命中，但抓取方向字段校验失败"
    elif "JSON_TARGET_FIELD_MISSING" in flags:
        reason = "URL记录ID命中，但目标正文/数据字段缺失"
    elif "JSON_TARGET_KEYWORD_MISSING" in flags:
        reason = f"URL记录ID命中，但指定期数内未命中专属关键字：{record.keyword}"
    elif "DIRECTION_WINDOW_EXCLUDED" in flags:
        reason = f"指定{record.issue}期不在{record.position}方向最近3组有效候选内"
    elif "CROSS_DOCUMENT_CONFLICT" in flags:
        reason = "不同原始文档对同一期给出不同尾数，已拒绝"
    elif "MULTIPLE_DOCUMENT_CANDIDATES" in flags:
        reason = "多个原始文档均命中目标候选，来源不唯一，已拒绝"
    elif "MULTIPLE_TAIL_CANDIDATES" in flags:
        reason = "指定期数出现多个不同显式尾数，已拒绝"
    elif "NO_ISSUE_WINDOW" in flags:
        reason = f"专属模块中未找到指定{record.issue}期"
    elif "AUDIT_ERROR" in flags:
        reason = "专属解析异常"
    else:
        reason = f"指定{record.issue}期存在，但没有符合专属格式的有效尾数（最多二尾）"
    return f"未找到：{record.label} {record.url} - {reason}"


def _audit_single_document(text: str, issue: str, target: Target) -> AuditRecord:
    text = normalize_digits(text)
    issue = normalize_digits(issue)
    structured_payloads = _structured_json_payloads(text)
    if structured_payloads:
        return audit_result_from_structured_json(structured_payloads, issue, target)
    keyword = render_keyword(target.keyword, issue)
    if target.mode == "hongyun":
        candidates = collect_hongyun_candidates(text, keyword)
        direction = direction_candidates(candidates, target.position)
        window_candidates = direction_candidate_occurrences(candidates, direction)
        matched = [candidate for candidate in window_candidates if candidate.issue == issue]
        all_target_candidates = [candidate for candidate in candidates if candidate.issue == issue]
        target_windows = issue_windows(text, issue)
        selected = (
            matched[-1]
            if matched and target.position in {"尾部", "tail", "bottom", "下"}
            else matched[0]
            if matched
            else None
        )
        value = selected.value if selected else ""
        result = f"{value} {target.label}" if value else ""
        matched_window = selected.window if selected else ""
        audit_windows = [candidate.window for candidate in matched] or target_windows
        selected_block = selected.block if selected else (direction[-1].block if direction else text)
        flags = list(audit_flags(selected_block, audit_windows, matched_window, keyword, target.mode))
        if keyword and text.find(keyword) < 0:
            flags.append("KEYWORD_NOT_FOUND")
        if not matched and all_target_candidates:
            flags.append("DIRECTION_WINDOW_EXCLUDED")
        if len({candidate.value for candidate in matched}) > 1:
            flags.append("MULTIPLE_TAIL_CANDIDATES")
            result = ""
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
                text,
                candidate_position,
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
            candidate_evidence=candidate_evidence,
        )

    peer_keywords = tuple(render_keyword(item, issue) for item in target.peer_keywords)
    blocks = pick_blocks(text, keyword, peer_keywords, target.mode)
    candidates = collect_candidates(blocks, target)
    direction = direction_candidates(candidates, target.position)
    window_candidates = direction_candidate_occurrences(candidates, direction)
    matched = [candidate for candidate in window_candidates if candidate.issue == issue]
    all_target_candidates = [candidate for candidate in candidates if candidate.issue == issue]
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
    selected_block = selected.block if selected else (direction[-1].block if direction else (blocks[-1] if blocks and target.position in {"尾部", "tail", "bottom", "下"} else (blocks[0] if blocks else "")))
    result = f"{value} {target.label}" if value else ""
    audit_windows = [candidate.window for candidate in matched] or target_windows
    flags = list(audit_flags(selected_block, audit_windows, matched_window, keyword, target.mode))
    if not matched and all_target_candidates:
        flags.append("DIRECTION_WINDOW_EXCLUDED")
    if len({candidate.value for candidate in matched}) > 1:
        flags.append("MULTIPLE_TAIL_CANDIDATES")
    if len({candidate.block_index for candidate in matched}) > 1:
        flags.append("MULTIPLE_MODULE_CANDIDATES")
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
            text,
            candidate_position,
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
        candidate_evidence=candidate_evidence,
    )


def audit_result_from_text(text: str, issue: str, target: Target) -> AuditRecord:
    issue = normalize_digits(issue)
    documents = split_documents(text)
    structured_payloads: list[object] = []
    structured_documents = []
    for document in documents:
        payloads = _structured_json_payloads(document.raw_content)
        if payloads:
            structured_payloads.extend(payloads)
            structured_documents.append(document)
    uses_structured_record = bool(
        target.record_id
        or target.mode == "manager_article_tail"
        or "/article/admin/" in target.url
        or "/article/manager/" in target.url
    )
    if uses_structured_record and not structured_payloads:
        return AuditRecord(
            target.label,
            target.url,
            issue,
            "",
            render_keyword(target.keyword, issue),
            target.position,
            target.mode,
            "",
            ("JSON_RECORD_NOT_FOUND",),
            record_id=target.record_id,
            source_kind="json",
            target_id=target.target_id,
            parser_id=target.parser_id,
            rule_version=target.rule_version,
        )
    if structured_payloads and uses_structured_record:
        record = audit_result_from_structured_json(structured_payloads, issue, target)
        source_document = structured_documents[0]
        if record.record_match_count == 1 and record.record_id:
            for document in structured_documents:
                document_matches, _ = _structured_record_matches(
                    _structured_json_payloads(document.raw_content),
                    record.record_id,
                )
                if len(document_matches) == 1:
                    source_document = document
                    break
        document_id = (
            source_document.document_id
            if record.record_match_count == 1
            else "structured-multi"
        )
        evidence = tuple(
            replace(
                item,
                document_id=source_document.document_id,
                source_kind=source_document.document_type,
                source_position=source_document.original_order,
                record_id=record.record_id or item.record_id,
                record_path=record.record_path or item.record_path,
                source_url=source_document.source_url or target.url,
                parser_id=target.parser_id,
                rule_version=target.rule_version,
            )
            for item in record.candidate_evidence
        )
        return replace(
            record,
            document_id=document_id,
            source_kind="json",
            source_position=source_document.original_order,
            target_id=target.target_id,
            parser_id=target.parser_id,
            rule_version=target.rule_version,
            candidate_evidence=evidence,
        )

    records: list[AuditRecord] = []
    for document in documents:
        record = _audit_single_document(document.raw_content, issue, target)
        evidence = tuple(
            replace(
                item,
                document_id=document.document_id,
                source_kind=document.document_type,
                source_position=document.original_order,
                record_id=record.record_id or item.record_id,
                record_path=record.record_path or item.record_path,
                source_url=document.source_url or target.url,
                parser_id=target.parser_id,
                rule_version=target.rule_version,
            )
            for item in record.candidate_evidence
        )
        records.append(
            replace(
                record,
                document_id=document.document_id,
                source_kind=document.document_type,
                source_position=document.original_order,
                target_id=target.target_id,
                parser_id=target.parser_id,
                rule_version=target.rule_version,
                candidate_evidence=evidence,
            )
        )

    successful = [record for record in records if record.result]
    if not successful:
        def failure_score(record: AuditRecord) -> int:
            flags = set(record.risk_flags)
            return (
                (4 if "KEYWORD_NOT_FOUND" not in flags else 0)
                + (2 if "NO_ISSUE_WINDOW" not in flags else 0)
                + (1 if record.matched_window else 0)
            )

        best_score = max(failure_score(record) for record in records)
        best = [record for record in records if failure_score(record) == best_score]
        return best[-1] if target.position in {"尾部", "tail", "bottom", "下"} else best[0]
    if (
        len(successful) > 1
        and target.mode == "top_priority_conflict"
        and target.position in {"顶部", "top", "上"}
        and len({record.result for record in successful}) > 1
    ):
        selected = successful[0]
        return replace(
            selected,
            risk_flags=tuple(
                dict.fromkeys((*selected.risk_flags, "TOP_PRIORITY_CONFLICT_SELECTED"))
            ),
        )
    selected = successful[-1] if target.position in {"尾部", "tail", "bottom", "下"} else successful[0]
    if len(successful) > 1:
        flags = tuple(dict.fromkeys((*selected.risk_flags, "MULTIPLE_DOCUMENT_CANDIDATES")))
        if len({record.result for record in successful}) > 1:
            flags = tuple(dict.fromkeys((*flags, "CROSS_DOCUMENT_CONFLICT")))
        return replace(selected, result="", risk_flags=flags)
    return selected



def extract_result_from_text(
    text: str,
    issue: str,
    label: str,
    keyword: str = "",
    position: str = "顶部",
    mode: str = "tail",
) -> str:
    target = Target("", position, label, keyword, mode)
    return audit_result_from_text(normalize_digits(text), normalize_digits(issue), target).result
