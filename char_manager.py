#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools CP932 字元統計與替代字元管理。

本模組只負責掃描文字、建立本次工作映射及匯出兩行替換字串。
不讀取、不修改、不合併任何 UIF 設定。原始 ADVSCRIPT2 解析重用同目錄的 scr.py。
Python 3.10+；僅使用標準函式庫。
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import os
import queue
import sys
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Iterable, Iterator

import scr as script_tool


APP_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS = APP_DIR / "tool" / "char_manager.json"
CANDIDATE_EXCLUSION_CONFIG_PATH = APP_DIR / "tool" / "char_candidate_exclusions.json"
COMMON_CHARACTERS_PATH = APP_DIR / "tool" / "常用字.txt"

TRANSLATION_EXTENSIONS = {
    ".json", ".txt", ".csv", ".tsv", ".po", ".pot", ".ftl", ".ini",
    ".xml", ".html", ".htm", ".md", ".yaml", ".yml",
}
FILTER_KEYS = (
    "common_characters",
    "zh_punctuation",
    "ja_punctuation",
    "common_symbols",
)
DEFAULT_CANDIDATE_FILTERS = {
    "common_characters": True,
    "zh_punctuation": True,
    "ja_punctuation": True,
    "common_symbols": True,
}
EXCLUSION_GROUP_LABELS = {
    "zh_punctuation": "中文標點",
    "ja_punctuation": "日文標點",
    "common_symbols": "常用符號",
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
    excluded_targets: set[str] = field(default_factory=set)
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
    common_characters: set[str] = field(default_factory=set)
    symbol_exclusions: dict[str, set[str]] = field(default_factory=dict)
    custom_excluded_characters: set[str] = field(default_factory=set)
    exclusion_config_path: Path = CANDIDATE_EXCLUSION_CONFIG_PATH
    exclusion_config_sha256: str | None = None
    common_characters_sha256: str | None = None

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
                if not can_encode(char, "cp932") and char not in mapped
            ),
            key=lambda item: (-item.count, ord(item.character)),
        )

    @property
    def excluded_builtin_characters(self) -> set[str]:
        excluded: set[str] = set()
        if self.candidate_filters.get("common_characters", True):
            excluded.update(self.common_characters)
        for key in ("zh_punctuation", "ja_punctuation", "common_symbols"):
            if self.candidate_filters.get(key, True):
                excluded.update(self.symbol_exclusions.get(key, set()))
        excluded.update(self.custom_excluded_characters)
        return excluded

    @property
    def safe_candidates(self) -> list[Glyph]:
        # 任一必要文字來源掃描不完整，都不能把部分結果當成安全候選。
        if not self.translation_scan_complete:
            return []
        if self.source_scan_performed and not self.source_scan_complete:
            return []
        return [
            glyph for glyph in self.glyphs
            if not candidate_exclusion_reasons(glyph.character, self)
        ]


# ---------------------------------------------------------------------------
# 字元分類與編碼
# ---------------------------------------------------------------------------

def validate_text_encoding(encoding: str) -> str:
    name = encoding.strip()
    if not name:
        raise CharacterManagerError("文字編碼不可為空白。")
    try:
        return codecs.lookup(name).name
    except LookupError as exc:
        raise CharacterManagerError(f"不支援的文字編碼：{encoding!r}") from exc


def can_encode(character: str, encoding: str = "cp932") -> bool:
    try:
        character.encode(encoding, errors="strict")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


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
    """保留漢字、標點及符號；排除英文、日文假名、數字及空白。"""
    if len(character) != 1 or character.isspace() or _is_kana(character):
        return False
    name = unicodedata.name(character, "")
    if name.startswith("LATIN "):
        return False
    if character.isascii() and character.isalpha():
        return False
    if unicodedata.category(character).startswith("N"):
        return False
    category = unicodedata.category(character)
    return _is_han(character) or category.startswith(("P", "S"))


