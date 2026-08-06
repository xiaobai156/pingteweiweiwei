import json
from dataclasses import replace
from pathlib import Path

from ptw.constants import TARGETS_FILE
from ptw.domain.models import Target
from ptw.domain.validation import validate_target
from ptw.fetching.dynamic_api import record_id_from_url
from ptw.text import clean_text


def load_targets(path: Path = TARGETS_FILE) -> list[Target]:
    if path.suffix.lower() != ".json":
        raise ValueError("正式入口只允许读取targets.json")
    items = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(items, list):
        raise ValueError(f"{path.name} 格式错误，顶层必须是数组")

    targets: list[Target] = []
    seen_ids: set[str] = set()
    seen_parser_ids: set[str] = set()
    seen_labels: set[str] = set()
    for index, item in enumerate(items, start=1):
        source = f"{path.name}:{index}"
        if not isinstance(item, dict):
            raise ValueError(f"{source} 格式错误，必须是对象")
        try:
            target = Target(
                url=clean_text(item["url"]),
                position=clean_text(item["position"]),
                label=clean_text(item["label"]),
                keyword=clean_text(item["keyword"]),
                mode=clean_text(item["mode"]),
                record_author=clean_text(item.get("record_author", "")),
                record_title=clean_text(item.get("record_title", "")),
                record_category=clean_text(item.get("record_category", "")),
                record_direction=clean_text(item.get("record_direction", "")),
                record_content_field=clean_text(item.get("record_content_field", "")),
                target_id=clean_text(item["target_id"]),
                parser_id=clean_text(item["parser_id"]),
                rule_version=item["rule_version"],
                record_id=clean_text(item.get("record_id", "")),
                value_policy=clean_text(item["value_policy"]),
            )
        except KeyError as exc:
            raise ValueError(f"{source} 缺少字段 {exc.args[0]}") from exc
        validate_target(target, source)
        if target.record_id and record_id_from_url(target.url) != target.record_id:
            raise ValueError(f"{source} record_id与URL不一致")
        if target.target_id in seen_ids:
            raise ValueError(f"{source} target_id重复：{target.target_id}")
        if target.parser_id in seen_parser_ids:
            raise ValueError(f"{source} parser_id重复：{target.parser_id}")
        if target.label in seen_labels:
            raise ValueError(f"{source} 目录名重复：{target.label}")
        seen_ids.add(target.target_id)
        seen_parser_ids.add(target.parser_id)
        seen_labels.add(target.label)
        targets.append(target)
    from ptw.parsing.registry import registry

    registry.register_many(targets)
    keywords_by_url: dict[str, list[str]] = {}
    for target in targets:
        peers = keywords_by_url.setdefault(target.url, [])
        if target.keyword not in peers:
            peers.append(target.keyword)
    return [
        replace(target, peer_keywords=tuple(keywords_by_url[target.url]))
        for target in targets
    ]


def render_keyword(keyword: str, issue: str) -> str:
    return keyword.replace("{issue}", issue).replace("{期数}", issue)
