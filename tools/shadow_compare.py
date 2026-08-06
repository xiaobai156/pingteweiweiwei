import argparse
import importlib.util
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ptw.config import load_targets  # noqa: E402
from ptw.constants import STRUCTURED_JSON_BEGIN, STRUCTURED_JSON_END  # noqa: E402
from ptw.fetching.documents import serialize_documents  # noqa: E402
from ptw.fetching.http import fetch_documents  # noqa: E402
from ptw.parsing.pipeline import audit_result_from_text  # noqa: E402


LEGACY_ROOT = ROOT.with_name("平特尾尾尾尾尾")


def load_legacy_module():
    path = LEGACY_ROOT / "tail_fetcher.py"
    spec = importlib.util.spec_from_file_location("ptw_shadow_legacy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载旧版模块")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def legacy_text(documents) -> str:
    normal = [item.raw_content for item in documents if item.document_type != "json"]
    structured = [
        f"{STRUCTURED_JSON_BEGIN}\n{item.raw_content}\n{STRUCTURED_JSON_END}"
        for item in documents
        if item.document_type == "json"
    ]
    return "\n".join((*normal, *structured))


def formula_only(window: str) -> bool:
    compact = re.sub(r"\s+", "", window)
    has_formula = bool(re.search(r"[+＋]\d{1,2}=\d", compact))
    has_explicit = bool(
        re.search(r"平特|平尾|特尾|一尾平特|精平|稳中|杀一尾", compact)
    )
    return has_formula and not has_explicit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("issues", nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    legacy = load_legacy_module()
    targets = load_targets()
    old_targets = {(item.label, item.url): item for item in legacy.load_targets()}
    urls = sorted({item.url for item in targets})
    fetched = {}
    fetch_errors = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_documents, url, 30): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                fetched[url] = future.result()
            except Exception as exc:
                fetch_errors[url] = f"{type(exc).__name__}: {exc}"

    cache = json.loads((ROOT / "recent_10_cache.json").read_text(encoding="utf-8-sig"))
    cache_by_id = {item["target_id"]: item for item in cache["sites"]}
    comparisons = []
    for target in targets:
        old_target = old_targets[(target.label, target.url)]
        documents = fetched.get(target.url)
        for issue in args.issues:
            if documents is None:
                comparisons.append(
                    {
                        "label": target.label,
                        "url": target.url,
                        "issue": issue,
                        "status": "fetch_failed",
                        "error": fetch_errors.get(target.url, "unknown"),
                    }
                )
                continue
            new_record = audit_result_from_text(serialize_documents(documents), issue, target)
            old_record = legacy.audit_result_from_text(legacy_text(documents), issue, old_target)
            cached = cache_by_id[target.target_id].get("values", {}).get(issue, "")
            status = "same"
            if old_record.result != new_record.result:
                status = (
                    "precision_rejection"
                    if target.value_policy == "explicit_tail_only"
                    and old_record.result
                    and not new_record.result
                    else "parser_difference"
                )
            elif cached and new_record.result:
                new_value = new_record.result[: -len(target.label)].strip()
                if new_value != cached:
                    status = "cache_source_conflict"
            comparisons.append(
                {
                    "label": target.label,
                    "url": target.url,
                    "issue": issue,
                    "status": status,
                    "old_result": old_record.result,
                    "new_result": new_record.result,
                    "cached_value": cached,
                    "new_flags": list(new_record.risk_flags),
                    "new_document_id": new_record.document_id,
                    "old_formula_only": formula_only(old_record.matched_window),
                }
            )

    by_target_issue = {
        (item["label"], item["issue"]): item
        for item in comparisons
        if item["status"] != "fetch_failed"
    }
    current_issue = args.issues[0]
    policy_recommendations = []
    for target in targets:
        current = by_target_issue.get((target.label, current_issue))
        past = [
            by_target_issue.get((target.label, issue))
            for issue in args.issues[1:]
        ]
        should_be_strict = bool(
            current
            and not current.get("old_formula_only")
            and any(item and item.get("old_formula_only") for item in past)
        )
        if should_be_strict != (target.value_policy == "explicit_tail_only"):
            policy_recommendations.append(
                {
                    "label": target.label,
                    "current_policy": target.value_policy,
                    "recommended_policy": (
                        "explicit_tail_only" if should_be_strict else "mode_default"
                    ),
                }
            )

    report = {
        "issues": args.issues,
        "target_count": len(targets),
        "url_count": len(urls),
        "fetch_error_count": len(fetch_errors),
        "fetch_errors": fetch_errors,
        "policy_recommendations": policy_recommendations,
        "comparisons": comparisons,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for item in comparisons:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
