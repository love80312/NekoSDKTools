#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools 字元統計與 CP932 替代字元管理工具。

功能：
- 掃描翻譯 JSON／文字文件，統計漢字與特殊符號。
- 解析 ADVSCRIPT2 原始腳本，避免把二進位 TXT 當一般文字。
- 由 Python CP932 codec 建立漢字／特殊符號字形清單。
- 排除原始腳本已使用、現有映射已占用及人工排除的候選字元。
- 讀取／更新 UIF uif_config.json 的 source_characters / target_characters。
- 可獨立以命令列執行，也提供可嵌入 NekoSDKTools GUI 的 Frame。

Python 3.10+；僅使用標準函式庫。
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import queue
import re
import sys
import tempfile
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Iterable, Iterator

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "tool" / "uif_config.json"
DEFAULT_SETTINGS = APP_DIR / "tool" / "char_manager.json"
TRANSLATION_EXTENSIONS = {
    ".json", ".txt", ".csv", ".tsv", ".po", ".pot", ".ftl", ".ini",
    ".xml", ".html", ".htm", ".md", ".yaml", ".yml",
}
SOURCE_EXTENSIONS = {".json", ".txt", ".bin", ".scr", ".csv", ".tsv"}
ADVSCRIPT_MAGIC = b"NEKOSDK_ADVSCRIPT2\0"


class CharacterManagerError(Exception):
    """字元管理工具錯誤。"""


@dataclass
class CharacterStat:
    character: str
    count: int = 0
    files: set[str] = field(default_factory=set)


@dataclass
class Glyph:
    character: str
    codepoint: str
    name: str
    category: str
    cp932_hex: str


@dataclass
class ScanResult:
    translation_root: Path
    source_root: Path | None
    report_root: Path
    translation_text_encoding: str
    stats: dict[str, CharacterStat]
    original_characters: set[str]
    glyphs: list[Glyph]
    safe_candidates: list[Glyph]
    mapping: dict[str, str]  # source -> target
    excluded_sources: set[str]
    scanned_translation_files: int
    scanned_source_files: int
    translation_scan_complete: bool
    source_scan_complete: bool
    skipped_files: list[str]
    errors: list[str]

    @property
    def mapped_targets(self) -> set[str]:
        return set(self.mapping.values())

    @property
    def missing_targets(self) -> list[CharacterStat]:
        mapped = self.mapped_targets
        return sorted(
            (
                stat for char, stat in self.stats.items()
                if not can_encode(char, "cp932") and char not in mapped
            ),
            key=lambda item: (-item.count, ord(item.character)),
        )


