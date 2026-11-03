"""测试用配置装载助手。

站点被封存后，针对真实配置的解析器断言仍需要拿到该站的真实 Target。
这里把「活跃配置 targets.json」和「封存配置 archived_targets.json」一起暴露，
避免封存动作把真实站点相关的断言直接变成 StopIteration。
"""

import json
from pathlib import Path

from ptw.config import load_targets

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_CONFIG = ROOT / "targets.json"
ARCHIVED_CONFIG = ROOT / "archived_targets.json"


def config_files() -> list[Path]:
    return [path for path in (ACTIVE_CONFIG, ARCHIVED_CONFIG) if path.exists()]


def read_config(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("sites", [])
    if not isinstance(data, list):
        raise ValueError(f"{path.name} 不是站点数组")
    return data


def raw_items() -> list[dict]:
    items: list[dict] = []
    for path in config_files():
        items.extend(read_config(path))
    return items


def all_targets() -> list:
    targets: list = []
    for path in config_files():
        targets.extend(load_targets(path))
    return targets


def target_by_label(label: str):
    for path in config_files():
        if any(item.get("label") == label for item in read_config(path)):
            return next(item for item in load_targets(path) if item.label == label)
    raise LookupError(f"活跃与封存配置里都找不到站点：{label}")
