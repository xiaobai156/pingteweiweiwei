import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ptw.config import load_targets
from ptw.domain.models import AuditRecord, Target
from ptw.domain.validation import is_publishable
from ptw.fetching.dynamic_api import _fetch_manager_article_payload, record_id_from_url
from ptw.parsing.pipeline import (
    audit_result_from_text,
    extract_result_from_text,
    format_not_found_error,
)
from ptw.storage.atomic import atomic_write_pair_with_backup, atomic_write_with_backup
from ptw.storage.cache import result_from_recent_10_cache, update_recent_10_cache
from ptw.storage.output import format_progress_line, format_success_output
from ptw.storage.transaction import commit_single_issue
from tail_fetcher import run


class ExtractResultTests(unittest.TestCase):
    def test_record_id_from_supported_detail_urls(self):
        self.assertEqual(record_id_from_url("https://example.test/topic/654250.html"), "654250")
        self.assertEqual(record_id_from_url("https://example.test/bbs/topic.php?id=17879"), "17879")
        self.assertEqual(record_id_from_url("https://example.test/topic.php?id=1&topic_id=1"), "1")
        self.assertEqual(record_id_from_url("https://example.test/topic.php?id=1&topic_id=2"), "")
        self.assertEqual(record_id_from_url("https://example.test/article/admin/12345"), "12345")
        self.assertEqual(
            record_id_from_url(
                "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj"
            ),
            "6a43d17d57dc857ae1bdf9dd",
        )
        self.assertEqual(record_id_from_url("https://example.test/list/index.html"), "")

    def test_manager_article_parser_locks_id_author_title_and_keyword(self):
        payload = {
            "records": [
                {
                    "id": "other-record",
                    "authorNickname": "诱饵文章",
                    "title": "平特一尾",
                    "html": "201期:〖降龙伏虎〗 🍔平特一尾🍔【9】开:00准",
                },
                {
                    "id": "6a43d17d57dc857ae1bdf9dd",
                    "authorNickname": "降龙伏虎",
                    "title": "202期：【平特一尾】〓 连续中奖",
                    "html": "200期:〖降龙伏虎〗 🍔平特一尾🍔【6】开:鸡46准<br>"
                    "201期:〖降龙伏虎〗 🍔平特一尾🍔【0】开:猪32错",
                },
            ]
        }
        target = Target(
            "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
        )
        self.assertEqual(audit_result_from_text(json.dumps(payload, ensure_ascii=False), "201", target).result, "0尾 降龙伏虎")
        self.assertEqual(audit_result_from_text(json.dumps(payload, ensure_ascii=False), "200", target).result, "6尾 降龙伏虎")

    def test_manager_article_parser_rejects_wrong_id_and_duplicate_id(self):
        target = Target(
            "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
        )
        wrong_id = {"id": "other-record", "authorNickname": "降龙伏虎", "title": "平特一尾", "html": "201期〖降龙伏虎〗 🍔平特一尾🍔【9】"}
        wrong = audit_result_from_text(json.dumps({"records": [wrong_id]}, ensure_ascii=False), "201", target)
        self.assertEqual(wrong.result, "")
        self.assertIn("JSON_RECORD_NOT_FOUND", wrong.risk_flags)

        duplicate = dict(wrong_id)
        duplicate["id"] = "6a43d17d57dc857ae1bdf9dd"
        duplicate["html"] = "201期〖降龙伏虎〗 🍔平特一尾🍔【5】"
        record = audit_result_from_text(json.dumps({"records": [duplicate, duplicate]}, ensure_ascii=False), "201", target)
        self.assertEqual(record.result, "")
        self.assertIn("JSON_RECORD_MULTIPLE_MATCHES", record.risk_flags)

    def test_manager_article_parser_rejects_same_record_without_exact_keyword(self):
        payload = {
            "id": "6a43d17d57dc857ae1bdf9dd",
            "authorNickname": "降龙伏虎",
            "title": "202期：【平特一尾】〓 连续中奖",
            "html": "201期:其他栏目【9】开:00准",
        }
        target = Target(
            "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
        )
        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "201", target)
        self.assertEqual(record.result, "")
        self.assertIn("JSON_TARGET_KEYWORD_MISSING", record.risk_flags)

    def test_manager_article_rejects_foreign_module_value_after_target_issue(self):
        target_id = "6a43d17d57dc857ae1bdf9dd"
        payload = {
            "id": target_id,
            "authorNickname": "降龙伏虎",
            "title": "平特一尾",
            "html": (
                "211期:〖降龙伏虎〗 🍔平特一尾🍔\n"
                "下一个模块 其他\n"
                "〖降龙伏虎〗 🍔平特一尾🍔【9尾】"
            ),
        }
        target = Target(
            f"https://example.test/article/manager/{target_id}?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
            record_id=target_id,
        )
        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "211", target)
        self.assertEqual("", record.result)
        self.assertFalse(is_publishable(record))

    def test_dynamic_admin_page_without_record_payload_cannot_fallback_to_html(self):
        target = Target(
            "https://example.test/article/admin/123",
            "顶部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
            record_id="123",
            target_id="ptw_0123456789abcdef",
            parser_id="site.ptw_0123456789abcdef",
            rule_version=1,
        )
        record = audit_result_from_text("目标模块\n211期 平特一尾【3】", "211", target)
        self.assertEqual("", record.result)
        self.assertIn("JSON_RECORD_NOT_FOUND", record.risk_flags)
        self.assertFalse(is_publishable(record))

    def test_dynamic_record_id_keeps_leading_zero_identity(self):
        target_id = "001"
        target = Target(
            f"https://example.test/article/manager/{target_id}?url=dyj",
            "尾部",
            "目标",
            "〖目标〗 🍔平特一尾🍔",
            "manager_article_tail",
            "目标",
            "平特一尾",
            record_content_field="html",
            record_id=target_id,
        )
        payload = {
            "id": "1",
            "authorNickname": "目标",
            "title": "平特一尾",
            "html": "211期:〖目标〗 🍔平特一尾🍔【9】",
        }
        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "211", target)
        self.assertEqual("", record.result)
        self.assertIn("JSON_RECORD_NOT_FOUND", record.risk_flags)

    def test_manager_article_parser_rejects_configured_record_id_mismatch(self):
        payload = {
            "id": "6a43d17d57dc857ae1bdf9dd",
            "authorNickname": "降龙伏虎",
            "title": "平特一尾",
            "html": "201期:〖降龙伏虎〗 🍔平特一尾🍔【0】",
        }
        target = Target(
            "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
            record_id="wrong-record",
        )
        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "201", target)
        self.assertEqual("", record.result)
        self.assertIn("JSON_CONFIG_RECORD_ID_MISMATCH", record.risk_flags)

    def test_manager_article_api_payload_decodes_base64_and_locks_id(self):
        import base64

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": "6a43d17d57dc857ae1bdf9dd",
                    "authorNickname": "降龙伏虎",
                    "title": base64.b64encode("202期：【平特一尾】〓 连续中奖".encode()).decode(),
                    "html": base64.b64encode("201期:〖降龙伏虎〗 🍔平特一尾🍔【0】开:猪32错".encode()).decode(),
                }

        class FakeSession:
            def get(self, url, **kwargs):
                self.url = url
                return FakeResponse()

        payload = _fetch_manager_article_payload(
            "https://example.test/article/manager/6a43d17d57dc857ae1bdf9dd?url=dyj",
            FakeSession(),
            "<html></html>",
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded["id"], "6a43d17d57dc857ae1bdf9dd")
        self.assertIn("平特一尾", decoded["title"])
        self.assertIn("201期", decoded["html"])

    def test_json_script_is_parsed_without_whole_page_fallback(self):
        payload = {
            "records": [
                {
                    "id": "654250",
                    "author": "嘻嘻哈哈",
                    "title": "澳门小纸条",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期平特5尾",
                }
            ]
        }
        html = (
            "<html>诱饵文章 197期平特9尾"
            "<script type=\"application/json\">"
            + json.dumps(payload, ensure_ascii=False)
            + "</script></html>"
        )
        target = Target(
            "https://example.test/topic/654250.html",
            "尾部",
            "嘻嘻哈哈",
            "嘻嘻哈哈",
            "site_dedicated_tail",
            "嘻嘻哈哈",
            "澳门小纸条",
            "平特一尾",
        )
        self.assertEqual(audit_result_from_text(html, "197", target).result, "5尾 嘻嘻哈哈")

    def test_json_record_metadata_mismatch_is_rejected(self):
        payload = {
            "records": [
                {
                    "id": "654250",
                    "author": "其他作者",
                    "title": "澳门小纸条",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期平特5尾",
                }
            ]
        }
        target = Target(
            "https://example.test/topic/654250.html",
            "尾部",
            "嘻嘻哈哈",
            "嘻嘻哈哈",
            "site_dedicated_tail",
            "嘻嘻哈哈",
            "澳门小纸条",
            "平特一尾",
        )
        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "197", target)
        self.assertEqual(record.result, "")
        self.assertIn("JSON_AUTHOR_MISMATCH", record.risk_flags)

    def test_json_aggregate_selects_url_id_record_before_extracting(self):
        payload = {
            "data": {
                "records": [
                    {
                        "id": "999999",
                        "author": "诱饵文章",
                        "title": "其他栏目",
                        "category": "平特一尾",
                        "position": "尾部",
                        "content": "197期平特9尾",
                    },
                    {
                        "id": "654250",
                        "author": "嘻嘻哈哈",
                        "title": "澳门小纸条",
                        "category": "平特一尾",
                        "position": "尾部",
                        "content": "197期平特5尾",
                    },
                ]
            }
        }
        target = Target(
            "https://example.test/topic/654250.html",
            "尾部",
            "嘻嘻哈哈",
            "嘻嘻哈哈",
            "site_dedicated_tail",
            "嘻嘻哈哈",
            "澳门小纸条",
            "平特一尾",
        )

        self.assertEqual(
            audit_result_from_text(json.dumps(payload, ensure_ascii=False), "197", target).result,
            "5尾 嘻嘻哈哈",
        )

    def test_json_aggregate_rejects_duplicate_url_id(self):
        payload = {
            "records": [
                {
                    "id": "654250",
                    "author": "嘻嘻哈哈",
                    "title": "澳门小纸条",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期平特5尾",
                },
                {
                    "id": "654250",
                    "author": "嘻嘻哈哈",
                    "title": "澳门小纸条旧文",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期平特9尾",
                },
            ]
        }
        target = Target(
            "https://example.test/topic/654250.html",
            "尾部",
            "嘻嘻哈哈",
            "嘻嘻哈哈",
            "site_dedicated_tail",
            "嘻嘻哈哈",
            "澳门小纸条",
            "平特一尾",
        )

        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "197", target)

        self.assertEqual(record.result, "")
        self.assertIn("JSON_RECORD_MULTIPLE_MATCHES", record.risk_flags)

    def test_json_aggregate_rejects_cross_record_content(self):
        payload = {
            "records": [
                {
                    "id": "654250",
                    "author": "嘻嘻哈哈",
                    "title": "澳门小纸条",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期待更新",
                },
                {
                    "id": "999999",
                    "author": "其他作者",
                    "title": "其他文章",
                    "category": "平特一尾",
                    "position": "尾部",
                    "content": "197期平特5尾",
                },
            ]
        }
        target = Target(
            "https://example.test/topic/654250.html",
            "尾部",
            "嘻嘻哈哈",
            "嘻嘻哈哈",
            "site_dedicated_tail",
            "嘻嘻哈哈",
            "澳门小纸条",
            "平特一尾",
        )

        record = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "197", target)

        self.assertEqual(record.result, "")
        self.assertIn("NO_RESULT", record.risk_flags)

    def test_cache_update_rejects_conflicting_existing_issue(self):
        target = Target("https://example.test", "尾部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            original = {
                "schema": 1,
                "issues": [197],
                "sites": [
                    {
                        "name": "目标",
                        "url": "https://example.test",
                        "pick": "bottom",
                        "keyword": "目标",
                        "mode": "site_dedicated_tail",
                        "values": {"197": "3尾"},
                    }
                ],
            }
            cache_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            self.assertFalse(update_recent_10_cache([target], ["5尾 目标"], "197", cache_path))
            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), original)

    def test_atomic_output_write_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.txt"
            path.write_text("old", encoding="utf-8")

            atomic_write_with_backup(path, "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            backups = list(path.parent.glob("result.txt.bak*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old")

    def test_atomic_pair_write_updates_both_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            success = Path(tmp) / "success.txt"
            failure = Path(tmp) / "failure.txt"
            success.write_text("old success", encoding="utf-8")
            failure.write_text("old failure", encoding="utf-8")
            atomic_write_pair_with_backup(success, "new success", failure, "new failure")
            self.assertEqual("new success", success.read_text(encoding="utf-8"))
            self.assertEqual("new failure", failure.read_text(encoding="utf-8"))

    def test_atomic_pair_write_rolls_back_first_file_if_second_replace_fails(self):
        import ptw.storage.atomic as atomic

        with tempfile.TemporaryDirectory() as tmp:
            success = Path(tmp) / "success.txt"
            failure = Path(tmp) / "failure.txt"
            success.write_text("old success", encoding="utf-8")
            failure.write_text("old failure", encoding="utf-8")
            real_replace = atomic.os.replace
            calls = 0

            def replace_with_failure(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated second replace failure")
                return real_replace(source, destination)

            with patch.object(atomic.os, "replace", side_effect=replace_with_failure):
                with self.assertRaises(OSError):
                    atomic_write_pair_with_backup(success, "new success", failure, "new failure")
            self.assertEqual("old success", success.read_text(encoding="utf-8"))
            self.assertEqual("old failure", failure.read_text(encoding="utf-8"))

    def test_single_issue_commit_rolls_back_cache_when_output_commit_fails(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "recent_10_cache.json"
            cache.write_text(
                json.dumps({"schema": 1, "issues": [], "sites": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            before = cache.read_bytes()
            with patch("ptw.storage.transaction.atomic_write_pair_with_backup", side_effect=OSError("output failed")):
                with self.assertRaises(OSError):
                    commit_single_issue(
                        lambda targets, results, issue: update_recent_10_cache(
                            targets, results, issue, cache
                        ),
                        [target],
                        ["9尾 目标"],
                        "84",
                        root / "success.txt",
                        "9尾 目标\n",
                        root / "failure.txt",
                        "",
                        cache_path=cache,
                    )
            self.assertEqual(before, cache.read_bytes())

    def test_atomic_output_write_serializes_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.txt"
            values = [f"value-{index}" for index in range(20)]

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda value: atomic_write_with_backup(path, value), values))

            all_contents = {path.read_text(encoding="utf-8")}
            all_contents.update(
                backup.read_text(encoding="utf-8") for backup in path.parent.glob("result.txt.bak*")
            )
            self.assertEqual(all_contents, set(values))
            self.assertFalse(path.with_name(f".{path.name}.lock").exists())

    def test_format_progress_line_matches_terminal_style(self):
        self.assertEqual(
            format_progress_line(2, 120, 2, 0, 17.12, "亡魂丧魄"),
            "[进度 2/120 1% 成功 2 失败 0 用时 17.1s] 当前: 亡魂丧魄",
        )

    def test_audit_records_matched_window_and_open_number_risk(self):
        target = Target("https://example.test", "顶部", "天下彩", "天下彩【平特一肖一尾】")
        record = audit_result_from_text(
            "天下彩【平特一肖一尾】\n167期:平特一尾【444】开:00准",
            "167",
            target,
        )

        self.assertEqual(record.result, "4尾 天下彩")
        self.assertIn("167期", record.matched_window)
        self.assertIn("OPEN_NUMBER_NEARBY", record.risk_flags)

    def test_audit_marks_next_line_value(self):
        target = Target("https://example.test", "尾部", "流合", "澳彩•平特一尾", "site_dedicated_tail")
        record = audit_result_from_text(
            "澳彩•平特一尾\n184期\n【111尾】\n开：？00",
            "184",
            target,
        )

        self.assertEqual(record.result, "1尾 流合")
        self.assertIn("NEXT_LINE_VALUE", record.risk_flags)

    def test_open_number_is_risk_but_not_a_tail_conflict(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        record = audit_result_from_text(
            "目标\n184期 平特一尾【111】开:29准",
            "184",
            target,
        )

        self.assertEqual(record.result, "1尾 目标")
        self.assertNotIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)
        self.assertIn("OPEN_NUMBER_NEARBY", record.risk_flags)

    def test_conflicting_explicit_tails_in_one_window_are_rejected(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        record = audit_result_from_text(
            "目标\n184期 平特一尾【111】 平特一尾【999】开:29准",
            "184",
            target,
        )

        self.assertEqual("", record.result)
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_direction_uses_only_three_high_confidence_candidates(self):
        text = (
            "目标\n"
            "205期 平特一尾【5】\n"
            "206期 平特一尾【6】\n"
            "207期 平特一尾【7】\n"
            "208期 平特一尾【8】\n"
            "209期 平特一尾【9】\n"
            "210期 平特一尾【0】\n"
            "211期 平特一尾【1】\n"
        )
        top = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        tail = Target("https://example.test", "尾部", "目标", "目标", "site_dedicated_tail")

        top_old = audit_result_from_text(text, "211", top)
        top_new = audit_result_from_text(text, "207", top)
        tail_old = audit_result_from_text(text, "205", tail)
        tail_new = audit_result_from_text(text, "209", tail)

        self.assertEqual("", top_old.result)
        self.assertIn("DIRECTION_WINDOW_EXCLUDED", top_old.risk_flags)
        self.assertEqual("7尾 目标", top_new.result)
        self.assertEqual("", tail_old.result)
        self.assertIn("DIRECTION_WINDOW_EXCLUDED", tail_old.risk_flags)
        self.assertEqual("9尾 目标", tail_new.result)

    def test_direction_window_rejects_conflicting_same_issue_across_modules(self):
        text = (
            "目标\n211期 平特一尾【3】\n"
            "目标\n211期 平特一尾【8】\n"
        )
        for position in ("顶部", "尾部"):
            with self.subTest(position=position):
                record = audit_result_from_text(
                    text,
                    "211",
                    Target("https://example.test", position, "目标", "目标", "site_dedicated_tail"),
                )
                self.assertEqual("", record.result)
                self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_direction_window_deduplicates_same_issue_same_value_but_keeps_evidence(self):
        text = (
            "目标\n"
            "208期 平特一尾【8】\n"
            "209期 平特一尾【9】\n"
            "210期 平特一尾【0】\n"
            "目标\n"
            "210期 平特一尾【0】\n"
            "211期 平特一尾【1】\n"
        )
        target = Target("https://example.test", "尾部", "目标", "目标", "site_dedicated_tail")
        previous = audit_result_from_text(text, "209", target)
        record = audit_result_from_text(text, "210", target)
        self.assertEqual("9尾 目标", previous.result)
        self.assertEqual("0尾 目标", record.result)
        self.assertEqual(2, len(record.candidate_evidence))
        self.assertEqual(["0尾", "0尾"], [item.value for item in record.candidate_evidence])

    def test_not_found_error_explains_missing_keyword(self):
        target = Target("https://example.test", "顶部", "目标", "专属模块", "site_dedicated_tail")
        record = audit_result_from_text("其他模块\n84期 平特一尾【9】", "84", target)

        self.assertIn("KEYWORD_NOT_FOUND", record.risk_flags)
        self.assertEqual(
            format_not_found_error(record),
            "未找到：目标 https://example.test - 未命中专属关键字：专属模块",
        )

    def test_not_found_error_explains_missing_requested_issue(self):
        target = Target("https://example.test", "顶部", "目标", "专属模块", "site_dedicated_tail")
        record = audit_result_from_text("专属模块\n85期 平特一尾【9】", "84", target)

        self.assertIn("NO_ISSUE_WINDOW", record.risk_flags)
        self.assertEqual(
            format_not_found_error(record),
            "未找到：目标 https://example.test - 专属模块中未找到指定84期",
        )

    def test_not_found_error_explains_invalid_tail_format(self):
        target = Target("https://example.test", "顶部", "目标", "专属模块", "site_dedicated_tail")
        record = audit_result_from_text("专属模块\n84期 平特一尾【59尾】", "84", target)

        self.assertIn("NO_RESULT", record.risk_flags)
        self.assertEqual(
            format_not_found_error(record),
            "未找到：目标 https://example.test - 指定84期存在，但没有符合专属格式的有效尾数（最多二尾）",
        )

    def test_issue_requires_complete_numeric_boundary(self):
        text = "目标\n184期 平特一尾【9】开:00准"
        self.assertEqual(
            extract_result_from_text(text, "84", "目标", keyword="目标", mode="site_dedicated_tail"),
            "",
        )

    def test_issue_window_stops_before_next_issue(self):
        text = "目标\n84期 本期说明\n85期 平特一尾【9】开:00准"
        self.assertEqual(
            extract_result_from_text(text, "84", "目标", keyword="目标", mode="site_dedicated_tail"),
            "",
        )

    def test_issue_window_stops_before_next_issue_on_same_line(self):
        text = "目标\n84期 本期说明 85期 平特一尾【9】开:00准"
        self.assertEqual(
            extract_result_from_text(text, "84", "目标", keyword="目标", mode="site_dedicated_tail"),
            "",
        )

    def test_distinct_digits_cannot_be_collapsed_into_one_tail(self):
        text = "目标\n84期 平特一尾【59尾】开:00准"
        self.assertEqual(
            extract_result_from_text(text, "84", "目标", keyword="目标", mode="site_dedicated_tail"),
            "",
        )

    def test_explicit_two_tail_format_returns_two_separate_tails(self):
        text = "目标\n192期:平特二尾【3.5】开:00准"
        self.assertEqual(
            extract_result_from_text(text, "192", "目标", keyword="目标", mode="site_dedicated_tail"),
            "3尾 5尾 目标",
        )

    def test_two_tail_conflict_across_modules_is_rejected(self):
        text = "目标\n192期:平特二尾【3.5】开:00准\n目标\n192期:二尾平特《6.8》开:00准"
        for position in ("顶部", "尾部"):
            with self.subTest(position=position):
                record = audit_result_from_text(
                    text,
                    "192",
                    Target("https://example.test", position, "目标", "目标", "site_dedicated_tail"),
                )
                self.assertEqual("", record.result)
                self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_all_strict_modes_reject_conflicting_direction_candidates(self):
        cases = [
            ("site_dedicated_tail", "目标\n84期 平特一尾【1】开:00准\n目标\n84期 平特一尾【9】开:00准"),
            ("strict_issue_tail", "目标\n84期 平特一尾【1】开:00准\n目标\n84期 平特一尾【9】开:00准"),
            ("open_number_tail", "目标\n84期开:21\n目标\n84期开:29"),
            ("next_line_bracket_tail", "84期目标\n【1】\n84期目标\n【9】"),
        ]
        for mode, text in cases:
            with self.subTest(mode=mode, position="top"):
                target = Target("https://example.test", "顶部", "目标", "目标", mode)
                record = audit_result_from_text(text, "84", target)
                self.assertEqual("", record.result)
                self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)
            with self.subTest(mode=mode, position="tail"):
                target = Target("https://example.test", "尾部", "目标", "目标", mode)
                record = audit_result_from_text(text, "84", target)
                self.assertEqual("", record.result)
                self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_success_output_appends_numeric_tail_ranking(self):
        results = ["7尾 鸿运", "1尾 熊出没", "一尾有码 蛇蛋图", "1尾 天下彩"]
        self.assertEqual(
            format_success_output(results),
            "7尾 鸿运\n1尾 熊出没\n一尾有码 蛇蛋图\n1尾 天下彩\n\n"
            "尾数排行榜\n1尾：2条\n7尾：1条\n",
        )

    def test_success_output_ranks_both_tails(self):
        self.assertEqual(
            format_success_output(["3尾 5尾 甲", "5尾 6尾 乙"]),
            "3尾 5尾 甲\n5尾 6尾 乙\n\n尾数排行榜\n5尾：2条\n3尾：1条\n6尾：1条\n",
        )

    def test_fullwidth_tail_is_normalized_for_result_ranking_and_cache(self):
        text = "目标\n８４期 平特一尾【９】开:００准"
        result = extract_result_from_text(text, "84", "目标", keyword="目标", mode="site_dedicated_tail")
        self.assertEqual(result, "9尾 目标")
        self.assertIn("9尾：1条", format_success_output([result]))

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
            self.assertTrue(update_recent_10_cache([target], ["９尾 目标"], "８４", cache_path))
            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["sites"][0]["values"]["84"], "9尾")

    def test_recent_10_cache_preserves_two_tail_value(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            self.assertTrue(update_recent_10_cache([target], ["3尾 5尾 目标"], "192", cache_path))
            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))

            self.assertEqual(data["sites"][0]["values"]["192"], "3尾 5尾")
            self.assertEqual(result_from_recent_10_cache(target, "192", cache_path), "3尾 5尾 目标")

    def test_recent_10_cache_merges_current_issue_and_keeps_window(self):
        targets = [
            Target("https://example.test/a", "顶部", "甲目录", "甲目录", "site_dedicated_tail"),
            Target("https://example.test/b", "尾部", "乙目录", "乙目录", "site_dedicated_tail"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            old_values = {str(issue): "1尾" for issue in range(190, 180, -1)}
            old_values["180"] = "0尾"
            cache_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "issues": list(range(190, 180, -1)),
                        "sites": [
                            {
                                "name": "甲目录",
                                "url": "https://example.test/a",
                                "pick": "top",
                                "keyword": "甲目录",
                                "mode": "site_dedicated_tail",
                                "values": old_values,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            self.assertTrue(update_recent_10_cache(targets, ["2尾 甲目录"], "191", cache_path))
            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))

        self.assertEqual(data["window_size"], 10)
        self.assertEqual(data["issues"], [191, 190, 189, 188, 187, 186, 185, 184, 183, 182])
        first = data["sites"][0]
        self.assertEqual(first["name"], "甲目录")
        self.assertEqual(first["pick"], "top")
        self.assertEqual(first["values"]["191"], "2尾")
        self.assertNotIn("181", first["values"])
        self.assertEqual(data["sites"][1]["pick"], "bottom")

    def test_recent_10_cache_preserves_existing_site_order(self):
        targets = [
            Target("https://example.test/a", "顶部", "甲目录", "甲目录", "site_dedicated_tail"),
            Target("https://example.test/b", "尾部", "乙目录", "乙目录", "site_dedicated_tail"),
            Target("https://example.test/c", "顶部", "丙目录", "丙目录", "site_dedicated_tail"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "issues": [190],
                        "sites": [
                            {
                                "name": "乙目录",
                                "url": "https://example.test/b",
                                "pick": "bottom",
                                "keyword": "乙目录",
                                "mode": "site_dedicated_tail",
                                "values": {"190": "7尾"},
                            },
                            {
                                "name": "甲目录",
                                "url": "https://example.test/a",
                                "pick": "top",
                                "keyword": "甲目录",
                                "mode": "site_dedicated_tail",
                                "values": {"190": "3尾"},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            update_recent_10_cache(
                targets,
                ["1尾 甲目录", "2尾 乙目录", "4尾 丙目录"],
                "191",
                cache_path,
            )
            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))

        self.assertEqual([site["name"] for site in data["sites"]], ["乙目录", "甲目录", "丙目录"])
        self.assertEqual(data["sites"][0]["values"]["191"], "2尾")
        self.assertEqual(data["sites"][1]["values"]["191"], "1尾")
        self.assertEqual(data["sites"][2]["values"]["191"], "4尾")

    def test_recent_10_cache_result_requires_exact_site_identity(self):
        target = Target("https://example.test/a", "顶部", "甲目录", "甲目录", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "sites": [
                            {
                                "name": "甲目录",
                                "url": "https://example.test/a",
                                "pick": "top",
                                "mode": "site_dedicated_tail",
                                "keyword": "甲目录",
                                "values": {"189": "4尾"},
                            },
                            {
                                "name": "甲目录",
                                "url": "https://example.test/other",
                                "pick": "top",
                                "mode": "site_dedicated_tail",
                                "keyword": "甲目录",
                                "values": {"189": "9尾"},
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            self.assertEqual(result_from_recent_10_cache(target, "189", cache_path), "4尾 甲目录")
            self.assertEqual(
                result_from_recent_10_cache(
                    Target("https://example.test/a", "尾部", "甲目录", "甲目录", "site_dedicated_tail"),
                    "189",
                    cache_path,
                ),
                "",
            )

            self.assertEqual(
                result_from_recent_10_cache(
                    Target("https://example.test/a", "顶部", "甲目录", "新关键字", "site_dedicated_tail"),
                    "189",
                    cache_path,
                ),
                "",
            )

    def test_corrupt_recent_10_cache_is_never_overwritten(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            original = b"{broken json"
            cache_path.write_bytes(original)

            self.assertFalse(update_recent_10_cache([target], ["9尾 目标"], "84", cache_path))
            self.assertEqual(cache_path.read_bytes(), original)

    def test_non_object_recent_10_cache_is_never_overwritten(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        for original in (b"[]", b"null", b'"text"'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                cache_path = Path(tmp) / "recent_10_cache.json"
                cache_path.write_bytes(original)

                self.assertFalse(update_recent_10_cache([target], ["9尾 目标"], "84", cache_path))
                self.assertEqual(cache_path.read_bytes(), original)
                self.assertFalse(cache_path.with_name(f".{cache_path.name}.lock").exists())

    def test_malformed_cache_object_is_never_rebuilt(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        originals = [
            {"schema": 2, "issues": [], "sites": {}},
            {"schema": 2, "issues": []},
            {"schema": 3, "issues": [], "sites": []},
        ]
        for original in originals:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                cache_path = Path(tmp) / "recent_10_cache.json"
                raw = json.dumps(original, ensure_ascii=False).encode("utf-8")
                cache_path.write_bytes(raw)
                self.assertFalse(update_recent_10_cache([target], ["9尾 目标"], "84", cache_path))
                self.assertEqual(raw, cache_path.read_bytes())

    def test_recent_10_cache_write_is_atomic_and_keeps_backup(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            original = json.dumps({"schema": 1, "issues": [83], "sites": []}, ensure_ascii=False).encode("utf-8")
            cache_path.write_bytes(original)

            self.assertTrue(update_recent_10_cache([target], ["9尾 目标"], "84", cache_path))
            self.assertEqual(cache_path.with_suffix(".json.bak").read_bytes(), original)
            self.assertFalse(any(path.name.endswith(".tmp") for path in Path(tmp).iterdir()))

    def test_recent_10_cache_serializes_concurrent_issue_updates(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            issues = ["84", "85", "86", "87"]
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(update_recent_10_cache, [target], [f"{issue[-1]}尾 目标"], issue, cache_path)
                    for issue in issues
                ]
                self.assertTrue(all(future.result() for future in futures))

            data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(set(data["issues"]), {84, 85, 86, 87})
            self.assertEqual(set(data["sites"][0]["values"]), set(issues))
            self.assertFalse(cache_path.with_name(f".{cache_path.name}.lock").exists())

    def test_recent_10_cache_is_byte_identical_when_no_target_succeeds(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标",
            "site_dedicated_tail",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "recent_10_cache.json"
            original = json.dumps(
                {"schema": 1, "issues": [83], "sites": []},
                ensure_ascii=False,
            ).encode("utf-8")
            cache_path.write_bytes(original)
            self.assertTrue(update_recent_10_cache([target], [], "84", cache_path))
            self.assertEqual(original, cache_path.read_bytes())
            self.assertFalse(cache_path.with_suffix(".json.bak").exists())

    def test_run_does_not_use_recent_cache_as_result_fallback(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp, patch("tail_fetcher.load_targets", return_value=[target]), patch(
            "tail_fetcher.fetch_text", return_value="目标\n84期 本期无结果"
        ):
            results, errors = run(
                "84",
                Path(tmp) / "success.txt",
                Path(tmp) / "fail.txt",
                update_cache=False,
                audit_path=Path(tmp) / "audit.txt",
            )

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("指定84期存在，但没有符合专属格式的有效尾数（最多二尾）", errors[0])

    def test_run_does_not_write_audit_when_page_never_loads(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp, patch("tail_fetcher.load_targets", return_value=[target]), patch(
            "tail_fetcher.fetch_text", side_effect=urllib.error.URLError("offline")
        ):
            audit_path = Path(tmp) / "audit.txt"
            results, errors = run(
                "84",
                Path(tmp) / "success.txt",
                Path(tmp) / "fail.txt",
                update_cache=False,
                audit_path=audit_path,
            )
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertFalse(audit_path.exists())

    def test_run_records_runtime_fetch_failure_without_aborting(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        with tempfile.TemporaryDirectory() as tmp, patch("tail_fetcher.load_targets", return_value=[target]), patch(
            "tail_fetcher.fetch_text", side_effect=RuntimeError("脚本抓取失败：missing.js")
        ):
            success_path = Path(tmp) / "success.txt"
            fail_path = Path(tmp) / "fail.txt"
            results, errors = run(
                "84",
                success_path,
                fail_path,
                update_cache=False,
            )

            self.assertEqual([], results)
            self.assertEqual(1, len(errors))
            self.assertIn("脚本抓取失败：missing.js", errors[0])
            self.assertIn("脚本抓取失败：missing.js", fail_path.read_text(encoding="utf-8"))

    def test_run_does_not_publish_success_when_cache_transaction_is_rejected(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标",
            "site_dedicated_tail",
        )
        with tempfile.TemporaryDirectory() as tmp, \
                patch("tail_fetcher.load_targets", return_value=[target]), \
                patch("tail_fetcher.fetch_text", return_value="目标\n84期 平特一尾【9】"), \
                patch("tail_fetcher.update_recent_10_cache", return_value=False):
            success_path = Path(tmp) / "success.txt"
            fail_path = Path(tmp) / "fail.txt"
            results, errors = run("84", success_path, fail_path, update_cache=True)
            self.assertEqual([], results)
            self.assertEqual("", success_path.read_text(encoding="utf-8"))
            self.assertIn("缓存更新被拒绝", fail_path.read_text(encoding="utf-8"))
            self.assertTrue(any("缓存更新被拒绝" in error for error in errors))

    def test_run_rejects_nonempty_result_with_hard_risk_flag(self):
        target = Target("https://example.test", "顶部", "目标", "目标", "site_dedicated_tail")
        unsafe_record = AuditRecord(
            label="目标",
            url=target.url,
            issue="84",
            result="9尾 目标",
            keyword=target.keyword,
            position=target.position,
            mode=target.mode,
            matched_window="84期 平特一尾【999】",
            risk_flags=("MULTIPLE_TAIL_CANDIDATES",),
        )
        with tempfile.TemporaryDirectory() as tmp, \
                patch("tail_fetcher.load_targets", return_value=[target]), \
                patch("tail_fetcher.fetch_text", return_value="ignored"), \
                patch("tail_fetcher.audit_result_from_text", return_value=unsafe_record):
            success_path = Path(tmp) / "success.txt"
            fail_path = Path(tmp) / "fail.txt"
            results, errors = run("84", success_path, fail_path, update_cache=False)
            self.assertEqual([], results)
            self.assertEqual("", success_path.read_text(encoding="utf-8"))
            self.assertIn("多个不同显式尾数", fail_path.read_text(encoding="utf-8"))

    def test_hongyun_uses_exact_module_title(self):
        target = next(item for item in load_targets() if item.label == "鸿运")
        self.assertEqual(target.keyword, "澳门鸿运『平特一尾』")
        self.assertEqual(target.mode, "hongyun")

    def test_hongyun_parser_reads_golden_house_tail_only(self):
        text = """
澳门鸿运『平特一尾』
170期平特一尾(02.12.22.32.42)
澳门鸿运『平特一尾』
170期《黄金屋》平特尾:[777尾] 开[00]准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "170",
                "鸿运",
                keyword="澳门鸿运『平特一尾』",
                position="顶部",
                mode="hongyun",
            ),
            "7尾 鸿运",
        )

    def test_hongyun_parser_rejects_conflicting_complete_modules(self):
        text = """
澳门鸿运『平特一尾』
170期《黄金屋》平特尾:[111尾] 开[00]准
澳门鸿运『平特一尾』
170期《黄金屋》平特尾:[999尾] 开[00]准
"""
        record = audit_result_from_text(
            text,
            "170",
            next(item for item in load_targets() if item.label == "鸿运"),
        )
        self.assertEqual("", record.result)
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_hongyun_issue_requires_complete_numeric_boundary(self):
        text = """
澳门鸿运『平特一尾』
184期《黄金屋》平特尾:[777尾] 开[00]准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "84",
                "鸿运",
                keyword="澳门鸿运『平特一尾』",
                position="顶部",
                mode="hongyun",
            ),
            "",
        )

    def test_hongyun_rejects_multiple_tail_digits(self):
        text = """
澳门鸿运『平特一尾』
170期《黄金屋》平特尾:[59尾] 开[00]准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "170",
                "鸿运",
                keyword="澳门鸿运『平特一尾』",
                position="顶部",
                mode="hongyun",
            ),
            "",
        )

    def test_extracts_tail_from_bottom_summary(self):
        text = """
澳门熊出没「规律一尾」
166期 20-15-01-04-21-28 T06+8=4尾√
167期 平特4尾
"""
        self.assertEqual(extract_result_from_text(text, "167", "熊出没"), "4尾 熊出没")

    def test_extracts_repeated_digit_tail(self):
        text = """
天下彩【平特一肖一尾】
167期:平特一尾【444】开00准
"""
        self.assertEqual(extract_result_from_text(text, "167", "天下彩"), "4尾 天下彩")

    def test_extracts_number_group_tail(self):
        text = """
九龙论坛『平特1尾』
166期平特1尾【08-18-28-38-48】开:00准
"""
        self.assertEqual(extract_result_from_text(text, "166", "九龙论坛"), "8尾 九龙论坛")

    def test_extracts_next_line_digit(self):
        text = """
一本万利【欢乐平尾】
160期: 欢乐平尾 开:00错
【3】
"""
        self.assertEqual(extract_result_from_text(text, "160", "一本万利"), "3尾 一本万利")

    def test_extracts_phrase_mode(self):
        text = """
澳门蛇蛋图『四字头尾』
166期:【一尾有码平特开】开:00准
"""
        self.assertEqual(extract_result_from_text(text, "166", "蛇蛋图", mode="phrase"), "一尾有码 蛇蛋图")

    def test_skips_same_issue_lines_without_tail(self):
        text = """
天下彩【平特一肖一尾】
166期诗句：今期必中
166期:平特一尾【888】开28准
"""
        self.assertEqual(extract_result_from_text(text, "166", "天下彩"), "8尾 天下彩")

    def test_extracts_more_bracket_styles(self):
        self.assertEqual(
            extract_result_from_text("166期平特一尾《666》开00准", "166", "犹恐失之"),
            "6尾 犹恐失之",
        )
        self.assertEqual(
            extract_result_from_text("166期: 平特一尾〖3〗开00准", "166", "所爱一生"),
            "3尾 所爱一生",
        )
        self.assertEqual(
            extract_result_from_text("166期：【平特一尾】{888}开00准", "166", "春色满园"),
            "8尾 春色满园",
        )
        self.assertEqual(
            extract_result_from_text("166期 平尾3", "166", "熱門高手"),
            "3尾 熱門高手",
        )

    def test_ignores_zodiac_when_no_tail_digit_exists(self):
        self.assertEqual(extract_result_from_text("166期平特 蛇肖", "166", "嘻嘻哈哈"), "")
        self.assertEqual(extract_result_from_text("166期：╠(平特一肖)╣▲马马马▼开00准", "166", "众志成城"), "")

    def test_extracts_tail_after_earlier_zodiac_text(self):
        text = """
166期：(平特一肖)羊羊羊
166期：(平特一尾)666
"""
        self.assertEqual(extract_result_from_text(text, "166", "平特一肖"), "6尾 平特一肖")

    def test_extracts_wufulinmen_style(self):
        text = "166期:《五福临门》平特①尾【7】开:牛06错"
        self.assertEqual(extract_result_from_text(text, "166", "五福临门"), "7尾 五福临门")

    def test_extracts_tail_from_later_line_in_same_issue_block(self):
        text = """
167期:〖高级一码〗12
167期:〖高级三码〗12.24.10
167期:〖精选⑤码〗12.24.10.32.04
167期:〖精选⑧码〗12.24.10.32.04.23.35.33
167期:〖精选①肖〗羊
167期:〖内部三肖〗羊鸡猪
167期:〖内部四肖〗羊鸡猪兔
167期:〖必开六肖〗羊鸡猪兔猴狗
167期:〖金牌单双〗单数+兔羊
167期:〖家畜野兽〗家禽+猴兔
167期:〖金牌六尾〗7-4-5-8-1-0
167期:〖特码大小〗小数
167期:〖平特一肖〗牛牛牛
167期:〖平特一尾〗666
"""
        self.assertEqual(extract_result_from_text(text, "167", "杀伐决断"), "6尾 杀伐决断")

    def test_does_not_fallback_to_whole_page_when_keyword_missing(self):
        text = """
别的模块
167期:平特一尾【666】开00准
"""
        self.assertEqual(
            extract_result_from_text(text, "167", "目标模块", keyword="目标关键字", position="顶部"),
            "",
        )

    def test_keyword_must_match_exactly_without_normalized_fallback(self):
        text = """
目标 关键字
167期:平特一尾【666】开00准
"""
        self.assertEqual(
            extract_result_from_text(text, "167", "目标模块", keyword="目标关键字", position="顶部"),
            "",
        )

    def test_top_rejects_conflicting_candidates_in_same_module(self):
        text = """
目标关键字
167期:平特一尾【111】开00准
167期:平特一尾【999】开00准
"""
        target = Target("https://example.test", "顶部", "目标模块", "目标关键字")
        record = audit_result_from_text(text, "167", target)
        self.assertEqual(record.result, "")
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_tail_rejects_conflicting_candidates_in_same_module(self):
        text = """
目标关键字
167期:平特一尾【111】开00准
167期:平特一尾【999】开00准
"""
        target = Target("https://example.test", "尾部", "目标模块", "目标关键字")
        record = audit_result_from_text(text, "167", target)
        self.assertEqual(record.result, "")
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_peer_module_keyword_stops_same_document_contamination(self):
        target = Target(
            "https://example.test",
            "尾部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
            peer_keywords=("目标模块", "诱饵模块"),
        )
        text = (
            "目标模块\n"
            "211期 平特一尾【3】\n"
            "诱饵模块\n"
            "211期 平特一尾【8】\n"
        )
        self.assertEqual("3尾 目标", audit_result_from_text(text, "211", target).result)

    def test_unconfigured_module_between_issue_and_bracket_is_not_crossed(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
        )
        text = "目标模块 211期\n其他栏目\n【9】\n"
        self.assertEqual("", audit_result_from_text(text, "211", target).result)

    def test_foreign_module_issue_cannot_supply_target_tail(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
        )
        text = (
            "目标模块\n"
            "211期 目标模块无尾\n"
            "其他栏目\n"
            "211期 平特一尾【9】\n"
        )
        record = audit_result_from_text(text, "211", target)
        self.assertEqual("", record.result)
        self.assertFalse(is_publishable(record))

    def test_foreign_module_before_issue_cannot_supply_target_tail(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
        )
        text = "目标模块\n其他栏目\n211期 平特一尾【9】\n"
        record = audit_result_from_text(text, "211", target)
        self.assertEqual("", record.result)
        self.assertFalse(is_publishable(record))

    def test_target_block_is_not_truncated_at_8000_characters(self):
        target = Target(
            "https://example.test",
            "顶部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
        )
        text = "目标模块\n" + ("说明文字" * 2500) + "\n211期 平特一尾【7】\n"
        self.assertEqual("7尾 目标", audit_result_from_text(text, "211", target).result)

    def test_repeated_target_modules_still_follow_top_and_tail(self):
        text = (
            "目标模块\n"
            "211期 平特一尾【3】\n"
            "目标模块\n"
            "211期 平特一尾【8】\n"
        )
        common = {
            "url": "https://example.test",
            "label": "目标",
            "keyword": "目标模块",
            "mode": "site_dedicated_tail",
            "peer_keywords": ("目标模块",),
        }
        top = Target(position="顶部", **common)
        tail = Target(position="尾部", **common)
        self.assertEqual("", audit_result_from_text(text, "211", top).result)
        self.assertEqual("", audit_result_from_text(text, "211", tail).result)

    def test_hongyun_direction_window_rejects_old_issue(self):
        text = (
            "澳门鸿运『平特一尾』\n"
            "205期《黄金屋》平特尾:[5尾]\n"
            "206期《黄金屋》平特尾:[6尾]\n"
            "207期《黄金屋》平特尾:[7尾]\n"
            "208期《黄金屋》平特尾:[8尾]\n"
            "209期《黄金屋》平特尾:[9尾]\n"
            "210期《黄金屋》平特尾:[0尾]\n"
            "211期《黄金屋》平特尾:[1尾]\n"
        )
        target = Target(
            "https://example.test",
            "尾部",
            "鸿运",
            "澳门鸿运『平特一尾』",
            "hongyun",
        )
        old = audit_result_from_text(text, "205", target)
        current = audit_result_from_text(text, "209", target)
        self.assertEqual("", old.result)
        self.assertIn("DIRECTION_WINDOW_EXCLUDED", old.risk_flags)
        self.assertEqual("9尾 鸿运", current.result)

    def test_issue_placeholder_in_keyword_uses_requested_issue(self):
        text = """
167期【小诸葛】原创料【平特二尾】
167期:平特一尾【111】开00准
168期【小诸葛】原创料【平特二尾】
168期:平特一尾【888】开00准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "老诸葛",
                keyword="{issue}期【小诸葛】原创料【平特二尾】",
                position="尾部",
            ),
            "8尾 老诸葛",
        )

    def test_line_after_issue_parser_ignores_later_ad_tails(self):
        text = """
平特一尾
167期
广告
访问官网
精华资料
 广告
6666尾再次爆中吗？
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "167",
                "星云神算",
                keyword="平特一尾",
                position="尾部",
                mode="tail_after_issue",
            ),
            "",
        )

    def test_shenqi_macau_parser_reads_requested_issue_pingwei_bracket(self):
        text = """
神奇平特尾专区
168期平尾[1]投资500万中
167期平尾[3]投资500万中
165期平尾[7]投资500万中
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "167",
                "神奇澳门",
                keyword="神奇平特尾专区",
                position="尾部",
                mode="shenqi_macau",
            ),
            "3尾 神奇澳门",
        )

    def test_xingyun_parser_reads_again_explode_row_for_requested_issue(self):
        text = """
平特一尾
167期:23-03-40-39-07-11T19 19+10=9尾
168期
9999尾再次爆中吗?
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "星云神算",
                keyword="平特一尾",
                position="尾部",
                mode="xingyun",
            ),
            "9尾 星云神算",
        )

    def test_xingyun_parser_does_not_take_again_explode_row_for_other_issue(self):
        text = """
平特一尾
167期:23-03-40-39-07-11T19 等待开奖
168期
9999尾再次爆中吗?
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "167",
                "星云神算",
                keyword="平特一尾",
                position="尾部",
                mode="xingyun",
            ),
            "",
        )

    def test_xingyun_parser_rejects_multiple_tail_digits(self):
        text = """
平特一尾
168期
59尾再次爆中吗?
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "星云神算",
                keyword="平特一尾",
                position="尾部",
                mode="xingyun",
            ),
            "",
        )

    def test_yibenwanli_parser_reads_open_number_tail(self):
        text = """
一本万利【至强平尾】
167期:🌳至强平尾🌳开:39准
168期:🌳至强平尾🌳开:40准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "一本万利",
                keyword="一本万利【至强平尾】",
                position="尾部",
                mode="open_number_tail",
            ),
            "0尾 一本万利",
        )

    def test_sanxiancai_parser_reads_history_row_under_jingpin_area(self):
        text = """
精品区 170期【平特一尾】已公开
167期【平特一尾】【888尾】开：19错
168期【平特一尾】【111尾】开：11中
170期【平特一尾】【444尾】开：00中
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "三仙财",
                keyword="精品区",
                position="尾部",
                mode="sanxiancai",
            ),
            "1尾 三仙财",
        )

    def test_sanxiancai_parser_rejects_multiple_tail_digits(self):
        text = """
精品区 170期【平特一尾】已公开
168期【平特一尾】【59尾】开：11中
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "168",
                "三仙财",
                keyword="精品区",
                position="尾部",
                mode="sanxiancai",
            ),
            "",
        )


    def test_chuhuo_pingwei_parser_reads_next_line_bracket_for_requested_issue(self):
        text = """
出货平尾
177期:🎪出货平尾🎪开:05准
【5】
178期:🎪出货平尾🎪开:00准
【每晚20点关注发财】
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "177",
                "出货平尾",
                keyword="出货平尾",
                position="尾部",
                mode="next_line_bracket_tail",
            ),
            "5尾 出货平尾",
        )

    def test_chuhuo_pingwei_parser_ignores_other_bracket_digits_in_same_window(self):
        text = """
出货平尾
177期:【4】🎪出货平尾🎪开:05准
【5】
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "177",
                "出货平尾",
                keyword="出货平尾",
                position="尾部",
                mode="next_line_bracket_tail",
            ),
            "5尾 出货平尾",
        )
    def test_chuhuo_pingwei_parser_does_not_use_open_number_as_fallback(self):
        text = """
出货平尾
178期:🎪出货平尾🎪开:00准
【每晚20点关注发财】
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "178",
                "出货平尾",
                keyword="出货平尾",
                position="尾部",
                mode="next_line_bracket_tail",
            ),
            "",
        )
    def test_strict_issue_tail_parser_reads_new_topic_styles(self):
        cases = [
            ("""
应接不暇
177期：【平特一尾】【555】开：05准
178期：【平特一尾】【888】开：00准
""", "178", "应接不暇", "8尾 应接不暇"),
            ("""
冲锋陷阵
177期:平特一尾【666尾】开:00准
""", "177", "冲锋陷阵", "6尾 冲锋陷阵"),
            ("""
层出不穷
178期平特一尾《6》开000准
""", "178", "层出不穷", "6尾 层出不穷"),
            ("""
大义灭亲
177期 平8尾
""", "177", "大义灭亲", "8尾 大义灭亲"),
            ("""
仰不愧天
177期平特 6尾
""", "177", "仰不愧天", "6尾 仰不愧天"),
            ("""
夯雀先飞
178期：☆精平一尾☆【555尾】开：00准
""", "178", "夯雀先飞", "5尾 夯雀先飞"),
            ("""
值得你信赖
178期:◆平特一尾【2】开 ?? 准
""", "178", "值得你信赖", "2尾 值得你信赖"),
        ]
        for text, issue, label, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    extract_result_from_text(
                        text,
                        issue,
                        label,
                        keyword=label,
                        position="顶部",
                        mode="strict_issue_tail",
                    ),
                    expected,
                )

    def test_strict_issue_tail_parser_does_not_use_open_number_fallback(self):
        text = """
目标目录
178期:平特一尾 开:00准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "178",
                "目标目录",
                keyword="目标目录",
                position="顶部",
                mode="strict_issue_tail",
            ),
            "",
        )

    def test_site_dedicated_tail_parser_reads_formula_and_nearby_formats(self):
        cases = [
            ("""
目标模块
177期 27-11-24-05-40-33T15 +02=7√
""", "177", "目标模块", "7尾 目标模块"),
            ("""
目标模块
177期 21-37-46-13-40-47T07 +3=6尾√
""", "177", "目标模块", "6尾 目标模块"),
            ("""
目标模块
177期 平8尾
""", "177", "目标模块", "8尾 目标模块"),
            ("""
目标模块
177期:目标模块开:05准
【5】
""", "177", "目标模块", "5尾 目标模块"),
        ]
        for text, issue, label, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    extract_result_from_text(
                        text,
                        issue,
                        label,
                        keyword=label,
                        position="顶部",
                        mode="site_dedicated_tail",
                    ),
                    expected,
                )

    def test_explicit_tail_policy_rejects_rewritten_formula_history(self):
        target = Target(
            "https://example.test/topic/1",
            "尾部",
            "目标",
            "目标模块",
            "site_dedicated_tail",
            value_policy="explicit_tail_only",
        )
        text = (
            "目标模块\n"
            "210期 40-37-15-17-42-48 T49 +9=9尾√\n"
            "211期 平特999尾\n"
        )
        self.assertEqual("", audit_result_from_text(text, "210", target).result)
        self.assertEqual("9尾 目标", audit_result_from_text(text, "211", target).result)

    def test_site_dedicated_tail_parser_rejects_open_number_without_tail_signal(self):
        text = """
目标模块
177期:目标模块开:05准
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "177",
                "目标模块",
                keyword="目标模块",
                position="顶部",
                mode="site_dedicated_tail",
            ),
            "",
        )

    def test_site_dedicated_tail_parser_reads_more_real_delimiters(self):
        cases = [
            ("目标\n183期:平特一尾〓9尾〓开:00准", "9尾 目标"),
            ("目标\n183期:平特★⑴尾┣3┫开:23准", "3尾 目标"),
            ("目标\n183期:╒一尾平特『333╕开：00准", "3尾 目标"),
            ("目标\n183期【平特⒈尾】（4）开00准", "4尾 目标"),
            ("目标\n183期 平特二尾《5.9》开00准", "5尾 9尾 目标"),
            ("目标\n183期：一尾平特 【333】开00准", "3尾 目标"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    extract_result_from_text(text, "183", "目标", keyword="目标", position="顶部", mode="site_dedicated_tail"),
                    expected,
                )

    def test_site_dedicated_tail_parser_reads_repeated_and_angle_bracket_tail_formats(self):
        cases = [
            ("目标\n192期 平特444尾", "4尾 目标"),
            ("目标\n192期:平特一尾««666»»开00准", "6尾 目标"),
            ("目标\n192期:平特一尾«111尾»开00准", "1尾 目标"),
            ("目标\n192期【平特一尾准】〔000尾〕开:00准", "0尾 目标"),
            ("目标\n192期:平特二尾《3.5》开:00准", "3尾 5尾 目标"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    extract_result_from_text(text, "192", "目标", keyword="目标", position="顶部", mode="site_dedicated_tail"),
                    expected,
                )

    def test_site_dedicated_tail_parser_reads_remaining_explicit_formats(self):
        cases = [
            ("目标\n183期平尾[2]投资500万中", "2尾 目标"),
            ("目标\n183期平特(1)尾【333】开0000准", "3尾 目标"),
            ("目标\n183期\n本期买【777尾】100%\n00准", "7尾 目标"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    extract_result_from_text(text, "183", "目标", keyword="目标", position="顶部", mode="site_dedicated_tail"),
                    expected,
                )

    def test_site_dedicated_tail_parser_reads_multiline_issue_layout(self):
        text = """
初雪未央
183期：
初雪未央
平特一尾
【1】
开 ?? 准
"""
        self.assertEqual(
            extract_result_from_text(text, "183", "初雪未央", keyword="初雪未央", position="顶部", mode="site_dedicated_tail"),
            "1尾 初雪未央",
        )

    def test_site_dedicated_tail_parser_reads_reversed_one_tail_pingte(self):
        text = """
朱弦玉磬
183期【①尾平特】【333】开000准
"""
        self.assertEqual(
            extract_result_from_text(text, "183", "朱弦玉磬", keyword="朱弦玉磬", position="顶部", mode="site_dedicated_tail"),
            "3尾 朱弦玉磬",
        )

    def test_site_dedicated_tail_parser_reads_formula_pingwei_next_line(self):
        text = """
一本万利【公式平尾】
170期:💥公式平尾💥开:38准
【8】
"""
        self.assertEqual(
            extract_result_from_text(text, "170", "一本万利", keyword="一本万利【公式平尾】", position="尾部", mode="site_dedicated_tail"),
            "8尾 一本万利",
        )

    def test_site_dedicated_tail_parser_reads_yibenwanli_xuanji_tewei(self):
        text = """
一本万利【玄机特尾】
187期:🐚玄机特尾🐚开:38中
【8】
188期:🐚玄机特尾🐚开:01中
【1】
"""
        self.assertEqual(
            extract_result_from_text(text, "188", "一本万利", keyword="一本万利【玄机特尾】", position="尾部", mode="site_dedicated_tail"),
            "1尾 一本万利",
        )

    def test_site_dedicated_tail_parser_reads_pingwei_digit_suffix(self):
        text = """
一马平川
183期 平尾9
"""
        self.assertEqual(
            extract_result_from_text(text, "183", "一马平川", keyword="一马平川", position="尾部", mode="site_dedicated_tail"),
            "9尾 一马平川",
        )

    def test_site_dedicated_tail_parser_reads_new_approved_site_formats(self):
        cases = [
            (
                """
精华贴183期【平特一尾】已公开
174期15-08-34-13-28-21T41 +01=6尾√
183期 平特6尾
""",
                "183",
                "一点红第二",
                "精华贴",
                "6尾 一点红第二",
            ),
            (
                """
184期🀄大赢家高手🀄【流芳千古🀄平特一尾】重金铲庄
183期:〖流芳千古〗 🥽平特一尾🥽【4】开:羊24准
184期:〖流芳千古〗 🥽平特一尾🥽【9】开:00准
""",
                "183",
                "流芳千古",
                "流芳千古",
                "4尾 流芳千古",
            ),
            (
                """
平特帖183期:【平特一尾】
作者:重气徇命
183期平特一尾（222）开:00准
""",
                "183",
                "重气徇命",
                "重气徇命",
                "2尾 重气徇命",
            ),
            (
                """
[精品资料]183期:【平特一尾】
角角落落 发表于 07月02日 11:08:56
182期 15-23-09-45-24-39T41 +02=1√
183期 平1尾
""",
                "183",
                "角角落落",
                "角角落落",
                "1尾 角角落落",
            ),
        ]
        for text, issue, label, keyword, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    extract_result_from_text(text, issue, label, keyword=keyword, position="尾部", mode="site_dedicated_tail"),
                    expected,
                )

    def test_site_dedicated_tail_parser_rejects_new_site_open_number_only(self):
        text = """
184期🀄大赢家高手🀄【流芳千古🀄平特一尾】重金铲庄
184期:〖流芳千古〗 🥽平特一尾🥽开:09准
"""
        self.assertEqual(
            extract_result_from_text(text, "184", "流芳千古", keyword="流芳千古", position="尾部", mode="site_dedicated_tail"),
            "",
        )

    def test_site_dedicated_tail_parser_reads_one_time_exception_formats(self):
        cases = [
            ("六合宝典『平特一尾』\n183期平特一尾【888】→开:00赢！", "183", "六合宝典", "六合宝典『平特一尾』", "8尾 六合宝典"),
            ("澳门一点红【平特一尾】准准\n183期:〖平特一尾〗【888】开00准", "183", "一点红", "澳门一点红【平特一尾】准准", "8尾 一点红"),
            ("平特帖183期:【平特一尾】\n作者:进退惟谷\n183期:♗平特一尾♗〈555〉开00准", "183", "进退惟谷", "进退惟谷", "5尾 进退惟谷"),
            ("平特帖183期:【平特一尾】\n作者:恩怨了了\n183期平特一尾《888》开0000准", "183", "恩怨了了", "恩怨了了", "8尾 恩怨了了"),
        ]
        for text, issue, label, keyword, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    extract_result_from_text(text, issue, label, keyword=keyword, position="顶部", mode="site_dedicated_tail"),
                    expected,
                )

    def test_site_dedicated_tail_parser_reads_split_issue_tail_row(self):
        text = """
澳彩•平特一尾
184期
【111尾】
开：？00
"""
        self.assertEqual(
            extract_result_from_text(text, "184", "流合", keyword="澳彩•平特一尾", position="顶部", mode="site_dedicated_tail"),
            "1尾 流合",
        )

    def test_caimin_shuji_parser_strips_zero_padding_from_tail(self):
        text = """
207期（平特一尾）14中09（全网独有）
192期 27-30-45-08-01-37T25 +7=2尾√
206期 09-49-37-03-40-35T47 +7=4尾√
207期 平04尾
"""
        self.assertEqual(
            extract_result_from_text(
                text,
                "207",
                "彩民书籍",
                keyword="207期（平特一尾）",
                position="尾部",
                mode="caimin_shuji_tail",
            ),
            "4尾 彩民书籍",
        )

    def test_taxue_uses_precise_gui_lu_yi_wei_module(self):
        text = """
184期:踏雪无痕网【平特一尾】788840a.com
澳门踏雪无痕
『规律一尾』
183期 23-09-46-37-42-03T24 +01=8√
184期 平特8尾
澳门踏雪无痕
『财富一波』
184期：财富一波【蓝波】
"""
        self.assertEqual(
            extract_result_from_text(text, "184", "踏雪", keyword="澳门踏雪无痕\n『规律一尾』", position="顶部", mode="site_dedicated_tail"),
            "8尾 踏雪",
        )
if __name__ == "__main__":
    unittest.main()









