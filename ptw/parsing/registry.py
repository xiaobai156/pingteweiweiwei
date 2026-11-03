from dataclasses import dataclass

from ptw.config import render_keyword
from ptw.domain.models import Target
from ptw.domain.validation import SUPPORTED_MODES
from ptw.parsing.primitives import (
    extract_caimin_shuji_tail,
    extract_explicit_two_tail,
    extract_next_line_bracket_tail,
    extract_numeric_two_tail,
    extract_open_number_tail,
    extract_phrase,
    extract_sanxiancai,
    extract_shenqi_macau,
    extract_site_dedicated_tail,
    extract_strict_issue_tail,
    extract_tail,
    extract_xingyun,
)


@dataclass(frozen=True)
class ParserStrategy:
    parser_id: str
    mode: str
    value_policy: str


class ParserRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, ParserStrategy] = {}

    def register(self, target: Target) -> None:
        expected = f"site.{target.target_id}"
        if not target.target_id or target.parser_id != expected:
            raise ValueError(f"{target.label} 未绑定站点专属parser_id")
        if target.mode not in SUPPORTED_MODES:
            raise ValueError(f"{target.label} 使用未注册解析模式：{target.mode}")
        strategy = ParserStrategy(target.parser_id, target.mode, target.value_policy)
        existing = self._strategies.get(target.parser_id)
        if existing and existing != strategy:
            raise ValueError(f"{target.label} parser_id已绑定不同解析规则")
        self._strategies[target.parser_id] = strategy

    def register_many(self, targets: list[Target]) -> None:
        for target in targets:
            self.register(target)

    def resolve(self, target: Target) -> ParserStrategy:
        expected = f"site.{target.target_id}"
        if not target.target_id or target.parser_id != expected:
            raise ValueError(f"{target.label} 未绑定站点专属parser_id")
        if target.mode not in SUPPORTED_MODES:
            raise ValueError(f"{target.label} 使用未注册解析模式：{target.mode}")
        strategy = self._strategies.get(target.parser_id)
        if strategy is None:
            raise ValueError(f"{target.label} parser_id未在注册表中注册")
        if strategy.mode != target.mode or strategy.value_policy != target.value_policy:
            raise ValueError(f"{target.label} parser_id与解析规则不一致")
        return strategy


registry = ParserRegistry()


def extract_value_by_mode(window: str, issue: str, target: Target) -> str:
    registry.resolve(target) if target.parser_id else None
    keyword = render_keyword(target.keyword, issue)
    two_tail = extract_explicit_two_tail(window)
    if two_tail:
        return two_tail
    extractors = {
        "xingyun": lambda: extract_xingyun(window, issue),
        "open_number_tail": lambda: extract_open_number_tail(window, issue),
        "next_line_bracket_tail": lambda: extract_next_line_bracket_tail(window, issue, keyword),
        "numeric_two_tail": lambda: extract_numeric_two_tail(window, issue),
        "site_dedicated_tail": lambda: extract_site_dedicated_tail(
            window,
            issue,
            keyword,
            allow_formula=target.value_policy != "explicit_tail_only",
        ),
        "caimin_shuji_tail": lambda: extract_caimin_shuji_tail(window, issue),
        "strict_issue_tail": lambda: extract_strict_issue_tail(window, issue),
        "sanxiancai": lambda: extract_sanxiancai(window, issue),
        "shenqi_macau": lambda: extract_shenqi_macau(window, issue),
        "top_priority_conflict": lambda: extract_site_dedicated_tail(
            window,
            issue,
            keyword,
            allow_formula=target.value_policy != "explicit_tail_only",
        ),
        "phrase": lambda: extract_phrase(window),
        "tail": lambda: extract_tail(window),
        "tail_after_issue": lambda: extract_tail(window),
    }
    extractor = extractors.get(target.mode)
    if extractor is None:
        return ""
    return extractor()
