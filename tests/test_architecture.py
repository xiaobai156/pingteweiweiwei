import ast
import json
import tempfile
import unittest
from pathlib import Path

from config_fixtures import all_targets, raw_items


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureContractTests(unittest.TestCase):
    def test_command_entrypoints_are_thin(self):
        for name in ("tail_fetcher.py", "multi_issue_fetcher.py"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 100, name)

    def test_batch_entries_choose_one_python_runtime_without_retrying_the_job(self):
        for name in ("运行抓取.bat", "多期抓取.bat"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("where py", text.lower())
            self.assertNotIn("if errorlevel 1 python", text.lower())

    def test_runtime_modules_follow_dependency_direction(self):
        forbidden = {
            "ptw.domain": {"ptw.fetching", "ptw.parsing", "ptw.services", "ptw.storage"},
            "ptw.fetching": {"ptw.parsing", "ptw.services", "ptw.storage"},
            "ptw.parsing": {"ptw.services", "ptw.storage"},
            "ptw.storage": {"ptw.fetching", "ptw.parsing", "ptw.services"},
        }
        for path in (ROOT / "ptw").rglob("*.py"):
            relative = path.relative_to(ROOT).with_suffix("")
            module = ".".join(relative.parts)
            owner = next((prefix for prefix in forbidden if module.startswith(prefix)), "")
            if not owner:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            blocked = {
                imported
                for imported in imports
                for prefix in forbidden[owner]
                if imported == prefix or imported.startswith(prefix + ".")
            }
            self.assertEqual(set(), blocked, f"{module}: {sorted(blocked)}")

    def test_targets_have_unique_stable_identity_and_parser_binding(self):
        targets = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
        required = {"target_id", "parser_id", "rule_version", "value_policy"}
        for index, target in enumerate(targets, start=1):
            self.assertTrue(required.issubset(target), f"target {index}")
            self.assertRegex(target["target_id"], r"^ptw_[a-f0-9]{16}$")
            self.assertEqual(f"site.{target['target_id']}", target["parser_id"])
            self.assertIsInstance(target["rule_version"], int)
            self.assertGreaterEqual(target["rule_version"], 1)
            self.assertIn(target["value_policy"], {"mode_default", "explicit_tail_only"})
        self.assertEqual(len(targets), len({item["target_id"] for item in targets}))
        self.assertEqual(len(targets), len({item["parser_id"] for item in targets}))

    def test_every_target_resolves_through_the_dedicated_registry(self):
        from ptw.config import load_targets
        from ptw.fetching.dynamic_api import record_id_from_url
        from ptw.parsing.registry import registry

        targets = load_targets(ROOT / "targets.json")
        for target in targets:
            strategy = registry.resolve(target)
            self.assertEqual(target.parser_id, strategy.parser_id)
            self.assertEqual(target.mode, strategy.mode)
            self.assertEqual(target.value_policy, strategy.value_policy)
            if "/article/admin/" in target.url or "/article/manager/" in target.url:
                self.assertEqual(record_id_from_url(target.url), target.record_id)

    def test_archived_config_is_disjoint_from_active_config(self):
        active = json.loads((ROOT / "targets.json").read_text(encoding="utf-8-sig"))
        archived = json.loads((ROOT / "archived_targets.json").read_text(encoding="utf-8-sig"))
        active_ids = {item["target_id"] for item in active}
        active_labels = {item["label"] for item in active}
        archived_ids = {item["target_id"] for item in archived}
        archived_labels = {item["label"] for item in archived}

        self.assertEqual(len(active_ids), len(active))
        self.assertEqual(len(archived_ids), len(archived))
        self.assertFalse(active_ids & archived_ids)
        self.assertFalse(active_labels & archived_labels)
        self.assertEqual(191, len(active) + len(archived))
        for site in archived:
            with self.subTest(label=site["label"]):
                self.assertEqual(site["parser_id"], f"site.{site['target_id']}")
                self.assertTrue(site.get("archived_reason"))
                self.assertTrue(site.get("archived_at"))

    def test_unregistered_parser_id_is_rejected(self):
        from ptw.domain.models import Target
        from ptw.parsing.registry import registry

        target = Target(
            "https://example.test/topic/1",
            "顶部",
            "未注册",
            "专属模块",
            "site_dedicated_tail",
            target_id="ptw_abcdefabcdefabcd",
            parser_id="site.ptw_abcdefabcdefabcd",
            rule_version=1,
        )
        with self.assertRaisesRegex(ValueError, "未注册"):
            registry.resolve(target)

    def test_manager_target_requires_all_available_record_boundaries(self):
        from dataclasses import replace

        from ptw.config import load_targets
        from ptw.domain.validation import validate_target

        target = next(item for item in all_targets() if item.mode == "manager_article_tail")
        required = {
            "record_id": "动态详情页缺少record_id",
            "record_author": "Manager文章缺少record_author",
            "record_title": "Manager文章缺少record_title",
            "record_content_field": "Manager文章缺少record_content_field",
        }
        for field, message in required.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, message):
                    validate_target(replace(target, **{field: ""}), "test")

    def test_dynamic_record_id_mismatch_fails_configuration_load(self):
        from ptw.config import load_targets

        manager = next(item for item in raw_items() if item["mode"] == "manager_article_tail")
        manager["record_id"] = "not-the-url-record"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "targets.json"
            path.write_text(json.dumps([manager], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "record_id与URL不一致"):
                load_targets(path)

    def test_admin_target_requires_all_available_record_boundaries(self):
        from dataclasses import replace

        from ptw.domain.models import Target
        from ptw.domain.validation import validate_target

        target = Target(
            "https://example.test/article/admin/admin-record",
            "顶部",
            "管理文章",
            "管理文章专属",
            "site_dedicated_tail",
            record_author="目标作者",
            record_title="目标标题",
            record_category="平特一肖",
            record_direction="top",
            record_content_field="content",
            target_id="ptw_0123456789abcdef",
            parser_id="site.ptw_0123456789abcdef",
            rule_version=1,
            record_id="admin-record",
        )
        required = {
            "record_author": "Admin文章缺少record_author",
            "record_title": "Admin文章缺少record_title",
            "record_category": "Admin文章缺少record_category",
            "record_direction": "Admin文章缺少record_direction",
            "record_content_field": "Admin文章缺少record_content_field",
        }
        for field, message in required.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, message):
                    validate_target(replace(target, **{field: ""}), "test")

    def test_cache_update_one_preserves_unrelated_sites_and_rejects_conflict(self):
        from ptw.domain.models import Target
        from ptw.storage.cache import update_one

        target = Target(
            "https://example.test/a",
            "顶部",
            "甲",
            "甲专属",
            "site_dedicated_tail",
            target_id="ptw_1111111111111111",
            parser_id="site.ptw_1111111111111111",
            rule_version=1,
        )
        cache = {
            "schema": 2,
            "window_size": 10,
            "issues": [211, 210],
            "sites": [
                {
                    "target_id": target.target_id,
                    "name": "甲",
                    "url": target.url,
                    "pick": "top",
                    "keyword": target.keyword,
                    "mode": target.mode,
                    "parser_id": target.parser_id,
                    "rule_version": 1,
                    "values": {"211": "3尾"},
                },
                {
                    "target_id": "ptw_2222222222222222",
                    "name": "乙",
                    "url": "https://example.test/b",
                    "pick": "bottom",
                    "keyword": "乙专属",
                    "mode": "site_dedicated_tail",
                    "parser_id": "site.ptw_2222222222222222",
                    "rule_version": 1,
                    "values": {"211": "8尾", "210": "6尾"},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recent_10_cache.json"
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(update_one(path, target, "210", "4尾"))
            updated = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertEqual({"211": "8尾", "210": "6尾"}, updated["sites"][1]["values"])
            before = path.read_bytes()
            self.assertFalse(update_one(path, target, "210", "9尾"))
            self.assertEqual(before, path.read_bytes())

    def test_partial_cache_preserves_unselected_sites(self):
        from ptw.domain.models import Target
        from ptw.storage.cache import _new_site, update_recent_10_cache_partial

        selected = Target(
            "https://example.test/a",
            "顶部",
            "甲",
            "甲",
            "site_dedicated_tail",
            target_id="ptw_1111111111111111",
            parser_id="site.ptw_1111111111111111",
        )
        unselected = Target(
            "https://example.test/b",
            "底部",
            "乙",
            "乙",
            "site_dedicated_tail",
            target_id="ptw_2222222222222222",
            parser_id="site.ptw_2222222222222222",
        )
        selected_entry = _new_site(selected)
        selected_entry["values"] = {"266": "1尾"}
        unselected_entry = _new_site(unselected)
        unselected_entry["values"] = {"266": "8尾", "265": "7尾"}
        cache = {
            "schema": 2,
            "window_size": 10,
            "issues": [266, 265],
            "sites": [selected_entry, unselected_entry],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "recent_10_cache.json"
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(
                update_recent_10_cache_partial([selected], ["3尾 甲"], "267", path)
            )
            updated = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertEqual({"266": "1尾", "267": "3尾"}, updated["sites"][0]["values"])
            self.assertEqual(unselected_entry, updated["sites"][1])
            self.assertEqual([267, 266, 265], updated["issues"])
            self.assertFalse(path.with_suffix(".json.bak").exists())

    def test_no_backup_pair_write_preserves_existing_files_without_bak(self):
        from ptw.storage.atomic import atomic_write_pair_without_backup

        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "success.txt"
            second = Path(temp_dir) / "failure.txt"
            first.write_text("old success", encoding="utf-8")
            second.write_text("old failure", encoding="utf-8")
            atomic_write_pair_without_backup(first, "new success", second, "new failure")
            self.assertEqual("new success", first.read_text(encoding="utf-8"))
            self.assertEqual("new failure", second.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(temp_dir).glob("*.bak*")))

    def test_multi_issue_service_declares_cache_read_only(self):
        from ptw.services.multi_issue import MultiIssueService

        self.assertTrue(MultiIssueService.cache_read_only)


if __name__ == "__main__":
    unittest.main()