def is_safe_source_character(character: str) -> bool:
    """來源候選只使用 CP932 可編碼的非 ASCII 漢字、標點或符號。"""
    return (
        len(character) == 1
        and ord(character) > 0x7F
        and is_report_character(character)
        and can_encode(character, "cp932")
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
# 設定檔與排除清單
# ---------------------------------------------------------------------------

def load_manager_settings(path: Path = DEFAULT_SETTINGS) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "version": 3,
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
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(f"字元管理設定檔無法讀取：{path}\n{exc}") from exc
    if not isinstance(value, dict):
        raise CharacterManagerError("char_manager.json 根節點必須是物件。")

    for key in ("excluded_source_characters", "excluded_target_characters"):
        values = value.get(key, [])
        if not isinstance(values, list) or any(
            not isinstance(item, str) or len(item) != 1 for item in values
        ):
            raise CharacterManagerError(f"char_manager.json 的 {key} 格式錯誤。")
        defaults[key] = list(dict.fromkeys(values))

    for key in ("text_encoding", "source_command_encoding", "source_dialogue_encoding"):
        if isinstance(value.get(key), str) and value[key].strip():
            defaults[key] = value[key]

    filters = value.get("candidate_filters", {})
    if not isinstance(filters, dict):
        raise CharacterManagerError("char_manager.json 的 candidate_filters 必須是物件。")
    for key in FILTER_KEYS:
        # 舊版 moe_4808 只做設定相容轉名，不會載入或下載教育部字表。
        legacy_value = filters.get("moe_4808", True) if key == "common_characters" else True
        selected = filters.get(key, legacy_value)
        if not isinstance(selected, bool):
            raise CharacterManagerError(f"candidate_filters.{key} 必須是布林值。")
        defaults["candidate_filters"][key] = selected
    defaults["version"] = 3
    return defaults


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def save_manager_settings(path: Path, settings: dict[str, Any]) -> None:
    output = {
        "version": 3,
        "excluded_source_characters": sorted(set(settings.get("excluded_source_characters", [])), key=ord),
        "excluded_target_characters": sorted(set(settings.get("excluded_target_characters", [])), key=ord),
        "text_encoding": str(settings.get("text_encoding", "utf-8-sig")),
        "source_command_encoding": str(settings.get("source_command_encoding", "cp932")),
        "source_dialogue_encoding": str(settings.get("source_dialogue_encoding", "auto")),
        "candidate_filters": {
            key: bool(settings.get("candidate_filters", {}).get(key, True))
            for key in FILTER_KEYS
        },
    }
    atomic_write_json(path, output)


def default_candidate_exclusion_data() -> dict[str, Any]:
    return {
        "version": 2,
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
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CharacterManagerError(f"候選排除清單無法讀取：{path}\n{exc}") from exc
    if not isinstance(value, dict):
        raise CharacterManagerError("char_candidate_exclusions.json 根節點必須是物件。")
    groups = value.get("symbol_groups", {})
    if not isinstance(groups, dict):
        raise CharacterManagerError("symbol_groups 必須是物件。")
    for key in ("zh_punctuation", "ja_punctuation", "common_symbols"):
        chars = groups.get(key, defaults["symbol_groups"][key])
        if not isinstance(chars, str):
            raise CharacterManagerError(f"symbol_groups.{key} 必須是字串。")
        defaults["symbol_groups"][key] = "".join(dict.fromkeys(ch for ch in chars if not ch.isspace()))
    custom = value.get("custom_excluded_characters", "")
    if isinstance(custom, list):
        if any(not isinstance(ch, str) or len(ch) != 1 for ch in custom):
            raise CharacterManagerError("custom_excluded_characters 陣列項目必須各是一個字元。")
        custom = "".join(custom)
    if not isinstance(custom, str):
        raise CharacterManagerError("custom_excluded_characters 必須是字串或單字元陣列。")
    defaults["custom_excluded_characters"] = "".join(
        dict.fromkeys(ch for ch in custom if not ch.isspace())
    )
    defaults["version"] = 2
    return defaults


def load_common_characters(path: Path = COMMON_CHARACTERS_PATH) -> set[str]:
    """讀取使用者提供的單行常用字清單；不要求固定字數、不連線下載。"""
    if not path.is_file():
        raise CharacterManagerError(
            f"找不到常用字清單：{path}\n請保留專案中的 tool/常用字.txt。"
        )
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CharacterManagerError(f"常用字清單無法讀取：{path}\n{exc}") from exc
    characters = {ch for ch in content if not ch.isspace()}
    if not characters:
        raise CharacterManagerError(f"常用字清單沒有任何字元：{path}")
    return characters


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 翻譯文件與原始腳本掃描
# ---------------------------------------------------------------------------

def _is_symlink_or_junction(path: Path) -> bool:
    """判斷符號連結及 Windows Junction；不追蹤連結目標。"""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def iter_text_files(root: Path) -> Iterator[Path]:
    """列舉翻譯文件；符號連結及目錄連結會作為警示項目回傳。"""
    root = Path(root)
    if _is_symlink_or_junction(root):
        if root.is_dir() or root.suffix.lower() in TRANSLATION_EXTENSIONS:
            yield root
        return
    if root.is_file():
        if root.suffix.lower() in TRANSLATION_EXTENSIONS:
            yield root
        return

    # 不跟隨目錄符號連結；Windows Junction 亦明確從遞迴清單移除。
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        retained_directories: list[str] = []
        for dirname in dirnames:
            path = current_path / dirname
            if _is_symlink_or_junction(path):
                yield path
            else:
                retained_directories.append(dirname)
        dirnames[:] = retained_directories

        for filename in filenames:
            path = current_path / filename
            if path.suffix.lower() not in TRANSLATION_EXTENSIONS:
                continue
            if _is_symlink_or_junction(path) or path.is_file():
                yield path


def _collect_json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _collect_json_strings(item)
    elif isinstance(value, dict):
        # JSON 鍵通常是欄位名稱或識別碼，不列入翻譯文字統計。
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


def _add_stats(stats: dict[str, CharacterStat], value: str, relative: str) -> None:
    for character in value:
        if not is_report_character(character):
            continue
        stat = stats.setdefault(character, CharacterStat(character))
        stat.count += 1
        stat.files.add(relative)


def scan_translation_root(
    root: Path, text_encoding: str,
) -> tuple[dict[str, CharacterStat], int, list[str]]:
    stats: dict[str, CharacterStat] = {}
    scanned = 0
    errors: list[str] = []
    for path in iter_text_files(root):
        if _is_symlink_or_junction(path):
            link_kind = "目錄" if path.is_dir() else "檔案"
            errors.append(
                f"翻譯資料：偵測到{link_kind}符號連結／Junction，"
                f"為避免追蹤至連結目標而未讀取：{path}"
            )
            continue
        try:
            strings = _load_text_document(path, text_encoding)
            scanned += 1
            relative = str(path.relative_to(root)) if root.is_dir() else path.name
            for value in strings:
                _add_stats(stats, value, relative)
        except Exception as exc:
            errors.append(f"翻譯資料：{path}：{type(exc).__name__}: {exc}")
    return stats, scanned, errors


def scan_source_root(
    root: Path,
    command_encoding: str = "cp932",
    dialogue_encoding: str = "auto",
) -> tuple[dict[str, CharacterStat], int, list[str]]:
    """重用 scr.py 解析 ADVSCRIPT2；統計可辨識的名稱、對話及選項。"""
    root = root.expanduser().resolve()
    if not root.exists():
        raise CharacterManagerError(f"原始遊戲腳本位置不存在：{root}")
    try:
        command_codec = script_tool.validate_encoding(command_encoding)
        dialogue_codec = script_tool.validate_dialogue_encoding(dialogue_encoding)
    except Exception as exc:
        raise CharacterManagerError(f"原始腳本編碼設定錯誤：{exc}") from exc

    if root.is_file():
        files = [root]
    else:
        files = sorted(
            (path for path in root.rglob("*") if path.is_file() and not path.is_symlink()),
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
                parsed, command_codec, dialogue_codec, str(path)
            )
            scanned += 1
            relative = str(path.relative_to(root)) if root.is_dir() else path.name
            for item in items:
                for value in item.values():
                    if isinstance(value, str):
                        _add_stats(stats, value, relative)
        except Exception as exc:
            errors.append(f"原始腳本：{path}：{type(exc).__name__}: {exc}")
    if scanned == 0:
        errors.append(f"指定位置沒有找到可解析的 NEKOSDK_ADVSCRIPT2 腳本：{root}")
    return stats, scanned, errors


# ---------------------------------------------------------------------------
# CP932 字形目錄與候選判斷
# ---------------------------------------------------------------------------

def build_cp932_glyph_catalog() -> list[Glyph]:
    """列舉 CP932 可解碼的一字元漢字、標點及符號。"""
    byte_map: dict[str, bytes] = {}

    def add(raw: bytes) -> None:
        try:
            decoded = raw.decode("cp932", errors="strict")
        except UnicodeDecodeError:
            return
        if len(decoded) != 1 or not is_report_character(decoded):
            return
        previous = byte_map.get(decoded)
        if previous is None or len(raw) < len(previous):
            byte_map[decoded] = raw

    for first in range(256):
        add(bytes((first,)))
    for first in range(256):
        for second in range(256):
            add(bytes((first, second)))

    glyphs = [
        Glyph(
            character=character,
            codepoint=f"U+{ord(character):04X}",
            name=unicodedata.name(character, "未命名字元"),
            category=_glyph_category(character),
            cp932_hex=raw.hex(" ").upper(),
        )
        for character, raw in byte_map.items()
    ]
    return sorted(glyphs, key=lambda item: (ord(item.character), item.cp932_hex))


def candidate_exclusion_reasons(character: str, result: ScanResult) -> list[str]:
    reasons: list[str] = []
    if not result.translation_scan_complete:
        reasons.append("翻譯文件掃描不完整")
    if not is_safe_source_character(character):
        reasons.append("不符合 CP932 來源字元條件")
    if character in result.stats:
        reasons.append("翻譯文件已使用")
    if result.source_scan_performed and not result.source_scan_complete:
        reasons.append("原始腳本掃描不完整")
    elif result.source_scan_performed and character in result.original_characters:
        reasons.append("原始遊戲腳本已使用")
    if (
        result.candidate_filters.get("common_characters", True)
        and character in result.common_characters
    ):
        reasons.append("自訂常用字清單")
    for key, label in EXCLUSION_GROUP_LABELS.items():
        if result.candidate_filters.get(key, True) and character in result.symbol_exclusions.get(key, set()):
            reasons.append(label)
    if character in result.custom_excluded_characters:
        reasons.append("自訂排除清單")
    if character in result.excluded_sources:
        reasons.append("人工排除")
    if character in set(result.mapping) | set(result.mapping.values()):
        reasons.append("目前工作映射已占用")
    return reasons


def _validate_new_mapping(
    mapping: dict[str, str],
    *,
    translation_characters: Iterable[str],
    source_characters: Iterable[str] = (),
    excluded_sources: Iterable[str] = (),
    excluded_candidate_characters: Iterable[str] = (),
    existing_mapping: dict[str, str] | None = None,
) -> None:
    """驗證本次工作映射；不讀取任何 UIF 或外部既有映射。"""
    used_translation = set(translation_characters)
    used_source = set(source_characters)
    excluded = set(excluded_sources)
    excluded_candidates = set(excluded_candidate_characters)
    existing = existing_mapping or {}
    if len(mapping) != len(set(mapping)):
        raise CharacterManagerError("映射來源字元重複。")
    if len(mapping.values()) != len(set(mapping.values())):
        raise CharacterManagerError("映射目標字元重複。")
    used_sources = set(existing)
    used_targets = set(existing.values())
    for source, target in mapping.items():
        if len(source) != 1 or len(target) != 1:
            raise CharacterManagerError("來源與目標都必須各是一個 Unicode 字元。")
        if not is_safe_source_character(source):
            raise CharacterManagerError(f"來源字元 {source!r} 不是 CP932 可用的非 ASCII 漢字／標點／符號。")
        if can_encode(target, "cp932"):
            raise CharacterManagerError(f"目標字元 {target!r} 本身可用 CP932 編碼，不需要替換。")
        if source in used_translation:
            raise CharacterManagerError(f"來源字元 {source!r} 已出現在翻譯文件中。")
        if source in used_source:
            raise CharacterManagerError(f"來源字元 {source!r} 已出現在原始遊戲腳本中。")
        if source in excluded_candidates:
            raise CharacterManagerError(f"來源字元 {source!r} 命中常用字／符號排除清單。")
        if source in excluded:
            raise CharacterManagerError(f"來源字元 {source!r} 已列入人工排除清單。")
        if source in used_sources:
            raise CharacterManagerError(f"來源字元 {source!r} 已在目前工作映射中使用。")
        if target in used_targets:
            raise CharacterManagerError(f"目標字元 {target!r} 已在目前工作映射中使用。")
        used_sources.add(source)
        used_targets.add(target)


def _validate_result_snapshot(result: ScanResult) -> None:
    if not result.translation_scan_complete:
        raise CharacterManagerError("翻譯文件掃描不完整，不能建立或匯出映射。")
    if result.source_scan_performed and not result.source_scan_complete:
        raise CharacterManagerError("原始遊戲腳本掃描不完整，不能建立或匯出映射。")
    current_exclusion = _file_sha256(result.exclusion_config_path)
    if current_exclusion != result.exclusion_config_sha256:
        raise CharacterManagerError("標點／符號排除清單在掃描後已變更，請重新掃描。")
    if _file_sha256(COMMON_CHARACTERS_PATH) != result.common_characters_sha256:
        raise CharacterManagerError("tool/常用字.txt 在掃描後已變更，請重新掃描。")


def validate_work_mapping(result: ScanResult, mapping: dict[str, str]) -> None:
    _validate_result_snapshot(result)
    valid_targets = {
        character for character in result.stats
        if not can_encode(character, "cp932") and character not in result.excluded_targets
    }
    invalid_targets = [target for target in mapping.values() if target not in valid_targets]
    if invalid_targets:
        raise CharacterManagerError(
            "工作映射包含不屬於本次待替換清單的目標字元："
            + "、".join(invalid_targets)
        )
    _validate_new_mapping(
        mapping,
        translation_characters=result.stats,
        source_characters=result.original_characters,
        excluded_sources=result.excluded_sources,
        excluded_candidate_characters=result.excluded_builtin_characters,
    )


# ---------------------------------------------------------------------------
# 報表與掃描主流程
# ---------------------------------------------------------------------------

def _write_csv(path: Path, headers: list[str], rows: Iterable[Iterable[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def write_reports(result: ScanResult) -> dict[str, Path]:
    output = result.report_root
    output.mkdir(parents=True, exist_ok=True)

    character_rows = []
    for character, stat in sorted(result.stats.items(), key=lambda pair: (-pair[1].count, ord(pair[0]))):
        supported = can_encode(character, "cp932")
        character_rows.append([
            character, f"U+{ord(character):04X}", unicodedata.name(character, "未命名字元"),
            _glyph_category(character), stat.count, len(stat.files),
            "是" if supported else "否",
            "已映射" if character in result.mapped_targets else ("不需替換" if supported else "待替換"),
        ])
    character_report = output / "character_report.csv"
    _write_csv(character_report, [
        "字元", "Unicode", "Unicode 名稱", "類別", "出現次數", "使用檔案數", "CP932 可編碼", "狀態",
    ], character_rows)

    source_report: Path | None = None
    if result.source_scan_performed:
        source_rows = []
        for character, stat in sorted(result.source_stats.items(), key=lambda pair: (-pair[1].count, ord(pair[0]))):
            source_rows.append([
                character, f"U+{ord(character):04X}", unicodedata.name(character, "未命名字元"),
                _glyph_category(character), stat.count, len(stat.files), "、".join(sorted(stat.files)),
            ])
        source_report = output / "source_character_report.csv"
        _write_csv(source_report, [
            "原文使用字元", "Unicode", "Unicode 名稱", "類別", "出現次數", "使用檔案數", "檔案清單",
        ], source_rows)
    else:
        stale = output / "source_character_report.csv"
        if stale.exists():
            stale.unlink()

    manual_excluded = result.excluded_sources
    mapped_sources = set(result.mapping)
    mapped_targets = set(result.mapping.values())
    glyph_rows = []
    for glyph in result.glyphs:
        char = glyph.character
        reasons = candidate_exclusion_reasons(char, result)
        glyph_rows.append([
            char, glyph.codepoint, glyph.name, glyph.category, glyph.cp932_hex,
            "是" if char in result.stats else "否",
            "未掃描" if not result.source_scan_performed else ("是" if char in result.original_characters else "否"),
            "是" if char in result.common_characters else "否",
            "是" if char in result.symbol_exclusions.get("zh_punctuation", set()) else "否",
            "是" if char in result.symbol_exclusions.get("ja_punctuation", set()) else "否",
            "是" if char in result.symbol_exclusions.get("common_symbols", set()) else "否",
            "是" if char in result.custom_excluded_characters else "否",
            "是" if char in manual_excluded else "否",
            "是" if char in mapped_sources or char in mapped_targets else "否",
            "是" if not reasons else "否",
            "；".join(reasons),
        ])
    glyph_report = output / "cp932_glyphs.csv"
    _write_csv(glyph_report, [
        "字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組",
        "翻譯已使用", "原文已使用", "常用字清單命中", "中文標點命中", "日文標點命中",
        "常用符號命中", "自訂排除", "人工排除", "映射占用", "可用候選", "排除原因",
    ], glyph_rows)

    candidate_report = output / "safe_candidate_glyphs.csv"
    _write_csv(candidate_report, [
        "來源候選字元", "Unicode", "Unicode 名稱", "類別", "CP932 位元組",
    ], ([g.character, g.codepoint, g.name, g.category, g.cp932_hex] for g in result.safe_candidates))

    missing = result.missing_targets
    unmapped_txt = output / "unmapped_characters.txt"
    unmapped_txt.write_text("".join(stat.character for stat in missing), encoding="utf-8", newline="\n")

    mapping_report = output / "mapping_pairs.csv"
    _write_csv(mapping_report, ["來源字元", "目標字元", "來源 Unicode", "目標 Unicode"], (
        [source, target, f"U+{ord(source):04X}", f"U+{ord(target):04X}"]
        for source, target in result.mapping.items()
    ))

    metadata_report = output / "scan_metadata.json"
    metadata = {
        "version": 3,
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
        "common_characters_file": str(COMMON_CHARACTERS_PATH),
        "common_characters_count": len(result.common_characters),
        "common_characters_sha256": result.common_characters_sha256,
        "exclusion_config_path": str(result.exclusion_config_path),
        "exclusion_config_sha256": result.exclusion_config_sha256,
        "manual_excluded_sources": sorted(result.excluded_sources, key=ord),
        "source_errors": result.source_errors,
        "errors": result.errors,
        "mapping_pairs": len(result.mapping),
        "mapping_export": "使用 GUI 匯出兩行 TXT；本工具不讀取或寫入 UIF。",
    }
    metadata_report.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    guide = output / "README_報表說明.txt"
    if not result.translation_scan_complete:
        translation_status = "不完整；安全候選已停用，不能匯出映射。"
    else:
        translation_status = "完整。"
    source_status = (
        f"已指定：{result.source_root}\n有效腳本：{result.scanned_source_files}\n"
        f"掃描完整：{'是' if result.source_scan_complete else '否'}"
        if result.source_scan_performed else "未指定；候選未經原始遊戲腳本占用檢查。"
    )
    guide.write_text("\n".join([
        "NekoSDKTools 字元管理掃描報表", "=" * 36, "",
        f"翻譯文件：{result.translation_root}",
        f"翻譯檔案：{result.scanned_translation_files}",
        f"翻譯掃描：{translation_status}",
        "原始遊戲腳本：", source_status, "",
        f"自訂常用字清單：{COMMON_CHARACTERS_PATH}",
        f"常用字數量（去除空白及重複後）：{len(result.common_characters)}",
        f"常用字排除：{'啟用' if result.candidate_filters.get('common_characters', True) else '停用'}",
        f"中文標點排除：{'啟用' if result.candidate_filters.get('zh_punctuation', True) else '停用'}",
        f"日文標點排除：{'啟用' if result.candidate_filters.get('ja_punctuation', True) else '停用'}",
        f"常用符號排除：{'啟用' if result.candidate_filters.get('common_symbols', True) else '停用'}", "",
        "檔案用途：",
        "character_report.csv：翻譯文件字元統計、出現次數、檔案數及 CP932 狀態。",
        "source_character_report.csv：原始腳本可辨識文字統計；只有指定原文時產生。",
        "cp932_glyphs.csv：CP932 字形目錄、使用狀態、各項排除命中及排除原因。",
        "safe_candidate_glyphs.csv：通過本次掃描及排除條件的來源候選。",
        "unmapped_characters.txt：尚未映射的 CP932 不支援目標字元。",
        "mapping_pairs.csv：本次工作區目前建立的映射清單。",
        "scan_metadata.json：掃描路徑、編碼、篩選條件、清單雜湊及完整性。",
        "mapping_draft.json：GUI 保存的工作草稿；保存後才產生。", "",
        "注意：常用字.txt 是使用者自訂清單，不是教育部 4,808 字表。",
        "常用字及標點／符號排除只影響來源候選，不影響翻譯統計或待替換目標。",
        "未指定原始遊戲腳本時，候選沒有經過原文占用檢查。",
        "任何必要掃描不完整時，安全候選會清空。",
        "本工具只匯出兩行替換字串，不讀取、不修改、不合併 UIF 設定。",
    ]) + "\n", encoding="utf-8", newline="\n")

    result_paths = {
        "character_report": character_report,
        "glyph_report": glyph_report,
        "candidate_report": candidate_report,
        "unmapped_txt": unmapped_txt,
        "mapping_report": mapping_report,
        "metadata_report": metadata_report,
        "report_guide": guide,
    }
    if source_report is not None:
        result_paths["source_report"] = source_report
    return result_paths


def scan_project(
    translation_root: Path,
    source_root: Path | None = None,
    report_root: Path | None = None,
    settings_path: Path | None = None,
    *,
    text_encoding: str = "utf-8-sig",
    command_encoding: str = "cp932",
    dialogue_encoding: str = "auto",
    candidate_filters: dict[str, bool] | None = None,
) -> ScanResult:
    """掃描翻譯文件與選填的已解包 ADVSCRIPT2 腳本。"""
    translation_root = translation_root.expanduser().resolve()
    if not translation_root.is_dir():
        raise CharacterManagerError(f"翻譯來源必須是存在的資料夾：{translation_root}")
    text_encoding = validate_text_encoding(text_encoding)

    resolved_source: Path | None = None
    if source_root is not None and str(source_root).strip():
        resolved_source = source_root.expanduser().resolve()
        if not resolved_source.exists():
            raise CharacterManagerError(f"原始遊戲腳本位置不存在：{resolved_source}")

    if report_root is None:
        report_root = translation_root.parent / (translation_root.name + "_char_report")
    report_root = report_root.expanduser().resolve()
    if _path_is_within(report_root, translation_root):
        raise CharacterManagerError("報表資料夾不可放在翻譯資料夾內。")
    if resolved_source and resolved_source.is_dir() and _path_is_within(report_root, resolved_source):
        raise CharacterManagerError("報表資料夾不可放在原始腳本資料夾內。")

    settings = load_manager_settings(settings_path or DEFAULT_SETTINGS)
    active_filters = dict(settings["candidate_filters"])
    if candidate_filters is not None:
        for key in FILTER_KEYS:
            if key in candidate_filters:
                if not isinstance(candidate_filters[key], bool):
                    raise CharacterManagerError(f"候選排除選項 {key} 必須是布林值。")
                active_filters[key] = candidate_filters[key]

    exclusion_data = load_candidate_exclusion_data()
    common_chars = load_common_characters()
    symbol_groups = {
        key: set(exclusion_data["symbol_groups"][key])
        for key in ("zh_punctuation", "ja_punctuation", "common_symbols")
    }
    stats, scanned, errors = scan_translation_root(translation_root, text_encoding)
    if scanned == 0 and not errors:
        raise CharacterManagerError("指定位置沒有找到支援的翻譯文件（JSON／TXT／CSV／TSV／PO／FTL 等）。")

    source_stats: dict[str, CharacterStat] = {}
    source_scanned = 0
    source_errors: list[str] = []
    if resolved_source is not None:
        source_stats, source_scanned, source_errors = scan_source_root(
            resolved_source, command_encoding, dialogue_encoding
        )

    result = ScanResult(
        translation_root=translation_root,
        report_root=report_root,
        translation_text_encoding=text_encoding,
        stats=stats,
        glyphs=build_cp932_glyph_catalog(),
        scanned_translation_files=scanned,
        translation_scan_complete=not errors,
        errors=errors,
        excluded_sources=set(settings["excluded_source_characters"]),
        excluded_targets=set(settings["excluded_target_characters"]),
        source_root=resolved_source,
        source_scan_performed=resolved_source is not None,
        source_scan_complete=None if resolved_source is None else not source_errors,
        scanned_source_files=source_scanned,
        source_stats=source_stats,
        source_errors=source_errors,
        command_encoding=command_encoding,
        dialogue_encoding=dialogue_encoding,
        candidate_filters=active_filters,
        common_characters=common_chars,
        symbol_exclusions=symbol_groups,
        custom_excluded_characters=set(exclusion_data["custom_excluded_characters"]),
        exclusion_config_path=CANDIDATE_EXCLUSION_CONFIG_PATH,
        exclusion_config_sha256=_file_sha256(CANDIDATE_EXCLUSION_CONFIG_PATH),
        common_characters_sha256=_file_sha256(COMMON_CHARACTERS_PATH),
    )
    write_reports(result)
    return result


# ---------------------------------------------------------------------------
# 兩行替換字串匯出
# ---------------------------------------------------------------------------

def make_two_line_mapping(mapping: dict[str, str]) -> str:
    """產生只有兩行的替換字串：第一行來源，第二行目標。"""
    if not mapping:
        raise CharacterManagerError("目前沒有可匯出的映射。")
    sources = "".join(mapping.keys())
    targets = "".join(mapping.values())
    if len(sources) != len(targets):
        raise CharacterManagerError("來源與目標字元數量不一致，取消匯出。")
    if len(set(sources)) != len(sources):
        raise CharacterManagerError("來源字元有重複，取消匯出。")
    if len(set(targets)) != len(targets):
        raise CharacterManagerError("目標字元有重複，取消匯出。")
    return sources + "\n" + targets + "\n"


def export_two_line_mapping(path: Path, mapping: dict[str, str]) -> None:
    payload = make_two_line_mapping(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class CharacterManagerFrame(ttk.Frame):
    """獨立字元管理 GUI；只輸出兩行替換字串，不連接 UIF。"""

    def __init__(self, master: tk.Misc, app_dir: Path | None = None, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.app_dir = (app_dir or APP_DIR).resolve()
        self.settings_path = self.app_dir / "tool" / "char_manager.json"
        self.last_work_path = self.app_dir / "tool" / "char_manager_last_work.json"
        self.result: ScanResult | None = None
        self.mapping: dict[str, str] = {}
        self.excluded_sources: set[str] = set()
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._resume_draft: dict[str, Any] | None = None
        self._resume_warnings: list[str] = []

        self.initial_settings = load_manager_settings(self.settings_path)
        self.excluded_sources = set(self.initial_settings["excluded_source_characters"])
        self._build_ui()
        self._refresh_resume_button()
        self.after(120, self._poll_events)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        path_box = ttk.LabelFrame(self, text="掃描來源")
        path_box.grid(row=0, column=0, sticky="ew", padx=5, pady=4)
        path_box.columnconfigure(1, weight=1)

        self.translation_var = tk.StringVar()
        self.source_var = tk.StringVar()
        self.encoding_var = tk.StringVar(value=self.initial_settings["text_encoding"])
        self.command_encoding_var = tk.StringVar(value=self.initial_settings["source_command_encoding"])
        self.dialogue_encoding_var = tk.StringVar(value=self.initial_settings["source_dialogue_encoding"])

        ttk.Label(path_box, text="翻譯文件資料夾：").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(path_box, textvariable=self.translation_var).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(path_box, text="選擇…", command=self._pick_translation).grid(row=0, column=2, padx=4, pady=3)
        ttk.Label(path_box, text="原始腳本資料夾（選填）：").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(path_box, textvariable=self.source_var).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(path_box, text="選擇…", command=self._pick_source).grid(row=1, column=2, padx=4, pady=3)

        options = ttk.LabelFrame(self, text="編碼與來源候選排除")
        options.grid(row=1, column=0, sticky="ew", padx=5, pady=4)
        ttk.Label(options, text="翻譯文字編碼：").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Combobox(options, textvariable=self.encoding_var, values=("utf-8-sig", "utf-8", "cp932", "gbk", "big5"), width=14).grid(row=0, column=1, sticky="w", padx=4, pady=3)
        ttk.Label(options, text="原文指令編碼：").grid(row=0, column=2, sticky="w", padx=4, pady=3)
        ttk.Combobox(options, textvariable=self.command_encoding_var, values=("cp932", "shift_jis", "utf-8", "gbk", "big5"), width=14).grid(row=0, column=3, sticky="w", padx=4, pady=3)
        ttk.Label(options, text="原文對話編碼：").grid(row=0, column=4, sticky="w", padx=4, pady=3)
        ttk.Combobox(options, textvariable=self.dialogue_encoding_var, values=("auto", "cp932", "gbk", "utf-8", "big5"), width=14).grid(row=0, column=5, sticky="w", padx=4, pady=3)

        self.candidate_filter_vars = {
            key: tk.BooleanVar(value=self.initial_settings["candidate_filters"].get(key, True))
            for key in FILTER_KEYS
        }
        labels = {
            "common_characters": "排除 tool/常用字.txt",
            "zh_punctuation": "排除中文標點",
            "ja_punctuation": "排除日文標點",
            "common_symbols": "排除常用符號",
        }
        for index, key in enumerate(FILTER_KEYS):
            ttk.Checkbutton(
                options, text=labels[key], variable=self.candidate_filter_vars[key],
                command=self._on_candidate_filter_changed,
            ).grid(row=1, column=index, sticky="w", padx=6, pady=3)

        command_row = ttk.Frame(self)
        command_row.grid(row=2, column=0, sticky="ew", padx=5, pady=4)
        self.scan_button = ttk.Button(command_row, text="開始掃描與統計", command=self._start_scan)
        self.scan_button.pack(side="left", padx=3)
        ttk.Label(command_row, text="候選搜尋：").pack(side="left", padx=(12, 2))
        self.candidate_search_var = tk.StringVar()
        search_entry = ttk.Entry(command_row, textvariable=self.candidate_search_var, width=22)
        search_entry.pack(side="left", padx=3)
        search_entry.bind("<KeyRelease>", lambda _event: self._refresh_candidate_tree())
        self.status_var = tk.StringVar(value="請選擇翻譯文件資料夾。")
        ttk.Label(command_row, textvariable=self.status_var).pack(side="left", padx=10)

        help_label = ttk.Label(
            self,
            text="常用字清單只排除來源候選，不影響翻譯統計或待替換目標。未指定原始腳本時，不會檢查原文占用。",
            foreground="#666666",
        )
        help_label.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 3))

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=5, column=0, sticky="nsew", padx=5, pady=4)
        target_box = ttk.LabelFrame(panes, text="待替換目標字元（CP932 無法編碼）")
        candidate_box = ttk.LabelFrame(panes, text="來源候選字元（CP932 可編碼）")
        panes.add(target_box, weight=1)
        panes.add(candidate_box, weight=2)
        target_box.rowconfigure(0, weight=1)
        target_box.columnconfigure(0, weight=1)
        candidate_box.rowconfigure(0, weight=1)
        candidate_box.columnconfigure(0, weight=1)

        self.target_tree = ttk.Treeview(target_box, columns=("count", "files", "unicode"), show="tree headings", height=10)
        self.target_tree.heading("#0", text="目標字元")
        self.target_tree.heading("count", text="次數")
        self.target_tree.heading("files", text="檔案數")
        self.target_tree.heading("unicode", text="Unicode")
        self.target_tree.column("#0", width=95, anchor="center")
        self.target_tree.column("count", width=60, anchor="e")
        self.target_tree.column("files", width=65, anchor="e")
        self.target_tree.column("unicode", width=100, anchor="center")
        self.target_tree.grid(row=0, column=0, sticky="nsew")
        target_scroll = ttk.Scrollbar(target_box, orient="vertical", command=self.target_tree.yview)
        target_scroll.grid(row=0, column=1, sticky="ns")
        self.target_tree.configure(yscrollcommand=target_scroll.set)

        self.candidate_tree = ttk.Treeview(candidate_box, columns=("unicode", "name", "bytes"), show="tree headings", height=10)
        self.candidate_tree.heading("#0", text="來源字元")
        self.candidate_tree.heading("unicode", text="Unicode")
        self.candidate_tree.heading("name", text="Unicode 名稱")
        self.candidate_tree.heading("bytes", text="CP932 位元組")
        self.candidate_tree.column("#0", width=90, anchor="center")
        self.candidate_tree.column("unicode", width=100, anchor="center")
        self.candidate_tree.column("name", width=220)
        self.candidate_tree.column("bytes", width=90, anchor="center")
        self.candidate_tree.grid(row=0, column=0, sticky="nsew")
        candidate_scroll = ttk.Scrollbar(candidate_box, orient="vertical", command=self.candidate_tree.yview)
        candidate_scroll.grid(row=0, column=1, sticky="ns")
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)

        action_row = ttk.Frame(self)
        action_row.grid(row=6, column=0, sticky="ew", padx=5, pady=4)
        for label, command in (
            ("建立配對 →", self._add_pair),
            ("移除配對", self._remove_pair),
            ("排除選取來源字元", self._exclude_candidate),
            ("清空人工排除", self._clear_exclusions),
            ("匯出兩行替換字串…", self._export_two_line_mapping),
            ("保存工作草稿", self._save_mapping_draft),
        ):
            ttk.Button(action_row, text=label, command=command).pack(side="left", padx=3)
        self.resume_button = ttk.Button(action_row, text="繼續上次工作…", command=self._resume_last_work, state="disabled")
        self.resume_button.pack(side="right", padx=3)

        mapping_box = ttk.LabelFrame(self, text="本次工作映射（來源 → 目標）")
        mapping_box.grid(row=7, column=0, sticky="ew", padx=5, pady=4)
        mapping_box.columnconfigure(0, weight=1)
        self.mapping_tree = ttk.Treeview(mapping_box, columns=("target", "source_unicode", "target_unicode"), show="tree headings", height=5)
        self.mapping_tree.heading("#0", text="來源字元")
        self.mapping_tree.heading("target", text="目標字元")
        self.mapping_tree.heading("source_unicode", text="來源 Unicode")
        self.mapping_tree.heading("target_unicode", text="目標 Unicode")
        self.mapping_tree.column("#0", width=100, anchor="center")
        self.mapping_tree.column("target", width=100, anchor="center")
        self.mapping_tree.column("source_unicode", width=120, anchor="center")
        self.mapping_tree.column("target_unicode", width=120, anchor="center")
        self.mapping_tree.grid(row=0, column=0, sticky="ew")
        mapping_scroll = ttk.Scrollbar(mapping_box, orient="vertical", command=self.mapping_tree.yview)
        mapping_scroll.grid(row=0, column=1, sticky="ns")
        self.mapping_tree.configure(yscrollcommand=mapping_scroll.set)

        self.summary_var = tk.StringVar(value="映射尚未建立。")
        ttk.Label(self, textvariable=self.summary_var).grid(row=8, column=0, sticky="w", padx=8, pady=4)

    def _pick_translation(self) -> None:
        value = filedialog.askdirectory(title="選擇翻譯文件資料夾", parent=self)
        if value:
            self.translation_var.set(value)

    def _pick_source(self) -> None:
        value = filedialog.askdirectory(title="選擇已解包的原始遊戲腳本資料夾（選填）", parent=self)
        if value:
            self.source_var.set(value)

    def _current_candidate_filters(self) -> dict[str, bool]:
        return {key: bool(variable.get()) for key, variable in self.candidate_filter_vars.items()}

    def _on_candidate_filter_changed(self) -> None:
        if self.result is not None:
            self.result.candidate_filters = self._current_candidate_filters()
            self._refresh_all()
        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("設定儲存失敗", str(exc), parent=self)

        # 候選排除開關也是本次工作狀態；立即同步草稿並保留現有映射。
        if self.result is not None:
            self._save_mapping_draft(show_message=False)

    def _save_settings_from_ui(self) -> None:
        settings = load_manager_settings(self.settings_path)
        settings["excluded_source_characters"] = sorted(self.excluded_sources, key=ord)
        settings["text_encoding"] = self.encoding_var.get().strip() or "utf-8-sig"
        settings["source_command_encoding"] = self.command_encoding_var.get().strip() or "cp932"
        settings["source_dialogue_encoding"] = self.dialogue_encoding_var.get().strip() or "auto"
        settings["candidate_filters"] = self._current_candidate_filters()
        save_manager_settings(self.settings_path, settings)

    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        translation_text = self.translation_var.get().strip().strip('"')
        translation = Path(translation_text).expanduser()
        if not translation.is_dir():
            messagebox.showerror("無法掃描", "請指定存在的翻譯文件資料夾。", parent=self)
            return
        source_text = self.source_var.get().strip().strip('"')
        source_root: Path | None = None
        if source_text:
            source_root = Path(source_text).expanduser()
            if not source_root.is_dir():
                messagebox.showerror("原始腳本路徑錯誤", "GUI 的原始腳本欄位請選擇已解包的資料夾。", parent=self)
                return
        encoding = self.encoding_var.get().strip() or "utf-8-sig"
        command_encoding = self.command_encoding_var.get().strip() or "cp932"
        dialogue_encoding = self.dialogue_encoding_var.get().strip() or "auto"
        filters = self._current_candidate_filters()
        try:
            validate_text_encoding(encoding)
            settings = load_manager_settings(self.settings_path)
            settings["text_encoding"] = encoding
            settings["source_command_encoding"] = command_encoding
            settings["source_dialogue_encoding"] = dialogue_encoding
            settings["candidate_filters"] = filters
            save_manager_settings(self.settings_path, settings)
            self.excluded_sources = set(settings["excluded_source_characters"])
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("設定錯誤", str(exc), parent=self)
            return

        if self.result is not None and self.mapping and self._resume_draft is None:
            old_source = self.result.source_root.resolve() if self.result.source_root else None
            new_source = source_root.resolve() if source_root else None
            if translation.resolve() == self.result.translation_root.resolve() and old_source == new_source:
                self._resume_draft = self._draft_payload()
            else:
                self._save_mapping_draft(show_message=False)

        report = translation.resolve().parent / (translation.resolve().name + "_char_report")
        self.scan_button.configure(state="disabled")
        self.status_var.set("掃描中：讀取翻譯文件、原始腳本及 CP932 字形目錄…")

        def worker() -> None:
            try:
                result = scan_project(
                    translation_root=translation,
                    source_root=source_root,
                    report_root=report,
                    settings_path=self.settings_path,
                    text_encoding=encoding,
                    command_encoding=command_encoding,
                    dialogue_encoding=dialogue_encoding,
                    candidate_filters=filters,
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
                    self.excluded_sources = set(payload.excluded_sources)
                    self.mapping = {}
                    self._resume_warnings = []
                    if self._resume_draft is not None:
                        try:
                            self.mapping = self._validate_restored_mapping(self._resume_draft)
                        except CharacterManagerError as exc:
                            messagebox.showerror("工作草稿無法恢復", f"{exc}\n\n原始草稿不會被覆蓋。", parent=self)
                        finally:
                            self._resume_draft = None
                    self._refresh_all()
                    source_text = (
                        f"原始腳本已掃描 {payload.scanned_source_files} 個檔案"
                        if payload.source_scan_complete else (
                            f"原始腳本掃描不完整（{len(payload.source_errors)} 個錯誤）"
                            if payload.source_scan_performed else "未指定原始腳本，未檢查原文占用"
                        )
                    )
                    self.status_var.set(
                        f"翻譯檔 {payload.scanned_translation_files} 個；"
                        f"待替換 {len(self._current_missing_targets())} 字；"
                        f"安全候選 {len(payload.safe_candidates)} 字。{source_text}。"
                    )
                    warnings = []
                    if payload.errors:
                        warnings.append("翻譯文件錯誤：\n" + "\n".join(payload.errors[:15]))
                    if payload.source_errors:
                        warnings.append("原始腳本錯誤：\n" + "\n".join(payload.source_errors[:15]))
                    if warnings:
                        messagebox.showwarning("部分檔案無法完整掃描", "\n\n".join(warnings), parent=self)
                    if payload.translation_scan_complete and (not payload.source_scan_performed or payload.source_scan_complete):
                        self._save_mapping_draft(show_message=False)
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _current_missing_targets(self) -> list[CharacterStat]:
        if self.result is None:
            return []
        settings = load_manager_settings(self.settings_path)
        excluded = set(settings["excluded_target_characters"])
        mapped = set(self.mapping.values())
        return [
            stat for stat in self.result.missing_targets
            if stat.character not in mapped and stat.character not in excluded
        ]

    def _refresh_all(self) -> None:
        for tree in (self.target_tree, self.candidate_tree, self.mapping_tree):
            tree.delete(*tree.get_children())
        if self.result is None:
            self._update_summary()
            return
        self.result.mapping = dict(self.mapping)
        self.result.excluded_sources = set(self.excluded_sources)
        self.result.candidate_filters = self._current_candidate_filters()
        for stat in self._current_missing_targets():
            self.target_tree.insert(
                "", "end", iid=f"t{ord(stat.character)}", text=stat.character,
                values=(stat.count, len(stat.files), f"U+{ord(stat.character):04X}"),
            )
        self._refresh_candidate_tree()
        for source, target in self.mapping.items():
            self.mapping_tree.insert(
                "", "end", iid=f"m{ord(source)}", text=source,
                values=(target, f"U+{ord(source):04X}", f"U+{ord(target):04X}"),
            )
        try:
            write_reports(self.result)
        except OSError as exc:
            self.status_var.set(f"報表更新失敗：{exc}")
        self._update_summary()

    def _refresh_candidate_tree(self) -> None:
        if self.result is None:
            return
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        query = self.candidate_search_var.get().strip().casefold()
        for glyph in self.result.safe_candidates:
            if glyph.character in self.mapping or glyph.character in self.mapping.values():
                continue
            searchable = " ".join((glyph.character, glyph.codepoint, glyph.name, glyph.category)).casefold()
            if query and query not in searchable:
                continue
            self.candidate_tree.insert(
                "", "end", iid=f"c{ord(glyph.character)}", text=glyph.character,
                values=(glyph.codepoint, glyph.name, glyph.cp932_hex),
            )

    def _update_summary(self) -> None:
        if self.result is None:
            self.summary_var.set("映射尚未建立。")
            return
        self.summary_var.set(
            f"目前 {len(self.mapping)} 組映射；尚未映射 {len(self._current_missing_targets())} 個目標字元。"
            "匯出檔只有兩行：第一行來源字元、第二行目標字元。"
        )

    def _add_pair(self) -> None:
        if self.result is None:
            messagebox.showwarning("尚未掃描", "請先掃描翻譯文件。", parent=self)
            return
        target_selection = self.target_tree.selection()
        source_selection = self.candidate_tree.selection()
        if not target_selection or not source_selection:
            messagebox.showinfo("未選擇字元", "請各選擇一個目標字元及來源候選字元。", parent=self)
            return
        target = chr(int(target_selection[0][1:]))
        source = chr(int(source_selection[0][1:]))
        if source in self.mapping:
            messagebox.showerror("來源重複", f"來源字元 {source!r} 已有配對。", parent=self)
            return
        if target in self.mapping.values():
            messagebox.showerror("目標重複", f"目標字元 {target!r} 已有配對。", parent=self)
            return
        try:
            _validate_result_snapshot(self.result)
            if source not in {glyph.character for glyph in self.result.safe_candidates}:
                raise CharacterManagerError(f"來源字元 {source!r} 目前不是安全候選，請重新掃描。")
            if target not in self.result.stats or can_encode(target, "cp932"):
                raise CharacterManagerError(f"目標字元 {target!r} 不是本次掃描的 CP932 不支援字元。")
            _validate_new_mapping(
                {source: target},
                translation_characters=self.result.stats,
                source_characters=self.result.original_characters,
                excluded_sources=self.excluded_sources,
                excluded_candidate_characters=self.result.excluded_builtin_characters,
                existing_mapping=self.mapping,
            )
        except CharacterManagerError as exc:
            messagebox.showerror("無法建立配對", str(exc), parent=self)
            return
        self.mapping[source] = target
        self.result.mapping = dict(self.mapping)
        self._refresh_all()
        self._save_mapping_draft(show_message=False)

    def _remove_pair(self) -> None:
        selected = self.mapping_tree.selection()
        if not selected:
            return
        source = chr(int(selected[0][1:]))
        self.mapping.pop(source, None)
        if self.result is not None:
            self.result.mapping = dict(self.mapping)
        self._refresh_all()
        self._save_mapping_draft(show_message=False)

    def _exclude_candidate(self) -> None:
        selected = self.candidate_tree.selection()
        if not selected:
            messagebox.showinfo("未選擇字元", "請先選擇要排除的來源候選字元。", parent=self)
            return
        character = chr(int(selected[0][1:]))
        self.excluded_sources.add(character)
        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=self)
            return
        self._refresh_all()
        self._save_mapping_draft(show_message=False)
        self.status_var.set(f"已將 {character} 加入人工排除清單。")

    def _clear_exclusions(self) -> None:
        if not self.excluded_sources:
            messagebox.showinfo("排除清單", "目前沒有人工排除的來源字元。", parent=self)
            return
        if not messagebox.askyesno("清空人工排除", f"確定清除 {len(self.excluded_sources)} 個字元？", parent=self):
            return
        self.excluded_sources.clear()
        try:
            self._save_settings_from_ui()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=self)
            return
        self._refresh_all()
        self._save_mapping_draft(show_message=False)

    def _export_two_line_mapping(self) -> None:
        if self.result is None:
            messagebox.showwarning("尚未掃描", "請先掃描並建立替換映射。", parent=self)
            return
        try:
            validate_work_mapping(self.result, self.mapping)
        except CharacterManagerError as exc:
            messagebox.showerror("映射驗證失敗", str(exc), parent=self)
            return
        if not self.mapping:
            messagebox.showwarning("沒有映射", "目前沒有可匯出的替換映射。", parent=self)
            return
        value = filedialog.asksaveasfilename(
            title="匯出兩行替換字串", initialdir=str(self.result.report_root),
            initialfile="replacement_mapping.txt", defaultextension=".txt",
            filetypes=(("文字檔", "*.txt"), ("所有檔案", "*.*")), parent=self,
        )
        if not value:
            return
        try:
            export_two_line_mapping(Path(value), self.mapping)
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("匯出失敗", str(exc), parent=self)
            return
        self.status_var.set(f"已匯出兩行替換字串：{value}")
        messagebox.showinfo(
            "匯出完成",
            "檔案只有兩行：\n第一行是 source_characters。\n第二行是 target_characters。\n請自行貼入你的 UIF 設定。",
            parent=self,
        )

    def _draft_payload(self) -> dict[str, Any]:
        if self.result is None:
            raise CharacterManagerError("尚未掃描，沒有可保存的工作資料。")
        return {
            "version": 3,
            "translation_root": str(self.result.translation_root),
            "report_root": str(self.result.report_root),
            "translation_text_encoding": self.encoding_var.get().strip() or "utf-8-sig",
            "source_root": str(self.result.source_root) if self.result.source_root else None,
            "source_command_encoding": self.result.command_encoding,
            "source_dialogue_encoding": self.result.dialogue_encoding,
            "candidate_filters": dict(self.result.candidate_filters),
            "mapping": [{"source": source, "target": target} for source, target in self.mapping.items()],
        }

    def _save_mapping_draft(self, show_message: bool = True) -> None:
        if self.result is None:
            if show_message:
                messagebox.showwarning("尚未掃描", "請先掃描翻譯文件。", parent=self)
            return
        try:
            draft_path = self.result.report_root / "mapping_draft.json"
            if draft_path.is_file():
                backup = draft_path.with_suffix(draft_path.suffix + ".bak")
                import shutil
                shutil.copy2(draft_path, backup)
            atomic_write_json(draft_path, self._draft_payload())
            atomic_write_json(self.last_work_path, {"version": 1, "draft_path": str(draft_path)})
            self._refresh_resume_button()
            if show_message:
                messagebox.showinfo("保存完成", f"工作草稿已保存：\n{draft_path}", parent=self)
        except (OSError, CharacterManagerError) as exc:
            if show_message:
                messagebox.showerror("保存失敗", str(exc), parent=self)
            else:
                self.status_var.set(f"自動保存草稿失敗：{exc}")

    def _read_last_draft(self) -> tuple[Path, dict[str, Any]]:
        if not self.last_work_path.is_file():
            raise CharacterManagerError("找不到上次工作的索引檔。")
        try:
            pointer = json.loads(self.last_work_path.read_text(encoding="utf-8-sig"))
            if not isinstance(pointer, dict) or pointer.get("version") != 1:
                raise ValueError("上次工作索引版本或格式錯誤。")
            draft_path = Path(pointer["draft_path"]).expanduser()
            draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise CharacterManagerError(f"無法讀取上次工作草稿：{exc}") from exc
        # 舊版草稿可能曾匯入外部 UIF 映射，不能自動把那些字元帶入新工作。
        if not isinstance(draft, dict) or draft.get("version") != 3:
            raise CharacterManagerError(
                "這是舊版工作草稿，可能包含曾從其他設定載入的映射。\n"
                "為避免舊字元混入，新版本不會自動還原舊草稿；請重新掃描並建立新工作。"
            )
        if not isinstance(draft.get("mapping"), list) or not draft.get("translation_root"):
            raise CharacterManagerError("工作草稿缺少必要欄位。")
        return draft_path, draft

    def _refresh_resume_button(self) -> None:
        available = False
        if self.last_work_path.is_file():
            try:
                pointer = json.loads(self.last_work_path.read_text(encoding="utf-8-sig"))
                draft_path = Path(pointer.get("draft_path", ""))
                if draft_path.is_file():
                    draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
                    available = isinstance(draft, dict) and draft.get("version") == 3
            except (OSError, ValueError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError):
                available = False
        self.resume_button.configure(state="normal" if available else "disabled")

    def _resume_last_work(self) -> None:
        try:
            _draft_path, draft = self._read_last_draft()
            translation = Path(str(draft["translation_root"])).expanduser()
            if not translation.is_dir():
                raise CharacterManagerError(f"上次使用的翻譯資料夾不存在：\n{translation}")
            source_text = str(draft.get("source_root") or "")
            if source_text and not Path(source_text).is_dir():
                raise CharacterManagerError(f"上次使用的原始腳本資料夾不存在：\n{source_text}")
            self.translation_var.set(str(translation))
            self.source_var.set(source_text)
            self.encoding_var.set(str(draft.get("translation_text_encoding", "utf-8-sig")))
            self.command_encoding_var.set(str(draft.get("source_command_encoding", "cp932")))
            self.dialogue_encoding_var.set(str(draft.get("source_dialogue_encoding", "auto")))
            saved_filters = draft.get("candidate_filters", {})
            if isinstance(saved_filters, dict):
                for key, variable in self.candidate_filter_vars.items():
                    variable.set(bool(saved_filters.get(key, True)))
            self._resume_draft = draft
            self._start_scan()
        except (OSError, CharacterManagerError) as exc:
            messagebox.showerror("無法繼續工作", str(exc), parent=self)

    def _validate_restored_mapping(self, draft: dict[str, Any]) -> dict[str, str]:
        if self.result is None:
            raise CharacterManagerError("沒有最新掃描結果，無法恢復映射。")
        pairs = draft.get("mapping", [])
        if not isinstance(pairs, list):
            raise CharacterManagerError("草稿 mapping 必須是陣列。")
        restored: dict[str, str] = {}
        warnings: list[str] = []
        for index, pair in enumerate(pairs, start=1):
            if not isinstance(pair, dict):
                warnings.append(f"第 {index} 組資料格式錯誤，已略過。")
                continue
            source, target = pair.get("source"), pair.get("target")
            if not isinstance(source, str) or len(source) != 1 or not isinstance(target, str) or len(target) != 1:
                warnings.append(f"第 {index} 組來源或目標不是單一字元，已略過。")
                continue
            try:
                if source in restored or target in restored.values():
                    raise CharacterManagerError("來源或目標重複。")
                if source not in {glyph.character for glyph in self.result.safe_candidates}:
                    raise CharacterManagerError("來源不再是安全候選。")
                if target not in {stat.character for stat in self._current_missing_targets()}:
                    raise CharacterManagerError("目標已不在目前待替換清單中。")
                _validate_new_mapping(
                    {source: target}, translation_characters=self.result.stats,
                    source_characters=self.result.original_characters,
                    excluded_sources=self.excluded_sources,
                    excluded_candidate_characters=self.result.excluded_builtin_characters,
                    existing_mapping=restored,
                )
                restored[source] = target
            except CharacterManagerError as exc:
                warnings.append(f"第 {index} 組 {source!r} → {target!r}：{exc}")
        if warnings:
            self._resume_warnings = warnings
            messagebox.showwarning("工作草稿部分恢復", "以下映射未恢復：\n\n" + "\n".join(warnings[:30]), parent=self)
        return restored


# ---------------------------------------------------------------------------
# CLI：僅提供掃描，不提供任何 UIF 套用功能
# ---------------------------------------------------------------------------

def _command_scan(args: argparse.Namespace) -> int:
    result = scan_project(
        translation_root=Path(args.translations),
        source_root=Path(args.source) if args.source else None,
        report_root=Path(args.output) if args.output else None,
        settings_path=Path(args.settings),
        text_encoding=args.text_encoding,
        command_encoding=args.command_encoding,
        dialogue_encoding=args.dialogue_encoding,
    )
    print(f"翻譯檔案：{result.scanned_translation_files}")
    print(f"翻譯掃描完整：{'是' if result.translation_scan_complete else '否'}")
    print(f"統計字元：{len(result.stats)}")
    print(f"CP932 不支援且尚未映射：{len(result.missing_targets)}")
    print(f"CP932 可用候選：{len(result.safe_candidates)}")
    if result.source_scan_performed:
        print(f"原始腳本：{result.scanned_source_files} 個有效腳本；完整性：{'完整' if result.source_scan_complete else '不完整'}")
    else:
        print("原始腳本：未指定；未檢查原文占用")
    print(f"報表資料夾：{result.report_root}")
    for name, path in write_reports(result).items():
        print(f"{name}: {path}")
    for error in result.errors[:30]:
        print("翻譯警告：" + error, file=sys.stderr)
    for error in result.source_errors[:30]:
        print("原文警告：" + error, file=sys.stderr)
    return 1 if result.errors or result.source_errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="統計翻譯文字及管理 CP932 替代字元映射。")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="掃描翻譯文件及選填的原始 ADVSCRIPT2 腳本")
    scan.add_argument("--translations", required=True, help="翻譯 JSON／TXT 資料夾")
    scan.add_argument("--source", help="選填：已解包的原始 ADVSCRIPT2 腳本檔案或資料夾")
    scan.add_argument("--command-encoding", default="cp932", help="原始腳本指令／場景編碼")
    scan.add_argument("--dialogue-encoding", default="auto", help="原始腳本對話編碼")
    scan.add_argument("--output", help="報表輸出資料夾")
    scan.add_argument("--settings", default=str(DEFAULT_SETTINGS), help="char_manager.json 路徑")
    scan.add_argument("--text-encoding", default="utf-8-sig", help="TXT／CSV 等翻譯文件編碼")
    scan.set_defaults(func=_command_scan)
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
