import csv
import json
import tempfile
import unittest
from pathlib import Path

import uif_char_helper as helper


class UIFCharHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.uif = self.game / "uif_config.json"
        self.report = self.root / "translation_char_report"
        self.report.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_uif(self, source="夬叺", target="婭伙", enabled=True):
        self.uif.write_text(json.dumps({
            "character_substitution": {
                "enable": enabled,
                "source_characters": source,
                "target_characters": target,
                "repeat": ["楽", "搾"],
            },
            "other": {"untouched": "測試"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_report(self, missing="婭伙妮", candidates=("夬", "叺", "鷭", "豸"), complete=True):
        metadata = {
            "translation_root": str(self.root / "translations"),
            "translation_text_encoding": "utf-8",
            "translation_scan_complete": complete,
            "source_scan_performed": False,
            "source_scan_complete": None,
        }
        (self.report / "scan_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        (self.report / "unmapped_characters.txt").write_text(missing, encoding="utf-8")
        with (self.report / "character_report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["字元", "出現次數", "狀態"])
            for index, ch in enumerate(dict.fromkeys(missing), start=1):
                writer.writerow([ch, index * 10, "待替換"])
        with (self.report / "mapping_pairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["來源字元", "目標字元", "來源 Unicode", "目標 Unicode"])
        with (self.report / "safe_candidate_glyphs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["來源候選字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組"])
            for ch in candidates:
                writer.writerow([ch, f"U+{ord(ch):04X}", "test", "漢字", "81 40"])

    def test_read_existing_ui_mapping_and_keep_duplicate_targets(self):
        self.write_uif(source="夬叺鷭", target="婭啊啊")
        enabled, pairs, repeat = helper.read_uif(self.uif)
        self.assertTrue(enabled)
        self.assertEqual([(p.source, p.target) for p in pairs], [("夬", "婭"), ("叺", "啊"), ("鷭", "啊")])
        self.assertEqual(repeat, ["楽", "搾"])

    def test_auto_pair_excludes_existing_ui_source_and_target(self):
        self.write_uif(source="夬叺", target="婭伙")
        self.write_report(missing="婭伙妮", candidates=("夬", "叺", "鷭", "豸"))
        context = helper.load_context(self.uif, self.report)
        self.assertEqual(context.target_characters, ["妮"])
        self.assertEqual(context.candidate_characters, ["鷭", "豸"])
        self.assertEqual(context.already_mapped_targets, ["婭", "伙"])
        self.assertEqual(context.target_counts["婭"], 10)
        new = helper.auto_pair(context)
        self.assertEqual([(p.source, p.target) for p in new], [("鷭", "妮")])


    def test_char_manager_manual_pairs_are_preserved_and_removed_from_auto_candidates(self):
        self.write_uif(source="夬", target="婭")
        self.write_report(missing="婭伙妮", candidates=("夬", "叺", "鷭", "豸", "亜"))
        with (self.report / "mapping_pairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["來源字元", "目標字元", "來源 Unicode", "目標 Unicode"])
            writer.writerow(["亜", "妮", "U+4E9C", "U+59AE"])

        context = helper.load_context(self.uif, self.report)
        self.assertEqual([(p.source, p.target) for p in context.work_pairs], [("亜", "妮")])
        self.assertEqual(context.target_characters, ["伙"])
        self.assertNotIn("亜", context.candidate_characters)
        self.assertEqual(
            [(p.source, p.target) for p in helper.combine_pairs(context.existing_pairs, context.work_pairs)],
            [("夬", "婭"), ("亜", "妮")],
        )

    def test_existing_duplicate_target_is_allowed_in_two_line_export(self):
        self.write_uif(source="夬叺", target="啊啊")
        _enabled, old, _repeat = helper.read_uif(self.uif)
        new = [helper.MappingPair("鷭", "妮")]
        lines = helper.make_two_line_text(helper.combine_pairs(old, new)).splitlines()
        self.assertEqual(lines, ["夬叺鷭", "啊啊妮"])

    def test_incomplete_scan_is_rejected(self):
        self.write_uif()
        self.write_report(complete=False)
        with self.assertRaises(helper.UIFHelperError):
            helper.load_context(self.uif, self.report)

    def test_json_replaces_values_but_not_keys_and_preserves_layout(self):
        source = '{\n  "婭": "翻譯婭",\n  "items": ["婭", {"message": "伙"}]\n}\n'
        converted, count = helper._transform_json_preserving_layout(source, {"婭": "夬", "伙": "叺"})
        self.assertEqual(count, 3)
        self.assertIn('"婭": "翻譯夬"', converted)
        self.assertIn('"message": "叺"', converted)
        self.assertTrue(converted.startswith('{\n  '))

    def test_duplicate_reverse_target_uses_first_source_and_warns(self):
        pairs = [helper.MappingPair("夬", "啊"), helper.MappingPair("叺", "啊")]
        reverse, ambiguous = helper.build_reverse_mapping(pairs)
        self.assertEqual(reverse["啊"], "夬")
        self.assertEqual(ambiguous, ["啊"])

    def test_replace_to_new_directory_keeps_source_unchanged(self):
        self.write_uif(source="夬", target="婭")
        self.write_report(missing="婭", candidates=("叺", "鷭"))
        translations = self.root / "translations"
        translations.mkdir()
        original = translations / "dialog.json"
        original.write_text('{"message": "婭婭"}\n', encoding="utf-8")
        pairs = [helper.MappingPair("夬", "婭", True)]
        output = self.root / "translated_replaced"
        result = helper.replace_translation_files(translations, output, pairs, "utf-8")
        self.assertEqual(result["replacements"], 2)
        self.assertEqual(json.loads(original.read_text(encoding="utf-8"))["message"], "婭婭")
        self.assertEqual(json.loads((output / "dialog.json").read_text(encoding="utf-8"))["message"], "夬夬")

    def test_output_inside_source_is_rejected(self):
        source = self.root / "translations"
        source.mkdir()
        output = source / "out"
        with self.assertRaises(helper.UIFHelperError):
            helper.replace_translation_files(source, output, [helper.MappingPair("夬", "婭")])


if __name__ == "__main__":
    unittest.main()
