import re

from ptw.domain.models import ParseResult, Target

TARGET_ID_PATTERN = re.compile(r"^ptw_[a-f0-9]{16}$")
VALID_POSITIONS = {"顶部", "尾部", "top", "tail", "bottom", "上", "下"}
SUPPORTED_MODES = {
    "tail",
    "tail_after_issue",
    "phrase",
    "xingyun",
    "open_number_tail",
    "next_line_bracket_tail",
    "numeric_two_tail",
    "site_dedicated_tail",
    "caimin_shuji_tail",
    "strict_issue_tail",
    "sanxiancai",
    "shenqi_macau",
    "top_priority_conflict",
    "hongyun",
    "manager_article_tail",
}

HARD_PARSE_FLAGS = {
    "NO_RESULT",
    "NO_ISSUE_WINDOW",
    "KEYWORD_NOT_FOUND",
    "MULTIPLE_TAIL_CANDIDATES",
    "MULTIPLE_DOCUMENT_CANDIDATES",
    "CROSS_DOCUMENT_CONFLICT",
    "DIRECTION_WINDOW_EXCLUDED",
    "AUDIT_ERROR",
}


def is_publishable(record: ParseResult | None) -> bool:
    if record is None or not record.result:
        return False
    return not any(
        flag in HARD_PARSE_FLAGS
        or (flag.startswith("JSON_") and flag != "JSON_RECORD_UNIQUE_MATCH")
        for flag in record.risk_flags
    )


def validate_target(target: Target, source: str) -> None:
    if not target.url.startswith(("http://", "https://")):
        raise ValueError(f"{source} URL无效")
    if not target.label:
        raise ValueError(f"{source} 目录名不能为空")
    if not target.keyword:
        raise ValueError(f"{source} 专属关键字不能为空")
    if target.position not in VALID_POSITIONS:
        raise ValueError(f"{source} 位置无效：{target.position}")
    if target.mode not in SUPPORTED_MODES:
        raise ValueError(f"{source} 未注册解析模式：{target.mode}")
    if not TARGET_ID_PATTERN.fullmatch(target.target_id):
        raise ValueError(f"{source} target_id无效：{target.target_id}")
    if target.parser_id != f"site.{target.target_id}":
        raise ValueError(f"{source} parser_id未与target_id专属绑定")
    if not isinstance(target.rule_version, int) or target.rule_version < 1:
        raise ValueError(f"{source} rule_version必须是正整数")
    if ("/article/admin/" in target.url or "/article/manager/" in target.url) and not target.record_id:
        raise ValueError(f"{source} 动态详情页缺少record_id")
    if "/article/admin/" in target.url:
        required = {
            "record_author": target.record_author,
            "record_title": target.record_title,
            "record_category": target.record_category,
            "record_direction": target.record_direction,
            "record_content_field": target.record_content_field,
        }
        for field, value in required.items():
            if not value:
                raise ValueError(f"{source} Admin文章缺少{field}")
    if target.mode == "manager_article_tail":
        if "/article/manager/" not in target.url:
            raise ValueError(f"{source} Manager解析模式与URL不匹配")
        required = {
            "record_author": target.record_author,
            "record_title": target.record_title,
            "record_content_field": target.record_content_field,
        }
        for field, value in required.items():
            if not value:
                raise ValueError(f"{source} Manager文章缺少{field}")
    if target.value_policy not in {"mode_default", "explicit_tail_only"}:
        raise ValueError(f"{source} value_policy无效：{target.value_policy}")
