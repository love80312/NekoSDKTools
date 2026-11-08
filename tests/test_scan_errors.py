import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import char_manager as cm


class ScanTraversalErrorTests(unittest.TestCase):
    def test_translation_walk_permission_error_marks_scan_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "valid.json").write_text(
                json.dumps({"message": "😀"}, ensure_ascii=False),
                encoding="utf-8",
            )
            denied = translations / "locked"

            def fake_walk(path, topdown=True, onerror=None, followlinks=False):
                self.assertFalse(followlinks)
                if onerror is not None:
                    onerror(PermissionError(errno.EACCES, "access denied", str(denied)))
                yield str(path), [], ["valid.json"]

            with patch.object(cm.os, "walk", side_effect=fake_walk), \
                 patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(
                    translations,
                    report_root=root / "report",
                    settings_path=root / "missing_settings.json",
                )

            self.assertEqual(result.scanned_translation_files, 1)
            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(denied) in item for item in result.errors))
            self.assertIn("😀", result.stats)

    def test_source_walk_permission_error_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            denied = root / "locked"

            def fake_walk(path, topdown=True, onerror=None, followlinks=False):
                self.assertFalse(followlinks)
                if onerror is not None:
                    onerror(PermissionError(errno.EACCES, "access denied", str(denied)))
                yield str(path), [], []

            with patch.object(cm.os, "walk", side_effect=fake_walk):
                _stats, scanned, errors = cm.scan_source_root(root)

            self.assertEqual(scanned, 0)
            self.assertTrue(any(str(denied) in item for item in errors))

    def test_legacy_windows_reparse_point_fallback_detects_junction(self):
        class LegacyPath:
            def is_symlink(self):
                return False

            def lstat(self):
                return SimpleNamespace(st_file_attributes=0x400)

        # 模擬沒有 Path.is_junction() 的舊版 Windows Python。
        with patch.object(cm.os, "name", "nt"):
            self.assertTrue(cm._is_symlink_or_junction(LegacyPath()))

    def test_source_walk_error_disables_safe_candidates_even_when_script_was_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            translations = root / "translations"
            translations.mkdir()
            (translations / "strings.json").write_text(
                json.dumps({"message": "😀"}, ensure_ascii=False),
                encoding="utf-8",
            )
            source = root / "source"
            source.mkdir()
            script_file = source / "empty_script.bin"
            cm.script_tool.write_script(cm.script_tool.Script([]), script_file)
            denied = source / "restricted"
            original_walk = os.walk

            def fake_walk(path, topdown=True, onerror=None, followlinks=False):
                if Path(path) == source:
                    if onerror is not None:
                        onerror(PermissionError(errno.EACCES, "access denied", str(denied)))
                    yield str(path), [], [script_file.name]
                    return
                yield from original_walk(
                    path, topdown=topdown, onerror=onerror, followlinks=followlinks
                )

            with patch.object(cm.os, "walk", side_effect=fake_walk), \
                 patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(
                    translations,
                    source_root=source,
                    report_root=root / "report",
                    settings_path=root / "missing_settings.json",
                )

            self.assertEqual(result.scanned_source_files, 1)
            self.assertFalse(result.source_scan_complete)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(denied) in item for item in result.source_errors))

    def test_source_file_and_directory_symlinks_are_reported_and_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            external_file = root / "outside.bin"
            external_file.write_bytes(b"not an ADVSCRIPT2 file")
            external_dir = root / "outside_dir"
            external_dir.mkdir()
            (external_dir / "hidden.bin").write_bytes(b"not an ADVSCRIPT2 file")

            file_link = source / "linked.bin"
            dir_link = source / "linked_dir"
            try:
                file_link.symlink_to(external_file)
                dir_link.symlink_to(external_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境無法建立符號連結：{exc}")

            with patch.object(cm.script_tool, "read_script", side_effect=AssertionError("不應讀取連結目標")):
                _stats, scanned, errors = cm.scan_source_root(source)

            self.assertEqual(scanned, 0)
            self.assertTrue(any(str(file_link) in item for item in errors))
            self.assertTrue(any(str(dir_link) in item for item in errors))

    def test_translation_symlink_root_is_not_resolved_away(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "strings.json").write_text(
                json.dumps({"message": "🫠"}, ensure_ascii=False),
                encoding="utf-8",
            )
            link = root / "translations_link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境無法建立符號連結：{exc}")

            with patch.object(cm, "load_common_characters", return_value={"亜"}):
                result = cm.scan_project(
                    link,
                    report_root=root / "report",
                    settings_path=root / "missing_settings.json",
                )

            self.assertFalse(result.translation_scan_complete)
            self.assertEqual(result.scanned_translation_files, 0)
            self.assertEqual(result.safe_candidates, [])
            self.assertTrue(any(str(link) in item for item in result.errors))

    def test_source_symlink_root_is_not_resolved_away(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            link = root / "source_link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"目前環境無法建立符號連結：{exc}")

            _stats, scanned, errors = cm.scan_source_root(link)

            self.assertEqual(scanned, 0)
            self.assertTrue(any(str(link) in item for item in errors))


if __name__ == "__main__":
    unittest.main()
