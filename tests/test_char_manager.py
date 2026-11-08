import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import char_manager as cm


class CharacterManagerTests(unittest.TestCase):
    def test_two_line_export_has_only_sources_and_targets(self):
        value = cm.make_two_line_mapping({"亜": "😀", "唖": "🫠"})
        self.assertEqual(value, "亜唖\n😀🫠\n")
        self.assertEqual(value.count("\n"), 2)

    def test_duplicate_targets_are_rejected(self):
        with self.assertRaises(cm.CharacterManagerError):
            cm.make_two_line_mapping({"亜": "😀", "唖": "😀"})

    def test_valid_work_mapping_can_be_exported_after_targets_are_marked_mapped(self):
        result = cm.ScanResult(
            translation_root=Path("translations"), report_root=Path("reports"),
            translation_text_encoding="utf-8-sig",
            stats={"😀": cm.CharacterStat("😀", 1, {"a.json"})}, glyphs=[],
            scanned_translation_files=1, translation_scan_complete=True, errors=[],
        )
        result.mapping = {"亜": "😀"}
        result.exclusion_config_sha256 = cm._file_sha256(cm.CANDIDATE_EXCLUSION_CONFIG_PATH)
        result.common_characters_sha256 = cm._file_sha256(cm.COMMON_CHARACTERS_PATH)
        cm.validate_work_mapping(result, result.mapping)

    def test_custom_common_characters_are_not_a_fixed_length_moe_list(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "常用字.txt"
            path.write_text("亜唖亜\n", encoding="utf-8")
            self.assertEqual(cm.load_common_characters(path), {"亜", "唖"})

    def test_common_list_only_filters_source_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "strings.json").write_text(
                json.dumps({"message": "😀"}, ensure_ascii=False), encoding="utf-8"
            )
            settings = root / "char_manager.json"
            settings.write_text(json.dumps({"candidate_filters": {
                "common_characters": True, "zh_punctuation": False,
                "ja_punctuation": False, "common_symbols": False,
            }}), encoding="utf-8")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(translations, report_root=root / "report", settings_path=settings)
            self.assertIn("😀", result.stats)
            self.assertNotIn("亜", {item.character for item in result.safe_candidates})
            self.assertTrue(result.translation_scan_complete)

    def test_partial_translation_scan_disables_all_safe_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "ok.json").write_text(json.dumps({"message": "😀"}, ensure_ascii=False), encoding="utf-8")
            (translations / "broken.json").write_text("{bad json", encoding="utf-8")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(translations, report_root=root / "report", settings_path=root / "missing_settings.json")
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])

    def test_candidate_filter_change_is_saved_to_draft_without_losing_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "report"
            report.mkdir()
            translation = root / "translations"
            translation.mkdir()
            draft_path = report / "mapping_draft.json"
            old_draft = {
                "version": 3, "translation_root": str(translation), "report_root": str(report),
                "translation_text_encoding": "utf-8-sig", "source_root": None,
                "source_command_encoding": "cp932", "source_dialogue_encoding": "auto",
                "candidate_filters": {"common_characters": True, "zh_punctuation": True,
                                      "ja_punctuation": True, "common_symbols": True},
                "mapping": [{"source": "亜", "target": "😀"}],
            }
            draft_path.write_text(json.dumps(old_draft, ensure_ascii=False), encoding="utf-8")
            new_filters = {"common_characters": False, "zh_punctuation": True,
                           "ja_punctuation": False, "common_symbols": True}
            result = cm.ScanResult(
                translation_root=translation, report_root=report, translation_text_encoding="utf-8-sig",
                stats={"😀": cm.CharacterStat("😀", 1, {"a.json"})}, glyphs=[],
                scanned_translation_files=1, translation_scan_complete=True, errors=[],
                candidate_filters=dict(old_draft["candidate_filters"]),
            )
            fake = SimpleNamespace(
                result=result, mapping={"亜": "😀"}, encoding_var=SimpleNamespace(get=lambda: "utf-8-sig"),
                last_work_path=root / "char_manager_last_work.json",
                _current_candidate_filters=lambda: dict(new_filters), _refresh_all=Mock(),
                _save_settings_from_ui=Mock(), _refresh_resume_button=Mock(),
            )
            fake._draft_payload = MethodType(cm.CharacterManagerFrame._draft_payload, fake)
            fake._save_mapping_draft = MethodType(cm.CharacterManagerFrame._save_mapping_draft, fake)
            cm.CharacterManagerFrame._on_candidate_filter_changed(fake)
            saved = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["candidate_filters"], new_filters)
            self.assertEqual(saved["mapping"], old_draft["mapping"])
            self.assertEqual(json.loads(fake.last_work_path.read_text(encoding="utf-8"))["draft_path"], str(draft_path))
            backup = draft_path.with_suffix(draft_path.suffix + ".bak")
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), old_draft)

    def test_report_unmapped_matches_excluded_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "report"
            result = cm.ScanResult(
                translation_root=root, report_root=report, translation_text_encoding="utf-8",
                stats={"😀": cm.CharacterStat("😀", 3, {"a.json"}), "🫠": cm.CharacterStat("🫠", 2, {"a.json"})},
                glyphs=[], scanned_translation_files=1, translation_scan_complete=True, errors=[],
                excluded_targets={"🫠"},
            )
            cm.write_reports(result)
            self.assertEqual((report / "unmapped_characters.txt").read_text(encoding="utf-8"), "😀")
            metadata = json.loads((report / "scan_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["excluded_targets"], ["🫠"])


    def test_supported_file_symlink_marks_translation_scan_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "valid.json").write_text(json.dumps({"message": "😀"}, ensure_ascii=False), encoding="utf-8")
            external = root / "outside.json"
            external.write_text(json.dumps({"message": "🫠"}, ensure_ascii=False), encoding="utf-8")
            link = translations / "linked.json"
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境不允許建立符號連結：{exc}")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(translations, report_root=root / "report", settings_path=root / "missing_settings.json")
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(link) in error for error in result.errors))
            self.assertIn("😀", result.stats)
            self.assertNotIn("🫠", result.stats)

    def test_symlink_only_scan_returns_incomplete_result_instead_of_generic_no_files_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            external = root / "outside.json"
            external.write_text(json.dumps({"message": "🫠"}, ensure_ascii=False), encoding="utf-8")
            link = translations / "linked.json"
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境不允許建立符號連結：{exc}")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(translations, report_root=root / "report", settings_path=root / "missing_settings.json")
            self.assertEqual(result.scanned_translation_files, 0)
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(link) in error for error in result.errors))

    def test_symlink_directory_marks_translation_scan_incomplete_without_following_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "valid.json").write_text(json.dumps({"message": "😀"}, ensure_ascii=False), encoding="utf-8")
            external_dir = root / "outside"
            external_dir.mkdir()
            (external_dir / "hidden.json").write_text(json.dumps({"message": "🫠"}, ensure_ascii=False), encoding="utf-8")
            link_dir = translations / "linked_dir"
            try:
                link_dir.symlink_to(external_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境不允許建立目錄符號連結：{exc}")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(translations, report_root=root / "report", settings_path=root / "missing_settings.json")
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(link_dir) in error for error in result.errors))
            self.assertIn("😀", result.stats)
            self.assertNotIn("🫠", result.stats)

    def test_no_external_mapping_import_or_apply_api(self):
        self.assertFalse(hasattr(cm, "load_uif_mapping"))
        self.assertFalse(hasattr(cm, "write_uif_mapping"))
        self.assertFalse(hasattr(cm, "apply_mapping_csv"))
        parser = cm.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--mapping", "unused.csv"])


if __name__ == "__main__":
    unittest.main()
