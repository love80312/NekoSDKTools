import csv
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest import mock
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

    def test_cli_export_includes_char_manager_manual_pairs(self):
        self.write_uif(source="夬", target="婭")
        self.write_report(missing="婭伙妮", candidates=("夬", "亜", "鷭", "豸"))
        with (self.report / "mapping_pairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["來源字元", "目標字元", "來源 Unicode", "目標 Unicode"])
            writer.writerow(["亜", "妮", "U+4E9C", "U+59AE"])

        output = self.root / "cli_mapping.txt"
        with redirect_stdout(StringIO()):
            exit_code = helper.main([
                "--no-gui", "--uif", str(self.uif), "--report", str(self.report),
                "--export-two-line", str(output),
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "夬亜鷭\n婭妮伙\n")

    def test_cp932_residual_characters_are_reported_without_reencoding_utf8(self):
        self.write_uif(source="夬", target="婭")
        self.write_report(missing="婭", candidates=("叺", "鷭"))
        translations = self.root / "translations"
        translations.mkdir()
        (translations / "dialog.txt").write_text("婭😀", encoding="utf-8")
        output = self.root / "output"

        result = helper.replace_translation_files(
            translations, output, [helper.MappingPair("夬", "婭", True)], "utf-8"
        )

        self.assertEqual((output / "dialog.txt").read_text(encoding="utf-8"), "夬😀")
        self.assertEqual(result["cp932_unsupported"], {"😀": 1})
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("U+1F600", result["warnings"][0])
        self.assertEqual(result["errors"], [])

    def test_windows_reparse_point_fallback_detects_junction_on_old_python(self):
        fake_stat = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x0400)
        with mock.patch.object(Path, "lstat", return_value=fake_stat):
            self.assertTrue(helper._is_link(Path("legacy-junction")))

    def test_source_root_link_is_rejected(self):
        source = self.root / "linked_translations"
        source.mkdir()
        output = self.root / "output"
        with mock.patch.object(helper, "_is_link", side_effect=lambda path: Path(path) == source):
            with self.assertRaisesRegex(helper.UIFHelperError, "翻譯來源本身"):
                helper.replace_translation_files(
                    source, output, [helper.MappingPair("夬", "婭")], "utf-8"
                )

    def test_require_current_context_rejects_changed_paths_and_clears_state(self):
        context = helper.HelperContext(
            uif_path=self.uif.resolve(), report_root=self.report.resolve(),
            translation_root=None, text_encoding="utf-8", substitution_enabled=True,
            repeat_characters=[], existing_pairs=[], work_pairs=[], target_characters=[],
            already_mapped_targets=[], target_counts={}, candidate_characters=[],
            metadata={}, warnings=[],
        )
        fake = SimpleNamespace(
            context=context,
            new_pairs=[helper.MappingPair("夬", "婭")],
            uif_var=SimpleNamespace(get=lambda: str(self.uif)),
            report_var=SimpleNamespace(get=lambda: str(self.root / "other_report")),
            _refresh=lambda: None,
        )
        with self.assertRaisesRegex(helper.UIFHelperError, "路徑已與目前載入資料不同"):
            helper.UIFCharHelperApp._require_current_context(fake)
        self.assertIsNone(fake.context)
        self.assertEqual(fake.new_pairs, [])

    def test_load_failure_does_not_keep_previous_context(self):
        context = object()
        fake = SimpleNamespace(
            context=context,
            new_pairs=[helper.MappingPair("夬", "婭")],
            uif_var=SimpleNamespace(get=lambda: str(self.uif)),
            report_var=SimpleNamespace(get=lambda: str(self.report)),
            status_var=SimpleNamespace(set=lambda _value: None),
            _refresh=lambda: None,
        )
        with mock.patch.object(helper, "load_context", side_effect=helper.UIFHelperError("bad report")), \
             mock.patch.object(helper.messagebox, "showerror"):
            helper.UIFCharHelperApp._load(fake)
        self.assertIsNone(fake.context)
        self.assertEqual(fake.new_pairs, [])

    def test_json_cp932_check_scans_values_but_not_keys_or_escaped_literals(self):
        self.write_uif(source="夬", target="婭")
        self.write_report(missing="婭", candidates=("叺", "鷭"))
        translations = self.root / "translations"
        translations.mkdir()
        # emoji 放在鍵與值；值使用 JSON escape，不能只掃描輸出檔的原始文字。
        (translations / "dialog.json").write_text(
            r'{"😀": "婭\ud83d\ude00", "message": "婭"}', encoding="utf-8"
        )
        result = helper.replace_translation_files(
            translations, self.root / "output", [helper.MappingPair("夬", "婭", True)], "utf-8"
        )
        self.assertEqual(result["cp932_unsupported"], {"😀": 1})

    def test_cp932_unsupported_counts_are_aggregated_across_files(self):
        translations = self.root / "translations"
        translations.mkdir()
        (translations / "a.txt").write_text("😀😀", encoding="utf-8")
        (translations / "b.txt").write_text("😁😀", encoding="utf-8")
        result = helper.replace_translation_files(
            translations, self.root / "output", [helper.MappingPair("夬", "婭")], "utf-8"
        )
        self.assertEqual(result["cp932_unsupported"], {"😀": 3, "😁": 1})
        self.assertEqual(len(result["warnings"]), 2)

    def test_mapping_snapshot_contains_all_pairs_and_does_not_overwrite(self):
        pairs = [
            helper.MappingPair("夬", "婭", True),   # UIF 既有映射
            helper.MappingPair("亜", "妮", False),  # char_manager 人工映射
            helper.MappingPair("鷭", "伙", False),  # 本次自動配對
        ]

        first = helper.save_mapping_snapshot(self.report, pairs)
        second = helper.save_mapping_snapshot(self.report, pairs)

        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertEqual(first.parent, self.report.resolve())
        self.assertEqual(second.parent, self.report.resolve())
        self.assertNotEqual(first, second)

        expected = "夬亜鷭\n婭妮伙\n"
        self.assertEqual(first.read_text(encoding="utf-8"), expected)
        self.assertEqual(second.read_text(encoding="utf-8"), expected)
        self.assertEqual(len(first.read_text(encoding="utf-8").splitlines()), 2)

    def test_replace_action_saves_same_complete_pairs_before_running_replacement(self):
        translations = self.root / "translations"
        translations.mkdir()
        output = self.root / "replaced"

        pairs = [
            helper.MappingPair("夬", "婭", True),
            helper.MappingPair("亜", "妮", False),
            helper.MappingPair("鷭", "伙", False),
        ]
        context = SimpleNamespace(
            report_root=self.report.resolve(),
            text_encoding="utf-8",
        )
        fake = SimpleNamespace(
            context=context,
            translation_var=SimpleNamespace(get=lambda: str(translations)),
            output_var=SimpleNamespace(get=lambda: str(output)),
            encoding_var=SimpleNamespace(get=lambda: ""),
            status_var=SimpleNamespace(set=lambda _value: None),
            _require_current_context=lambda: context,
            _all_pairs=lambda: pairs,
        )
        result = {
            "files_total": 1,
            "files_changed": 1,
            "replacements": 3,
            "output_root": output,
            "ambiguous_targets": [],
            "cp932_unsupported": {},
            "warnings": [],
            "errors": [],
        }

        with mock.patch.object(
            helper, "replace_translation_files", return_value=result
        ) as replace_mock, mock.patch.object(helper.messagebox, "showinfo"):
            helper.UIFCharHelperApp._replace(fake)

        replace_mock.assert_called_once_with(
            translations.resolve(), output.resolve(), pairs, "utf-8"
        )

        snapshots = list(self.report.glob("uif_mapping_*.txt"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            snapshots[0].read_text(encoding="utf-8"),
            "夬亜鷭\n婭妮伙\n",
        )


if __name__ == "__main__":
    unittest.main()
