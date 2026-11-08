import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scr


def make_text_record(name: bytes, message: bytes, main: bytes) -> scr.Record:
    args = [b"\0"] * 32
    args[0] = name
    args[1] = message
    return scr.Record(
        header=(0, 0, 0, scr.TEXT_OPCODE),
        nums32=(0,) * 32,
        extra=0,
        nums16=(0,) * 16,
        main=main,
        args=args,
    )


def create_script(path: Path, message: str = "Original") -> None:
    record = make_text_record(
        b"\0",
        message.encode("cp932") + b"\0",
        b"\0",
    )
    scr.write_script(scr.Script([record]), path)


class MixedEncodingImportTests(unittest.TestCase):
    def test_changed_gbk_dialogue_preserves_main_bytes_not_decodable_as_cp932(self):
        main = "龘".encode("gbk") + b"\0"
        self.assertRaises(UnicodeDecodeError, main[:-1].decode, "cp932")
        record = make_text_record(
            b"\0", "舊文字".encode("gbk") + b"\0", main
        )
        script = scr.Script([record])

        changed = scr.import_items(
            script,
            [{"message": "測試完成"}],
            "cp932",
            "auto",
            "fixture.json",
        )

        self.assertEqual(changed, 1)
        self.assertEqual(record.args[1], "測試完成".encode("gbk") + b"\0")
        self.assertEqual(record.main, main)

    def test_unchanged_gbk_script_round_trip_is_byte_identical(self):
        record = make_text_record(
            b"\0",
            "原始對話".encode("gbk") + b"\0",
            "龘".encode("gbk") + b"\0",
        )
        script = scr.Script([record])

        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "original.bin"
            rebuilt = Path(temp) / "rebuilt.bin"
            scr.write_script(script, original)
            original_bytes = original.read_bytes()

            parsed = scr.read_script(original)
            items = scr.export_items(parsed, "cp932", "auto", str(original))
            self.assertEqual(items, [{"message": "原始對話"}])
            changed = scr.import_items(
                parsed, items, "cp932", "auto", str(original)
            )
            self.assertEqual(changed, 0)
            scr.write_script(parsed, rebuilt)

            self.assertEqual(rebuilt.read_bytes(), original_bytes)

    def test_cp932_mirrored_main_still_updates_translation(self):
        main = "[テキスト表示] Alice\nHello".encode("cp932") + b"\0"
        record = make_text_record(
            "Alice".encode("cp932") + b"\0",
            b"Hello\0",
            main,
        )
        script = scr.Script([record])

        changed = scr.import_items(
            script,
            [{"name": "Bob", "message": "Translated"}],
            "cp932",
            "auto",
            "fixture.json",
        )

        self.assertEqual(changed, 1)
        self.assertEqual(record.args[0], b"Bob\0")
        self.assertEqual(record.args[1], b"Translated\0")
        self.assertEqual(
            scr.decode_field(record.main, "cp932", "main"),
            "[テキスト表示] Bob\nTranslated",
        )

    def test_load_translation_reports_non_object_item_index(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid_item.json"
            path.write_text(
                '[{"message": "第一句"}, null, {"message": "第三句"}]',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                scr.ScriptError,
                r"JSON item 1 .*must be an object; got NoneType",
            ):
                scr.load_translation(path)

    def test_read_script_rejects_non_script_before_reading_full_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large_resource.bin"
            path.write_bytes(b"NOT_AN_ADVSCRIPT2_FILE" + b"X" * (4 * 1024 * 1024))

            # 非腳本資源只應讀取 MAGIC 長度的檔頭，不應呼叫整檔 read_bytes()。
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("read_bytes() must not be used"),
            ):
                with self.assertRaisesRegex(
                    scr.ScriptError, "invalid ADVSCRIPT2 signature"
                ):
                    scr.read_script(path)

    def test_windows_reparse_point_fallback_detects_legacy_junction(self):
        fake_stat = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0x0400,
        )
        with mock.patch.object(Path, "lstat", return_value=fake_stat):
            self.assertTrue(scr.is_link_or_junction(Path("legacy-junction")))

    def test_iter_files_rejects_symbolic_link_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            link = root / "linked_root"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境不允許建立目錄符號連結：{exc}")

            with self.assertRaisesRegex(
                scr.ScriptError, "symbolic links and junctions are not supported"
            ):
                scr.iter_files(link)

    def test_iter_files_rejects_nested_file_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            outside_file = root / "outside.bin"
            outside_file.write_bytes(b"resource")
            outside_dir = root / "outside_dir"
            outside_dir.mkdir()

            file_link = source / "linked_file.bin"
            dir_link = source / "linked_dir"
            try:
                file_link.symlink_to(outside_file)
                dir_link.symlink_to(outside_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境不允許建立符號連結：{exc}")

            with self.assertRaisesRegex(
                scr.ScriptError, "symbolic links and junctions are not supported"
            ):
                scr.iter_files(source)

    def test_folder_export_skips_large_non_script_without_reading_entire_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            output = root / "json"
            original.mkdir()
            create_script(original / "scene.txt", "Export me")
            resource = original / "large_video.bin"
            resource.write_bytes(b"VIDEO" + b"R" * (2 * 1024 * 1024))

            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("read_bytes() must not be used"),
            ):
                scr.export_path(original, output, "cp932", "cp932")

            exported = json.loads(
                (output / "scene.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exported, [{"message": "Export me"}])
            self.assertFalse((output / "large_video.json").exists())

    def test_folder_import_copies_large_resource_without_parsing_it_as_script(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            translations = root / "json"
            output = root / "output"
            original.mkdir()
            translations.mkdir()

            create_script(original / "scene.txt", "Original")
            resource = original / "large_video.bin"
            resource_data = b"VIDEO" + b"R" * (2 * 1024 * 1024)
            resource.write_bytes(resource_data)
            (translations / "scene.json").write_text(
                json.dumps([{"message": "Translated"}], ensure_ascii=False),
                encoding="utf-8",
            )

            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("read_bytes() must not be used"),
            ):
                scr.import_path(
                    original, translations, output, "cp932", "cp932"
                )

            self.assertEqual((output / "large_video.bin").read_bytes(), resource_data)
            imported = scr.read_script(output / "scene.txt")
            self.assertEqual(imported.records[0].args[1], b"Translated\0")

    def test_folder_import_reports_all_json_failures_without_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            translations = root / "json"
            output = root / "output"
            original.mkdir()
            translations.mkdir()
            output.mkdir()

            # 保留既有輸出資料，驗證失敗時不可動到它。
            sentinel = output / "keep.txt"
            sentinel.write_text("KEEP", encoding="utf-8")

            create_script(original / "a_valid.txt", "A")
            create_script(original / "b_schema_error.txt", "B")
            create_script(original / "c_invalid_json.txt", "C")
            create_script(original / "d_missing_json.txt", "D")

            (translations / "a_valid.json").write_text(
                json.dumps([{"message": "Translated A"}], ensure_ascii=False),
                encoding="utf-8",
            )
            (translations / "b_schema_error.json").write_text(
                json.dumps([{"name": "missing message"}], ensure_ascii=False),
                encoding="utf-8",
            )
            (translations / "c_invalid_json.json").write_text(
                '[{"message": "unfinished"',
                encoding="utf-8",
            )
            # d_missing_json.json 故意不建立。

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(scr.ScriptError):
                    scr.import_path(
                        original,
                        translations,
                        output,
                        "cp932",
                        "cp932",
                    )

            log = stdout.getvalue() + "\n" + stderr.getvalue()

            # 第一個有效 JSON 已通過驗證，後續失敗檔仍須繼續檢查。
            self.assertIn("a_valid.json", log)
            self.assertIn("驗證成功", log)

            # 欄位結構錯誤必須包含檔案、項目索引及缺少的欄位。
            self.assertIn("b_schema_error.json", log)
            self.assertIn("JSON item 0", log)
            self.assertIn("'message'", log)

            # 語法錯誤必須列出對應 JSON 及具體原因。
            self.assertIn("c_invalid_json.json", log)
            self.assertIn("invalid JSON in", log)

            # 缺少 JSON 的項目也要列出。
            self.assertIn("d_missing_json.txt", log)
            self.assertIn("d_missing_json.json", log)

            # 批次驗證失敗，不得提交任何部分匯入結果。
            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP")
            self.assertFalse((output / "a_valid.txt").exists())
            self.assertFalse((output / "b_schema_error.txt").exists())
            self.assertFalse((output / "c_invalid_json.txt").exists())

    def test_folder_import_commits_only_after_every_json_validates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            translations = root / "json"
            output = root / "output"
            original.mkdir()
            translations.mkdir()

            create_script(original / "a.txt", "Original A")
            create_script(original / "b.txt", "Original B")

            (translations / "a.json").write_text(
                json.dumps([{"message": "Translated A"}], ensure_ascii=False),
                encoding="utf-8",
            )
            (translations / "b.json").write_text(
                json.dumps([{"message": "Translated B"}], ensure_ascii=False),
                encoding="utf-8",
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                scr.import_path(
                    original,
                    translations,
                    output,
                    "cp932",
                    "cp932",
                )

            self.assertTrue((output / "a.txt").is_file())
            self.assertTrue((output / "b.txt").is_file())

            imported_a = scr.read_script(output / "a.txt")
            imported_b = scr.read_script(output / "b.txt")
            self.assertEqual(
                imported_a.records[0].args[1],
                b"Translated A\0",
            )
            self.assertEqual(
                imported_b.records[0].args[1],
                b"Translated B\0",
            )
            self.assertIn("[匯入完成]", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
