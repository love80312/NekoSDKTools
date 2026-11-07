#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools 字元統計與 CP932 替代字元管理工具。

翻譯文件是唯一必要掃描來源；不需要原始遊戲腳本。
同時提供命令列掃描及可嵌入 GUI.py 的 CharacterManagerFrame。
Python 3.10+，僅使用標準函式庫。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import re
import sys
import tempfile
import threading
import unicodedata
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
    report_root: Path
    translation_text_encoding: str
    stats: dict[str, CharacterStat]
    glyphs: list[Glyph]
    scanned_translation_files: int
    translation_scan_complete: bool
    errors: list[str]
    mapping: dict[str, str] = field(default_factory=dict)
    excluded_sources: set[str] = field(default_factory=set)

    @property
    def source_root(self) -> None:
        """保留舊版介面相容性；目前不掃描原始遊戲腳本。"""
        return None

    @property
    def original_characters(self) -> set[str]:
        """原始遊戲文字不再是必要掃描來源。"""
        return set()

    @property
    def source_scan_complete(self) -> bool:
        """保留舊版介面相容性。"""
        return True

    @property
    def scanned_source_files(self) -> int:
        return 0

    @property
    def mapped_targets(self) -> set[str]:
        return set(self.mapping.values())

    @property
    def missing_targets(self) -> list[CharacterStat]:
        mapped = self.mapped_targets
        return sorted(
            (
                stat for char, stat in self.stats.items()
                if not can_encode(char, "cp932")
                and char not in mapped
            ),
            key=lambda item: (-item.count, ord(item.character)),
        )

    @property
    def safe_candidates(self) -> list[Glyph]:
        used = set(self.stats)
        mapped = set(self.mapping) | set(self.mapping.values())
        excluded = self.excluded_sources

        return [
            glyph for glyph in self.glyphs
            if is_safe_source_character(glyph.character)
            and glyph.character not in used
            and glyph.character not in mapped
            and glyph.character not in excluded
        ]


# ---------------------------------------------------------------------------
# JSON 註解及 UIF 設定
# ---------------------------------------------------------------------------

def strip_json_comments(source: str) -> str:
    """移除 UIF JSON 註解，同時保留字串內容、換行及字元偏移。"""
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
                if (
                    source[i] == "*"
                    and i + 1 < len(source)
                    and source[i + 1] == "/"
                ):
                    out[i] = out[i + 1] = " "
                    i += 2
                    break

                if source[i] not in "\r\n":
                    out[i] = " "
                i += 1
            else:
                raise CharacterManagerError(
                    "uif_config.json 有未結束的區塊註解。"
                )
            continue

        i += 1

    return "".join(out)


def load_json_with_comments(path: Path) -> tuple[Any, str]:
    try:
        original = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CharacterManagerError(
            f"無法讀取設定檔：{path}\n{exc}"
        ) from exc

    try:
        data = json.loads(strip_json_comments(original))
    except json.JSONDecodeError as exc:
        raise CharacterManagerError(
            f"JSON 格式錯誤：{path}\n"
            f"第 {exc.lineno} 行，第 {exc.colno} 欄：{exc.msg}"
        ) from exc

    return data, original


def can_encode(character: str, encoding: str = "cp932") -> bool:
    try:
        character.encode(encoding, errors="strict")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


# ---------------------------------------------------------------------------
# 字元分類
# ---------------------------------------------------------------------------

