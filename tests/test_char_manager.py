import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            translation_root=Path("translations"),
            report_root=Path("reports"),
            translation_text_encoding="utf-8-sig",
            stats={"😀": cm.CharacterStat("😀", 1, {"a.json"})},
            glyphs=[],
            scanned_translation_files=1,
            translation_scan_complete=True,
            errors=[],
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
                json.dumps({"message": "😀"}, ensure_ascii=False),
                encoding="utf-8",
            )
            settings = root / "char_manager.json"
            settings.write_text(json.dumps({
                "candidate_filters": {
                    "common_characters": True,
                    "zh_punctuation": False,
                    "ja_punctuation": False,
                    "common_symbols": False,
                }
            }), encoding="utf-8")
            report = root / "report"
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(
                    translations,
                    report_root=report,
                    settings_path=settings,
                )
            self.assertIn("😀", result.stats)
            self.assertNotIn("亜", {item.character for item in result.safe_candidates})
            self.assertTrue(result.translation_scan_complete)

    def test_partial_translation_scan_disables_all_safe_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "ok.json").write_text(
                json.dumps({"message": "😀"}, ensure_ascii=False), encoding="utf-8"
            )
            (translations / "broken.json").write_text("{bad json", encoding="utf-8")
            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(
                    translations,
                    report_root=root / "report",
                    settings_path=root / "missing_settings.json",
                )
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])

    def test_no_external_mapping_import_or_apply_api(self):
        self.assertFalse(hasattr(cm, "load_uif_mapping"))
        self.assertFalse(hasattr(cm, "write_uif_mapping"))
        self.assertFalse(hasattr(cm, "apply_mapping_csv"))
        parser = cm.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--mapping", "unused.csv"])


if __name__ == "__main__":
    unittest.main()
