import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
