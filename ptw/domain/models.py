from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    url: str
    position: str
    label: str
    keyword: str
    mode: str = "tail"
    record_author: str = ""
    record_title: str = ""
    record_category: str = ""
    record_direction: str = ""
    record_content_field: str = ""
    target_id: str = ""
    parser_id: str = ""
    rule_version: int = 1
    record_id: str = ""
    value_policy: str = "mode_default"
    peer_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchDocument:
    source_url: str
    document_type: str
    document_id: str
    raw_content: str
    parent_document_id: str = ""
    record_id: str = ""
    record_path: str = ""
    original_order: int = 0
    fetched_at: str = ""


@dataclass(frozen=True)
class CandidateEvidence:
    issue: str
    value: str
    anchor_text: str
    raw_issue_line: str
    raw_value_lines: tuple[str, ...]
    block_id: str = ""
    block_start: int = -1
    block_end: int = -1
    anchor_position: int = -1
    candidate_position: int = -1
    document_id: str = ""
    source_kind: str = ""
    source_position: int = -1
    record_id: str = ""
    record_path: str = ""
    source_url: str = ""
    parser_id: str = ""
    rule_version: int = 1
    page_position: int = -1
    block_position: int = -1


@dataclass(frozen=True)
class ParseResult:
    label: str
    url: str
    issue: str
    result: str
    keyword: str
    position: str
    mode: str
    matched_window: str
    risk_flags: tuple[str, ...]
    record_id: str = ""
    record_path: str = ""
    record_count: int = 0
    record_match_count: int = 0
    source_kind: str = "html"
    document_id: str = ""
    target_id: str = ""
    parser_id: str = ""
    rule_version: int = 1
    source_position: int = -1
    candidate_evidence: tuple[CandidateEvidence, ...] = ()


AuditRecord = ParseResult
