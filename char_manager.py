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
import hashlib
import json
import os
import queue
import re
import sys
import tempfile
import threading
import unicodedata
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Iterable, Iterator

import scr as script_tool


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "tool" / "uif_config.json"
DEFAULT_SETTINGS = APP_DIR / "tool" / "char_manager.json"

TRANSLATION_EXTENSIONS = {
    ".json", ".txt", ".csv", ".tsv", ".po", ".pot", ".ftl", ".ini",
    ".xml", ".html", ".htm", ".md", ".yaml", ".yml",
}

# 教育部《常用國字標準字體表》4,808 字。
# 第一次執行時下載官方 ODS 並快取成 UTF-8 文字檔；之後可離線使用。
MOE_COMMON_CHAR_COUNT = 4808
MOE_COMMON_CHARACTERS_PATH = APP_DIR / "tool" / "MOE_4808_常用國字.txt"
MOE_COMMON_CHARACTERS_ODS_PATH = APP_DIR / "tool" / "教育部4808個常用字.ods"
MOE_COMMON_CHARACTERS_URL = (
    "https://language.moe.gov.tw/uploads/files/17694982751710.ods"
)
MOE_COMMON_CHARACTERS_INFO_URL = (
    "https://language.moe.gov.tw/material/info?"
    "m=9fe3ff5a-5a8c-4817-9e60-6337dd55a509"
)
CANDIDATE_EXCLUSION_CONFIG_PATH = (
    APP_DIR / "tool" / "char_candidate_exclusions.json"
)
FILTER_KEYS = (
    "moe_4808",
    "zh_punctuation",
    "ja_punctuation",
    "common_symbols",
)
DEFAULT_CANDIDATE_FILTERS = {
    "moe_4808": True,
    "zh_punctuation": True,
    "ja_punctuation": True,
    "common_symbols": True,
}
EXCLUSION_GROUP_LABELS = {
    "zh_punctuation": "中文標點",
    "ja_punctuation": "日文標點",
    "common_symbols": "常用符號",
}
ODS_NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
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
    source_root: Path | None = None
    source_scan_performed: bool = False
    source_scan_complete: bool | None = None
    scanned_source_files: int = 0
    source_stats: dict[str, CharacterStat] = field(default_factory=dict)
    source_errors: list[str] = field(default_factory=list)
    command_encoding: str = "cp932"
    dialogue_encoding: str = "auto"
    candidate_filters: dict[str, bool] = field(
        default_factory=lambda: dict(DEFAULT_CANDIDATE_FILTERS)
    )
    moe_common_characters: set[str] = field(default_factory=set)
    symbol_exclusions: dict[str, set[str]] = field(default_factory=dict)
    custom_excluded_characters: set[str] = field(default_factory=set)
    exclusion_config_path: Path = CANDIDATE_EXCLUSION_CONFIG_PATH
    exclusion_config_sha256: str | None = None
    moe_characters_sha256: str | None = None

    @property
    def original_characters(self) -> set[str]:
        return set(self.source_stats)

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
    def excluded_builtin_characters(self) -> set[str]:
        excluded: set[str] = set()
        if self.candidate_filters.get("moe_4808", True):
            excluded.update(self.moe_common_characters)
        for key in ("zh_punctuation", "ja_punctuation", "common_symbols"):
            if self.candidate_filters.get(key, True):
                excluded.update(self.symbol_exclusions.get(key, set()))
        excluded.update(self.custom_excluded_characters)
        return excluded

    @property
    def safe_candidates(self) -> list[Glyph]:
        # 原文掃描一旦指定，就必須完整；部分掃描結果不可冒充安全候選。
        if self.source_scan_performed and not self.source_scan_complete:
            return []
        return [
            glyph for glyph in self.glyphs
            if not candidate_exclusion_reasons(glyph.character, self)
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

    mapping = dict(zip(source_list, target_list))
    _validate_mapping(mapping)
    return mapping


def _validate_mapping(mapping: dict[str, str]) -> None:
    """只驗證映射結構，不套用新映射的 CP932 規則。

    UIF 歷史映射可能包含 CP932 可編碼的目標字元，也可能有來源與
    目標交集。這些都必須保留；新映射的安全條件由
    _validate_new_mapping() 獨立檢查。
    """
    if not isinstance(mapping, dict):
        raise CharacterManagerError("映射資料必須是字典。")

    sources: set[str] = set()
    targets: set[str] = set()

    for source, target in mapping.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise CharacterManagerError(
                "每組映射的來源與目標都必須是字串。"
            )

        if len(source) != 1 or len(target) != 1:
            raise CharacterManagerError(
                "每組映射的來源與目標都必須各是一個 Unicode 字元。"
            )

        # 既有映射仍必須有可由遊戲 CP932 文字流表示的來源字元。
        # 只放寬舊版錯誤的「目標也必須無法 CP932 編碼」及「禁止交集」限制。
        if not can_encode(source, "cp932"):
            raise CharacterManagerError(
                f"來源字元 {source!r} 無法以 CP932 編碼。"
            )

        if not is_safe_source_character(source):
            raise CharacterManagerError(
                f"來源字元 {source!r} 不屬於安全的非 ASCII 漢字／符號範圍。"
            )

        if source in sources:
            raise CharacterManagerError(
                f"映射來源 {source!r} 重複。"
            )

        if target in targets:
            raise CharacterManagerError(
                f"映射目標 {target!r} 重複。"
            )

        sources.add(source)
        targets.add(target)


def _validate_new_mapping(
    mapping: dict[str, str],
    *,
    translation_characters: Iterable[str] = (),
    source_characters: Iterable[str] = (),
    excluded_sources: Iterable[str] = (),
    excluded_candidate_characters: Iterable[str] = (),
    existing_mapping: dict[str, str] | None = None,
) -> None:
    """僅驗證本次新增映射，不重新限制 UIF 既有映射。"""
    _validate_mapping(mapping)

    used = set(translation_characters)
    source_used = set(source_characters)
    excluded = set(excluded_sources)
    excluded_candidates = set(excluded_candidate_characters)
    existing = existing_mapping or {}
    _validate_mapping(existing)

    existing_targets = set(existing.values())

    for source, target in mapping.items():
        if not can_encode(source, "cp932"):
            raise CharacterManagerError(
                f"新來源字元 {source!r} 無法以 CP932 編碼。"
            )

        if not is_safe_source_character(source):
            raise CharacterManagerError(
                f"新來源字元 {source!r} 不屬於預設安全的非 ASCII 漢字／符號範圍。"
            )

        if can_encode(target, "cp932"):
            raise CharacterManagerError(
                f"新目標字元 {target!r} 本身可用 CP932 編碼，不需要替換。"
            )

        if source in used:
            raise CharacterManagerError(
                f"來源字元 {source!r} 已出現在翻譯文件中。"
            )

        if source in source_used:
            raise CharacterManagerError(
                f"來源字元 {source!r} 已出現在原始遊戲腳本中。"
            )

        if source in excluded_candidates:
            raise CharacterManagerError(
                f"來源字元 {source!r} 命中內建候選排除清單。"
            )

        if source in excluded:
            raise CharacterManagerError(
                f"來源字元 {source!r} 已列入人工排除清單。"
            )

        if source in existing_targets:
            raise CharacterManagerError(
                f"來源字元 {source!r} 已被既有 UIF 映射作為目標，不能再當作新來源。"
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

    # 寫入函式只能做結構驗證；新增映射安全性由呼叫端負責。
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

    # 寫入前再次驗證 JSON 及映射長度、唯一性。
    final_data = json.loads(strip_json_comments(updated))
    final_section = final_data["character_substitution"]

    final_sources = final_section.get("source_characters")
    final_targets = final_section.get("target_characters")

    if not isinstance(final_sources, str) or not isinstance(final_targets, str):
        raise CharacterManagerError(
            "更新後 UIF 的映射欄位不是字串，取消寫入。"
        )

    if len(final_sources) != len(final_targets):
        raise CharacterManagerError(
            "更新後的 UIF 映射長度不一致，取消寫入。"
        )

    _validate_mapping(dict(zip(final_sources, final_targets)))

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
        "version": 2,
        "excluded_source_characters": [],
        "excluded_target_characters": [],
        "text_encoding": "utf-8-sig",
        "source_command_encoding": "cp932",
        "source_dialogue_encoding": "auto",
        "candidate_filters": dict(DEFAULT_CANDIDATE_FILTERS),
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

    if isinstance(value.get("source_command_encoding"), str):
        defaults["source_command_encoding"] = value["source_command_encoding"]

    if isinstance(value.get("source_dialogue_encoding"), str):
        defaults["source_dialogue_encoding"] = value["source_dialogue_encoding"]

    filters = value.get("candidate_filters", {})
    if not isinstance(filters, dict):
        raise CharacterManagerError(
            "char_manager.json 的 candidate_filters 必須是物件。"
        )
    for key in FILTER_KEYS:
        selected = filters.get(key, DEFAULT_CANDIDATE_FILTERS[key])
        if not isinstance(selected, bool):
            raise CharacterManagerError(
                f"char_manager.json 的 candidate_filters.{key} 必須是布林值。"
            )
        defaults["candidate_filters"][key] = selected

    defaults["version"] = 2
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


def scan_source_root(
    root: Path,
    command_encoding: str = "cp932",
    dialogue_encoding: str = "auto",
) -> tuple[dict[str, CharacterStat], int, list[str]]:
    """透過 scr.py 解析 ADVSCRIPT2，統計角色名稱、對話及可辨識選項。"""
    root = root.expanduser().resolve()
    if not root.exists():
        raise CharacterManagerError(
            f"原始遊戲腳本位置不存在：{root}"
        )

    try:
        command_codec = script_tool.validate_encoding(command_encoding)
        dialogue_codec = script_tool.validate_dialogue_encoding(dialogue_encoding)
    except Exception as exc:
        raise CharacterManagerError(
            f"原始腳本編碼設定錯誤：{exc}"
        ) from exc

    if root.is_file():
        files = [root]
    else:
        files = sorted(
            (
                path for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda item: item.as_posix().casefold(),
        )

    stats: dict[str, CharacterStat] = {}
    scanned = 0
    errors: list[str] = []

    for path in files:
        try:
            with path.open("rb") as handle:
                signature = handle.read(len(script_tool.MAGIC))
            if signature != script_tool.MAGIC:
                continue

            parsed = script_tool.read_script(path)
            items = script_tool.export_items(
                parsed,
                command_codec,
                dialogue_codec,
                str(path),
            )
            scanned += 1

            relative = (
                str(path.relative_to(root))
                if root.is_dir()
                else path.name
            )
            for item in items:
                for value in item.values():
                    if not isinstance(value, str):
                        continue
                    for character in value:
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
                f"原始腳本：{path}：{type(exc).__name__}: {exc}"
            )

    if scanned == 0:
        errors.append(
            f"指定位置沒有找到可解析的 NEKOSDK_ADVSCRIPT2 腳本：{root}"
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
# 候選排除清單與教育部 4,808 常用國字
# ---------------------------------------------------------------------------

def _is_cjk_ideograph(character: str) -> bool:
    if len(character) != 1:
        return False
    name = unicodedata.name(character, "")
    return (
        name.startswith("CJK UNIFIED IDEOGRAPH")
        or name.startswith("CJK COMPATIBILITY IDEOGRAPH")
    )


def default_candidate_exclusion_data() -> dict[str, Any]:
    return {
        "version": 1,
        "moe_source": "教育部《常用國字標準字體表》4,808 字",
        "moe_source_url": MOE_COMMON_CHARACTERS_INFO_URL,
        "moe_ods_url": MOE_COMMON_CHARACTERS_URL,
        "moe_characters_file": MOE_COMMON_CHARACTERS_PATH.name,
        "symbol_groups": {
            "zh_punctuation": (
                "，。！？；：、…‥—–「」『』“”‘’"
                "（）〔〕［］【】〈〉《》〖〗〝〞﹁﹂﹃﹄﹑﹔﹕"
            ),
            "ja_punctuation": "、。「」『』・〜〝〟〘〙〚〛",
            "common_symbols": (
                "※〒○●◎☆★♪♬♩♫♭♯△▲▽▼□■◆◇"
                "←↑→↓↔↕±×÷≠≤≥≈∞℃℉￥＄＃％‰"
            ),
        },
        "custom_excluded_characters": "",
    }


def load_candidate_exclusion_data(
    path: Path = CANDIDATE_EXCLUSION_CONFIG_PATH,
) -> dict[str, Any]:
    defaults = default_candidate_exclusion_data()
    if not path.is_file():
        return defaults
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(
            f"候選排除清單無法讀取：{path}\n{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CharacterManagerError(
            "char_candidate_exclusions.json 根節點必須是物件。"
        )

    groups = value.get("symbol_groups", {})
    if not isinstance(groups, dict):
        raise CharacterManagerError(
            "char_candidate_exclusions.json 的 symbol_groups 必須是物件。"
        )
    for key in ("zh_punctuation", "ja_punctuation", "common_symbols"):
        chars = groups.get(key, defaults["symbol_groups"][key])
        if not isinstance(chars, str):
            raise CharacterManagerError(
                f"symbol_groups.{key} 必須是字串。"
            )
        defaults["symbol_groups"][key] = "".join(dict.fromkeys(chars))

    custom = value.get("custom_excluded_characters", "")
    if isinstance(custom, list):
        if any(not isinstance(ch, str) or len(ch) != 1 for ch in custom):
            raise CharacterManagerError(
                "custom_excluded_characters 清單每個項目都必須是一個字元。"
            )
        custom = "".join(custom)
    if not isinstance(custom, str):
        raise CharacterManagerError(
            "custom_excluded_characters 必須是字串或單字元陣列。"
        )
    defaults["custom_excluded_characters"] = "".join(
        dict.fromkeys(ch for ch in custom if not ch.isspace())
    )

    defaults["version"] = 1
    defaults["moe_source"] = str(value.get("moe_source", defaults["moe_source"]))
    defaults["moe_source_url"] = str(
        value.get("moe_source_url", defaults["moe_source_url"])
    )
    defaults["moe_ods_url"] = str(
        value.get("moe_ods_url", defaults["moe_ods_url"])
    )
    defaults["moe_characters_file"] = str(
        value.get("moe_characters_file", defaults["moe_characters_file"])
    )
    return defaults


def _ods_cell_text(cell: ET.Element) -> str:
    paragraphs: list[str] = []
    paragraph_tag = f"{{{ODS_NS['text']}}}p"
    for node in cell.iter(paragraph_tag):
        paragraphs.append("".join(node.itertext()).strip())
    return "".join(paragraphs).strip()


def _read_moe_characters_from_ods(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml_data = archive.read("content.xml")
        root = ET.fromstring(xml_data)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise CharacterManagerError(
            f"教育部 ODS 字表無法解析：{path}\n{exc}"
        ) from exc

    row_tag = f"{{{ODS_NS['table']}}}table-row"
    cell_tags = {
        f"{{{ODS_NS['table']}}}table-cell",
        f"{{{ODS_NS['table']}}}covered-table-cell",
    }
    rows: list[list[str]] = []
    for row in root.iter(row_tag):
        cells: list[str] = []
        for cell in list(row):
            if cell.tag not in cell_tags:
                continue
            repeated = int(
                cell.attrib.get(
                    f"{{{ODS_NS['table']}}}number-columns-repeated",
                    "1",
                )
            )
            repeated = max(1, min(repeated, 100))
            cells.extend([_ods_cell_text(cell)] * repeated)
        rows.append(cells)

    # 優先尋找表頭欄位，避免把說明文字誤納入字表。
    header_row = -1
    character_column = -1
    for row_index, cells in enumerate(rows[:100]):
        for col_index, value in enumerate(cells):
            compact = value.replace(" ", "").replace("\n", "")
            if compact in {"常用字", "常用國字", "國字", "字形"}:
                header_row = row_index
                character_column = col_index
                break
        if character_column >= 0:
            break

    characters: set[str] = set()
    if header_row >= 0 and character_column >= 0:
        for cells in rows[header_row + 1:]:
            if character_column >= len(cells):
                continue
            value = cells[character_column]
            for character in value:
                if _is_cjk_ideograph(character):
                    characters.add(character)

    # 部分 ODS 以每字一格、沒有明確表頭的方式編排；
    # 在整份工作表中找出單字元漢字作為備援。
    if len(characters) != MOE_COMMON_CHAR_COUNT:
        fallback: set[str] = set()
        for cells in rows:
            for value in cells:
                stripped = value.strip()
                if len(stripped) == 1 and _is_cjk_ideograph(stripped):
                    fallback.add(stripped)
        if len(fallback) == MOE_COMMON_CHAR_COUNT:
            characters = fallback

    if len(characters) != MOE_COMMON_CHAR_COUNT:
        raise CharacterManagerError(
            "教育部 ODS 字表驗證失敗："
            f"應有 {MOE_COMMON_CHAR_COUNT} 個不重複漢字，"
            f"實際取得 {len(characters)} 個。"
            "請確認下載的是教育部「教育部4808個常用字.ods」。"
        )
    return characters


def ensure_moe_common_characters() -> set[str]:
    """載入已快取字表；沒有快取時解析本機 ODS 或下載官方 ODS。"""
    if MOE_COMMON_CHARACTERS_PATH.is_file():
        try:
            content = MOE_COMMON_CHARACTERS_PATH.read_text(
                encoding="utf-8-sig"
            )
        except OSError as exc:
            raise CharacterManagerError(
                f"教育部常用字清單無法讀取：{exc}"
            ) from exc

        characters = {
            ch for ch in content
            if _is_cjk_ideograph(ch)
        }
        if len(characters) == MOE_COMMON_CHAR_COUNT:
            return characters
        raise CharacterManagerError(
            "MOE_4808_常用國字.txt 數量錯誤："
            f"應有 4,808 個不重複漢字，實際為 {len(characters)} 個。"
            "請刪除錯誤快取，並重新執行掃描。"
        )

    ods_path = MOE_COMMON_CHARACTERS_ODS_PATH
    if not ods_path.is_file():
        request = urllib.request.Request(
            MOE_COMMON_CHARACTERS_URL,
            headers={"User-Agent": "NekoSDKTools/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
            if not payload.startswith(b"PK"):
                raise CharacterManagerError(
                    "教育部下載網址回傳的內容不是有效 ODS/ZIP 檔案。"
                )
            ods_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = ods_path.with_suffix(".ods.download")
            temporary.write_bytes(payload)
            temporary.replace(ods_path)
        except Exception as exc:
            raise CharacterManagerError(
                "找不到教育部 4,808 常用國字清單，且無法下載官方 ODS。\n"
                f"官方資料頁：{MOE_COMMON_CHARACTERS_INFO_URL}\n"
                "請下載「教育部4808個常用字.ods」，放到：\n"
                f"{ods_path}\n"
                f"詳細錯誤：{exc}"
            ) from exc

    characters = _read_moe_characters_from_ods(ods_path)
    MOE_COMMON_CHARACTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOE_COMMON_CHARACTERS_PATH.write_text(
        "".join(sorted(characters, key=ord)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return characters


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_exclusion_reasons(
    character: str,
    result: ScanResult,
) -> list[str]:
    reasons: list[str] = []
    if not is_safe_source_character(character):
        reasons.append("不符合 CP932 來源字元條件")
    if character in result.stats:
        reasons.append("翻譯文件已使用")
    if result.source_scan_performed and not result.source_scan_complete:
        reasons.append("原始腳本掃描不完整")
    elif result.source_scan_performed and character in result.original_characters:
        reasons.append("原始遊戲腳本已使用")

    if (
        result.candidate_filters.get("moe_4808", True)
        and character in result.moe_common_characters
    ):
        reasons.append("教育部 4,808 常用國字")

    for key, label in EXCLUSION_GROUP_LABELS.items():
        if (
            result.candidate_filters.get(key, True)
            and character in result.symbol_exclusions.get(key, set())
        ):
            reasons.append(label)

    if character in result.custom_excluded_characters:
        reasons.append("自訂排除清單")
    if character in result.excluded_sources:
        reasons.append("人工排除")
    if character in set(result.mapping) | set(result.mapping.values()):
        reasons.append("目前工作映射已占用")
    return reasons


def _load_enabled_candidate_exclusions(
    filter_options: dict[str, bool],
    exclusion_data: dict[str, Any],
) -> tuple[set[str], dict[str, set[str]], set[str], set[str]]:
    moe = (
        ensure_moe_common_characters()
        if filter_options.get("moe_4808", True)
        else set()
    )
    groups = {
        key: set(exclusion_data["symbol_groups"][key])
        for key in ("zh_punctuation", "ja_punctuation", "common_symbols")
    }
    active_symbols: set[str] = set()
    for key in groups:
        if filter_options.get(key, True):
            active_symbols.update(groups[key])
    custom = set(exclusion_data.get("custom_excluded_characters", ""))
    forbidden_builtin = set(active_symbols) | set(custom)
    if filter_options.get("moe_4808", True):
        forbidden_builtin.update(moe)
    return moe, groups, active_symbols, forbidden_builtin


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


def write_reports(
    result: ScanResult,
    *,
    update_mapping_templates: bool = True,
) -> dict[str, Path]:
    output = result.report_root
    output.mkdir(parents=True, exist_ok=True)

    mapped_targets = result.mapped_targets
    character_rows = []
    for character, stat in sorted(
        result.stats.items(),
        key=lambda pair: (-pair[1].count, ord(pair[0])),
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
                "已映射" if character in mapped_targets
                else ("不需替換" if supported else "待替換")
            ),
        ])

    character_report = output / "character_report.csv"
    _write_csv(
        character_report,
        [
            "字元", "Unicode", "Unicode 名稱", "類別",
            "出現次數", "使用檔案數", "CP932 可編碼", "狀態",
        ],
        character_rows,
    )

    source_report: Path | None = None
    if result.source_scan_performed:
        source_rows = []
        for character, stat in sorted(
            result.source_stats.items(),
            key=lambda pair: (-pair[1].count, ord(pair[0])),
        ):
            source_rows.append([
                character,
                f"U+{ord(character):04X}",
                unicodedata.name(character, "未命名字元"),
                _glyph_category(character),
                stat.count,
                len(stat.files),
                "、".join(sorted(stat.files)),
            ])
        source_report = output / "source_character_report.csv"
        _write_csv(
            source_report,
            [
                "原文使用字元", "Unicode", "Unicode 名稱", "類別",
                "出現次數", "使用檔案數", "檔案清單",
            ],
            source_rows,
        )
    else:
        stale_source_report = output / "source_character_report.csv"
        if stale_source_report.exists():
            stale_source_report.unlink()

    mapped_sources = set(result.mapping)
    mapped_targets = set(result.mapping.values())
    glyph_rows = []
    for glyph in result.glyphs:
        char = glyph.character
        reasons = candidate_exclusion_reasons(char, result)
        punctuation_hit = (
            char in result.symbol_exclusions.get("zh_punctuation", set())
            or char in result.symbol_exclusions.get("ja_punctuation", set())
        )
        glyph_rows.append([
            char,
            glyph.codepoint,
            glyph.name,
            glyph.category,
            glyph.cp932_hex,
            "是" if char in result.stats else "否",
            (
                "未掃描"
                if not result.source_scan_performed
                else ("是" if char in result.original_characters else "否")
            ),
            (
                "未篩選"
                if not result.candidate_filters.get("moe_4808", True)
                else ("是" if char in result.moe_common_characters else "否")
            ),
            "是" if punctuation_hit else "否",
            (
                "是"
                if char in result.symbol_exclusions.get("common_symbols", set())
                else "否"
            ),
            "是" if char in result.excluded_sources else "否",
            "是" if char in mapped_sources or char in mapped_targets else "否",
            "是" if not reasons else "否",
            "；".join(reasons),
        ])

    glyph_report = output / "cp932_glyphs.csv"
    _write_csv(
        glyph_report,
        [
            "字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組",
            "翻譯已使用", "原文已使用", "教育部常用字（停用時標示未篩選）",
            "中文／日文標點清單命中", "常用符號清單命中", "人工排除",
            "映射占用", "可用候選", "排除原因",
        ],
        glyph_rows,
    )

    candidate_report = output / "safe_candidate_glyphs.csv"
    _write_csv(
        candidate_report,
        [
            "來源候選字元", "Unicode", "Unicode 名稱",
            "類別", "CP932 位元組",
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
    unmapped_txt = output / "unmapped_characters.txt"

    if update_mapping_templates or not mapping_template.exists():
        _write_csv(
            mapping_template,
            [
                "目標中文字", "目標 Unicode", "出現次數",
                "來源候選字元（自行填入）", "來源 Unicode（參考）", "備註",
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

    if update_mapping_templates or not unmapped_txt.exists():
        unmapped_txt.write_text(
            "".join(stat.character for stat in missing),
            encoding="utf-8",
            newline="\n",
        )



    metadata_report = output / "scan_metadata.json"
    metadata = {
        "version": 2,
        "translation_root": str(result.translation_root),
        "translation_text_encoding": result.translation_text_encoding,
        "translation_files_scanned": result.scanned_translation_files,
        "translation_scan_complete": result.translation_scan_complete,
        "source_root": str(result.source_root) if result.source_root else None,
        "source_scan_performed": result.source_scan_performed,
        "source_scan_complete": result.source_scan_complete,
        "source_files_scanned": result.scanned_source_files,
        "source_command_encoding": result.command_encoding,
        "source_dialogue_encoding": result.dialogue_encoding,
        "candidate_filters": dict(result.candidate_filters),
        "moe_common_characters_count": len(result.moe_common_characters),
        "exclusion_config_path": str(result.exclusion_config_path),
        "exclusion_config_sha256": result.exclusion_config_sha256,
        "moe_characters_sha256": result.moe_characters_sha256,
        "manual_excluded_sources": sorted(result.excluded_sources, key=ord),
        "source_errors": result.source_errors,
        "errors": result.errors,
    }
    metadata_report.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    guide = output / "README_報表說明.txt"
    source_status = (
        (
            f"已指定：{result.source_root}\n"
            f"有效腳本：{result.scanned_source_files}\n"
            f"掃描完整：{'是' if result.source_scan_complete else '否'}"
        )
        if result.source_scan_performed
        else "未指定；本次候選未經原始遊戲腳本占用檢查。"
    )
    guide.write_text(
        "\n".join([
            "NekoSDKTools 字元管理掃描報表",
            "=" * 36,
            "",
            f"翻譯文件：{result.translation_root}",
            f"翻譯檔案：{result.scanned_translation_files}",
            f"翻譯掃描完整：{'是' if result.translation_scan_complete else '否'}",
            "原始遊戲腳本：",
            source_status,
            "",
            "候選排除設定：",
            f"教育部 4,808 常用國字：{'啟用' if result.candidate_filters.get('moe_4808', True) else '停用'}",
            f"中文常用標點：{'啟用' if result.candidate_filters.get('zh_punctuation', True) else '停用'}",
            f"日文常用標點：{'啟用' if result.candidate_filters.get('ja_punctuation', True) else '停用'}",
            f"常用符號：{'啟用' if result.candidate_filters.get('common_symbols', True) else '停用'}",
            "",
            "檔案用途：",
            "character_report.csv：翻譯文件中漢字及特殊符號的使用次數、檔案數與 CP932 狀態。",
            "source_character_report.csv：原始腳本已辨識文字的統計；只有指定原始腳本時才會產生。",
            "cp932_glyphs.csv：完整 CP932 字形目錄，以及各字是否使用、被排除或可用的原因。",
            "safe_candidate_glyphs.csv：依本次掃描及排除條件保留下來的 CP932 來源候選。",
            "mapping_template.csv：命令列手動填入替代來源字元的 CSV 範本。",
            "unmapped_characters.txt：尚未映射的目標中文字純文字清單。",
            "scan_metadata.json：本次掃描路徑、編碼、排除選項與完整性；CSV 套用時會重新驗證。",
            "mapping_draft.json：GUI 保存的工作草稿；有保存工作才會產生。",
            "",
            "注意：候選只代表通過目前掃描及排除條件，不代表遊戲字型一定包含該字形。",
            "若未指定原始遊戲腳本，請自行確認替代來源字元未出現在遊戲原文或其他資源。",
            "若原始腳本掃描不完整，safe_candidate_glyphs.csv 會保持空清單。",
        ]) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result_paths = {
        "character_report": character_report,
        "glyph_report": glyph_report,
        "candidate_report": candidate_report,
        "mapping_template": mapping_template,
        "unmapped_txt": unmapped_txt,
        "metadata_report": metadata_report,
        "report_guide": guide,
    }
    if source_report is not None:
        result_paths["source_report"] = source_report
    return result_paths

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
    candidate_filters: dict[str, bool] | None = None,
) -> ScanResult:
    """掃描翻譯文件及選填的 ADVSCRIPT2 原始腳本。"""
    translation_root = translation_root.expanduser().resolve()
    if not translation_root.exists():
        raise CharacterManagerError(
            f"翻譯資料夾／檔案不存在：{translation_root}"
        )
    if not translation_root.is_dir():
        raise CharacterManagerError(
            "目前請選擇翻譯資料夾，不接受單一檔案作為 GUI 掃描來源。"
        )

    resolved_source: Path | None = None
    if source_root is not None and str(source_root).strip():
        resolved_source = source_root.expanduser().resolve()
        if not resolved_source.exists():
            raise CharacterManagerError(
                f"原始遊戲腳本位置不存在：{resolved_source}"
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
    if resolved_source and resolved_source.is_dir() and _path_is_within(
        report_root, resolved_source
    ):
        raise CharacterManagerError(
            "報表輸出資料夾不能放在原始腳本資料夾內。"
        )

    settings = load_manager_settings(settings_path or DEFAULT_SETTINGS)
    active_filters = dict(settings["candidate_filters"])
    if candidate_filters is not None:
        for key in FILTER_KEYS:
            if key in candidate_filters:
                if not isinstance(candidate_filters[key], bool):
                    raise CharacterManagerError(
                        f"候選排除選項 {key} 必須是布林值。"
                    )
                active_filters[key] = candidate_filters[key]

    exclusion_data = load_candidate_exclusion_data()
    moe_chars, symbol_groups, _active_symbols, _forbidden = (
        _load_enabled_candidate_exclusions(
            active_filters,
            exclusion_data,
        )
    )
    custom_exclusions = set(exclusion_data["custom_excluded_characters"])

    stats, scanned, errors = scan_translation_root(
        translation_root,
        text_encoding,
    )
    if scanned == 0:
        raise CharacterManagerError(
            "指定位置沒有找到支援的翻譯文件（JSON／TXT／CSV／TSV／PO／FTL 等）。"
        )

    source_stats: dict[str, CharacterStat] = {}
    source_scanned = 0
    source_errors: list[str] = []
    if resolved_source is not None:
        source_stats, source_scanned, source_errors = scan_source_root(
            resolved_source,
            command_encoding,
            dialogue_encoding,
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
        excluded_sources=set(settings["excluded_source_characters"]),
        source_root=resolved_source,
        source_scan_performed=resolved_source is not None,
        source_scan_complete=(
            None if resolved_source is None else not source_errors
        ),
        scanned_source_files=source_scanned,
        source_stats=source_stats,
        source_errors=source_errors,
        command_encoding=command_encoding,
        dialogue_encoding=dialogue_encoding,
        candidate_filters=active_filters,
        moe_common_characters=moe_chars,
        symbol_exclusions=symbol_groups,
        custom_excluded_characters=custom_exclusions,
        exclusion_config_path=CANDIDATE_EXCLUSION_CONFIG_PATH,
        exclusion_config_sha256=_file_sha256(CANDIDATE_EXCLUSION_CONFIG_PATH),
        moe_characters_sha256=_file_sha256(MOE_COMMON_CHARACTERS_PATH),
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
    """結構性合併映射；允許既有 source/target 交集。"""
    _validate_mapping(base)
    _validate_mapping(incoming)

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
                    f"{label}衝突：目標 {target!r} 已經被來源 "
                    f"{other_source!r} 使用。"
                )

        merged[source] = target

    _validate_mapping(merged)
    return merged


def apply_mapping_csv(
    mapping_csv: Path,
    config_path: Path,
    settings_path: Path,
) -> dict[str, str]:
    """依掃描快照重新掃描翻譯與選填原始腳本，驗證 CSV 後更新 UIF。"""
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
    ).expanduser()
    encoding = str(
        metadata.get("translation_text_encoding", "utf-8-sig")
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
            + "\n".join(errors[:20])
        )

    filters = metadata.get(
        "candidate_filters",
        DEFAULT_CANDIDATE_FILTERS,
    )
    if not isinstance(filters, dict):
        raise CharacterManagerError(
            "scan_metadata.json 的 candidate_filters 格式錯誤。"
        )
    active_filters = {
        key: filters.get(key, DEFAULT_CANDIDATE_FILTERS[key])
        for key in FILTER_KEYS
    }
    if any(not isinstance(value, bool) for value in active_filters.values()):
        raise CharacterManagerError(
            "scan_metadata.json 的候選篩選設定含有非布林值。"
        )

    exclusion_data = load_candidate_exclusion_data()
    expected_exclusion_hash = metadata.get("exclusion_config_sha256")
    current_exclusion_hash = _file_sha256(CANDIDATE_EXCLUSION_CONFIG_PATH)
    if (
        expected_exclusion_hash is not None
        and expected_exclusion_hash != current_exclusion_hash
    ):
        raise CharacterManagerError(
            "候選排除清單在掃描後已變更。請重新掃描，再套用 CSV。"
        )

    moe_chars, _symbol_groups, _active_symbols, forbidden_builtin = (
        _load_enabled_candidate_exclusions(
            active_filters,
            exclusion_data,
        )
    )
    expected_moe_hash = metadata.get("moe_characters_sha256")
    current_moe_hash = _file_sha256(MOE_COMMON_CHARACTERS_PATH)
    if (
        active_filters.get("moe_4808", True)
        and expected_moe_hash is not None
        and expected_moe_hash != current_moe_hash
    ):
        raise CharacterManagerError(
            "教育部 4,808 常用字快取在掃描後已變更。請重新掃描。"
        )

    source_used: set[str] = set()
    if metadata.get("source_scan_performed"):
        if not metadata.get("source_scan_complete"):
            raise CharacterManagerError(
                "原始腳本在初次掃描時並未完整解析，拒絕套用映射。"
            )
        source_root_value = metadata.get("source_root")
        if not source_root_value:
            raise CharacterManagerError(
                "掃描紀錄標示已掃描原始腳本，但缺少來源路徑。"
            )
        source_root = Path(str(source_root_value)).expanduser()
        if not source_root.exists():
            raise CharacterManagerError(
                f"原始遊戲腳本資料夾已不存在：{source_root}"
            )
        source_stats, source_count, source_errors = scan_source_root(
            source_root,
            str(metadata.get("source_command_encoding", "cp932")),
            str(metadata.get("source_dialogue_encoding", "auto")),
        )
        if source_count == 0 or source_errors:
            raise CharacterManagerError(
                "套用前重新掃描原始遊戲腳本失敗，拒絕更新 UIF：\n"
                + "\n".join(source_errors[:20])
            )
        source_used = set(source_stats)

    latest_settings = load_manager_settings(settings_path)
    excluded_sources = (
        set(latest_settings["excluded_source_characters"])
        | set(exclusion_data.get("custom_excluded_characters", ""))
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
                target = (row.get("目標中文字") or "").strip()
                source = (
                    row.get("來源候選字元（自行填入）") or ""
                ).strip()
                if not source:
                    continue
                if len(source) != 1 or len(target) != 1:
                    raise CharacterManagerError(
                        f"CSV 第 {row_no} 列：來源與目標都必須各是一個字元。"
                    )
                if target not in stats:
                    raise CharacterManagerError(
                        f"CSV 第 {row_no} 列：目標字元 {target!r} 不在本次翻譯掃描結果中。"
                    )
                if source in additions:
                    raise CharacterManagerError(
                        f"CSV 第 {row_no} 列：來源字元 {source!r} 重複。"
                    )
                if target in additions.values():
                    raise CharacterManagerError(
                        f"CSV 第 {row_no} 列：目標字元 {target!r} 重複。"
                    )
                additions[source] = target
    except OSError as exc:
        raise CharacterManagerError(
            f"無法讀取映射 CSV：{exc}"
        ) from exc

    _validate_new_mapping(
        additions,
        translation_characters=stats,
        source_characters=source_used,
        excluded_sources=excluded_sources,
        excluded_candidate_characters=forbidden_builtin,
        existing_mapping=current,
    )
    merged = merge_mapping(current, additions, "映射")
    write_uif_mapping(config_path, merged)
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
        self._resume_warnings: list[str] = []

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
                "version": 2,
                "excluded_source_characters": [],
                "excluded_target_characters": [],
                "text_encoding": "utf-8-sig",
                "source_command_encoding": "cp932",
                "source_dialogue_encoding": "auto",
                "candidate_filters": dict(DEFAULT_CANDIDATE_FILTERS),
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
        self.source_var = tk.StringVar()
        self.encoding_var = tk.StringVar(
            value=self.initial_settings.get(
                "text_encoding",
                "utf-8-sig",
            )
        )
        self.command_encoding_var = tk.StringVar(
            value=self.initial_settings.get(
                "source_command_encoding",
                "cp932",
            )
        )
        self.dialogue_encoding_var = tk.StringVar(
            value=self.initial_settings.get(
                "source_dialogue_encoding",
                "auto",
            )
        )
        saved_filters = self.initial_settings.get(
            "candidate_filters",
            DEFAULT_CANDIDATE_FILTERS,
        )
        self.candidate_filter_vars = {
            key: tk.BooleanVar(
                value=bool(saved_filters.get(key, True))
            )
            for key in FILTER_KEYS
        }

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

        ttk.Label(
            paths,
            text="原始遊戲腳本（選填）：",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )

        ttk.Entry(
            paths,
            textvariable=self.source_var,
        ).grid(
            row=2,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=6,
            pady=4,
        )

        ttk.Button(
            paths,
            text="選擇資料夾",
            command=self._pick_source,
        ).grid(
            row=2,
            column=4,
            padx=6,
            pady=4,
        )

        ttk.Label(
            paths,
            text="原始腳本編碼：",
        ).grid(
            row=3,
            column=0,
            sticky="w",
            padx=6,
            pady=4,
        )

        ttk.Label(
            paths,
            text="指令／場景",
        ).grid(
            row=3,
            column=1,
            sticky="e",
            padx=(6, 2),
            pady=4,
        )

        ttk.Combobox(
            paths,
            textvariable=self.command_encoding_var,
            width=12,
            values=("cp932", "shift_jis", "utf-8", "gbk", "big5"),
        ).grid(
            row=3,
            column=2,
            sticky="w",
            padx=(2, 8),
            pady=4,
        )

        ttk.Label(
            paths,
            text="對話",
        ).grid(
            row=3,
            column=3,
            sticky="e",
            padx=(4, 2),
            pady=4,
        )

        ttk.Combobox(
            paths,
            textvariable=self.dialogue_encoding_var,
            width=10,
            values=("auto", "cp932", "shift_jis", "gbk", "utf-8", "big5"),
        ).grid(
            row=3,
            column=4,
            sticky="w",
            padx=(2, 6),
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
            rowspan=4,
            padx=8,
            pady=4,
            sticky="ns",
        )

        filter_frame = ttk.LabelFrame(
            self,
            text="CP932 來源候選排除",
        )
        filter_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, 6),
        )
        filter_frame.columnconfigure(0, weight=1)
        filter_frame.columnconfigure(1, weight=1)

        filter_labels = {
            "moe_4808": "教育部 4,808 常用國字",
            "zh_punctuation": "中文常用標點、引號及括號",
            "ja_punctuation": "日文常用標點及中點",
            "common_symbols": "星號、圖形、箭頭、數學及單位符號",
        }
        for index, key in enumerate(FILTER_KEYS):
            ttk.Checkbutton(
                filter_frame,
                text=filter_labels[key],
                variable=self.candidate_filter_vars[key],
                command=lambda selected_key=key: self._on_candidate_filter_changed(
                    selected_key
                ),
            ).grid(
                row=index // 2,
                column=index % 2,
                sticky="w",
                padx=8,
                pady=3,
            )

        ttk.Label(
            filter_frame,
            text=(
                "排除規則只影響 CP932 來源候選，不影響翻譯字元統計及待替換目標。"
                "原始遊戲腳本為選填；未指定時不檢查原文占用。"
            ),
            foreground="#555555",
            wraplength=1000,
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=8,
            pady=(2, 5),
        )

        # 大型字形預覽；次數、檔案數及 Unicode 欄位維持一般字級。

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
            text="CP932 來源候選（依排除規則過濾）",
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

        self.candidate_tree.bind(
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

    def _pick_source(self) -> None:
        value = filedialog.askdirectory(
            title="選擇已解包的原始遊戲腳本資料夾（選填）",
            parent=self,
        )
        if value:
            self.source_var.set(value)

    def _current_candidate_filters(self) -> dict[str, bool]:
        return {
            key: bool(variable.get())
            for key, variable in self.candidate_filter_vars.items()
        }

    def _on_candidate_filter_changed(self, changed_key: str) -> None:
        filters = self._current_candidate_filters()
        if self.result is not None:
            if filters.get("moe_4808", True) and not self.result.moe_common_characters:
                try:
                    self.status_var.set("載入教育部 4,808 常用國字清單…")
                    self.update_idletasks()
                    self.result.moe_common_characters = ensure_moe_common_characters()
                    self.result.moe_characters_sha256 = _file_sha256(
                        MOE_COMMON_CHARACTERS_PATH
                    )
                except CharacterManagerError as exc:
                    self.candidate_filter_vars["moe_4808"].set(False)
                    messagebox.showerror(
                        "無法啟用常用國字排除",
                        str(exc),
                        parent=self,
                    )
                    filters = self._current_candidate_filters()

            self.result.candidate_filters = filters
            self.result.mapping = dict(self.mapping)
            self.result.excluded_sources = set(self.excluded_sources)
            write_reports(self.result, update_mapping_templates=False)
            self._refresh_all()
            self.status_var.set(
                f"候選排除設定已更新：目前可用候選 {len(self.result.safe_candidates)} 個。"
            )

        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror(
                "設定儲存失敗",
                str(exc),
                parent=self,
            )

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
        settings["source_command_encoding"] = (
            self.command_encoding_var.get().strip() or "cp932"
        )
        settings["source_dialogue_encoding"] = (
            self.dialogue_encoding_var.get().strip() or "auto"
        )
        settings["candidate_filters"] = self._current_candidate_filters()
        if self.result is not None:
            self.result.excluded_sources = set(self.excluded_sources)
            self.result.candidate_filters = self._current_candidate_filters()
            self.result.mapping = dict(self.mapping)
            write_reports(self.result, update_mapping_templates=False)
        save_manager_settings(
            self.settings_path,
            settings,
        )

    # ----- 掃描 -----

    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        translation_text = self.translation_var.get().strip().strip('"')
        translation = Path(translation_text).expanduser()
        if not translation.is_dir():
            messagebox.showerror(
                "無法掃描",
                "請指定存在的翻譯文檔資料夾。",
                parent=self,
            )
            return

        source_text = self.source_var.get().strip().strip('"')
        source_root: Path | None = None
        if source_text:
            source_root = Path(source_text).expanduser()
            if not source_root.exists():
                messagebox.showerror(
                    "原始腳本路徑錯誤",
                    f"找不到指定的原始遊戲腳本位置：\n{source_root}",
                    parent=self,
                )
                return
            if not source_root.is_dir():
                messagebox.showerror(
                    "原始腳本路徑錯誤",
                    "GUI 的原始腳本欄位請選擇已解包的資料夾。",
                    parent=self,
                )
                return

        command_encoding = (
            self.command_encoding_var.get().strip() or "cp932"
        )
        dialogue_encoding = (
            self.dialogue_encoding_var.get().strip() or "auto"
        )
        candidate_filters = self._current_candidate_filters()

        # 同一翻譯與原文來源重掃時保留目前工作；改變掃描來源則另存舊草稿，
        # 不把依賴舊原文安全條件建立的映射直接帶到新來源。
        if (
            self.result is not None
            and self.mapping
            and self._resume_draft is None
        ):
            try:
                same_translation = (
                    translation.resolve()
                    == self.result.translation_root.resolve()
                )
                old_source = (
                    self.result.source_root.resolve()
                    if self.result.source_root is not None
                    else None
                )
                new_source = (
                    source_root.resolve()
                    if source_root is not None
                    else None
                )
                same_source = old_source == new_source
            except OSError:
                same_translation = same_source = False

            if same_translation and same_source:
                self._resume_draft = self._draft_payload()
            else:
                self._save_mapping_draft(show_message=False)

        try:
            settings = load_manager_settings(self.settings_path)
            settings["text_encoding"] = (
                self.encoding_var.get().strip() or "utf-8-sig"
            )
            settings["source_command_encoding"] = command_encoding
            settings["source_dialogue_encoding"] = dialogue_encoding
            settings["candidate_filters"] = candidate_filters
            save_manager_settings(self.settings_path, settings)
            self.excluded_sources = set(
                settings["excluded_source_characters"]
            )
        except (OSError, CharacterManagerError) as exc:
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
        self.scan_button.configure(state="disabled")
        self.status_var.set(
            "掃描中：讀取翻譯文件、建立 CP932 字形目錄及套用候選排除規則…"
        )

        encoding = self.encoding_var.get().strip() or "utf-8-sig"
        settings_path = self.settings_path

        def worker() -> None:
            try:
                result = scan_project(
                    translation_root=translation,
                    source_root=source_root,
                    report_root=report,
                    settings_path=settings_path,
                    text_encoding=encoding,
                    command_encoding=command_encoding,
                    dialogue_encoding=dialogue_encoding,
                    candidate_filters=candidate_filters,
                )
                self.events.put(("scan_done", result))
            except Exception as exc:
                self.events.put(
                    ("scan_error", f"{type(exc).__name__}: {exc}")
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
                    self.scan_button.configure(state="normal")
                    self.status_var.set("掃描失敗")
                    messagebox.showerror(
                        "掃描失敗",
                        str(payload),
                        parent=self,
                    )

                elif kind == "scan_done":
                    self.scan_button.configure(state="normal")
                    self.result = payload
                    self.excluded_sources = set(payload.excluded_sources)

                    restored_ok = True
                    if self._resume_draft is not None:
                        try:
                            (
                                self.mapping,
                                self._loaded_uif_sources,
                            ) = self._validate_restored_mapping(
                                self._resume_draft
                            )
                        except CharacterManagerError as exc:
                            restored_ok = False
                            self.mapping = {}
                            self._loaded_uif_sources.clear()
                            self._removed_uif_sources.clear()
                            self._uif_was_loaded = False
                            messagebox.showerror(
                                "工作草稿無法恢復",
                                f"{exc}\n\n原始草稿不會被覆蓋。"
                                "請先檢查或備份 mapping_draft.json。",
                                parent=self,
                            )
                        else:
                            if self._resume_warnings:
                                messagebox.showwarning(
                                    "工作草稿部分恢復",
                                    "下列映射未能恢復，其他有效配對已保留：\n\n"
                                    + "\n".join(self._resume_warnings[:30])
                                    + (
                                        "\n\n其餘問題已省略。"
                                        if len(self._resume_warnings) > 30
                                        else ""
                                    ),
                                    parent=self,
                                )
                        finally:
                            self._resume_draft = None
                    else:
                        self.mapping = {}
                        self._loaded_uif_sources.clear()
                        self._removed_uif_sources.clear()
                        self._uif_was_loaded = False
                        self._resume_warnings = []

                    # GUI 勾選狀態以本次掃描快照為準。
                    for key, variable in self.candidate_filter_vars.items():
                        variable.set(payload.candidate_filters.get(key, True))

                    self._refresh_all()
                    missing_count = len(self._current_missing_targets())
                    source_summary = (
                        (
                            f"原始腳本：已掃描 {payload.scanned_source_files} 個有效腳本"
                            if payload.source_scan_complete
                            else (
                                f"原始腳本：掃描不完整（{len(payload.source_errors)} 個錯誤），"
                                "候選清單已停用"
                            )
                        )
                        if payload.source_scan_performed
                        else "原始腳本：未指定，未檢查原文占用"
                    )
                    self.status_var.set(
                        f"完成：翻譯檔 {payload.scanned_translation_files} 個，"
                        f"待替換字元 {missing_count} 個，"
                        f"可用候選 {len(payload.safe_candidates)} 個。\n"
                        f"{source_summary}。\n報表：{payload.report_root}"
                    )

                    warnings = []
                    if payload.errors:
                        warnings.append(
                            "翻譯文件錯誤：\n" + "\n".join(payload.errors[:15])
                        )
                    if payload.source_errors:
                        warnings.append(
                            "原始遊戲腳本錯誤：\n"
                            + "\n".join(payload.source_errors[:15])
                        )
                    if warnings:
                        messagebox.showwarning(
                            "部分檔案無法完整掃描",
                            "\n\n".join(warnings),
                            parent=self,
                        )

                    if restored_ok:
                        self._save_mapping_draft(show_message=False)

        except queue.Empty:
            pass

        self.after(120, self._poll_events)

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
            tree.delete(*tree.get_children())

        if self.result is None:
            self._update_previews()
            return

        self.result.mapping = dict(self.mapping)
        self.result.excluded_sources = set(self.excluded_sources)
        self.result.candidate_filters = self._current_candidate_filters()

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

        # 讓 CSV 與 GUI 工作區映射同步，不保留初次掃描時的空映射狀態。
        try:
            write_reports(self.result, update_mapping_templates=False)
        except OSError as exc:
            self.status_var.set(f"報表更新失敗：{exc}")

    def _refresh_candidate_tree(self) -> None:
        if self.result is None:
            return

        self.candidate_tree.delete(*self.candidate_tree.get_children())
        query = (
            self.candidate_filter_var.get()
            .strip()
            .casefold()
        )

        for glyph in self.result.safe_candidates:
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

        try:
            _validate_new_mapping(
                {source: target},
                translation_characters=self.result.stats,
                source_characters=self.result.original_characters,
                excluded_sources=self.excluded_sources,
                excluded_candidate_characters=self.result.excluded_builtin_characters,
                existing_mapping=self.mapping,
            )
            candidate_mapping = merge_mapping(
                self.mapping,
                {source: target},
                "工作映射",
            )
        except CharacterManagerError as exc:
            messagebox.showerror(
                "映射無效",
                str(exc),
                parent=self,
            )
            return

        self.mapping = candidate_mapping

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
            "source_root": (
                str(self.result.source_root)
                if self.result.source_root is not None
                else None
            ),
            "source_command_encoding": self.result.command_encoding,
            "source_dialogue_encoding": self.result.dialogue_encoding,
            "candidate_filters": dict(self.result.candidate_filters),
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

    @staticmethod
    def _backup_draft_file(path: Path) -> Path | None:
        """第一次覆寫草稿前保留完整副本，不改動既有備份。"""
        if not path.is_file():
            return None

        backup = path.with_suffix(path.suffix + ".bak")

        # 每次覆寫前都更新備份，確保 .bak 是「緊接著被覆寫的上一版」，
        # 而不是只保留首次建立的舊草稿。
        import shutil
        shutil.copy2(path, backup)

        return backup

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
            self._backup_draft_file(draft_path)

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

            self.source_var.set(str(draft.get("source_root") or ""))
            self.command_encoding_var.set(
                str(draft.get("source_command_encoding", "cp932"))
            )
            self.dialogue_encoding_var.set(
                str(draft.get("source_dialogue_encoding", "auto"))
            )
            restored_filters = draft.get("candidate_filters", {})
            if isinstance(restored_filters, dict):
                for key, variable in self.candidate_filter_vars.items():
                    variable.set(bool(restored_filters.get(key, True)))

            self.config_path = config_path.resolve()
            self._draft_path = draft_path
            self._resume_draft = draft
            self._resume_warnings = []

            # 續作時依草稿記錄重新掃描翻譯文件及原始腳本。
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
        """逐組恢復草稿；無效映射只略過該組，不丟棄其他有效配對。"""
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

        if not isinstance(pairs, list):
            raise CharacterManagerError(
                "草稿 mapping 必須是陣列。"
            )

        restored: dict[str, str] = {}
        restored_targets: set[str] = set()
        self._resume_warnings = []

        loaded_entries: dict[str, str] = {}

        # 先逐組檢查，不因單一錯誤而中止整份草稿。
        for index, pair in enumerate(pairs, start=1):
            if not isinstance(pair, dict):
                self._resume_warnings.append(
                    f"第 {index} 組：資料格式不是物件，已略過。"
                )
                continue

            source = pair.get("source")
            target = pair.get("target")

            if (
                not isinstance(source, str)
                or len(source) != 1
                or not isinstance(target, str)
                or len(target) != 1
            ):
                self._resume_warnings.append(
                    f"第 {index} 組：來源或目標不是單一字元，已略過。"
                )
                continue

            if source in restored:
                self._resume_warnings.append(
                    f"第 {index} 組：來源 {source!r} 重複，已略過。"
                )
                continue

            if target in restored_targets:
                self._resume_warnings.append(
                    f"第 {index} 組：目標 {target!r} 重複，已略過。"
                )
                continue

            if source in loaded_set:
                # 已載入 UIF 的歷史映射只做結構檢查。
                # 不重新套用新來源／新目標的 CP932 規則。
                candidate = {source: target}
                _validate_mapping(candidate)
                loaded_entries[source] = target
            else:
                try:
                    _validate_new_mapping(
                        {source: target},
                        translation_characters=self.result.stats,
                        source_characters=self.result.original_characters,
                        excluded_sources=self.excluded_sources,
                        excluded_candidate_characters=(
                            self.result.excluded_builtin_characters
                        ),
                        existing_mapping=loaded_entries,
                    )
                except CharacterManagerError as exc:
                    self._resume_warnings.append(
                        f"第 {index} 組 {source!r} → {target!r}：{exc}"
                    )
                    continue

            restored[source] = target
            restored_targets.add(target)

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

            # 舊 UIF 映射允許 CP932 目標及來源／目標交集；
            # 只驗證結構，避免誤拒絕既有設定。
            _validate_mapping(incoming)

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

        if (
            self.result.source_scan_performed
            and not self.result.source_scan_complete
        ):
            messagebox.showerror(
                "原始腳本掃描不完整",
                "本次指定的原始遊戲腳本未完整解析，不能套用映射。\n"
                "請修正錯誤或清除原始腳本欄位後重新掃描。",
                parent=self,
            )
            return

        if not self.mapping:
            messagebox.showwarning(
                "沒有映射",
                "目前沒有可套用的替代映射。",
                parent=self,
            )
            return

        try:
            translation_root = self.result.translation_root
            selected_translation = Path(
                self.translation_var.get().strip().strip('"')
            ).expanduser().resolve()
            if selected_translation != translation_root.resolve():
                raise CharacterManagerError(
                    "目前選取的翻譯資料夾與本次掃描結果不同。"
                    "請先重新掃描，再套用映射。"
                )
            if not translation_root.is_dir():
                raise CharacterManagerError(
                    f"原翻譯資料夾已不存在：{translation_root}"
                )

            selected_source_text = self.source_var.get().strip().strip('"')
            selected_source = (
                Path(selected_source_text).expanduser().resolve()
                if selected_source_text
                else None
            )
            previous_source = (
                self.result.source_root.resolve()
                if self.result.source_root is not None
                else None
            )
            if selected_source != previous_source:
                raise CharacterManagerError(
                    "原始遊戲腳本來源與本次掃描結果不同。"
                    "請先重新掃描，再套用映射。"
                )

            self.status_var.set(
                "套用前重新掃描翻譯文件及原始腳本，確認來源字元未被使用…"
            )
            self.update_idletasks()

            fresh_stats, fresh_count, fresh_errors = scan_translation_root(
                translation_root,
                self.result.translation_text_encoding,
            )
            if fresh_count == 0 or fresh_errors:
                raise CharacterManagerError(
                    "套用前重新掃描翻譯文件失敗，拒絕更新 UIF：\n"
                    + "\n".join(fresh_errors[:20])
                )

            fresh_source_stats: dict[str, CharacterStat] = {}
            if self.result.source_scan_performed:
                if selected_source is None or not selected_source.exists():
                    raise CharacterManagerError(
                        "原始遊戲腳本來源已不存在，拒絕套用。"
                    )
                fresh_source_stats, source_count, source_errors = scan_source_root(
                    selected_source,
                    self.result.command_encoding,
                    self.result.dialogue_encoding,
                )
                if source_count == 0 or source_errors:
                    raise CharacterManagerError(
                        "套用前重新掃描原始遊戲腳本失敗，拒絕更新 UIF：\n"
                        + "\n".join(source_errors[:20])
                    )

            existing = load_uif_mapping(self.config_path)
            if self._uif_was_loaded:
                existing = {
                    source: target
                    for source, target in existing.items()
                    if source not in self._removed_uif_sources
                }

            new_entries = {
                source: target
                for source, target in self.mapping.items()
                if source not in existing
            }

            current_exclusion_hash = _file_sha256(
                self.result.exclusion_config_path
            )
            if current_exclusion_hash != self.result.exclusion_config_sha256:
                raise CharacterManagerError(
                    "候選標點／符號清單在掃描後已變更。請重新掃描，再套用映射。"
                )
            if (
                self.result.candidate_filters.get("moe_4808", True)
                and _file_sha256(MOE_COMMON_CHARACTERS_PATH)
                != self.result.moe_characters_sha256
            ):
                raise CharacterManagerError(
                    "教育部 4,808 常用字清單在掃描後已變更。請重新掃描。"
                )

            latest_settings = load_manager_settings(self.settings_path)
            latest_exclusion_data = load_candidate_exclusion_data()
            latest_excluded_sources = (
                set(self.excluded_sources)
                | set(latest_settings["excluded_source_characters"])
                | set(latest_exclusion_data.get("custom_excluded_characters", ""))
            )

            current_filters = self._current_candidate_filters()
            if current_filters != self.result.candidate_filters:
                raise CharacterManagerError(
                    "候選排除設定已在掃描後變更。請重新掃描，再套用映射。"
                )

            _validate_new_mapping(
                new_entries,
                translation_characters=fresh_stats,
                source_characters=set(fresh_source_stats),
                excluded_sources=latest_excluded_sources,
                excluded_candidate_characters=self.result.excluded_builtin_characters,
                existing_mapping=existing,
            )

            merged = merge_mapping(
                existing,
                self.mapping,
                "工作映射與 UIF 設定",
            )

            source_notice = (
                "已重新掃描原始遊戲腳本並確認候選來源未出現在已辨識文字中。"
                if self.result.source_scan_performed
                else "注意：本次未指定原始遊戲腳本，未能檢查候選來源是否出現在遊戲原文。"
            )
            if not messagebox.askyesno(
                "確認更新 UIF 設定",
                f"即將寫入 {len(merged)} 組映射。\n"
                f"新增 {len(new_entries)} 組；"
                f"保留既有 {len(existing)} 組。\n\n"
                f"{source_notice}\n\n"
                f"設定檔：\n{self.config_path}\n\n"
                "原設定會先備份成 .bak。是否繼續？",
                parent=self,
            ):
                self.status_var.set("已取消更新 UIF。")
                return

            write_uif_mapping(self.config_path, merged)
            self.mapping = merged
            self._loaded_uif_sources = set(merged)
            self._removed_uif_sources.clear()
            self._uif_was_loaded = True
            self._refresh_all()
            self._save_mapping_draft(show_message=False)

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
    source = Path(args.source) if args.source else None
    report = (
        Path(args.output)
        if args.output
        else (
            translation.resolve().parent
            / (translation.resolve().name + "_char_report")
        )
    )

    result = scan_project(
        translation_root=translation,
        source_root=source,
        report_root=report,
        settings_path=Path(args.settings),
        text_encoding=args.text_encoding,
        command_encoding=args.command_encoding,
        dialogue_encoding=args.dialogue_encoding,
    )

    print(f"翻譯檔案：{result.scanned_translation_files}")
    print(f"統計字元：{len(result.stats)}")
    print(f"CP932 不支援且尚未映射：{len(result.missing_targets)}")
    print(f"CP932 可用候選：{len(result.safe_candidates)}")
    if result.source_scan_performed:
        print(
            f"原始腳本：{result.scanned_source_files} 個有效腳本；"
            f"完整性：{'完整' if result.source_scan_complete else '不完整'}"
        )
    else:
        print("原始腳本：未指定；未檢查原文占用")
    print(f"報表資料夾：{result.report_root}")

    for name, path in write_reports(result).items():
        print(f"{name}: {path}")

    if result.errors:
        print(f"警告：{len(result.errors)} 個翻譯檔案未能讀取。")
        for error in result.errors[:30]:
            print("  " + error)

    if result.source_errors:
        print(f"警告：{len(result.source_errors)} 個原始腳本掃描問題。")
        for error in result.source_errors[:30]:
            print("  " + error)

    # 來源選填；若有指定卻未能完整掃描，回傳非零，避免自動化流程誤判。
    if result.source_scan_performed and not result.source_scan_complete:
        return 1
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
        "--source",
        help="選填：已解包的原始 ADVSCRIPT2 腳本檔案或資料夾",
    )

    scan.add_argument(
        "--command-encoding",
        default="cp932",
        help="原始腳本指令／場景編碼（預設 cp932）",
    )

    scan.add_argument(
        "--dialogue-encoding",
        default="auto",
        help="原始腳本對話編碼（預設 auto，可用 cp932／gbk／utf-8 等）",
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
