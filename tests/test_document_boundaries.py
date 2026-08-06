import unittest
import json

from ptw.domain.models import FetchDocument, Target
from ptw.domain.validation import is_publishable
from ptw.fetching.documents import serialize_documents
from ptw.parsing.pipeline import audit_result_from_text


class DocumentBoundaryTests(unittest.TestCase):
    @staticmethod
    def _structured_target(position="顶部"):
        return Target(
            "https://example.test/article/admin/target-record",
            position,
            "目标",
            "专属模块",
            "site_dedicated_tail",
            "目标作者",
            "目标标题",
            "平特一肖",
            "top" if position == "顶部" else "bottom",
            "content",
            record_id="target-record",
        )

    @staticmethod
    def _structured_payload(content):
        return {
            "records": [
                {
                    "id": "target-record",
                    "author": "目标作者",
                    "title": "目标标题",
                    "category": "平特一肖",
                    "direction": "top",
                    "content": content,
                }
            ]
        }

    def test_unrelated_json_does_not_override_static_target_document(self):
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/topic/1",
                    "html",
                    "main",
                    "专属模块\n207期 平特一尾【3】",
                    original_order=0,
                ),
                FetchDocument(
                    "https://example.test/settings.json",
                    "json",
                    "settings",
                    json.dumps({"theme": "dark"}, ensure_ascii=False),
                    parent_document_id="main",
                    original_order=1,
                ),
            ]
        )
        target = Target(
            "https://example.test/topic/1",
            "顶部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
        )
        self.assertEqual("3尾 目标", audit_result_from_text(text, "207", target).result)

    def test_parse_result_keeps_candidate_evidence_and_document_boundary(self):
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/topic/1",
                    "html",
                    "main",
                    "专属模块\n207期 平特一尾【3】",
                    original_order=0,
                )
            ]
        )
        target = Target(
            "https://example.test/topic/1",
            "顶部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
            target_id="ptw_0123456789abcdef",
            parser_id="site.ptw_0123456789abcdef",
            rule_version=3,
        )
        from ptw.parsing.registry import registry

        registry.register(target)
        record = audit_result_from_text(text, "207", target)
        self.assertEqual(1, len(record.candidate_evidence))
        evidence = record.candidate_evidence[0]
        self.assertEqual("3尾", evidence.value)
        self.assertEqual("专属模块", evidence.anchor_text)
        self.assertIn("207期", evidence.raw_issue_line)
        self.assertTrue(evidence.raw_value_lines)
        self.assertEqual("main", evidence.document_id)
        self.assertEqual("block-0", evidence.block_id)
        self.assertEqual("https://example.test/topic/1", evidence.source_url)
        self.assertEqual("site.ptw_0123456789abcdef", evidence.parser_id)
        self.assertEqual(3, evidence.rule_version)
        self.assertGreaterEqual(evidence.page_position, 0)
        self.assertEqual(0, evidence.block_position)

    def test_duplicate_dynamic_record_id_across_documents_is_rejected(self):
        target_id = "6a43d17d57dc857ae1bdf9dd"
        payloads = [
            {
                "id": target_id,
                "authorNickname": "降龙伏虎",
                "title": "平特一尾",
                "html": "207期:〖降龙伏虎〗 🍔平特一尾🍔【3】",
            },
            {
                "id": target_id,
                "authorNickname": "降龙伏虎",
                "title": "平特一尾",
                "html": "207期:〖降龙伏虎〗 🍔平特一尾🍔【8】",
            },
        ]
        text = serialize_documents(
            [
                FetchDocument(
                    f"https://example.test/api/{index}",
                    "json",
                    f"json-{index}",
                    json.dumps(payload, ensure_ascii=False),
                    original_order=index,
                )
                for index, payload in enumerate(payloads)
            ]
        )
        target = Target(
            f"https://example.test/article/manager/{target_id}?url=dyj",
            "尾部",
            "降龙伏虎",
            "〖降龙伏虎〗 🍔平特一尾🍔",
            "manager_article_tail",
            "降龙伏虎",
            "平特一尾",
            record_content_field="html",
        )
        record = audit_result_from_text(text, "207", target)
        self.assertEqual("", record.result)
        self.assertNotIn("KEYWORD_NOT_FOUND", record.risk_flags)
        self.assertIn("JSON_RECORD_MULTIPLE_MATCHES", record.risk_flags)

    def test_manager_record_rejects_two_explicit_values_in_same_issue_window(self):
        target_id = "6a43d17d57dc857ae1bdf9dd"
        payload = {
            "id": target_id,
            "authorNickname": "降龙伏虎",
            "title": "平特一尾",
            "html": (
                "211期:〖降龙伏虎〗 🍔平特一尾🍔【3】 "
                "〖降龙伏虎〗 🍔平特一尾🍔【8】"
            ),
        }
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/api/record",
                    "json",
                    "manager-json",
                    json.dumps(payload, ensure_ascii=False),
                    record_id=target_id,
                )
            ]
        )
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
        record = audit_result_from_text(text, "211", target)
        self.assertEqual("", record.result)
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_manager_candidate_evidence_keeps_record_identity(self):
        target_id = "6a43d17d57dc857ae1bdf9dd"
        payload = {
            "id": target_id,
            "authorNickname": "降龙伏虎",
            "title": "平特一尾",
            "html": "211期:〖降龙伏虎〗 🍔平特一尾🍔【3】",
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
        record = audit_result_from_text(
            json.dumps(payload, ensure_ascii=False),
            "211",
            target,
        )
        self.assertEqual(1, len(record.candidate_evidence))
        evidence = record.candidate_evidence[0]
        self.assertEqual(target_id, evidence.record_id)
        self.assertEqual("$", evidence.record_path)
        self.assertEqual("3尾", evidence.value)

    def test_generic_structured_record_requires_dedicated_keyword_boundary(self):
        payload = self._structured_payload("211期 其他模块 平特一尾【9】")
        record = audit_result_from_text(
            json.dumps(payload, ensure_ascii=False),
            "211",
            self._structured_target(),
        )
        self.assertEqual("", record.result)
        self.assertIn("JSON_TARGET_KEYWORD_MISSING", record.risk_flags)

    def test_generic_structured_record_rejects_conflicting_values_in_one_module(self):
        payload = self._structured_payload(
            "专属模块\n211期 平特一尾【3】\n211期 平特一尾【8】"
        )
        record = audit_result_from_text(
            json.dumps(payload, ensure_ascii=False),
            "211",
            self._structured_target(),
        )
        self.assertEqual("", record.result)
        self.assertIn("MULTIPLE_TAIL_CANDIDATES", record.risk_flags)

    def test_generic_structured_record_accepts_duplicate_same_value(self):
        payload = self._structured_payload(
            "专属模块\n211期 平特一尾【3】\n211期 平特一尾【3】"
        )
        record = audit_result_from_text(
            json.dumps(payload, ensure_ascii=False),
            "211",
            self._structured_target(),
        )
        self.assertEqual("3尾 目标", record.result)

    def test_generic_structured_record_applies_three_candidate_direction_window(self):
        payload = self._structured_payload(
            "专属模块\n"
            "205期 平特一尾【5】\n"
            "206期 平特一尾【6】\n"
            "207期 平特一尾【7】\n"
            "208期 平特一尾【8】\n"
            "209期 平特一尾【9】\n"
            "210期 平特一尾【0】\n"
            "211期 平特一尾【1】"
        )
        payload["records"][0]["direction"] = "bottom"
        target = self._structured_target("尾部")
        old = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "205", target)
        current = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "209", target)
        self.assertEqual("", old.result)
        self.assertIn("DIRECTION_WINDOW_EXCLUDED", old.risk_flags)
        self.assertEqual("9尾 目标", current.result)

    def test_manager_record_applies_three_candidate_direction_window(self):
        target_id = "6a43d17d57dc857ae1bdf9dd"
        rows = " ".join(
            f"{issue}期:〖降龙伏虎〗 🍔平特一尾🍔【{issue % 10}】"
            for issue in range(205, 212)
        )
        payload = {
            "id": target_id,
            "authorNickname": "降龙伏虎",
            "title": "平特一尾",
            "html": rows,
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
        old = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "205", target)
        current = audit_result_from_text(json.dumps(payload, ensure_ascii=False), "209", target)
        self.assertEqual("", old.result)
        self.assertIn("DIRECTION_WINDOW_EXCLUDED", old.risk_flags)
        self.assertEqual("9尾 降龙伏虎", current.result)

    def test_structured_candidate_evidence_keeps_record_identity_and_path(self):
        payload = self._structured_payload("专属模块\n211期 平特一尾【3】")
        record = audit_result_from_text(
            json.dumps(payload, ensure_ascii=False),
            "211",
            self._structured_target(),
        )
        self.assertEqual(1, len(record.candidate_evidence))
        evidence = record.candidate_evidence[0]
        self.assertEqual("target-record", evidence.record_id)
        self.assertTrue(evidence.record_path)
        self.assertEqual("3尾", evidence.value)

    def test_title_and_value_from_different_documents_cannot_be_joined(self):
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/topic/1",
                    "html",
                    "main",
                    "专属模块\n207期 本期无尾数",
                    original_order=0,
                ),
                FetchDocument(
                    "https://example.test/frame/1",
                    "iframe",
                    "frame-1",
                    "207期 平特一尾【9】",
                    parent_document_id="main",
                    original_order=1,
                ),
            ]
        )
        target = Target(
            "https://example.test/topic/1",
            "顶部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
        )
        record = audit_result_from_text(text, "207", target)
        self.assertEqual("", record.result)

    def test_complete_candidates_from_two_documents_follow_tail_direction(self):
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/topic/1",
                    "html",
                    "main",
                    "专属模块\n207期 平特一尾【3】",
                    original_order=0,
                ),
                FetchDocument(
                    "https://example.test/frame/1",
                    "iframe",
                    "frame-1",
                    "专属模块\n207期 平特一尾【8】",
                    parent_document_id="main",
                    original_order=1,
                ),
            ]
        )
        target = Target(
            "https://example.test/topic/1",
            "尾部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
        )
        record = audit_result_from_text(text, "207", target)
        self.assertEqual("", record.result)
        self.assertEqual("frame-1", record.document_id)
        self.assertIn("CROSS_DOCUMENT_CONFLICT", record.risk_flags)
        self.assertFalse(is_publishable(record))

    def test_structured_source_evidence_points_to_record_document(self):
        payload = self._structured_payload("专属模块\n211期 平特一尾【3】")
        text = serialize_documents(
            [
                FetchDocument(
                    "https://example.test/settings.json",
                    "json",
                    "settings",
                    json.dumps({"theme": "dark"}, ensure_ascii=False),
                    original_order=0,
                ),
                FetchDocument(
                    "https://example.test/api/target",
                    "json",
                    "target-api",
                    json.dumps(payload, ensure_ascii=False),
                    original_order=1,
                ),
            ]
        )
        record = audit_result_from_text(text, "211", self._structured_target("顶部"))
        self.assertEqual("3尾 目标", record.result)
        self.assertEqual("target-api", record.document_id)
        self.assertEqual(
            "https://example.test/api/target",
            record.candidate_evidence[0].source_url,
        )

    def test_top_and_tail_use_document_order_after_each_document_is_validated(self):
        documents = [
            FetchDocument(
                "https://example.test/topic/1",
                "html",
                "main",
                "专属模块\n207期 平特一尾【3】",
                original_order=0,
            ),
            FetchDocument(
                "https://example.test/frame/1",
                "iframe",
                "frame-1",
                "专属模块\n207期 平特一尾【3】",
                parent_document_id="main",
                original_order=1,
            ),
        ]
        text = serialize_documents(documents)
        top = Target(
            "https://example.test/topic/1",
            "顶部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
        )
        tail = Target(
            "https://example.test/topic/1",
            "尾部",
            "目标",
            "专属模块",
            "site_dedicated_tail",
        )
        for target in (top, tail):
            with self.subTest(position=target.position):
                record = audit_result_from_text(text, "207", target)
                self.assertEqual("", record.result)
                self.assertIn("MULTIPLE_DOCUMENT_CANDIDATES", record.risk_flags)
                self.assertFalse(is_publishable(record))


if __name__ == "__main__":
    unittest.main()