def _is_kana(character: str) -> bool:
    cp = ord(character)

    return (
        0x3041 <= cp <= 0x3096
        or 0x3097 <= cp <= 0x309F
        or 0x3031 <= cp <= 0x3035
        or 0x30A1 <= cp <= 0x30FA
        or 0x30FD <= cp <= 0x30FF
        or 0x31F0 <= cp <= 0x31FF
        or 0xFF66 <= cp <= 0xFF9F
        or 0x1B000 <= cp <= 0x1B16F
        or 0x1AFF0 <= cp <= 0x1AFFF
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


def is_report_character(character: str) -> bool:
    """保留漢字、標點及符號，排除英文、假名、數字及空白。"""
    if len(character) != 1:
        return False

    if character.isspace() or _is_kana(character):
        return False

    name = unicodedata.name(character, "")

    if name.startswith("LATIN "):
        return False

    if character.isascii() and character.isalpha():
        return False

    if unicodedata.category(character).startswith("N"):
        return False

    category = unicodedata.category(character)

    return (
        _is_han(character)
        or category.startswith(("P", "S"))
    )


def is_safe_source_character(character: str) -> bool:
    """預設來源候選只使用非 ASCII 漢字、標點或符號。"""
    return (
        len(character) == 1
        and ord(character) > 0x7F
        and is_report_character(character)
    )


def _glyph_category(character: str) -> str:
    if _is_han(character):
        return "漢字"

    category = unicodedata.category(character)

    if category.startswith("P"):
        return "標點符號"

    if category.startswith("S"):
        return "符號"

    return category


# ---------------------------------------------------------------------------
# UIF 映射讀寫
# ---------------------------------------------------------------------------

def load_uif_mapping(config_path: Path) -> dict[str, str]:
    if not config_path.is_file():
        raise CharacterManagerError(
            f"找不到 UIF 設定檔：{config_path}"
        )

    data, _ = load_json_with_comments(config_path)

    if not isinstance(data, dict):
        raise CharacterManagerError(
            "uif_config.json 根節點必須是物件。"
        )

    substitution = data.get("character_substitution", {})

    if not isinstance(substitution, dict):
        raise CharacterManagerError(
            "character_substitution 設定格式錯誤。"
        )

    sources = substitution.get("source_characters", "")
    targets = substitution.get("target_characters", "")

    if not isinstance(sources, str) or not isinstance(targets, str):
        raise CharacterManagerError(
            "source_characters / target_characters 必須是字串。"
        )

    source_list = list(sources)
    target_list = list(targets)

    if len(source_list) != len(target_list):
        raise CharacterManagerError(
            "source_characters 與 target_characters 長度不一致。"
        )

    if len(set(source_list)) != len(source_list):
        raise CharacterManagerError(
            "source_characters 有重複字元。"
        )

    if len(set(target_list)) != len(target_list):
        raise CharacterManagerError(
            "target_characters 有重複字元。"
        )

    return dict(zip(source_list, target_list))


def _validate_mapping(mapping: dict[str, str]) -> None:
    for source, target in mapping.items():
        if len(source) != 1 or len(target) != 1:
            raise CharacterManagerError(
                "每組映射的來源與目標都必須各是一個 Unicode 字元。"
            )

        if not can_encode(source, "cp932"):
            raise CharacterManagerError(
                f"來源字元 {source!r} 無法以 CP932 編碼。"
            )

        if not is_safe_source_character(source):
            raise CharacterManagerError(
                f"來源字元 {source!r} 不屬於預設安全的非 ASCII 漢字／符號範圍。"
            )

        if can_encode(target, "cp932"):
            raise CharacterManagerError(
                f"目標字元 {target!r} 本身可用 CP932 編碼，不需要替換。"
            )

    sources = set(mapping)
    targets = set(mapping.values())

    if len(sources) != len(mapping):
        raise CharacterManagerError("映射來源有重複字元。")

    if len(targets) != len(mapping):
        raise CharacterManagerError("映射目標有重複字元。")

    if sources & targets:
        raise CharacterManagerError(
            "來源字元與目標字元不能交叉重複，以免形成替換鏈。"
        )


def _replace_json_string_property(
    source: str,
    cleaned: str,
    key: str,
    value: str,
) -> str:
    pattern = re.compile(
        r'("' + re.escape(key) + r'"\s*:\s*)"(?:\\.|[^"\\])*"'
    )

    matches = list(pattern.finditer(cleaned))

    if len(matches) != 1:
        raise CharacterManagerError(
            f"uif_config.json 中應恰好有一個 {key} 欄位，"
            f"實際找到 {len(matches)} 個。"
        )

    match = matches[0]

    encoded = json.dumps(value, ensure_ascii=False)

    return (
        source[:match.start(0)]
        + match.group(1)
        + encoded
        + source[match.end(0):]
    )


def write_uif_mapping(
    config_path: Path,
    mapping: dict[str, str],
) -> None:
    """保留原本 UIF JSON 註解及排版，只更新兩個映射欄位。"""
    if not config_path.is_file():
        raise CharacterManagerError(
            f"找不到 UIF 設定檔：{config_path}"
        )

    _validate_mapping(mapping)

    original = config_path.read_text(encoding="utf-8-sig")
    cleaned = strip_json_comments(original)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise CharacterManagerError(
            f"UIF 設定檔格式錯誤：{exc}"
        ) from exc

    if (
        not isinstance(parsed, dict)
        or not isinstance(parsed.get("character_substitution"), dict)
    ):
        raise CharacterManagerError(
            "目前 uif_config.json 沒有 character_substitution 區段；"
            "本版本不會自動轉換成 text_processor 格式。"
        )

    updated = _replace_json_string_property(
        original,
        cleaned,
        "source_characters",
        "".join(mapping.keys()),
    )

    cleaned_updated = strip_json_comments(updated)

    updated = _replace_json_string_property(
        updated,
        cleaned_updated,
        "target_characters",
        "".join(mapping.values()),
    )

    # 寫入前再次驗證 JSON 及映射長度。
    final_data = json.loads(strip_json_comments(updated))
    final_section = final_data["character_substitution"]

    if (
        len(final_section["source_characters"])
        != len(final_section["target_characters"])
    ):
        raise CharacterManagerError(
            "更新後的 UIF 映射長度不一致，取消寫入。"
        )

    backup = config_path.with_suffix(config_path.suffix + ".bak")

    if not backup.exists():
        backup.write_text(
            original,
            encoding="utf-8",
            newline="\n",
        )

    temp_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(updated)

        os.replace(temp_name, config_path)

    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# 字元管理設定
# ---------------------------------------------------------------------------

def load_manager_settings(path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "version": 1,
        "excluded_source_characters": [],
        "excluded_target_characters": [],
        "text_encoding": "utf-8-sig",
    }

    if not path.is_file():
        return defaults

    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(
            f"字元管理設定檔無法讀取：{path}\n{exc}"
        ) from exc

    if not isinstance(value, dict):
        raise CharacterManagerError(
            "char_manager.json 根節點必須是物件。"
        )

    for key in (
        "excluded_source_characters",
        "excluded_target_characters",
    ):
        values = value.get(key, [])

        if (
            not isinstance(values, list)
            or any(
                not isinstance(item, str) or len(item) != 1
                for item in values
            )
        ):
            raise CharacterManagerError(
                f"char_manager.json 的 {key} 格式錯誤。"
            )

        defaults[key] = list(dict.fromkeys(values))

    if isinstance(value.get("text_encoding"), str):
        defaults["text_encoding"] = value["text_encoding"]

    return defaults


def save_manager_settings(
    path: Path,
    settings: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(
                settings,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")

        os.replace(temp_name, path)

    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# 翻譯文件掃描
# ---------------------------------------------------------------------------

def iter_text_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        if root.suffix.lower() in TRANSLATION_EXTENSIONS:
            yield root
        return

    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.as_posix().casefold(),
    ):
        if path.is_symlink():
            continue

        if path.is_file() and path.suffix.lower() in TRANSLATION_EXTENSIONS:
            yield path


def _collect_json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value

    elif isinstance(value, list):
        for item in value:
            yield from _collect_json_strings(item)

    elif isinstance(value, dict):
        # JSON key 通常是欄位名稱或識別碼，不列入翻譯字元統計。
        for item in value.values():
            yield from _collect_json_strings(item)


def _load_text_document(
    path: Path,
    text_encoding: str,
) -> list[str]:
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise CharacterManagerError(
                f"JSON 讀取失敗：{path}\n{exc}"
            ) from exc

        return list(_collect_json_strings(value))

    try:
        return [
            path.read_text(encoding=text_encoding)
        ]
    except (OSError, UnicodeError, LookupError) as exc:
        raise CharacterManagerError(
            f"文字文件讀取失敗：{path}\n"
            f"編碼 {text_encoding!r}：{exc}"
        ) from exc


def scan_translation_root(
    root: Path,
    text_encoding: str,
) -> tuple[dict[str, CharacterStat], int, list[str]]:
    stats: dict[str, CharacterStat] = {}
    scanned = 0
    errors: list[str] = []

    for path in iter_text_files(root):
        try:
            strings = _load_text_document(
                path,
                text_encoding,
            )

            scanned += 1

            if root.is_dir():
                relative = str(path.relative_to(root))
            else:
                relative = path.name

            for text_value in strings:
                for character in text_value:
                    if not is_report_character(character):
                        continue

                    stat = stats.setdefault(
                        character,
                        CharacterStat(character),
                    )

                    stat.count += 1
                    stat.files.add(relative)

        except Exception as exc:
            errors.append(
                f"翻譯資料：{path}：{type(exc).__name__}: {exc}"
            )

    return stats, scanned, errors


# ---------------------------------------------------------------------------
# CP932 字形目錄
# ---------------------------------------------------------------------------

def build_cp932_glyph_catalog() -> list[Glyph]:
    """列舉 CP932 可解碼的一字元漢字、標點及符號。"""
    byte_map: dict[str, bytes] = {}

    def add(raw: bytes) -> None:
        try:
            decoded = raw.decode(
                "cp932",
                errors="strict",
            )
        except UnicodeDecodeError:
            return

        if len(decoded) != 1:
            return

        if not is_report_character(decoded):
            return

        previous = byte_map.get(decoded)

        if previous is None or len(raw) < len(previous):
            byte_map[decoded] = raw

    for first in range(256):
        add(bytes((first,)))

    for first in range(256):
        for second in range(256):
            add(bytes((first, second)))

    glyphs: list[Glyph] = []

    for character, raw in byte_map.items():
        glyphs.append(
            Glyph(
                character=character,
                codepoint=f"U+{ord(character):04X}",
                name=unicodedata.name(
                    character,
                    "未命名字元",
                ),
                category=_glyph_category(character),
                cp932_hex=raw.hex(" ").upper(),
            )
        )

    return sorted(
        glyphs,
        key=lambda item: (
            ord(item.character),
            item.cp932_hex,
        ),
    )


# ---------------------------------------------------------------------------
# 報表
# ---------------------------------------------------------------------------

def _write_csv(
    path: Path,
    headers: list[str],
    rows: Iterable[Iterable[Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_reports(result: ScanResult) -> dict[str, Path]:
    output = result.report_root
    output.mkdir(parents=True, exist_ok=True)

    mapped_targets = result.mapped_targets

    character_rows = []

    for character, stat in sorted(
        result.stats.items(),
        key=lambda pair: (
            -pair[1].count,
            ord(pair[0]),
        ),
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
            (
                "已映射"
                if character in mapped_targets
                else ("不需替換" if supported else "待替換")
            ),
        ])

    character_report = output / "character_report.csv"

    _write_csv(
        character_report,
        [
            "字元",
            "Unicode",
            "Unicode 名稱",
            "類別",
            "出現次數",
            "使用檔案數",
            "CP932 可編碼",
            "狀態",
        ],
        character_rows,
    )

    mapped_sources = set(result.mapping)
    mapped_targets = set(result.mapping.values())
    excluded = result.excluded_sources
    safe = {
        glyph.character
        for glyph in result.safe_candidates
    }

    glyph_rows = []

    for glyph in result.glyphs:
        char = glyph.character

        glyph_rows.append([
            char,
            glyph.codepoint,
            glyph.name,
            glyph.category,
            glyph.cp932_hex,
            "未掃描",
            "是" if char in mapped_sources else "否",
            "是" if char in mapped_targets else "否",
            "是" if char in excluded else "否",
            "是" if char in safe else "否",
        ])

    glyph_report = output / "cp932_glyphs.csv"

    _write_csv(
        glyph_report,
        [
            "字元",
            "Unicode",
            "Unicode 名稱",
            "類別",
            "CP932 位元組",
            "原文已使用",
            "已作為來源",
            "已作為目標",
            "人工排除",
            "可用候選",
        ],
        glyph_rows,
    )

    candidate_report = output / "safe_candidate_glyphs.csv"

    _write_csv(
        candidate_report,
        [
            "來源候選字元",
            "Unicode",
            "Unicode 名稱",
            "類別",
            "CP932 位元組",
        ],
        (
            [
                glyph.character,
                glyph.codepoint,
                glyph.name,
                glyph.category,
                glyph.cp932_hex,
            ]
            for glyph in result.safe_candidates
        ),
    )

    missing = result.missing_targets

    mapping_template = output / "mapping_template.csv"

    _write_csv(
        mapping_template,
        [
            "目標中文字",
            "目標 Unicode",
            "出現次數",
            "來源候選字元（自行填入）",
            "來源 Unicode（參考）",
            "備註",
        ],
        (
            [
                stat.character,
                f"U+{ord(stat.character):04X}",
                stat.count,
                "",
                "",
                "",
            ]
            for stat in missing
        ),
    )

    unmapped_txt = output / "unmapped_characters.txt"

    unmapped_txt.write_text(
        "".join(stat.character for stat in missing),
        encoding="utf-8",
    )

    metadata_report = output / "scan_metadata.json"

    metadata = {
        "translation_root": str(result.translation_root),
        "translation_text_encoding": result.translation_text_encoding,
        "translation_files_scanned": result.scanned_translation_files,
        "translation_scan_complete": result.translation_scan_complete,
        "source_root": None,
        "source_files_scanned": 0,
        "source_scan_complete": True,
        "errors": result.errors,
    }

    metadata_report.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
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


# ---------------------------------------------------------------------------
# 掃描流程
# ---------------------------------------------------------------------------

def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def scan_project(
    translation_root: Path,
    source_root: Path | None = None,
    report_root: Path | None = None,
    config_path: Path | None = None,
    settings_path: Path | None = None,
    *,
    text_encoding: str = "utf-8-sig",
    command_encoding: str = "cp932",
    dialogue_encoding: str = "auto",
) -> ScanResult:
    """掃描翻譯文件；保留舊參數相容性，但不讀取原始腳本或 UIF 映射。"""
    translation_root = translation_root.expanduser().resolve()

    if not translation_root.exists():
        raise CharacterManagerError(
            f"翻譯資料夾／檔案不存在：{translation_root}"
        )

    if not translation_root.is_dir():
        raise CharacterManagerError(
            "目前請選擇翻譯資料夾，不接受單一檔案作為 GUI 掃描來源。"
        )

    if report_root is None:
        report_root = (
            translation_root.parent
            / (translation_root.name + "_char_report")
        )

    report_root = report_root.expanduser().resolve()

    if _path_is_within(report_root, translation_root):
        raise CharacterManagerError(
            "報表輸出資料夾不能放在翻譯資料夾內。"
        )

    settings = load_manager_settings(
        settings_path or DEFAULT_SETTINGS
    )

    stats, scanned, errors = scan_translation_root(
        translation_root,
        text_encoding,
    )

    if scanned == 0:
        raise CharacterManagerError(
            "指定位置沒有找到支援的翻譯文件（JSON／TXT／CSV／TSV／PO／FTL 等）。"
        )

    glyphs = build_cp932_glyph_catalog()

    result = ScanResult(
        translation_root=translation_root,
        report_root=report_root,
        translation_text_encoding=text_encoding,
        stats=stats,
        glyphs=glyphs,
        scanned_translation_files=scanned,
        translation_scan_complete=not errors,
        errors=errors,
        mapping={},
        excluded_sources=set(
            settings["excluded_source_characters"]
        ),
    )

    write_reports(result)

    return result


# ---------------------------------------------------------------------------
# 映射合併與 CSV 套用
# ---------------------------------------------------------------------------

def merge_mapping(
    base: dict[str, str],
    incoming: dict[str, str],
    label: str = "映射",
) -> dict[str, str]:
    merged = dict(base)

    for source, target in incoming.items():
        previous = merged.get(source)

        if previous is not None and previous != target:
            raise CharacterManagerError(
                f"{label}衝突：來源 {source!r} 已對應 "
                f"{previous!r}，不能改為 {target!r}。"
            )

        for other_source, other_target in merged.items():
            if other_source != source and other_target == target:
                raise CharacterManagerError(
                    f"{label}衝突：目標 {target!r} 已經被使用。"
                )

        if target in merged and target != source:
            raise CharacterManagerError(
                f"{label}衝突：目標 {target!r} "
                "同時是其他映射的來源。"
            )

        merged[source] = target

    _validate_mapping(merged)

    return merged


def apply_mapping_csv(
    mapping_csv: Path,
    config_path: Path,
    settings_path: Path,
) -> dict[str, str]:
    """重新掃描翻譯文件後，驗證 CSV 並套用 UIF。"""
    current = load_uif_mapping(config_path)
    settings = load_manager_settings(settings_path)

    metadata_path = mapping_csv.with_name("scan_metadata.json")

    if not metadata_path.is_file():
        raise CharacterManagerError(
            "找不到 scan_metadata.json；請保留完整報表資料夾。"
        )

    try:
        metadata = json.loads(
            metadata_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(
            f"無法讀取掃描狀態：{exc}"
        ) from exc

    if (
        not isinstance(metadata, dict)
        or not metadata.get("translation_scan_complete")
    ):
        raise CharacterManagerError(
            "翻譯文件掃描不完整，拒絕套用映射。"
        )

    translation_root = Path(
        str(metadata.get("translation_root", ""))
    )

    encoding = str(
        metadata.get(
            "translation_text_encoding",
            "utf-8-sig",
        )
    )

    if not translation_root.is_dir():
        raise CharacterManagerError(
            f"原翻譯資料夾已不存在：{translation_root}"
        )

    stats, count, errors = scan_translation_root(
        translation_root,
        encoding,
    )

    if count == 0 or errors:
        raise CharacterManagerError(
            "重新掃描翻譯文件失敗，拒絕套用：\n"
            + "\n".join(errors[:10])
        )

    additions: dict[str, str] = {}

    try:
        with mapping_csv.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            reader = csv.DictReader(handle)

            required = {
                "目標中文字",
                "來源候選字元（自行填入）",
            }

            if (
                not reader.fieldnames
                or not required.issubset(reader.fieldnames)
            ):
                raise CharacterManagerError(
                    "映射 CSV 欄位不正確，請使用 mapping_template.csv。"
                )

            for row_no, row in enumerate(reader, start=2):
                target = (
                    row.get("目標中文字") or ""
                ).strip()

                source = (
                    row.get("來源候選字元（自行填入）") or ""
                ).strip()

                if not source:
                    continue

                if len(source) != 1 or len(target) != 1:
                    raise CharacterManagerError(
                        f"CSV 第 {row_no} 列：來源與目標都必須各是一個字元。"
                    )

                if source in stats:
                    raise CharacterManagerError(
                        f"來源字元 {source!r} 已出現在翻譯文件中。"
                    )

                if source in settings["excluded_source_characters"]:
                    raise CharacterManagerError(
                        f"來源字元 {source!r} 已列入人工排除清單。"
                    )

                additions[source] = target

    except OSError as exc:
        raise CharacterManagerError(
            f"無法讀取映射 CSV：{exc}"
        ) from exc

    merged = merge_mapping(
        current,
        additions,
        "映射",
    )

    write_uif_mapping(
        config_path,
        merged,
    )

    return merged


# ---------------------------------------------------------------------------
# 草稿原子寫入
# ---------------------------------------------------------------------------

def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name

            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

            handle.write("\n")

        os.replace(temp_name, path)

    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class CharacterManagerFrame(ttk.Frame):
    """字元管理 GUI；不需要原始遊戲腳本。"""

    def __init__(
        self,
        master: tk.Misc,
        app_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)

        self.app_dir = (app_dir or APP_DIR).resolve()

        self.config_path = (
            self.app_dir / "tool" / "uif_config.json"
        )

        self.settings_path = (
            self.app_dir / "tool" / "char_manager.json"
        )

        self.last_work_path = (
            self.app_dir / "tool" / "char_manager_last_work.json"
        )

        self.result: ScanResult | None = None
        self.mapping: dict[str, str] = {}

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.excluded_sources: set[str] = set()

        self._resume_draft: dict[str, Any] | None = None
        self._draft_path: Path | None = None

        self._loaded_uif_sources: set[str] = set()
        self._removed_uif_sources: set[str] = set()
        self._uif_was_loaded = False

        try:
            self.initial_settings = load_manager_settings(
                self.settings_path
            )
            self.excluded_sources = set(
                self.initial_settings["excluded_source_characters"]
            )
        except CharacterManagerError:
            self.initial_settings = {
                "version": 1,
                "excluded_source_characters": [],
                "excluded_target_characters": [],
                "text_encoding": "utf-8-sig",
            }

        self._build_ui()
        self._refresh_resume_button()

        self.after(120, self._poll_events)

    # ----- GUI 建立 -----

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        paths = ttk.LabelFrame(
            self,
            text="掃描來源",
        )
        paths.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 7),
        )
        paths.columnconfigure(1, weight=1)

        self.translation_var = tk.StringVar()
        self.encoding_var = tk.StringVar(
            value=self.initial_settings.get(
                "text_encoding",
                "utf-8-sig",
            )
        )

        ttk.Label(
            paths,
            text="翻譯文檔：",
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )

        ttk.Entry(
            paths,
            textvariable=self.translation_var,
        ).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=6,
            pady=4,
        )

        ttk.Button(
            paths,
            text="選擇資料夾",
            command=self._pick_translation,
        ).grid(
            row=0,
            column=4,
            padx=6,
            pady=4,
        )

        ttk.Label(
            paths,
            text="翻譯文字編碼：",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )

        ttk.Combobox(
            paths,
            textvariable=self.encoding_var,
            width=14,
            values=(
                "utf-8-sig",
                "utf-8",
                "cp932",
                "gbk",
                "big5",
            ),
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=6,
            pady=4,
        )

        self.scan_button = ttk.Button(
            paths,
            text="開始掃描與統計",
            command=self._start_scan,
        )

        self.scan_button.grid(
            row=0,
            column=5,
            rowspan=2,
            padx=8,
            pady=4,
            sticky="ns",
        )

        ttk.Label(
            self,
            text=(
                "只統計漢字及特殊符號；英文、日文假名、數字與空白會排除。"
                "候選會排除翻譯已使用字元、既有映射及人工排除字元。"
                "不需要原始遊戲腳本；CP932 可編碼不代表遊戲字型一定有字形。"
            ),
            foreground="#555555",
            wraplength=1000,
        ).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, 6),
        )

        # 大型字形預覽；次數、檔案數及 Unicode 欄位維持一般字級。
        preview = ttk.Frame(self)
        preview.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(0, 6),
        )
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        target_preview_box = ttk.LabelFrame(
            preview,
            text="待替換目標字形預覽",
        )
        target_preview_box.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 4),
        )

        source_preview_box = ttk.LabelFrame(
            preview,
            text="CP932 來源字形預覽",
        )
        source_preview_box.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(4, 0),
        )

        self.target_preview_var = tk.StringVar(
            value="尚未選取"
        )
        self.source_preview_var = tk.StringVar(
            value="尚未選取"
        )

        preview_font = (
            "Microsoft JhengHei UI",
            34,
        )

        ttk.Label(
            target_preview_box,
            textvariable=self.target_preview_var,
            font=preview_font,
            anchor="center",
        ).pack(
            fill="both",
            expand=True,
            padx=8,
            pady=4,
        )

        ttk.Label(
            source_preview_box,
            textvariable=self.source_preview_var,
            font=preview_font,
            anchor="center",
        ).pack(
            fill="both",
            expand=True,
            padx=8,
            pady=4,
        )

        panes = ttk.Panedwindow(
            self,
            orient="horizontal",
        )

        panes.grid(
            row=3,
            column=0,
            sticky="nsew",
        )

        target_box = ttk.LabelFrame(
            panes,
            text="待替換中文字（CP932 不支援）",
        )

        candidate_box = ttk.LabelFrame(
            panes,
            text="CP932 來源候選（不掃描原始遊戲腳本）",
        )

        panes.add(target_box, weight=1)
        panes.add(candidate_box, weight=1)

        target_box.rowconfigure(0, weight=1)
        target_box.columnconfigure(0, weight=1)

        candidate_box.rowconfigure(1, weight=1)
        candidate_box.columnconfigure(0, weight=1)

        self.target_tree = ttk.Treeview(
            target_box,
            columns=("count", "files", "unicode"),
            show="tree headings",
            height=8,
            selectmode="browse",
        )

        self.target_tree.heading(
            "#0",
            text="目標字元",
        )

        for col, title, width in (
            ("count", "次數", 65),
            ("files", "檔案數", 65),
            ("unicode", "Unicode", 95),
        ):
            self.target_tree.heading(
                col,
                text=title,
            )
            self.target_tree.column(
                col,
                width=width,
                anchor="center",
                stretch=False,
            )

        self.target_tree.column(
            "#0",
            width=84,
            anchor="center",
        )

        self.target_tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        target_scroll = ttk.Scrollbar(
            target_box,
            orient="vertical",
            command=self.target_tree.yview,
        )

        target_scroll.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.target_tree.configure(
            yscrollcommand=target_scroll.set
        )

        search_row = ttk.Frame(candidate_box)
        search_row.grid(
            row=0,
            column=0,
            sticky="ew",
        )

        ttk.Label(
            search_row,
            text="搜尋候選：",
        ).pack(
            side="left",
            padx=4,
        )

        self.candidate_filter_var = tk.StringVar()

        ttk.Entry(
            search_row,
            textvariable=self.candidate_filter_var,
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=4,
            pady=4,
        )

        self.candidate_filter_var.trace_add(
            "write",
            lambda *_: self._refresh_candidate_tree(),
        )

        self.candidate_tree = ttk.Treeview(
            candidate_box,
            columns=("unicode", "name", "bytes"),
            show="tree headings",
            height=8,
            selectmode="browse",
        )

        self.candidate_tree.heading(
            "#0",
            text="來源字元",
        )
        self.candidate_tree.heading(
            "unicode",
            text="Unicode",
        )
        self.candidate_tree.heading(
            "name",
            text="Unicode 名稱",
        )
        self.candidate_tree.heading(
            "bytes",
            text="CP932",
        )

        self.candidate_tree.column(
            "#0",
            width=70,
            anchor="center",
            stretch=False,
        )
        self.candidate_tree.column(
            "unicode",
            width=90,
            anchor="center",
            stretch=False,
        )
        self.candidate_tree.column(
            "name",
            width=240,
            anchor="w",
        )
        self.candidate_tree.column(
            "bytes",
            width=80,
            anchor="center",
            stretch=False,
        )

        self.candidate_tree.grid(
            row=1,
            column=0,
            sticky="nsew",
        )

        candidate_scroll = ttk.Scrollbar(
            candidate_box,
            orient="vertical",
            command=self.candidate_tree.yview,
        )

        candidate_scroll.grid(
            row=1,
            column=1,
            sticky="ns",
        )

        self.candidate_tree.configure(
            yscrollcommand=candidate_scroll.set
        )

        self.target_tree.bind(
            "<<TreeviewSelect>>",
            self._update_previews,
            add="+",
        )

        self.candidate_tree.bind(
            "<<TreeviewSelect>>",
            self._update_previews,
            add="+",
        )

        # 表格文字略微放大，字形本身由上方獨立預覽區放大。
        style = ttk.Style(self)
        style.configure(
            "Treeview",
            font=("Microsoft JhengHei UI", 10),
            rowheight=25,
        )
        style.configure(
            "Treeview.Heading",
            font=("Microsoft JhengHei UI", 10, "bold"),
        )

        actions = ttk.Frame(self)
        actions.grid(
            row=4,
            column=0,
            sticky="ew",
            pady=6,
        )

        ttk.Button(
            actions,
            text="建立配對 →",
            command=self._add_pair,
        ).pack(
            side="left",
            padx=3,
        )

        ttk.Button(
            actions,
            text="移除配對",
            command=self._remove_pair,
        ).pack(
            side="left",
            padx=3,
        )

        ttk.Button(
            actions,
            text="排除選取來源字元",
            command=self._exclude_candidate,
        ).pack(
            side="left",
            padx=3,
        )

        ttk.Button(
            actions,
            text="清空人工排除",
            command=self._clear_exclusions,
        ).pack(
            side="left",
            padx=3,
        )

        workflow = ttk.Frame(self)
        workflow.grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(0, 6),
        )

        ttk.Button(
            workflow,
            text="讀取 UIF 映射…",
            command=self._load_uif_mapping_dialog,
        ).pack(
            side="left",
            padx=3,
        )

        ttk.Button(
            workflow,
            text="匯出兩行 TXT…",
            command=self._export_two_line_mapping,
        ).pack(
            side="left",
            padx=3,
        )

        ttk.Button(
            workflow,
            text="保存工作草稿",
            command=self._save_mapping_draft,
        ).pack(
            side="left",
            padx=3,
        )

        self.resume_button = ttk.Button(
            workflow,
            text="繼續上次工作…",
            command=self._resume_last_work,
            state="disabled",
        )

        self.resume_button.pack(
            side="right",
            padx=3,
        )

        ttk.Button(
            workflow,
            text="套用至 uif_config.json",
            command=self._apply_mapping,
        ).pack(
            side="right",
            padx=3,
        )

        mapping_box = ttk.LabelFrame(
            self,
            text="目前替換映射（來源 → 目標）",
        )

        mapping_box.grid(
            row=6,
            column=0,
            sticky="ew",
        )

        mapping_box.columnconfigure(0, weight=1)

        self.mapping_tree = ttk.Treeview(
            mapping_box,
            columns=(
                "target",
                "source_unicode",
                "target_unicode",
                "state",
            ),
            show="tree headings",
            height=5,
            selectmode="browse",
        )

        self.mapping_tree.heading(
            "#0",
            text="來源字元",
        )

        for col, title, width in (
            ("target", "目標字元", 95),
            ("source_unicode", "來源 U碼", 95),
            ("target_unicode", "目標 U碼", 95),
            ("state", "狀態", 100),
        ):
            self.mapping_tree.heading(
                col,
                text=title,
            )
            self.mapping_tree.column(
                col,
                width=width,
                anchor="center",
            )

        self.mapping_tree.column(
            "#0",
            width=95,
            anchor="center",
        )

        self.mapping_tree.grid(
            row=0,
            column=0,
            sticky="ew",
        )

        mapping_scroll = ttk.Scrollbar(
            mapping_box,
            orient="vertical",
            command=self.mapping_tree.yview,
        )

        mapping_scroll.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.mapping_tree.configure(
            yscrollcommand=mapping_scroll.set
        )

        self.mapping_tree.bind(
            "<<TreeviewSelect>>",
            self._update_previews,
            add="+",
        )

        self.status_var = tk.StringVar(
            value="請選擇翻譯文檔資料夾。"
        )

        ttk.Label(
            self,
            textvariable=self.status_var,
            foreground="#555555",
            wraplength=1000,
        ).grid(
            row=7,
            column=0,
            sticky="ew",
            pady=(6, 0),
        )

        self._refresh_mapping_tree()
        self._update_previews()

    # ----- 基本操作 -----

    def _pick_translation(self) -> None:
        value = filedialog.askdirectory(
            title="選擇翻譯文檔資料夾",
            parent=self,
        )

        if value:
            self.translation_var.set(value)

    def _update_previews(self, _event: Any = None) -> None:
        target = "尚未選取"
        source = "尚未選取"

        try:
            selection = self.target_tree.selection()

            if selection:
                target = chr(
                    int(selection[0][1:])
                )
        except (ValueError, IndexError, tk.TclError):
            pass

        try:
            selection = self.candidate_tree.selection()

            if selection:
                source = chr(
                    int(selection[0][1:])
                )
        except (ValueError, IndexError, tk.TclError):
            pass

        try:
            selection = self.mapping_tree.selection()

            if selection:
                item = self.mapping_tree.item(selection[0])
                source = str(item.get("text", source))
                values = item.get("values", ())

                if values:
                    target = str(values[0])
        except (IndexError, tk.TclError):
            pass

        self.target_preview_var.set(target)
        self.source_preview_var.set(source)

    def _save_settings_from_ui(self) -> None:
        settings = load_manager_settings(
            self.settings_path
        )

        settings["excluded_source_characters"] = sorted(
            self.excluded_sources,
            key=ord,
        )

        settings["text_encoding"] = (
            self.encoding_var.get().strip() or "utf-8-sig"
        )

        save_manager_settings(
            self.settings_path,
            settings,
        )

    # ----- 掃描 -----

    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        translation_text = (
            self.translation_var.get().strip().strip('"')
        )

        translation = Path(
            translation_text
        ).expanduser()

        if not translation.is_dir():
            messagebox.showerror(
                "無法掃描",
                "請指定存在的翻譯文檔資料夾。",
                parent=self,
            )
            return

        # 同一資料夾重掃時保留目前工作，掃描完成後再驗證恢復。
        if (
            self.result is not None
            and self.mapping
            and self._resume_draft is None
        ):
            try:
                same = (
                    translation.resolve()
                    == self.result.translation_root.resolve()
                )
            except OSError:
                same = False

            if same:
                self._resume_draft = self._draft_payload()
            else:
                self._save_mapping_draft(
                    show_message=False
                )

        try:
            settings = load_manager_settings(
                self.settings_path
            )

            settings["text_encoding"] = (
                self.encoding_var.get().strip() or "utf-8-sig"
            )

            save_manager_settings(
                self.settings_path,
                settings,
            )

            self.excluded_sources = set(
                settings["excluded_source_characters"]
            )

        except CharacterManagerError as exc:
            messagebox.showerror(
                "設定錯誤",
                str(exc),
                parent=self,
            )
            return

        report = (
            translation.parent
            / (translation.name + "_char_report")
        )

        self.scan_button.configure(
            state="disabled"
        )

        self.status_var.set(
            "掃描中：讀取翻譯文件及建立 CP932 字形目錄…"
        )

        encoding = (
            self.encoding_var.get().strip() or "utf-8-sig"
        )

        def worker() -> None:
            try:
                result = scan_project(
                    translation,
                    None,
                    report,
                    None,
                    self.settings_path,
                    text_encoding=encoding,
                )

                self.events.put(
                    ("scan_done", result)
                )

            except Exception as exc:
                self.events.put(
                    (
                        "scan_error",
                        f"{type(exc).__name__}: {exc}",
                    )
                )

        self.worker = threading.Thread(
            target=worker,
            name="NekoCharacterScan",
            daemon=True,
        )

        self.worker.start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()

                if kind == "scan_error":
                    self.scan_button.configure(
                        state="normal"
                    )

                    self.status_var.set("掃描失敗")

                    messagebox.showerror(
                        "掃描失敗",
                        str(payload),
                        parent=self,
                    )

                elif kind == "scan_done":
                    self.scan_button.configure(
                        state="normal"
                    )

                    self.result = payload

                    self.excluded_sources = set(
                        payload.excluded_sources
                    )

                    if self._resume_draft is not None:
                        try:
                            (
                                self.mapping,
                                self._loaded_uif_sources,
                            ) = self._validate_restored_mapping(
                                self._resume_draft
                            )

                        except CharacterManagerError as exc:
                            self.mapping = {}
                            self._loaded_uif_sources.clear()
                            self._removed_uif_sources.clear()
                            self._uif_was_loaded = False

                            messagebox.showwarning(
                                "工作草稿部分無法恢復",
                                f"{exc}\n\n"
                                "已保留本次掃描結果，"
                                "請重新建立發生衝突的映射。",
                                parent=self,
                            )

                        finally:
                            self._resume_draft = None

                    else:
                        # 初次掃描工作區保持空白，不自動讀取 UIF。
                        self.mapping = {}
                        self._loaded_uif_sources.clear()
                        self._removed_uif_sources.clear()
                        self._uif_was_loaded = False

                    self._refresh_all()

                    missing_count = len(
                        self._current_missing_targets()
                    )

                    self.status_var.set(
                        f"完成：翻譯檔 {payload.scanned_translation_files} 個，"
                        f"待替換字元 {missing_count} 個，"
                        f"可用候選 {len(payload.safe_candidates)} 個。\n"
                        f"不需要原始遊戲腳本。報表：{payload.report_root}"
                    )

                    if payload.errors:
                        messagebox.showwarning(
                            "部分檔案無法讀取",
                            "以下檔案未能完整納入統計：\n\n"
                            + "\n".join(payload.errors[:20]),
                            parent=self,
                        )

                    self._save_mapping_draft(
                        show_message=False
                    )

        except queue.Empty:
            pass

        self.after(
            120,
            self._poll_events,
        )

    # ----- 表格更新 -----

    def _current_missing_targets(self) -> list[CharacterStat]:
        if self.result is None:
            return []

        mapped = (
            set(self.mapping.values())
            | set(self.mapping.keys())
        )

        try:
            settings = load_manager_settings(
                self.settings_path
            )

            excluded = set(
                settings["excluded_target_characters"]
            )

        except CharacterManagerError:
            excluded = set()

        return sorted(
            (
                stat
                for character, stat in self.result.stats.items()
                if not can_encode(character, "cp932")
                and character not in mapped
                and character not in excluded
            ),
            key=lambda item: (
                -item.count,
                ord(item.character),
            ),
        )

    def _refresh_all(self) -> None:
        for tree in (
            self.target_tree,
            self.candidate_tree,
            self.mapping_tree,
        ):
            tree.delete(
                *tree.get_children()
            )

        if self.result is None:
            self._update_previews()
            return

        for stat in self._current_missing_targets():
            self.target_tree.insert(
                "",
                "end",
                iid=f"t{ord(stat.character)}",
                text=stat.character,
                values=(
                    stat.count,
                    len(stat.files),
                    f"U+{ord(stat.character):04X}",
                ),
            )

        self._refresh_candidate_tree()
        self._refresh_mapping_tree()
        self._update_previews()

    def _refresh_candidate_tree(self) -> None:
        if self.result is None:
            return

        self.candidate_tree.delete(
            *self.candidate_tree.get_children()
        )

        query = (
            self.candidate_filter_var.get()
            .strip()
            .casefold()
        )

        mapped_sources = set(self.mapping)
        mapped_targets = set(self.mapping.values())

        available = [
            glyph
            for glyph in self.result.glyphs
            if is_safe_source_character(glyph.character)
            and glyph.character not in self.result.stats
            and glyph.character not in mapped_sources
            and glyph.character not in mapped_targets
            and glyph.character not in self.excluded_sources
        ]

        for glyph in available:
            searchable = " ".join(
                (
                    glyph.character,
                    glyph.codepoint,
                    glyph.name,
                    glyph.category,
                )
            ).casefold()

            if query and query not in searchable:
                continue

            self.candidate_tree.insert(
                "",
                "end",
                iid=f"c{ord(glyph.character)}",
                text=glyph.character,
                values=(
                    glyph.codepoint,
                    glyph.name,
                    glyph.cp932_hex,
                ),
            )

    def _refresh_mapping_tree(self) -> None:
        self.mapping_tree.delete(
            *self.mapping_tree.get_children()
        )

        for source, target in self.mapping.items():
            state = (
                "UIF 既有"
                if source in self._loaded_uif_sources
                else "本次新增"
            )

            self.mapping_tree.insert(
                "",
                "end",
                iid=f"m{ord(source)}",
                text=source,
                values=(
                    target,
                    f"U+{ord(source):04X}",
                    f"U+{ord(target):04X}",
                    state,
                ),
            )

    # ----- 建立及移除映射 -----

    def _add_pair(self) -> None:
        if self.result is None:
            messagebox.showwarning(
                "尚未掃描",
                "請先掃描翻譯文檔。",
                parent=self,
            )
            return

        target_selection = self.target_tree.selection()
        source_selection = self.candidate_tree.selection()

        if not target_selection or not source_selection:
            messagebox.showwarning(
                "尚未選擇",
                "請分別選擇一個目標中文字及一個 CP932 來源字元。",
                parent=self,
            )
            return

        target = chr(
            int(target_selection[0][1:])
        )

        source = chr(
            int(source_selection[0][1:])
        )

        if source in self.mapping:
            messagebox.showerror(
                "映射衝突",
                f"來源 {source!r} 已經使用。",
                parent=self,
            )
            return

        if target in self.mapping.values() or target in self.mapping:
            messagebox.showerror(
                "映射衝突",
                f"目標 {target!r} 已經使用，請重新選擇。",
                parent=self,
            )
            return

        if source in self.result.stats:
            messagebox.showerror(
                "來源字元衝突",
                f"翻譯文件已使用 {source!r}，"
                "UIF 會連正常文字一起替換。",
                parent=self,
            )
            return

        if (
            not can_encode(source, "cp932")
            or not is_safe_source_character(source)
        ):
            messagebox.showerror(
                "來源字元無效",
                f"{source!r} 不是安全的 CP932 候選。",
                parent=self,
            )
            return

        if can_encode(target, "cp932"):
            messagebox.showerror(
                "目標字元無效",
                f"{target!r} 本身可由 CP932 編碼，不需要替換。",
                parent=self,
            )
            return

        self.mapping[source] = target

        self._refresh_all()

        self._save_mapping_draft(
            show_message=False
        )

    def _remove_pair(self) -> None:
        selected = self.mapping_tree.selection()

        if not selected:
            return

        source = chr(
            int(selected[0][1:])
        )

        self.mapping.pop(source, None)

        if source in self._loaded_uif_sources:
            self._removed_uif_sources.add(source)

        self._refresh_all()

        self._save_mapping_draft(
            show_message=False
        )

    # ----- 人工排除 -----

    def _exclude_candidate(self) -> None:
        selected = self.candidate_tree.selection()

        if not selected:
            messagebox.showinfo(
                "未選擇字元",
                "請先選擇要排除的來源候選字元。",
                parent=self,
            )
            return

        character = chr(
            int(selected[0][1:])
        )

        self.excluded_sources.add(character)

        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "儲存失敗",
                str(exc),
                parent=self,
            )
            return

        self._refresh_candidate_tree()

        self.status_var.set(
            f"已將 {character} 加入人工排除清單。"
        )

        self._save_mapping_draft(
            show_message=False
        )

    def _clear_exclusions(self) -> None:
        if not self.excluded_sources:
            messagebox.showinfo(
                "排除清單",
                "目前沒有人工排除的來源字元。",
                parent=self,
            )
            return

        if not messagebox.askyesno(
            "清空人工排除",
            f"確定清除 {len(self.excluded_sources)} 個人工排除字元？",
            parent=self,
        ):
            return

        self.excluded_sources.clear()

        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "儲存失敗",
                str(exc),
                parent=self,
            )
            return

        self._refresh_candidate_tree()

        self.status_var.set(
            "已清空人工排除清單。"
        )

        self._save_mapping_draft(
            show_message=False
        )

    # ----- 工作草稿 -----

    @staticmethod
    def _atomic_write_json(
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        atomic_write_json(
            path,
            payload,
        )

    def _draft_payload(self) -> dict[str, Any]:
        if self.result is None:
            raise CharacterManagerError(
                "尚未掃描，沒有可保存的工作資料。"
            )

        return {
            "version": 2,
            "translation_root": str(self.result.translation_root),
            "report_root": str(self.result.report_root),
            "translation_text_encoding": (
                self.encoding_var.get().strip() or "utf-8-sig"
            ),
            "config_path": str(self.config_path),
            "mapping": [
                {
                    "source": source,
                    "target": target,
                }
                for source, target in self.mapping.items()
            ],
            "loaded_uif_sources": sorted(
                self._loaded_uif_sources,
                key=ord,
            ),
            "removed_uif_sources": sorted(
                self._removed_uif_sources,
                key=ord,
            ),
            "uif_was_loaded": self._uif_was_loaded,
        }

    def _save_mapping_draft(
        self,
        show_message: bool = True,
    ) -> None:
        if self.result is None:
            if show_message:
                messagebox.showwarning(
                    "尚未掃描",
                    "請先掃描翻譯文檔並產生字表。",
                    parent=self,
                )
            return

        draft_path = (
            self.result.report_root / "mapping_draft.json"
        )

        try:
            self._atomic_write_json(
                draft_path,
                self._draft_payload(),
            )

            self._atomic_write_json(
                self.last_work_path,
                {
                    "version": 1,
                    "draft_path": str(draft_path),
                },
            )

            self._draft_path = draft_path
            self._refresh_resume_button()

            self.status_var.set(
                f"工作草稿已保存：{draft_path}"
            )

            if show_message:
                messagebox.showinfo(
                    "保存完成",
                    f"工作草稿已保存。\n\n{draft_path}",
                    parent=self,
                )

        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "保存失敗",
                str(exc),
                parent=self,
            )

    def _read_last_draft(
        self,
    ) -> tuple[Path, dict[str, Any]]:
        if not self.last_work_path.is_file():
            raise CharacterManagerError(
                "找不到上次工作的索引檔。"
            )

        try:
            pointer = json.loads(
                self.last_work_path.read_text(
                    encoding="utf-8-sig"
                )
            )

            if (
                not isinstance(pointer, dict)
                or pointer.get("version") != 1
            ):
                raise ValueError(
                    "索引檔版本或格式錯誤。"
                )

            draft_path = Path(
                pointer["draft_path"]
            ).expanduser()

            draft = json.loads(
                draft_path.read_text(
                    encoding="utf-8-sig"
                )
            )

        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise CharacterManagerError(
                f"無法讀取上次工作草稿：{exc}"
            ) from exc

        if (
            not isinstance(draft, dict)
            or draft.get("version") not in (1, 2)
        ):
            raise CharacterManagerError(
                "工作草稿版本不支援或格式錯誤。"
            )

        if (
            not isinstance(draft.get("mapping"), list)
            or not draft.get("translation_root")
        ):
            raise CharacterManagerError(
                "工作草稿缺少必要欄位。"
            )

        return draft_path, draft

    def _refresh_resume_button(self) -> None:
        available = False

        if self.last_work_path.is_file():
            try:
                pointer = json.loads(
                    self.last_work_path.read_text(
                        encoding="utf-8-sig"
                    )
                )

                available = Path(
                    pointer.get("draft_path", "")
                ).is_file()

            except (
                OSError,
                ValueError,
                TypeError,
                AttributeError,
                json.JSONDecodeError,
            ):
                available = False

        self.resume_button.configure(
            state="normal" if available else "disabled"
        )

    def _resume_last_work(self) -> None:
        try:
            draft_path, draft = self._read_last_draft()

            translation = Path(
                str(draft["translation_root"])
            ).expanduser()

            if not translation.is_dir():
                raise CharacterManagerError(
                    f"上次使用的翻譯資料夾不存在：\n{translation}"
                )

            config_path = Path(
                str(draft.get("config_path", self.config_path))
            ).expanduser()

            if not config_path.is_file():
                raise CharacterManagerError(
                    f"上次使用的 UIF 設定檔不存在：\n{config_path}"
                )

            self.translation_var.set(
                str(translation)
            )

            self.encoding_var.set(
                str(
                    draft.get(
                        "translation_text_encoding",
                        "utf-8-sig",
                    )
                )
            )

            self.config_path = config_path.resolve()
            self._draft_path = draft_path
            self._resume_draft = draft

            # 續作時重新掃描翻譯文件，不掃描原始遊戲腳本。
            self._start_scan()

        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "無法繼續工作",
                str(exc),
                parent=self,
            )

    def _validate_restored_mapping(
        self,
        draft: dict[str, Any],
    ) -> tuple[dict[str, str], set[str]]:
        if self.result is None:
            raise CharacterManagerError(
                "沒有最新掃描結果，無法恢復映射。"
            )

        pairs = draft.get("mapping", [])
        loaded = draft.get("loaded_uif_sources", [])
        removed = draft.get("removed_uif_sources", [])

        if not isinstance(loaded, list) or any(
            not isinstance(ch, str) or len(ch) != 1
            for ch in loaded
        ):
            raise CharacterManagerError(
                "草稿的 UIF 已載入來源清單格式錯誤。"
            )

        if not isinstance(removed, list) or any(
            not isinstance(ch, str) or len(ch) != 1
            for ch in removed
        ):
            raise CharacterManagerError(
                "草稿的 UIF 移除來源清單格式錯誤。"
            )

        loaded_set = set(loaded)
        removed_set = set(removed)

        if not removed_set.issubset(loaded_set):
            raise CharacterManagerError(
                "草稿的移除清單不屬於已載入的 UIF 映射。"
            )

        restored: dict[str, str] = {}

        if not isinstance(pairs, list):
            raise CharacterManagerError(
                "草稿 mapping 必須是陣列。"
            )

        for index, pair in enumerate(pairs, start=1):
            if not isinstance(pair, dict):
                raise CharacterManagerError(
                    f"草稿第 {index} 組映射格式錯誤。"
                )

            source = pair.get("source")
            target = pair.get("target")

            if (
                not isinstance(source, str)
                or len(source) != 1
                or not isinstance(target, str)
                or len(target) != 1
            ):
                raise CharacterManagerError(
                    f"草稿第 {index} 組來源／目標必須各是一個字元。"
                )

            if source in restored or target in restored.values():
                raise CharacterManagerError(
                    "草稿存在重複來源或目標字元。"
                )

            if (
                not can_encode(source, "cp932")
                or not is_safe_source_character(source)
            ):
                raise CharacterManagerError(
                    f"草稿來源 {source!r} 不是安全的 CP932 候選。"
                )

            # 新建立的來源字元必須確認沒有出現在最新翻譯文件中。
            if (
                source not in loaded_set
                and source in self.result.stats
            ):
                raise CharacterManagerError(
                    f"來源 {source!r} 已出現在最新翻譯文件中，"
                    "拒絕恢復此新映射。"
                )

            if can_encode(target, "cp932"):
                raise CharacterManagerError(
                    f"目標 {target!r} 本身可用 CP932 編碼，"
                    "拒絕恢復此映射。"
                )

            restored[source] = target

        _validate_mapping(restored)

        self._removed_uif_sources = removed_set
        self._uif_was_loaded = bool(
            draft.get(
                "uif_was_loaded",
                bool(loaded_set),
            )
        )

        return restored, loaded_set

    # ----- UIF 既有映射手動載入 -----

    def _load_uif_mapping_dialog(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇 UIF uif_config.json",
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
            filetypes=[
                ("UIF 設定檔", "uif_config.json"),
                ("JSON", "*.json"),
                ("所有檔案", "*.*"),
            ],
            parent=self,
        )

        if not value:
            return

        try:
            if self.result is None:
                raise CharacterManagerError(
                    "請先掃描翻譯文檔，再載入既有映射。"
                )

            incoming = load_uif_mapping(
                Path(value)
            )

            for source, target in incoming.items():
                if (
                    not can_encode(source, "cp932")
                    or not is_safe_source_character(source)
                ):
                    raise CharacterManagerError(
                        f"UIF 來源 {source!r} 不是安全的 CP932 漢字／符號。"
                    )

                if can_encode(target, "cp932"):
                    raise CharacterManagerError(
                        f"UIF 目標 {target!r} 本身可由 CP932 編碼。"
                    )

            merged = merge_mapping(
                self.mapping,
                incoming,
                "UIF 映射",
            )

            self.config_path = (
                Path(value).expanduser().resolve()
            )

            self.mapping = merged
            self._loaded_uif_sources.update(incoming)
            self._removed_uif_sources.difference_update(incoming)
            self._uif_was_loaded = True

            self._refresh_all()

            self._save_mapping_draft(
                show_message=False
            )

            self.status_var.set(
                f"已手動載入 UIF 映射 {len(incoming)} 組；"
                f"工作區共 {len(self.mapping)} 組。"
            )

        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "載入 UIF 映射失敗",
                str(exc),
                parent=self,
            )

    # ----- 匯出兩行字串 -----

    def _export_two_line_mapping(self) -> None:
        if not self.mapping:
            messagebox.showwarning(
                "沒有映射",
                "目前沒有可匯出的替換映射。",
                parent=self,
            )
            return

        value = filedialog.asksaveasfilename(
            title="匯出兩行替換字串",
            initialdir=str(
                self.result.report_root
                if self.result
                else self.app_dir
            ),
            initialfile="replacement_mapping.txt",
            defaultextension=".txt",
            filetypes=[
                ("文字檔", "*.txt"),
                ("所有檔案", "*.*"),
            ],
            parent=self,
        )

        if not value:
            return

        sources = "".join(self.mapping.keys())
        targets = "".join(self.mapping.values())

        if len(sources) != len(targets):
            messagebox.showerror(
                "映射錯誤",
                "來源及目標字元數量不一致，取消匯出。",
                parent=self,
            )
            return

        try:
            with Path(value).open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(sources + "\n")
                handle.write(targets + "\n")

            self.status_var.set(
                f"兩行替換字串已匯出：{value}"
            )

            messagebox.showinfo(
                "匯出完成",
                "第一行：source_characters\n"
                "第二行：target_characters",
                parent=self,
            )

        except OSError as exc:
            messagebox.showerror(
                "匯出失敗",
                str(exc),
                parent=self,
            )

    # ----- 明確套用 UIF -----

    def _apply_mapping(self) -> None:
        if self.result is None:
            messagebox.showwarning(
                "尚未掃描",
                "請先掃描翻譯文檔。",
                parent=self,
            )
            return

        if not self.result.translation_scan_complete:
            messagebox.showerror(
                "掃描不完整",
                "翻譯文件掃描不完整，拒絕套用替換映射。",
                parent=self,
            )
            return

        if not self.mapping:
            messagebox.showwarning(
                "沒有映射",
                "目前沒有可套用的替換映射。",
                parent=self,
            )
            return

        try:
            # 只有使用者按下「套用」後，才讀取正式 UIF 設定。
            # 若使用者沒有手動載入既有映射，仍會保留設定檔原有映射。
            existing = load_uif_mapping(
                self.config_path
            )

            if self._uif_was_loaded:
                existing = {
                    source: target
                    for source, target in existing.items()
                    if source not in self._removed_uif_sources
                }

            new_sources = (
                set(self.mapping) - set(existing)
            )

            for source in new_sources:
                if source in self.result.stats:
                    raise CharacterManagerError(
                        f"新來源 {source!r} 已出現在翻譯文件中。"
                    )

                if source in self.excluded_sources:
                    raise CharacterManagerError(
                        f"來源 {source!r} 已列入人工排除清單。"
                    )

            merged = merge_mapping(
                existing,
                self.mapping,
                "工作映射與 UIF 設定",
            )

            if not messagebox.askyesno(
                "確認更新 UIF 設定",
                f"即將寫入 {len(merged)} 組映射。\n"
                f"新增 {len(new_sources)} 組；"
                f"保留既有 {len(existing)} 組。\n\n"
                f"設定檔：\n{self.config_path}\n\n"
                "原設定會先備份成 .bak。是否繼續？",
                parent=self,
            ):
                return

            write_uif_mapping(
                self.config_path,
                merged,
            )

            self.mapping = merged
            self._loaded_uif_sources = set(merged)
            self._removed_uif_sources.clear()
            self._uif_was_loaded = True

            self._refresh_all()

            self._save_mapping_draft(
                show_message=False
            )

            self.status_var.set(
                f"已更新 UIF 映射，共 {len(merged)} 組。"
            )

            messagebox.showinfo(
                "更新完成",
                "uif_config.json 已更新並建立備份。\n"
                "請重新啟動遊戲，讓 UIF 重新載入設定。",
                parent=self,
            )

        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "更新失敗",
                str(exc),
                parent=self,
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _command_scan(args: argparse.Namespace) -> int:
    translation = Path(args.translations)

    report = (
        Path(args.output)
        if args.output
        else (
            translation.resolve().parent
            / (translation.resolve().name + "_char_report")
        )
    )

    result = scan_project(
        translation,
        None,
        report,
        None,
        Path(args.settings),
        text_encoding=args.text_encoding,
    )

    print(f"翻譯檔案：{result.scanned_translation_files}")
    print(f"統計字元：{len(result.stats)}")
    print(f"CP932 不支援且尚未映射：{len(result.missing_targets)}")
    print(f"CP932 候選字元：{len(result.safe_candidates)}")
    print(f"報表資料夾：{result.report_root}")

    for name, path in write_reports(result).items():
        print(f"{name}: {path}")

    if result.errors:
        print(
            f"警告：{len(result.errors)} 個檔案未能讀取。"
        )

        for error in result.errors[:30]:
            print("  " + error)

    return 0