def strip_json_comments(source: str) -> str:
    """Remove // and /* */ comments while preserving offsets and newlines.

    UIF uses nlohmann::json with ignore_comments=true. Python's json module
    does not, so comments are replaced with spaces before parsing.
    """
    out = list(source)
    i = 0
    in_string = False
    escaped = False
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(source) and source[i] not in "\r\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(source):
                if source[i] == "*" and i + 1 < len(source) and source[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    break
                if source[i] not in "\r\n":
                    out[i] = " "
                i += 1
            else:
                raise CharacterManagerError("uif_config.json 有未結束的區塊註解。")
            continue
        i += 1
    return "".join(out)


def load_json_with_comments(path: Path) -> tuple[Any, str]:
    try:
        original = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CharacterManagerError(f"無法讀取設定檔：{path}\n{exc}") from exc
    try:
        data = json.loads(strip_json_comments(original))
    except json.JSONDecodeError as exc:
        raise CharacterManagerError(
            f"JSON 格式錯誤：{path}\n第 {exc.lineno} 行，第 {exc.colno} 欄：{exc.msg}"
        ) from exc
    return data, original


def load_uif_mapping(config_path: Path) -> dict[str, str]:
    if not config_path.is_file():
        return {}
    data, _ = load_json_with_comments(config_path)
    if not isinstance(data, dict):
        raise CharacterManagerError("uif_config.json 根節點必須是物件。")
    substitution = data.get("character_substitution", {})
    if not isinstance(substitution, dict):
        raise CharacterManagerError("character_substitution 設定格式錯誤。")
    sources = substitution.get("source_characters", "")
    targets = substitution.get("target_characters", "")
    if not isinstance(sources, str) or not isinstance(targets, str):
        raise CharacterManagerError("source_characters / target_characters 必須是字串。")
    source_list = list(sources)
    target_list = list(targets)
    if len(source_list) != len(target_list):
        raise CharacterManagerError(
            "uif_config.json 的 source_characters 與 target_characters 長度不一致。"
        )
    if len(set(source_list)) != len(source_list):
        raise CharacterManagerError("uif_config.json 的 source_characters 有重複字元。")
    if len(set(target_list)) != len(target_list):
        raise CharacterManagerError("uif_config.json 的 target_characters 有重複字元。")
    return dict(zip(source_list, target_list))


def _replace_json_string_property(source: str, cleaned: str, key: str, value: str) -> str:
    # Use the comment-stripped text to find JSON properties, while applying
    # identical offsets to the original text so comments/formatting survive.
    pattern = re.compile(r'("' + re.escape(key) + r'"\s*:\s*)"(?:\\.|[^"\\])*"')
    matches = list(pattern.finditer(cleaned))
    if len(matches) != 1:
        raise CharacterManagerError(
            f"uif_config.json 中應恰好有一個 {key} 欄位，實際找到 {len(matches)} 個。"
        )
    match = matches[0]
    encoded = json.dumps(value, ensure_ascii=False)
    return source[:match.start(0)] + match.group(1) + encoded + source[match.end(0):]


def write_uif_mapping(config_path: Path, mapping: dict[str, str]) -> None:
    """Write source->target mapping, preserving comments and other config text."""
    if not config_path.is_file():
        raise CharacterManagerError(f"找不到 UIF 設定檔：{config_path}")
    if any(len(ch) != 1 for pair in mapping.items() for ch in pair):
        raise CharacterManagerError("映射表每個來源及目標必須各自是一個 Unicode 字元。")
    for source_character in mapping:
        if not can_encode(source_character, "cp932"):
            raise CharacterManagerError(f"來源字元 {source_character!r} 無法以 CP932 編碼。")
        if not is_safe_source_character(source_character):
            raise CharacterManagerError(f"來源字元 {source_character!r} 不屬於預設安全的非 ASCII 漢字／特殊符號範圍。")
    sources = list(mapping.keys())
    targets = list(mapping.values())
    if len(set(sources)) != len(sources):
        raise CharacterManagerError("映射來源有重複字元。")
    if len(set(targets)) != len(targets):
        raise CharacterManagerError("映射目標有重複字元；目前工具採一對一映射。")

    original = config_path.read_text(encoding="utf-8-sig")
    cleaned = strip_json_comments(original)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise CharacterManagerError(f"UIF 設定檔格式錯誤：{exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("character_substitution"), dict):
        raise CharacterManagerError(
            "目前 uif_config.json 沒有 character_substitution 區段；"
            "本版本尚未直接寫入 text_processor 格式。"
        )

    updated = _replace_json_string_property(
        original, cleaned, "source_characters", "".join(sources)
    )
    cleaned_updated = strip_json_comments(updated)
    updated = _replace_json_string_property(
        updated, cleaned_updated, "target_characters", "".join(targets)
    )
    # Validate the final content before touching the original file.
    final_data = json.loads(strip_json_comments(updated))
    final_sub = final_data["character_substitution"]
    if len(final_sub["source_characters"]) != len(final_sub["target_characters"]):
        raise CharacterManagerError("更新後的 UIF 映射長度不一致，取消寫入。")

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8", newline="\n")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=config_path.parent,
            prefix=f".{config_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(updated)
        os.replace(temp_name, config_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def load_manager_settings(settings_path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "version": 1,
        "excluded_source_characters": [],
        "excluded_target_characters": [],
        "text_encoding": "utf-8-sig",
    }
    if not settings_path.is_file():
        return defaults
    try:
        value = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(f"字元管理設定檔無法讀取：{settings_path}\n{exc}") from exc
    if not isinstance(value, dict):
        raise CharacterManagerError("char_manager.json 根節點必須是物件。")
    for key in ("excluded_source_characters", "excluded_target_characters"):
        raw = value.get(key, [])
        if not isinstance(raw, list) or any(not isinstance(item, str) or len(item) != 1 for item in raw):
            raise CharacterManagerError(f"char_manager.json 的 {key} 格式錯誤。")
        defaults[key] = list(dict.fromkeys(raw))
    if isinstance(value.get("text_encoding"), str):
        defaults["text_encoding"] = value["text_encoding"]
    defaults["version"] = 1
    return defaults


def save_manager_settings(settings_path: Path, settings: dict[str, Any]) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=settings_path.parent,
            prefix=f".{settings_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, settings_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def can_encode(character: str, encoding: str = "cp932") -> bool:
    try:
        character.encode(encoding, errors="strict")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _is_kana(character: str) -> bool:
    cp = ord(character)
    return (
        0x3041 <= cp <= 0x3096  # Hiragana letters
        or 0x3097 <= cp <= 0x309F  # Hiragana marks, iteration / digraph marks
        or 0x3031 <= cp <= 0x3035  # Vertical kana repeat marks
        or 0x30A1 <= cp <= 0x30FA  # Katakana letters
        or 0x30FD <= cp <= 0x30FF  # Katakana iteration / digraph marks
        or 0x31F0 <= cp <= 0x31FF  # Katakana Phonetic Extensions
        or 0xFF66 <= cp <= 0xFF9F  # Halfwidth Katakana
        or 0x1B000 <= cp <= 0x1B16F  # Kana supplement/extensions
        or 0x1AFF0 <= cp <= 0x1AFFF  # Kana Extended-B
    )


def _is_han(character: str) -> bool:
    name = unicodedata.name(character, "")
    return (
        name.startswith("CJK UNIFIED IDEOGRAPH")
        or name.startswith("CJK COMPATIBILITY IDEOGRAPH")
        or name in {
            "IDEOGRAPHIC ITERATION MARK",
            "IDEOGRAPHIC CLOSING MARK",
            "IDEOGRAPHIC NUMBER ZERO",
        }
    )


def _is_latin_letter(character: str) -> bool:
    name = unicodedata.name(character, "")
    return name.startswith("LATIN ") or character.isascii() and character.isalpha()


def _is_number(character: str) -> bool:
    return unicodedata.category(character).startswith("N")


def is_report_character(character: str) -> bool:
    """Keep Han ideographs and punctuation/symbols; discard Latin, kana, digits."""
    if len(character) != 1 or character.isspace() or _is_kana(character):
        return False
    if _is_latin_letter(character) or _is_number(character):
        return False
    category = unicodedata.category(character)
    return _is_han(character) or category.startswith(("P", "S"))


def is_safe_source_character(character: str) -> bool:
    """Default replacement sources are non-ASCII Han/punctuation/symbol glyphs.

    ASCII punctuation is kept in the full catalog for reference, but excluded
    from the default source pool because characters such as %, &, [], and \\ 
    may have script/format-control meaning even when absent from visible text.
    """
    return is_report_character(character) and ord(character) > 0x7F


def _glyph_category(character: str) -> str:
    if _is_han(character):
        return "漢字"
    category = unicodedata.category(character)
    if category.startswith("P"):
        return "標點符號"
    if category.startswith("S"):
        return "符號"
    return category


def iter_text_files(
    root: Path, allowed_extensions: set[str] = TRANSLATION_EXTENSIONS
) -> Iterator[Path]:
    if root.is_file():
        if root.suffix.lower() in allowed_extensions:
            yield root
        return
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            continue
        if path.is_file() and path.suffix.lower() in allowed_extensions:
            yield path


def _collect_json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _collect_json_strings(item)
    elif isinstance(value, dict):
        # JSON object keys are schema/metadata, not translated text.
        for item in value.values():
            yield from _collect_json_strings(item)


def _load_text_document(path: Path, text_encoding: str) -> list[str]:
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CharacterManagerError(f"JSON 讀取失敗：{path}\n{exc}") from exc
        return list(_collect_json_strings(value))
    try:
        return [path.read_text(encoding=text_encoding)]
    except (OSError, UnicodeError, LookupError) as exc:
        raise CharacterManagerError(
            f"文字文件讀取失敗：{path}\n編碼 {text_encoding!r}：{exc}"
        ) from exc


def _load_scr_module() -> Any:
    scr_path = APP_DIR / "scr.py"
    if not scr_path.is_file():
        raise CharacterManagerError(
            "找不到 scr.py；無法解析原始 ADVSCRIPT2 腳本。"
        )
    module_name = "_neko_scr_for_char_manager"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, scr_path)
    if spec is None or spec.loader is None:
        raise CharacterManagerError("無法載入 scr.py。")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _extract_script_strings(
    scr_module: Any,
    script: Any,
    source_name: str,
    command_encoding: str,
    dialogue_encoding: str,
) -> list[str]:
    """Decode all plausible textual fields, not only fields exported for translation.

    export_items() intentionally exports only editable dialogue/choice strings.
    Candidate-source exclusion must be more conservative: any text in record
    main/args can be seen by the game and should count as occupied.
    """
    values: list[str] = []
    choice_encoding = scr_module.dialogue_choice_encoding(dialogue_encoding)
    alternate_encodings = tuple(dict.fromkeys((command_encoding, choice_encoding)))

    for record_index, record in enumerate(script.records):
        context = f"{source_name} record {record_index}"
        for decoded, _encoding in scr_module.decode_candidates(
            record.main, alternate_encodings, f"{context} main"
        ):
            if decoded:
                values.append(decoded)

        record_encoding: str | None = None
        if record.header[3] == scr_module.TEXT_OPCODE:
            record_encoding = scr_module.resolve_text_record_encoding(
                record, command_encoding, dialogue_encoding, context
            )

        for arg_index, raw in enumerate(record.args):
            encodings = (
                tuple(dict.fromkeys((record_encoding, *alternate_encodings)))
                if record_encoding else alternate_encodings
            )
            for decoded, _encoding in scr_module.decode_candidates(
                raw, encodings, f"{context} arg {arg_index}"
            ):
                if decoded:
                    values.append(decoded)
    return values


def _scan_original_root(
    root: Path | None,
    command_encoding: str,
    dialogue_encoding: str,
) -> tuple[set[str], int, list[str], list[str]]:
    used: set[str] = set()
    scanned = 0
    skipped: list[str] = []
    errors: list[str] = []
    if root is None:
        return used, scanned, skipped, errors

    scr_module = None
    for path in iter_text_files(root, SOURCE_EXTENSIONS):
        if path.name.casefold() in {"uif_config.json", "char_manager.json"}:
            skipped.append(str(path))
            continue
        try:
            with path.open("rb") as handle:
                signature = handle.read(len(ADVSCRIPT_MAGIC))
            if signature == ADVSCRIPT_MAGIC:
                if scr_module is None:
                    scr_module = _load_scr_module()
                script = scr_module.read_script(path)
                strings = _extract_script_strings(
                    scr_module, script, str(path), command_encoding, dialogue_encoding
                )
            else:
                source_encoding = "utf-8-sig" if path.suffix.lower() == ".json" else command_encoding
                strings = _load_text_document(path, source_encoding)
            scanned += 1
            for text_value in strings:
                used.update(ch for ch in text_value if is_report_character(ch))
        except Exception as exc:
            errors.append(f"原始資料：{path}：{type(exc).__name__}: {exc}")
    return used, scanned, skipped, errors


def build_cp932_glyph_catalog() -> list[Glyph]:
    """Enumerate distinct CP932-decodable Han/punctuation/symbol characters."""
    byte_map: dict[str, bytes] = {}

    def add(raw: bytes) -> None:
        try:
            decoded = raw.decode("cp932", errors="strict")
        except UnicodeDecodeError:
            return
        if len(decoded) == 1 and is_report_character(decoded):
            # Keep the shortest byte sequence for each Unicode character.
            previous = byte_map.get(decoded)
            if previous is None or len(raw) < len(previous):
                byte_map[decoded] = raw

    for first in range(0x00, 0x100):
        add(bytes((first,)))
    for first in range(0x00, 0x100):
        for second in range(0x00, 0x100):
            add(bytes((first, second)))

    glyphs: list[Glyph] = []
    for character, raw in byte_map.items():
        codepoint = f"U+{ord(character):04X}"
        glyphs.append(
            Glyph(
                character=character,
                codepoint=codepoint,
                name=unicodedata.name(character, "未命名字元"),
                category=_glyph_category(character),
                cp932_hex=raw.hex(" ").upper(),
            )
        )
    return sorted(glyphs, key=lambda item: (ord(item.character), item.cp932_hex))


def scan_translation_root(
    root: Path, text_encoding: str
) -> tuple[dict[str, CharacterStat], int, list[str]]:
    stats: dict[str, CharacterStat] = {}
    scanned = 0
    errors: list[str] = []
    for path in iter_text_files(root):
        try:
            strings = _load_text_document(path, text_encoding)
            scanned += 1
            relative = str(path.relative_to(root)) if root.is_dir() else path.name
            for text_value in strings:
                for character in text_value:
                    if not is_report_character(character):
                        continue
                    stat = stats.setdefault(character, CharacterStat(character))
                    stat.count += 1
                    stat.files.add(relative)
        except Exception as exc:
            errors.append(f"翻譯資料：{path}：{type(exc).__name__}: {exc}")
    return stats, scanned, errors


def _write_csv(path: Path, headers: list[str], rows: Iterable[Iterable[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_reports(result: ScanResult) -> dict[str, Path]:
    output = result.report_root
    output.mkdir(parents=True, exist_ok=True)
    mapped_targets = result.mapped_targets
    source_mapped = set(result.mapping)
    original = result.original_characters
    excluded = result.excluded_sources
    safe = {glyph.character for glyph in result.safe_candidates}

    character_rows = []
    for character, stat in sorted(
        result.stats.items(), key=lambda pair: (-pair[1].count, ord(pair[0]))
    ):
        supported = can_encode(character, "cp932")
        character_rows.append([
            character,
            f"U+{ord(character):04X}",
            unicodedata.name(character, "未命名字元"),
            _glyph_category(character),
            stat.count,
            len(stat.files),
            "是" if supported else "否",
            "已映射" if character in mapped_targets else ("不需替換" if supported else "待替換"),
        ])
    character_report = output / "character_report.csv"
    _write_csv(
        character_report,
        ["字元", "Unicode", "Unicode 名稱", "類別", "出現次數", "使用檔案數", "CP932 可編碼", "狀態"],
        character_rows,
    )

    glyph_rows = []
    for glyph in result.glyphs:
        char = glyph.character
        glyph_rows.append([
            char, glyph.codepoint, glyph.name, glyph.category, glyph.cp932_hex,
            "是" if char in original else "否",
            "是" if char in source_mapped else "否",
            "是" if char in mapped_targets else "否",
            "是" if char in excluded else "否",
            "是" if char in safe else "否",
        ])
    glyph_report = output / "cp932_glyphs.csv"
    _write_csv(
        glyph_report,
        ["字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組", "原文已使用", "已作為來源", "已作為目標", "人工排除", "可用候選"],
        glyph_rows,
    )

    candidate_report = output / "safe_candidate_glyphs.csv"
    _write_csv(
        candidate_report,
        ["來源候選字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組"],
        ([g.character, g.codepoint, g.name, g.category, g.cp932_hex] for g in result.safe_candidates),
    )

    mapping_template = output / "mapping_template.csv"
    missing = result.missing_targets
    _write_csv(
        mapping_template,
        ["目標中文字", "目標 Unicode", "出現次數", "來源候選字元（自行填入）", "來源 Unicode（參考）", "備註"],
        ([stat.character, f"U+{ord(stat.character):04X}", stat.count, "", "", ""] for stat in missing),
    )

    unmapped_txt = output / "unmapped_characters.txt"
    unmapped_txt.write_text("".join(stat.character for stat in missing), encoding="utf-8")

    metadata_report = output / "scan_metadata.json"
    metadata = {
        "translation_root": str(result.translation_root),
        "translation_text_encoding": result.translation_text_encoding,
        "source_root": str(result.source_root) if result.source_root else None,
        "translation_files_scanned": result.scanned_translation_files,
        "source_files_scanned": result.scanned_source_files,
        "translation_scan_complete": result.translation_scan_complete,
        "source_scan_complete": result.source_scan_complete,
        "errors": result.errors,
    }
    metadata_report.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "character_report": character_report,
        "glyph_report": glyph_report,
        "candidate_report": candidate_report,
        "mapping_template": mapping_template,
        "unmapped_txt": unmapped_txt,
        "metadata_report": metadata_report,
    }


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    return _path_is_within(first, second) or _path_is_within(second, first)


def scan_project(
    translation_root: Path,
    source_root: Path | None,
    report_root: Path,
    config_path: Path,
    settings_path: Path,
    *,
    text_encoding: str = "utf-8-sig",
    command_encoding: str = "cp932",
    dialogue_encoding: str = "auto",
) -> ScanResult:
    translation_root = translation_root.expanduser().resolve()
    if not translation_root.exists():
        raise CharacterManagerError(f"翻譯資料夾／檔案不存在：{translation_root}")
    if source_root is not None:
        source_root = source_root.expanduser().resolve()
        if not source_root.exists():
            raise CharacterManagerError(f"原始腳本資料夾／檔案不存在：{source_root}")
        if _paths_overlap(translation_root, source_root):
            raise CharacterManagerError(
                "翻譯資料與原始腳本資料夾不能互相包含；請選擇兩個互相獨立的資料夾。"
            )
    report_root = report_root.expanduser().resolve()
    if _path_is_within(report_root, translation_root):
        raise CharacterManagerError(
            "報表輸出資料夾不能放在翻譯資料夾內，以免下次掃描把報表當翻譯內容。"
        )
    if source_root is not None and _path_is_within(report_root, source_root):
        raise CharacterManagerError(
            "報表輸出資料夾不能放在原始腳本資料夾內，以免下次掃描把報表當原始文字。"
        )

    mapping = load_uif_mapping(config_path)
    settings = load_manager_settings(settings_path)
    stats, scanned_translation, translation_errors = scan_translation_root(
        translation_root, text_encoding
    )
    if scanned_translation == 0:
        raise CharacterManagerError(
            "指定位置沒有找到支援的翻譯文檔（JSON／TXT／CSV／TSV／PO／FTL 等）。"
        )
    original, scanned_source, _skipped, source_errors = _scan_original_root(
        source_root, command_encoding, dialogue_encoding
    )
    translation_scan_complete = scanned_translation > 0 and not translation_errors
    source_scan_complete = source_root is not None and scanned_source > 0 and not source_errors
    glyphs = build_cp932_glyph_catalog()
    excluded = set(settings["excluded_source_characters"])
    mapped_sources = set(mapping)
    mapped_targets = set(mapping.values())
    # Prevent new source glyphs from colliding with an existing target glyph;
    # otherwise a second replacement pass may produce an unintended chain.
    # Exclude source glyphs already used by either the original game or the
    # translation. UIF substitution would otherwise alter legitimate text.
    translated_characters = set(stats)
    blocked_sources = (
        original | translated_characters | mapped_sources | mapped_targets | excluded
    )
    safe_candidates = (
        [glyph for glyph in glyphs if is_safe_source_character(glyph.character)
         and glyph.character not in blocked_sources]
        if source_scan_complete and translation_scan_complete else []
    )

    result = ScanResult(
        translation_root=translation_root,
        source_root=source_root,
        report_root=report_root,
        translation_text_encoding=text_encoding,
        stats=stats,
        original_characters=original,
        glyphs=glyphs,
        safe_candidates=safe_candidates,
        mapping=mapping,
        excluded_sources=excluded,
        scanned_translation_files=scanned_translation,
        scanned_source_files=scanned_source,
        translation_scan_complete=translation_scan_complete,
        source_scan_complete=source_scan_complete,
        skipped_files=[],
        errors=translation_errors + source_errors,
    )
    write_reports(result)
    return result


def apply_mapping_csv(
    mapping_csv: Path,
    config_path: Path,
    settings_path: Path,
    original_characters: set[str] | None = None,
) -> dict[str, str]:
    current = load_uif_mapping(config_path)
    settings = load_manager_settings(settings_path)
    excluded_sources = set(settings["excluded_source_characters"])

    metadata_report = mapping_csv.with_name("scan_metadata.json")
    if not metadata_report.is_file():
        raise CharacterManagerError(
            "找不到同一報表資料夾內的 scan_metadata.json；請重新掃描後再套用映射。"
        )
    try:
        metadata = json.loads(metadata_report.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(f"無法讀取掃描狀態：{exc}") from exc
    if not isinstance(metadata, dict) or not metadata.get("translation_scan_complete"):
        raise CharacterManagerError(
            "翻譯文檔掃描不完整，拒絕套用映射。請先修正讀取錯誤並重新掃描。"
        )

    translation_report = mapping_csv.with_name("character_report.csv")
    if not translation_report.is_file():
        raise CharacterManagerError(
            "找不到同一報表資料夾內的 character_report.csv；請保留完整報表資料夾。"
        )
    translated_characters: set[str] = set()
    translation_root_value = metadata.get("translation_root")
    translation_encoding = metadata.get("translation_text_encoding", "utf-8-sig")
    if not isinstance(translation_root_value, str) or not translation_root_value:
        raise CharacterManagerError("scan_metadata.json 缺少翻譯資料夾路徑。")
    if not isinstance(translation_encoding, str) or not translation_encoding:
        raise CharacterManagerError("scan_metadata.json 的翻譯文字編碼不正確。")
    translation_root = Path(translation_root_value)
    if not translation_root.exists():
        raise CharacterManagerError(f"原翻譯資料夾已不存在：{translation_root}")
    translated_stats, translated_count, translation_errors = scan_translation_root(
        translation_root, translation_encoding
    )
    if translated_count == 0 or translation_errors:
        details = "\n".join(translation_errors[:10]) if translation_errors else "沒有可讀取的翻譯文檔。"
        raise CharacterManagerError("重新掃描翻譯文檔失敗，拒絕套用：\n" + details)
    translated_characters = set(translated_stats)

    additions: list[tuple[str, str]] = []
    try:
        with mapping_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "目標中文字" not in reader.fieldnames or "來源候選字元（自行填入）" not in reader.fieldnames:
                raise CharacterManagerError("映射 CSV 欄位不正確，請使用 mapping_template.csv。")
            for row_number, row in enumerate(reader, start=2):
                target = (row.get("目標中文字") or "").strip()
                source = (row.get("來源候選字元（自行填入）") or "").strip()
                if not source:
                    continue
                if len(target) != 1 or len(source) != 1:
                    raise CharacterManagerError(f"CSV 第 {row_number} 列：目標及來源都必須各是一個字元。")
                additions.append((source, target))
    except OSError as exc:
        raise CharacterManagerError(f"無法讀取映射 CSV：{exc}") from exc

    existing_targets = set(current.values())
    existing_sources = set(current)
    for source, target in additions:
        if source in existing_sources:
            raise CharacterManagerError(f"來源字元 {source!r} 已存在於 UIF 映射中。")
        if source in existing_targets:
            raise CharacterManagerError(f"來源字元 {source!r} 已被當成既有目標字元，可能造成替換鏈衝突。")
        if source in translated_characters:
            raise CharacterManagerError(f"來源字元 {source!r} 已出現在翻譯文檔中；UIF 會連正常翻譯一起替換。")
        if source in excluded_sources:
            raise CharacterManagerError(f"來源字元 {source!r} 已列入人工排除清單。")
        if not is_safe_source_character(source):
            raise CharacterManagerError(f"來源字元 {source!r} 不是預設安全的非 ASCII 漢字／特殊符號候選。")
        if target in existing_targets:
            raise CharacterManagerError(f"目標字元 {target!r} 已經有映射。")
        if target in existing_sources:
            raise CharacterManagerError(f"目標字元 {target!r} 已經被當成來源字元，可能形成替換鏈衝突。")
        if can_encode(target, "cp932"):
            raise CharacterManagerError(f"目標字元 {target!r} 本身可用 CP932 編碼，不需要加入替換映射。")
        if not can_encode(source, "cp932"):
            raise CharacterManagerError(f"來源字元 {source!r} 無法以 CP932 編碼。")
        if original_characters is not None and source in original_characters:
            raise CharacterManagerError(f"來源字元 {source!r} 已出現在原始遊戲文字，不可當替代字元。")
        if target in existing_sources:
            raise CharacterManagerError(
                f"目標字元 {target!r} 已被當成既有來源字元；為避免替換衝突，請更換配對。"
            )
        current[source] = target
        existing_sources.add(source)
        existing_targets.add(target)
    write_uif_mapping(config_path, current)
    return current


# ------------------------------- Embedded GUI -------------------------------

class CharacterManagerFrame(ttk.Frame):
    """可直接嵌入 NekoSDKTools GUI 的字元管理頁籤。"""

    def __init__(self, master: tk.Misc, app_dir: Path | None = None, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.app_dir = (app_dir or APP_DIR).resolve()
        self.config_path = self.app_dir / "tool" / "uif_config.json"
        self.settings_path = self.app_dir / "tool" / "char_manager.json"
        self.result: ScanResult | None = None
        self.mapping: dict[str, str] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.excluded_sources: set[str] = set()
        try:
            self.initial_settings = load_manager_settings(self.settings_path)
            self.excluded_sources = set(self.initial_settings["excluded_source_characters"])
        except CharacterManagerError:
            self.initial_settings = {
                "version": 1,
                "excluded_source_characters": [],
                "excluded_target_characters": [],
                "text_encoding": "utf-8-sig",
            }
        self._build_ui()
        self.after(120, self._poll_events)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        paths = ttk.LabelFrame(self, text="掃描來源")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        paths.columnconfigure(1, weight=1)
        self.translation_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.encoding_var = tk.StringVar(
            value=self.initial_settings.get("text_encoding", "utf-8-sig")
        )
        self.command_encoding_var = tk.StringVar(value="cp932")
        self.dialogue_encoding_var = tk.StringVar(value="auto")
        self._path_line(paths, 0, "翻譯文檔：", self.translation_var, self._pick_translation)
        self._path_line(paths, 1, "原始遊戲腳本：", self.source_var, self._pick_source)
        ttk.Label(paths, text="翻譯 TXT 編碼：").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(
            paths, textvariable=self.encoding_var, width=14,
            values=("utf-8-sig", "utf-8", "cp932", "gbk", "big5"),
        ).grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(paths, text="原始腳本：").grid(row=2, column=2, sticky="e", padx=(10, 3), pady=4)
        ttk.Label(paths, text="CP932／auto", foreground="#666666").grid(row=2, column=3, sticky="w", padx=6, pady=4)
        self.scan_button = ttk.Button(paths, text="開始掃描與統計", command=self._start_scan)
        self.scan_button.grid(row=0, column=4, rowspan=2, padx=8, pady=4, sticky="ns")

        info = ttk.Label(
            self,
            text=(
                "只統計漢字及特殊符號；英文、日文假名、數字與空白會排除。"
                "候選會排除原文及翻譯已使用字元、既有映射與人工排除字元。"
                "CP932 可編碼不代表遊戲字型一定有字形，仍須實際測試。"
            ),
            foreground="#555555", wraplength=900,
        )
        info.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        target_box = ttk.LabelFrame(panes, text="待替換中文字（CP932 不支援且尚未映射）")
        candidate_box = ttk.LabelFrame(panes, text="CP932 來源候選（遊戲字型仍需測試）")
        panes.add(target_box, weight=1)
        panes.add(candidate_box, weight=1)
        target_box.rowconfigure(0, weight=1)
        target_box.columnconfigure(0, weight=1)
        candidate_box.rowconfigure(1, weight=1)
        candidate_box.columnconfigure(0, weight=1)

        self.target_tree = ttk.Treeview(
            target_box, columns=("count", "files", "unicode"), show="headings", height=9,
            selectmode="browse",
        )
        for col, title, width in (("count", "次數", 65), ("files", "檔案數", 65), ("unicode", "Unicode", 95)):
            self.target_tree.heading(col, text=title)
            self.target_tree.column(col, width=width, anchor="center", stretch=False)
        self.target_tree.heading("#0", text="目標中文字")
        self.target_tree.configure(show="tree headings")
        self.target_tree.column("#0", width=80, anchor="center")
        self.target_tree.grid(row=0, column=0, sticky="nsew")
        target_scroll = ttk.Scrollbar(target_box, orient="vertical", command=self.target_tree.yview)
        target_scroll.grid(row=0, column=1, sticky="ns")
        self.target_tree.configure(yscrollcommand=target_scroll.set)

        search_row = ttk.Frame(candidate_box)
        search_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(search_row, text="搜尋候選：").pack(side="left", padx=4)
        self.candidate_filter_var = tk.StringVar()
        filter_entry = ttk.Entry(search_row, textvariable=self.candidate_filter_var)
        filter_entry.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self.candidate_filter_var.trace_add("write", lambda *_: self._refresh_candidate_tree())
        self.candidate_tree = ttk.Treeview(
            candidate_box, columns=("unicode", "name", "bytes"), show="headings", height=9,
            selectmode="browse",
        )
        self.candidate_tree.heading("#0", text="來源字元")
        self.candidate_tree.heading("unicode", text="Unicode")
        self.candidate_tree.heading("name", text="Unicode 名稱")
        self.candidate_tree.heading("bytes", text="CP932")
        self.candidate_tree.column("#0", width=70, anchor="center", stretch=False)
        self.candidate_tree.column("unicode", width=90, anchor="center", stretch=False)
        self.candidate_tree.column("name", width=230, anchor="w")
        self.candidate_tree.column("bytes", width=80, anchor="center", stretch=False)
        self.candidate_tree.configure(show="tree headings")
        self.candidate_tree.grid(row=1, column=0, sticky="nsew")
        candidate_scroll = ttk.Scrollbar(candidate_box, orient="vertical", command=self.candidate_tree.yview)
        candidate_scroll.grid(row=1, column=1, sticky="ns")
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=7)
        ttk.Button(actions, text="建立配對 →", command=self._add_pair).pack(side="left", padx=(0, 5))
        ttk.Button(actions, text="移除配對", command=self._remove_pair).pack(side="left", padx=5)
        ttk.Button(actions, text="排除選取來源字元", command=self._exclude_candidate).pack(side="left", padx=5)
        ttk.Button(actions, text="清空人工排除", command=self._clear_exclusions).pack(side="left", padx=5)
        ttk.Button(actions, text="套用至 uif_config.json", command=self._apply_mapping).pack(side="right", padx=4)

        mapping_box = ttk.LabelFrame(self, text="目前替換映射（來源 → 顯示目標）")
        mapping_box.grid(row=4, column=0, sticky="ew")
        mapping_box.columnconfigure(0, weight=1)
        self.mapping_tree = ttk.Treeview(
            mapping_box, columns=("target", "source_unicode", "target_unicode"),
            show="headings", height=5, selectmode="browse",
        )
        for col, title, width in (
            ("target", "顯示目標字元", 120),
            ("source_unicode", "來源 Unicode", 110),
            ("target_unicode", "目標 Unicode", 110),
        ):
            self.mapping_tree.heading(col, text=title)
            self.mapping_tree.column(col, width=width, anchor="center")
        self.mapping_tree.heading("#0", text="儲存來源字元")
        self.mapping_tree.configure(show="tree headings")
        self.mapping_tree.column("#0", width=120, anchor="center")
        self.mapping_tree.grid(row=0, column=0, sticky="ew")
        map_scroll = ttk.Scrollbar(mapping_box, orient="vertical", command=self.mapping_tree.yview)
        map_scroll.grid(row=0, column=1, sticky="ns")
        self.mapping_tree.configure(yscrollcommand=map_scroll.set)

        self.status_var = tk.StringVar(value="請選擇翻譯文檔資料夾及原始遊戲腳本資料夾。")
        ttk.Label(self, textvariable=self.status_var, foreground="#555555", wraplength=900).grid(
            row=5, column=0, sticky="ew", pady=(6, 0)
        )

    def _path_line(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=3, sticky="ew", padx=6, pady=4)
        ttk.Button(parent, text="選擇資料夾", command=command).grid(row=row, column=4, padx=8, pady=4)

    def _pick_translation(self) -> None:
        value = filedialog.askdirectory(title="選擇翻譯文檔資料夾")
        if value:
            self.translation_var.set(value)

    def _pick_source(self) -> None:
        value = filedialog.askdirectory(title="選擇原始遊戲腳本資料夾")
        if value:
            self.source_var.set(value)

    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        translation = Path(self.translation_var.get().strip().strip('"')).expanduser()
        source_text = self.source_var.get().strip().strip('"')
        source = Path(source_text).expanduser() if source_text else None
        if not translation.exists():
            messagebox.showerror("無法掃描", "請指定存在的翻譯文檔資料夾。", parent=self)
            return
        if not translation.is_dir():
            messagebox.showerror("無法掃描", "翻譯文檔目前必須選擇資料夾。", parent=self)
            return
        if source is None or not source.is_dir():
            messagebox.showerror(
                "無法掃描", "請指定原始遊戲腳本資料夾。沒有原始文字就無法可靠排除已使用字元。", parent=self
            )
            return
        try:
            settings = load_manager_settings(self.settings_path)
            self.excluded_sources = set(settings["excluded_source_characters"])
            settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
            save_manager_settings(self.settings_path, settings)
        except CharacterManagerError as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=self)
            return
        report = translation.parent / (translation.name + "_char_report")
        self.scan_button.configure(state="disabled")
        self.status_var.set("掃描中：讀取翻譯文字、原始腳本及 CP932 字形表…")
        args = (
            translation, source, report, self.config_path, self.settings_path,
            self.encoding_var.get().strip() or "utf-8-sig",
            self.command_encoding_var.get().strip() or "cp932",
            self.dialogue_encoding_var.get().strip() or "auto",
        )

        def worker() -> None:
            try:
                result = scan_project(
                    args[0], args[1], args[2], args[3], args[4],
                    text_encoding=args[5], command_encoding=args[6], dialogue_encoding=args[7],
                )
                self.events.put(("scan_done", result))
            except Exception as exc:
                self.events.put(("scan_error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=worker, name="NekoCharacterScan", daemon=True)
        self.worker.start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "scan_error":
                    self.scan_button.configure(state="normal")
                    self.status_var.set("掃描失敗")
                    messagebox.showerror("掃描失敗", str(payload), parent=self)
                elif kind == "scan_done":
                    self.scan_button.configure(state="normal")
                    self.result = payload
                    self.mapping = dict(payload.mapping)
                    self.excluded_sources = set(payload.excluded_sources)
                    self._refresh_all()
                    paths = {
                        "character_report": payload.report_root / "character_report.csv",
                    }
                    missing_count = len(self._current_missing_targets())
                    source_note = "原始腳本掃描完整" if payload.source_scan_complete else "原始腳本掃描不完整；候選清單停用"
                    translation_note = "翻譯掃描完整" if payload.translation_scan_complete else "翻譯掃描不完整；候選清單停用"
                    self.status_var.set(
                        f"完成：翻譯檔 {payload.scanned_translation_files} 個，"
                        f"原始檔 {payload.scanned_source_files} 個，"
                        f"待替換字元 {missing_count} 個，"
                        f"可用候選 {len(payload.safe_candidates)} 個。\n"
                        f"{translation_note}；{source_note}。報表：{paths['character_report'].parent}"
                    )
                    if payload.errors:
                        messagebox.showwarning(
                            "部分檔案無法讀取",
                            "以下檔案未能完整納入統計：\n\n" + "\n".join(payload.errors[:20]),
                            parent=self,
                        )
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _refresh_all(self) -> None:
        self.target_tree.delete(*self.target_tree.get_children())
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        if self.result is None:
            return
        for stat in self._current_missing_targets():
            self.target_tree.insert(
                "", "end", iid=f"t{ord(stat.character)}",
                text=stat.character,
                values=(stat.count, len(stat.files), f"U+{ord(stat.character):04X}"),
            )
        self._refresh_candidate_tree()
        self._refresh_mapping_tree()

    def _current_missing_targets(self) -> list[CharacterStat]:
        if self.result is None:
            return []
        mapped = set(self.mapping.values())
        excluded_targets: set[str] = set()
        try:
            excluded_targets = set(load_manager_settings(self.settings_path).get("excluded_target_characters", []))
        except CharacterManagerError:
            pass
        return sorted(
            (
                stat for character, stat in self.result.stats.items()
                if not can_encode(character, "cp932")
                and character not in mapped
                and character not in excluded_targets
                and character not in self.mapping
            ),
            key=lambda item: (-item.count, ord(item.character)),
        )

    def _refresh_candidate_tree(self) -> None:
        if not hasattr(self, "candidate_tree") or self.result is None:
            return
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        query = self.candidate_filter_var.get().strip().casefold()
        mapped_sources = set(self.mapping)
        mapped_targets = set(self.mapping.values())
        available = [
            glyph for glyph in self.result.glyphs
            if is_safe_source_character(glyph.character)
            and glyph.character not in self.result.original_characters
            and glyph.character not in self.result.stats
            and glyph.character not in mapped_sources
            and glyph.character not in mapped_targets
            and glyph.character not in self.excluded_sources
        ]
        # Never claim a candidate is safe when no original script was scanned.
        if not self.result.source_scan_complete or not self.result.translation_scan_complete:
            available = []
        count = 0
        for glyph in available:
            searchable = " ".join((glyph.character, glyph.codepoint, glyph.name, glyph.category)).casefold()
            if query and query not in searchable:
                continue
            self.candidate_tree.insert(
                "", "end", iid=f"c{ord(glyph.character)}",
                text=glyph.character,
                values=(glyph.codepoint, glyph.name, glyph.cp932_hex),
            )
            count += 1

    def _refresh_mapping_tree(self) -> None:
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        for source, target in self.mapping.items():
            self.mapping_tree.insert(
                "", "end", iid=f"m{ord(source)}", text=source,
                values=(target, f"U+{ord(source):04X}", f"U+{ord(target):04X}"),
            )

    def _add_pair(self) -> None:
        if self.result is None:
            messagebox.showwarning("尚未掃描", "請先執行字元掃描。", parent=self)
            return
        target_selection = self.target_tree.selection()
        source_selection = self.candidate_tree.selection()
        if not target_selection or not source_selection:
            messagebox.showwarning("尚未選擇", "請分別選擇一個待替換中文字及來源候選字元。", parent=self)
            return
        target_iid = target_selection[0]
        source_iid = source_selection[0]
        target = chr(int(target_iid[1:]))
        source = chr(int(source_iid[1:]))
        if source in self.mapping:
            messagebox.showerror("映射衝突", f"來源字元 {source} 已經被使用。", parent=self)
            return
        if target in self.mapping.values():
            messagebox.showerror("映射衝突", f"目標字元 {target} 已經有映射。", parent=self)
            return
        if target in self.mapping:
            messagebox.showerror("映射衝突", f"目標字元 {target} 同時是現有來源字元。請選擇其他目標。", parent=self)
            return
        if source in self.result.original_characters:
            messagebox.showerror("來源字元已使用", f"原始遊戲文字已使用 {source}，不能拿來替換。", parent=self)
            return
        if source in self.result.stats:
            messagebox.showerror("翻譯字元衝突", f"翻譯文檔本身已使用 {source}，UIF 也會替換這個正常字元。", parent=self)
            return
        self.mapping[source] = target
        self._refresh_all()

    def _remove_pair(self) -> None:
        selected = self.mapping_tree.selection()
        if not selected:
            return
        source = chr(int(selected[0][1:]))
        self.mapping.pop(source, None)
        self._refresh_all()

    def _exclude_candidate(self) -> None:
        selected = self.candidate_tree.selection()
        if not selected:
            messagebox.showinfo("未選擇字元", "請先選擇要排除的來源候選字元。", parent=self)
            return
        character = chr(int(selected[0][1:]))
        self.excluded_sources.add(character)
        try:
            settings = load_manager_settings(self.settings_path)
            settings["excluded_source_characters"] = sorted(self.excluded_sources, key=ord)
            settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
            save_manager_settings(self.settings_path, settings)
        except CharacterManagerError as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=self)
            return
        self._refresh_candidate_tree()
        self.status_var.set(f"已將 {character} 加入人工排除清單。")

    def _clear_exclusions(self) -> None:
        if not self.excluded_sources:
            messagebox.showinfo("排除清單", "目前沒有人工排除的來源字元。", parent=self)
            return
        if not messagebox.askyesno(
            "清空人工排除",
            f"確定清除 {len(self.excluded_sources)} 個人工排除字元？\n"
            "原始腳本已使用及既有映射占用的字元仍會自動排除。",
            parent=self,
        ):
            return
        self.excluded_sources.clear()
        try:
            settings = load_manager_settings(self.settings_path)
            settings["excluded_source_characters"] = []
            settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
            save_manager_settings(self.settings_path, settings)
        except CharacterManagerError as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=self)
            return
        self._refresh_candidate_tree()
        self.status_var.set("已清空人工排除清單。")

    def _apply_mapping(self) -> None:
        if self.result is None:
            messagebox.showwarning("尚未掃描", "請先掃描翻譯及原始遊戲腳本。", parent=self)
            return
        if not messagebox.askyesno(
            "確認更新 UIF 設定",
            f"即將把 {len(self.mapping)} 組映射寫入：\n{self.config_path}\n\n"
            "原設定會先備份成 uif_config.json.bak。是否繼續？",
            parent=self,
        ):
            return
        try:
            if self.result is None or not self.result.source_scan_complete or not self.result.translation_scan_complete:
                raise CharacterManagerError("原始腳本或翻譯文檔掃描不完整，拒絕套用替換映射。")
            for source in self.mapping:
                if source in self.result.original_characters:
                    raise CharacterManagerError(f"來源字元 {source!r} 已出現在原始遊戲文字。")
                if source in self.result.stats:
                    raise CharacterManagerError(f"來源字元 {source!r} 已出現在翻譯文檔。")
                if source in self.excluded_sources:
                    raise CharacterManagerError(f"來源字元 {source!r} 已列入人工排除清單。")
            write_uif_mapping(self.config_path, self.mapping)
            settings = load_manager_settings(self.settings_path)
            settings["excluded_source_characters"] = sorted(self.excluded_sources, key=ord)
            settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
            save_manager_settings(self.settings_path, settings)
            self.status_var.set(f"已更新 UIF 映射，共 {len(self.mapping)} 組。下次執行遊戲時由 UIF 載入新設定。")
            messagebox.showinfo("更新完成", "uif_config.json 已更新。\n請重新啟動遊戲，讓 UIF 重新載入設定。", parent=self)
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("更新失敗", str(exc), parent=self)


# -------------------------------- CLI --------------------------------

def _command_scan(args: argparse.Namespace) -> int:
    translation = Path(args.translations)
    source = Path(args.source) if args.source else None
    report = Path(args.output) if args.output else translation.resolve().parent / (translation.resolve().name + "_char_report")
    result = scan_project(
        translation, source, report, Path(args.config), Path(args.settings),
        text_encoding=args.text_encoding,
        command_encoding=args.command_encoding,
        dialogue_encoding=args.dialogue_encoding,
    )
    paths = {
        "character_report": report / "character_report.csv",
        "glyph_report": report / "cp932_glyphs.csv",
        "candidate_report": report / "safe_candidate_glyphs.csv",
        "mapping_template": report / "mapping_template.csv",
        "unmapped_txt": report / "unmapped_characters.txt",
        "metadata_report": report / "scan_metadata.json",
    }
    print(f"翻譯檔案：{result.scanned_translation_files}")
    print(f"原始檔案：{result.scanned_source_files}")
    print(f"統計字元：{len(result.stats)}")
    print(f"CP932 不支援且尚未映射：{len(result.missing_targets)}")
    print(f"可用替代來源候選：{len(result.safe_candidates)}")
    print(f"報表資料夾：{result.report_root}")
    for name, path in paths.items():
        print(f"{name}: {path}")
    if result.errors:
        print(f"警告：{len(result.errors)} 個檔案未能讀取／解析。")
        for error in result.errors[:30]:
            print(f"  {error}")
    if source is None:
        print("注意：未指定原始腳本資料夾，候選字元尚未排除遊戲原文已使用字元；不可直接視為安全候選。")
    return 0


def _command_apply(args: argparse.Namespace) -> int:
    original_chars: set[str] | None = None
    if not args.source:
        raise CharacterManagerError("套用映射時必須指定 --source 原始遊戲腳本資料夾，重新確認候選來源沒有出現在原文。")
    _used, _count, _skipped, errors = _scan_original_root(
        Path(args.source), args.command_encoding, args.dialogue_encoding
    )
    if errors or _count == 0:
        detail = "\n".join(errors[:20]) if errors else "沒有找到可讀取的原始腳本／文字檔。"
        raise CharacterManagerError("原始腳本掃描不完整，拒絕套用：\n" + detail)
    original_chars = _used
    mapping = apply_mapping_csv(
        Path(args.mapping), Path(args.config), Path(args.settings), original_chars
    )
    print(f"已更新 UIF 映射：{len(mapping)} 組。")
    print(f"設定檔：{args.config}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="統計翻譯文字、建立 CP932 日文漢字／符號候選及管理 UIF 替換映射。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="掃描翻譯文檔並產生 CSV 報表")
    scan_parser.add_argument("--translations", required=True, help="翻譯 JSON／TXT 資料夾或單一檔案")
    scan_parser.add_argument("--source", help="原始 ADVSCRIPT2／日文文字資料夾，用於排除已使用字元")
    scan_parser.add_argument("--output", help="報表輸出資料夾")
    scan_parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="UIF uif_config.json 路徑")
    scan_parser.add_argument("--settings", default=str(DEFAULT_SETTINGS), help="char_manager.json 路徑")
    scan_parser.add_argument("--text-encoding", default="utf-8-sig", help="TXT／CSV 等文字文件編碼")
    scan_parser.add_argument("--command-encoding", default="cp932", help="ADVSCRIPT2 指令／場景編碼")
    scan_parser.add_argument("--dialogue-encoding", default="auto", help="ADVSCRIPT2 對話編碼")
    scan_parser.set_defaults(func=_command_scan)

    apply_parser = sub.add_parser("apply", help="讀取 mapping_template.csv 並更新 UIF 映射")
    apply_parser.add_argument("--mapping", required=True, help="已填寫來源字元的 mapping_template.csv")
    apply_parser.add_argument("--source", required=True, help="原始遊戲腳本資料夾；套用前會再次檢查來源字元是否已被使用")
    apply_parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="UIF uif_config.json 路徑")
    apply_parser.add_argument("--settings", default=str(DEFAULT_SETTINGS), help="char_manager.json 路徑")
    apply_parser.add_argument("--command-encoding", default="cp932", help="ADVSCRIPT2 指令／場景編碼")
    apply_parser.add_argument("--dialogue-encoding", default="auto", help="ADVSCRIPT2 對話編碼")
    apply_parser.set_defaults(func=_command_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (CharacterManagerError, OSError, UnicodeError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