def _command_apply(args: argparse.Namespace) -> int:
    mapping = apply_mapping_csv(
        Path(args.mapping),
        Path(args.config),
        Path(args.settings),
    )

    print(
        f"已更新 UIF 映射：{len(mapping)} 組。"
    )
    print(
        f"設定檔：{args.config}"
    )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="統計翻譯文字及管理 CP932 替代字元映射。"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    scan = sub.add_parser(
        "scan",
        help="掃描翻譯文件並產生 CSV 報表",
    )

    scan.add_argument(
        "--translations",
        required=True,
        help="翻譯 JSON／TXT 資料夾",
    )

    scan.add_argument(
        "--output",
        help="報表輸出資料夾",
    )

    scan.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS),
        help="char_manager.json 路徑",
    )

    scan.add_argument(
        "--text-encoding",
        default="utf-8-sig",
        help="TXT／CSV 等翻譯文件編碼",
    )

    scan.set_defaults(
        func=_command_scan
    )

    apply_parser = sub.add_parser(
        "apply",
        help="驗證 mapping_template.csv 並更新 UIF 映射",
    )

    apply_parser.add_argument(
        "--mapping",
        required=True,
        help="已填入來源字元的 mapping_template.csv",
    )

    apply_parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="UIF uif_config.json 路徑",
    )

    apply_parser.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS),
        help="char_manager.json 路徑",
    )

    apply_parser.set_defaults(
        func=_command_apply
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return int(args.func(args))

    except (
        CharacterManagerError,
        OSError,
        UnicodeError,
        LookupError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"error: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
