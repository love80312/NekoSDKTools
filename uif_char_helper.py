#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NekoSDKTools UIF 替代字元輔助工具。

功能：
1. 讀取使用者自行指定的遊戲 uif_config.json，不讀取專案 tool/uif_config.json。
2. 讀取 char_manager 報表中的 unmapped_characters.txt、safe_candidate_glyphs.csv
   及 scan_metadata.json。
3. 保留 UIF 既有映射，排除已使用的來源／目標，自動建立新增的一對一配對。
4. 輸出可直接貼入 UIF 的完整兩行 source_characters / target_characters。
5. 依完整映射將翻譯文件中的目標字替換為來源字，另存新資料夾，不覆寫原始檔。

僅使用 Python 3.10+ 標準函式庫；不匯入 char_manager.py。
"""
from __future__ import annotations

import argparse
import codecs
import csv
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Iterable


APP_DIR = Path(__file__).resolve().parent
TEXT_EXTENSIONS = {
    ".json", ".txt", ".csv", ".tsv", ".po", ".pot", ".ftl", ".ini",
    ".xml", ".html", ".htm", ".md", ".yaml", ".yml",
}


class UIFHelperError(Exception):
    """UIF 輔助工具錯誤。"""


@dataclass(frozen=True)
class MappingPair:
    source: str
    target: str
    is_existing: bool = False


@dataclass
class HelperContext:
    uif_path: Path
    report_root: Path
    translation_root: Path | None
    text_encoding: str
    substitution_enabled: bool
    repeat_characters: list[str]
    existing_pairs: list[MappingPair]
    work_pairs: list[MappingPair]
    target_characters: list[str]
    already_mapped_targets: list[str]
    target_counts: dict[str, int]
    candidate_characters: list[str]
    metadata: dict[str, Any]
    warnings: list[str]


def _one_unicode_character(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 1:
        raise UIFHelperError(f"{label} 必須是單一 Unicode 字元：{value!r}")
    codepoint = ord(value)
    if 0xD800 <= codepoint <= 0xDFFF:
        raise UIFHelperError(f"{label} 不可使用 UTF-16 surrogate：U+{codepoint:04X}")
    return value


def read_uif(path: Path) -> tuple[bool, list[MappingPair], list[str]]:
    """讀取指定的外部 UIF；保留既有配對順序及重複目標。"""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise UIFHelperError(f"找不到 UIF 設定檔：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UIFHelperError(f"UIF JSON 無法讀取：{path}\n{exc}") from exc
    if not isinstance(data, dict):
        raise UIFHelperError("UIF JSON 根節點必須是物件。")
    section = data.get("character_substitution")
    if not isinstance(section, dict):
        raise UIFHelperError("UIF 缺少 character_substitution 物件。")

    enabled = section.get("enable", False)
    if not isinstance(enabled, bool):
        raise UIFHelperError("character_substitution.enable 必須是布林值。")
    sources = section.get("source_characters", "")
    targets = section.get("target_characters", "")
    if not isinstance(sources, str) or not isinstance(targets, str):
        raise UIFHelperError("source_characters 與 target_characters 必須是字串。")
    if len(sources) != len(targets):
        raise UIFHelperError(
            "UIF 既有映射的來源／目標字數不一致："
            f"來源 {len(sources)} 字，目標 {len(targets)} 字。"
        )

    source_seen: set[str] = set()
    pairs: list[MappingPair] = []
    for index, (source, target) in enumerate(zip(sources, targets), start=1):
        _one_unicode_character(source, f"UIF 第 {index} 個來源字")
        _one_unicode_character(target, f"UIF 第 {index} 個目標字")
        if source in source_seen:
            raise UIFHelperError(
                f"UIF source_characters 有重複來源字 {source!r}（第 {index} 個）。"
                "同一來源字對應多個目標時無法安全反向替換，請先人工整理 UIF。"
            )
        source_seen.add(source)
        pairs.append(MappingPair(source, target, True))

    repeat_value = section.get("repeat", [])
    if repeat_value is None:
        repeat_value = []
    if not isinstance(repeat_value, list):
        raise UIFHelperError("character_substitution.repeat 必須是陣列。")
    repeats: list[str] = []
    for index, value in enumerate(repeat_value, start=1):
        repeats.append(_one_unicode_character(value, f"UIF repeat 第 {index} 個項目"))
    return enabled, pairs, repeats


def _read_metadata(report_root: Path) -> dict[str, Any]:
    path = report_root / "scan_metadata.json"
    if not path.is_file():
        raise UIFHelperError(f"報表缺少 scan_metadata.json：{path}")
    try:
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UIFHelperError(f"掃描中繼資料無法讀取：{path}\n{exc}") from exc
    if not isinstance(metadata, dict):
        raise UIFHelperError("scan_metadata.json 根節點必須是物件。")
    if metadata.get("translation_scan_complete") is not True:
        raise UIFHelperError("翻譯文件掃描不完整，禁止自動配對。請先重新完整掃描。")
    if metadata.get("source_scan_performed") and metadata.get("source_scan_complete") is not True:
        raise UIFHelperError("原始腳本掃描不完整，禁止自動配對。請先修正錯誤並重新掃描。")
    return metadata


def _read_unmapped(path: Path) -> tuple[list[str], list[str]]:
    if not path.is_file():
        raise UIFHelperError(f"找不到待替換字元清單：{path}")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise UIFHelperError(f"待替換字元清單無法讀取：{path}\n{exc}") from exc
    result: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for character in content:
        if character.isspace():
            continue
        _one_unicode_character(character, "待替換字元")
        if character in seen:
            warnings.append(f"待替換清單含重複字元 {character!r}，已去重。")
            continue
        seen.add(character)
        result.append(character)
    return result, warnings


def _read_candidates(path: Path) -> tuple[list[str], list[str]]:
    if not path.is_file():
        raise UIFHelperError(f"找不到安全候選字清單：{path}")
    result: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "來源候選字元" not in reader.fieldnames:
                raise UIFHelperError(
                    "safe_candidate_glyphs.csv 缺少「來源候選字元」欄位，"
                    "請使用目前版本 char_manager.py 重新產生報表。"
                )
            for row_number, row in enumerate(reader, start=2):
                character = row.get("來源候選字元", "")
                if not character:
                    continue
                _one_unicode_character(character, f"候選 CSV 第 {row_number} 列")
                try:
                    character.encode("cp932", errors="strict")
                except UnicodeEncodeError:
                    warnings.append(
                        f"候選 CSV 第 {row_number} 列字元 {character!r} 不能以 CP932 編碼，已略過。"
                    )
                    continue
                if character in seen:
                    warnings.append(f"候選 CSV 含重複來源字 {character!r}，已去重。")
                    continue
                seen.add(character)
                result.append(character)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise UIFHelperError(f"候選字 CSV 無法讀取：{path}\n{exc}") from exc
    return result, warnings


def _read_work_mapping(path: Path, existing_pairs: list[MappingPair]) -> tuple[list[MappingPair], list[str]]:
    """讀取 char_manager 的人工映射，避免已配對字元從 unmapped 清單消失後遺失。"""
    if not path.is_file():
        return [], ["報表沒有 mapping_pairs.csv；本次沒有可合併的 char_manager 人工配對。"]

    existing_by_source = {pair.source: pair.target for pair in existing_pairs}
    existing_targets = {pair.target for pair in existing_pairs}
    used_report_sources: set[str] = set()
    used_report_targets: set[str] = set()
    accepted: list[MappingPair] = []
    warnings: list[str] = []

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"來源字元", "目標字元"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise UIFHelperError(
                    "mapping_pairs.csv 缺少「來源字元」或「目標字元」欄位；"
                    "請使用目前版本 char_manager.py 重新產生報表。"
                )
            for row_number, row in enumerate(reader, start=2):
                source = row.get("來源字元", "")
                target = row.get("目標字元", "")
                if not source and not target:
                    continue
                _one_unicode_character(source, f"人工映射 CSV 第 {row_number} 列來源")
                _one_unicode_character(target, f"人工映射 CSV 第 {row_number} 列目標")
                try:
                    source.encode("cp932", errors="strict")
                except UnicodeEncodeError as exc:
                    raise UIFHelperError(
                        f"人工映射 CSV 第 {row_number} 列來源字 {source!r} 無法以 CP932 編碼。"
                    ) from exc
                if source in used_report_sources:
                    raise UIFHelperError(f"mapping_pairs.csv 有重複來源字：{source!r}。")
                if target in used_report_targets:
                    raise UIFHelperError(f"mapping_pairs.csv 有重複目標字：{target!r}。")
                used_report_sources.add(source)
                used_report_targets.add(target)

                if source in existing_by_source:
                    if existing_by_source[source] == target:
                        warnings.append(f"char_manager 人工映射 {source}→{target} 已存在於 UIF，未重複加入。")
                    else:
                        warnings.append(
                            f"char_manager 人工映射 {source}→{target} 與 UIF 既有來源 {source}→"
                            f"{existing_by_source[source]} 衝突，保留 UIF 設定並略過人工映射。"
                        )
                    continue
                if target in existing_targets:
                    warnings.append(
                        f"char_manager 人工映射 {source}→{target} 的目標已由 UIF 其他來源處理，"
                        "保留 UIF 設定並略過人工映射。"
                    )
                    continue
                accepted.append(MappingPair(source, target, False))
                existing_by_source[source] = target
                existing_targets.add(target)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise UIFHelperError(f"mapping_pairs.csv 無法讀取：{path}\n{exc}") from exc
    return accepted, warnings


def load_context(uif_path: Path, report_root: Path) -> HelperContext:
    """讀取指定 UIF 與 char_manager 報表，不載入專案內的 UIF 設定。"""
    uif_path = Path(uif_path).expanduser().resolve()
    report_root = Path(report_root).expanduser().resolve()
    if not report_root.is_dir():
        raise UIFHelperError(f"報表資料夾不存在：{report_root}")
    metadata = _read_metadata(report_root)
    enabled, existing_pairs, repeats = read_uif(uif_path)
    targets, target_warnings = _read_unmapped(report_root / "unmapped_characters.txt")
    candidates, candidate_warnings = _read_candidates(report_root / "safe_candidate_glyphs.csv")
    work_pairs, work_warnings = _read_work_mapping(report_root / "mapping_pairs.csv", existing_pairs)

    existing_targets = {pair.target for pair in existing_pairs}
    existing_sources = {pair.source for pair in existing_pairs}
    work_targets = {pair.target for pair in work_pairs}
    work_sources = {pair.source for pair in work_pairs}
    already_mapped_targets = [char for char in targets if char in existing_targets]
    pending_targets = [char for char in targets if char not in existing_targets and char not in work_targets]
    available_candidates = [
        char for char in candidates if char not in existing_sources and char not in work_sources
    ]

    target_counts: dict[str, int] = {}
    character_report = report_root / "character_report.csv"
    if character_report.is_file():
        try:
            with character_report.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames and "字元" in reader.fieldnames and "出現次數" in reader.fieldnames:
                    for row in reader:
                        char = row.get("字元", "")
                        if isinstance(char, str) and len(char) == 1:
                            try:
                                target_counts[char] = int(row.get("出現次數", "0"))
                            except (TypeError, ValueError):
                                target_counts[char] = 0
        except (OSError, UnicodeError, csv.Error) as exc:
            raise UIFHelperError(f"character_report.csv 無法讀取：{exc}") from exc

    warnings = target_warnings + candidate_warnings + work_warnings
    if not enabled:
        warnings.append("UIF 的 character_substitution.enable 目前為 false；映射已讀取，但遊戲端替換功能未啟用。")
    if len(pending_targets) == 0:
        warnings.append("本次待替換清單中的字元都已存在於 UIF，沒有新增目標字元。")
    if len(available_candidates) < len(pending_targets):
        warnings.append(
            f"安全候選不足：待配對 {len(pending_targets)} 字，可用候選 {len(available_candidates)} 字；"
            f"最多只能新增 {len(available_candidates)} 組。"
        )

    encoding = metadata.get("translation_text_encoding", "utf-8-sig")
    if not isinstance(encoding, str) or not encoding.strip():
        encoding = "utf-8-sig"
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise UIFHelperError(f"scan_metadata.json 記錄了不支援的翻譯編碼：{encoding!r}") from exc

    translation_value = metadata.get("translation_root")
    translation_root = Path(translation_value).expanduser() if isinstance(translation_value, str) and translation_value else None
    return HelperContext(
        uif_path=uif_path,
        report_root=report_root,
        translation_root=translation_root,
        text_encoding=encoding,
        substitution_enabled=enabled,
        repeat_characters=repeats,
        existing_pairs=existing_pairs,
        work_pairs=work_pairs,
        target_characters=pending_targets,
        already_mapped_targets=already_mapped_targets,
        target_counts=target_counts,
        candidate_characters=available_candidates,
        metadata=metadata,
        warnings=warnings,
    )


def auto_pair(context: HelperContext) -> list[MappingPair]:
    """依待替換字原順序及候選 CSV 原順序，建立穩定的一對一新增配對。"""
    used_sources = {pair.source for pair in context.existing_pairs + context.work_pairs}
    used_targets = {pair.target for pair in context.existing_pairs + context.work_pairs}
    candidates = [c for c in context.candidate_characters if c not in used_sources]
    targets = [t for t in context.target_characters if t not in used_targets]
    count = min(len(targets), len(candidates))
    new_pairs: list[MappingPair] = []
    for index in range(count):
        source = candidates[index]
        target = targets[index]
        if source in used_sources:
            continue
        if target in used_targets:
            continue
        new_pairs.append(MappingPair(source, target, False))
        used_sources.add(source)
        used_targets.add(target)
    return new_pairs


def combine_pairs(existing: Iterable[MappingPair], added: Iterable[MappingPair]) -> list[MappingPair]:
    """保留 UIF 舊映射的順序，再附加本次新增映射。"""
    combined = list(existing) + list(added)
    seen_sources: set[str] = set()
    for pair in combined:
        _one_unicode_character(pair.source, "映射來源")
        _one_unicode_character(pair.target, "映射目標")
        if pair.source in seen_sources:
            raise UIFHelperError(f"完整映射含重複來源字：{pair.source!r}")
        seen_sources.add(pair.source)
    return combined


def make_two_line_text(pairs: Iterable[MappingPair]) -> str:
    """產生兩行完整 UIF 映射；目標字可重複，因既有 UIF 可能如此設定。"""
    values = list(pairs)
    if not values:
        raise UIFHelperError("沒有可匯出的映射。")
    sources = "".join(pair.source for pair in values)
    targets = "".join(pair.target for pair in values)
    if len(sources) != len(targets):
        raise UIFHelperError("來源／目標字數不一致，取消匯出。")
    if len(set(sources)) != len(sources):
        raise UIFHelperError("來源字元重複，無法產生安全的兩行映射。")
    return sources + "\n" + targets + "\n"


def write_two_line_file(path: Path, pairs: Iterable[MappingPair]) -> None:
    payload = make_two_line_text(pairs)
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def build_reverse_mapping(pairs: Iterable[MappingPair]) -> tuple[dict[str, str], list[str]]:
    """建立目標字→來源字；既有重複目標採第一個來源，並回報歧義。"""
    reverse: dict[str, str] = {}
    ambiguous: list[str] = []
    for pair in pairs:
        previous = reverse.get(pair.target)
        if previous is None:
            reverse[pair.target] = pair.source
        elif previous != pair.source and pair.target not in ambiguous:
            ambiguous.append(pair.target)
    return reverse, ambiguous


def _transform_json_preserving_layout(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """僅改 JSON 字串值，保留鍵、縮排、空白及其餘原始格式。"""
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise UIFHelperError(f"JSON 格式錯誤：{exc}") from exc

    decoder = json.JSONDecoder()
    edits: list[tuple[int, int, str]] = []
    replacement_count = 0
    length = len(text)

    def skip_space(index: int) -> int:
        while index < length and text[index] in " \t\r\n":
            index += 1
        return index

    def parse_string(index: int, transform: bool) -> int:
        nonlocal replacement_count
        value, end = decoder.raw_decode(text, index)
        if not isinstance(value, str):
            raise UIFHelperError("JSON 字串解析狀態錯誤。")
        if transform:
            replaced, count = replace_text(value, mapping)
            if count:
                edits.append((index, end, json.dumps(replaced, ensure_ascii=False)))
                replacement_count += count
        return end

    def parse_value(index: int) -> int:
        index = skip_space(index)
        if index >= length:
            raise UIFHelperError("JSON 在解析值時意外結束。")
        current = text[index]
        if current == '"':
            return parse_string(index, True)
        if current == "{":
            index = skip_space(index + 1)
            if index < length and text[index] == "}":
                return index + 1
            while True:
                index = skip_space(index)
                if index >= length or text[index] != '"':
                    raise UIFHelperError("JSON 物件鍵格式錯誤。")
                index = parse_string(index, False)
                index = skip_space(index)
                if index >= length or text[index] != ":":
                    raise UIFHelperError("JSON 物件缺少冒號。")
                index = parse_value(index + 1)
                index = skip_space(index)
                if index < length and text[index] == ",":
                    index += 1
                    continue
                if index < length and text[index] == "}":
                    return index + 1
                raise UIFHelperError("JSON 物件分隔符號錯誤。")
        if current == "[":
            index = skip_space(index + 1)
            if index < length and text[index] == "]":
                return index + 1
            while True:
                index = parse_value(index)
                index = skip_space(index)
                if index < length and text[index] == ",":
                    index += 1
                    continue
                if index < length and text[index] == "]":
                    return index + 1
                raise UIFHelperError("JSON 陣列分隔符號錯誤。")
        # true / false / null / number：略過至下一個結構分隔符。
        while index < length and text[index] not in ",]} \t\r\n":
            index += 1
        if index == 0 or index >= length and not text[index - 1:index]:
            raise UIFHelperError("JSON 基本值格式錯誤。")
        return index

    end = parse_value(skip_space(0))
    if skip_space(end) != length:
        raise UIFHelperError("JSON 根節點後仍有多餘內容。")
    for start, stop, replacement in reversed(edits):
        text = text[:start] + replacement + text[stop:]
    return text, replacement_count


def replace_text(value: str, mapping: dict[str, str]) -> tuple[str, int]:
    """將翻譯目標字替換成 UIF source_characters 的來源字。"""
    if not value or not mapping:
        return value, 0
    output: list[str] = []
    count = 0
    for character in value:
        replacement = mapping.get(character)
        if replacement is None:
            output.append(character)
        else:
            output.append(replacement)
            count += 1
    return "".join(output), count


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def _ensure_output_location(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise UIFHelperError("輸出位置不可與原始翻譯資料夾相同。")
    if source.is_dir():
        try:
            output.relative_to(source)
        except ValueError:
            pass
        else:
            raise UIFHelperError("輸出資料夾不可建立在原始翻譯資料夾內。")
    if output.exists() and output.is_dir() and any(output.iterdir()):
        raise UIFHelperError(f"輸出資料夾不是空資料夾，為避免覆寫已存在資料而停止：{output}")
    if output.exists() and not output.is_dir():
        raise UIFHelperError(f"輸出檔案已存在，為避免覆寫而停止：{output}")


def _process_one_file(source_file: Path, output_file: Path, encoding: str,
                      mapping: dict[str, str]) -> tuple[int, str | None]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if source_file.suffix.lower() not in TEXT_EXTENSIONS:
        shutil.copy2(source_file, output_file)
        return 0, None
    try:
        raw = source_file.read_bytes()
        is_json = source_file.suffix.lower() == ".json"
        if is_json:
            # char_manager 的 JSON 掃描固定以 UTF-8-SIG 讀取，不受一般 TXT 編碼選項影響。
            has_bom = raw.startswith(codecs.BOM_UTF8)
            text = raw.decode("utf-8-sig", errors="strict")
        else:
            has_bom = False
            selected_codec = codecs.lookup(encoding).name
            if selected_codec == "utf-8-sig":
                has_bom = raw.startswith(codecs.BOM_UTF8)
                text = raw.decode("utf-8-sig" if has_bom else "utf-8", errors="strict")
            else:
                text = raw.decode(encoding, errors="strict")

        if is_json:
            converted, count = _transform_json_preserving_layout(text, mapping)
            encoded = converted.encode("utf-8")
            if has_bom:
                encoded = codecs.BOM_UTF8 + encoded
        else:
            converted, count = replace_text(text, mapping)
            encoded = converted.encode("utf-8" if codecs.lookup(encoding).name == "utf-8-sig" else encoding)
            if codecs.lookup(encoding).name == "utf-8-sig" and has_bom:
                encoded = codecs.BOM_UTF8 + encoded

        output_file.write_bytes(encoded)
        shutil.copystat(source_file, output_file, follow_symlinks=False)
        return count, None
    except (OSError, UnicodeError, LookupError, UIFHelperError) as exc:
        # 失敗檔仍複製原始內容，避免輸出資料夾遺失該資源；報告會明確標示未替換。
        try:
            shutil.copy2(source_file, output_file)
        except OSError as copy_exc:
            return 0, f"{source_file}：讀取／替換失敗（{exc}），複製也失敗（{copy_exc}）"
        return 0, f"{source_file}：未替換，已原樣複製。原因：{exc}"


def replace_translation_files(
    source_root: Path,
    output_root: Path,
    pairs: Iterable[MappingPair],
    encoding: str = "utf-8-sig",
) -> dict[str, Any]:
    """將翻譯檔另存至新位置；非文字檔原樣複製，不追蹤連結。"""
    source_root = Path(source_root).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    if not source_root.exists():
        raise UIFHelperError(f"翻譯來源不存在：{source_root}")
    try:
        encoding = codecs.lookup(encoding).name
    except LookupError as exc:
        raise UIFHelperError(f"不支援的翻譯文件編碼：{encoding!r}") from exc

    reverse, ambiguous = build_reverse_mapping(pairs)
    if not reverse:
        raise UIFHelperError("沒有可用映射，無法替換翻譯文件。")

    if source_root.is_file():
        output_file = output_root / source_root.name if output_root.exists() and output_root.is_dir() else output_root
        _ensure_output_location(source_root, output_file)
        count, error = _process_one_file(source_root, output_file, encoding, reverse)
        errors = [error] if error else []
        files_total = 1
        files_changed = 1 if count else 0
        output_result = output_file
    else:
        _ensure_output_location(source_root, output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        output_result = output_root
        errors: list[str] = []
        files_total = 0
        files_changed = 0
        total_replacements = 0

        def walk_error(exc: OSError) -> None:
            errors.append(f"無法列舉資料夾：{exc}")

        for current, dirnames, filenames in os.walk(source_root, topdown=True, onerror=walk_error, followlinks=False):
            current_path = Path(current)
            retained: list[str] = []
            for dirname in sorted(dirnames, key=str.casefold):
                directory = current_path / dirname
                if _is_link(directory):
                    errors.append(f"跳過目錄連結／Junction：{directory}")
                else:
                    retained.append(dirname)
            dirnames[:] = retained
            for filename in sorted(filenames, key=str.casefold):
                source_file = current_path / filename
                relative = source_file.relative_to(source_root)
                output_file = output_root / relative
                if _is_link(source_file):
                    errors.append(f"跳過檔案連結／Junction：{source_file}")
                    continue
                files_total += 1
                count, error = _process_one_file(source_file, output_file, encoding, reverse)
                total_replacements += count
                if count:
                    files_changed += 1
                if error:
                    errors.append(error)
        return {
            "files_total": files_total,
            "files_changed": files_changed,
            "replacements": total_replacements,
            "errors": errors,
            "ambiguous_targets": ambiguous,
            "output_root": output_result,
        }

    return {
        "files_total": files_total,
        "files_changed": files_changed,
        "replacements": count,
        "errors": errors,
        "ambiguous_targets": ambiguous,
        "output_root": output_root,
    }


class UIFCharHelperApp(ttk.Frame):
    """獨立輔助 GUI。"""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=8)
        self.master = master
        self.context: HelperContext | None = None
        self.new_pairs: list[MappingPair] = []
        self.uif_var = tk.StringVar()
        self.report_var = tk.StringVar()
        self.translation_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.encoding_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="請選擇遊戲內的 uif_config.json 與字元管理報表資料夾。")
        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.rowconfigure(5, weight=1)

        paths = ttk.LabelFrame(self, text="輸入資料")
        paths.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        paths.columnconfigure(1, weight=1)
        self._path_row(paths, 0, "遊戲內 uif_config.json：", self.uif_var, self._pick_uif)
        self._path_row(paths, 1, "字元管理報表資料夾：", self.report_var, self._pick_report)

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Button(actions, text="讀取設定與報表", command=self._load).pack(side="left", padx=3)
        ttk.Button(actions, text="自動一對一配對", command=self._auto).pack(side="left", padx=3)
        ttk.Button(actions, text="匯出完整兩行字串…", command=self._save_mapping).pack(side="left", padx=3)
        ttk.Button(actions, text="複製完整兩行", command=self._copy_mapping).pack(side="left", padx=3)

        self.summary = tk.Text(self, height=8, wrap="word", font=("Microsoft JhengHei UI", 10))
        self.summary.grid(row=2, column=0, sticky="ew", pady=4)
        self.summary.configure(state="disabled")

        list_box = ttk.LabelFrame(self, text="本次新增配對（來源字 → 翻譯目標字）")
        list_box.grid(row=3, column=0, sticky="nsew", pady=4)
        list_box.rowconfigure(0, weight=1)
        list_box.columnconfigure(0, weight=1)
        self.pair_tree = ttk.Treeview(list_box, columns=("target", "status"), show="tree headings", height=8)
        self.pair_tree.heading("#0", text="來源字元")
        self.pair_tree.heading("target", text="目標字元")
        self.pair_tree.heading("status", text="狀態")
        self.pair_tree.column("#0", width=120, anchor="center")
        self.pair_tree.column("target", width=120, anchor="center")
        self.pair_tree.column("status", width=100, anchor="center")
        self.pair_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_box, orient="vertical", command=self.pair_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.pair_tree.configure(yscrollcommand=scroll.set)

        output_box = ttk.LabelFrame(self, text="完整兩行映射預覽（第一行 source_characters、第二行 target_characters）")
        output_box.grid(row=4, column=0, sticky="ew", pady=4)
        output_box.columnconfigure(0, weight=1)
        self.output_text = tk.Text(output_box, height=4, wrap="none", font=("Consolas", 12))
        self.output_text.grid(row=0, column=0, sticky="ew", padx=4, pady=4)

        replace_box = ttk.LabelFrame(self, text="翻譯文件替換（另存新資料夾，不覆寫原始檔）")
        replace_box.grid(row=5, column=0, sticky="nsew", pady=4)
        replace_box.columnconfigure(1, weight=1)
        self._path_row(replace_box, 0, "翻譯文件資料夾：", self.translation_var, self._pick_translation)
        self._path_row(replace_box, 1, "替換後輸出資料夾：", self.output_var, self._pick_output)
        ttk.Label(replace_box, text="文字編碼（留白＝使用 scan_metadata.json）：").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(replace_box, textvariable=self.encoding_var, width=24).grid(row=2, column=1, sticky="w", padx=4, pady=3)
        ttk.Button(replace_box, text="開始替換並另存", command=self._replace).grid(row=3, column=1, sticky="w", padx=4, pady=6)

        ttk.Label(self, textvariable=self.status_var, foreground="#555555").grid(row=6, column=0, sticky="ew", pady=(5, 0))

    def _path_row(self, parent: ttk.Widget, row: int, label: str, variable: tk.StringVar, command: Any) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(parent, text="選擇…", command=command).grid(row=row, column=2, padx=4, pady=3)

    def _pick_uif(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇遊戲資料夾內的 uif_config.json", filetypes=(("JSON", "*.json"), ("所有檔案", "*.*")), parent=self
        )
        if value:
            self.uif_var.set(value)

    def _pick_report(self) -> None:
        value = filedialog.askdirectory(title="選擇 *_char_report 報表資料夾", parent=self)
        if value:
            self.report_var.set(value)

    def _pick_translation(self) -> None:
        initial = self.context.translation_root if self.context else None
        value = filedialog.askdirectory(
            title="選擇要替換的翻譯文件資料夾",
            initialdir=str(initial) if initial and initial.is_dir() else None,
            parent=self,
        )
        if value:
            self.translation_var.set(value)

    def _pick_output(self) -> None:
        value = filedialog.askdirectory(title="選擇替換後輸出資料夾（須為空資料夾或不存在）", parent=self)
        if value:
            self.output_var.set(value)

    def _set_summary(self, value: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", value)
        self.summary.configure(state="disabled")

    def _load(self) -> None:
        try:
            if not self.uif_var.get().strip() or not self.report_var.get().strip():
                raise UIFHelperError("請先選擇遊戲內 UIF 與報表資料夾。")
            context = load_context(Path(self.uif_var.get()), Path(self.report_var.get()))
            self.context = context
            self.new_pairs = []
            self.translation_var.set(str(context.translation_root or ""))
            self._refresh()
            self.status_var.set("已讀取外部 UIF 及本次掃描報表。")
        except (UIFHelperError, OSError) as exc:
            messagebox.showerror("讀取失敗", str(exc), parent=self)

    def _auto(self) -> None:
        if self.context is None:
            self._load()
            if self.context is None:
                return
        self.new_pairs = auto_pair(self.context)
        self._refresh()
        missing = max(0, len(self.context.target_characters) - len(self.new_pairs))
        self.status_var.set(
            f"自動配對完成：新增 {len(self.new_pairs)} 組；仍未配對 {missing} 字。"
        )
        if missing:
            messagebox.showwarning(
                "候選字不足",
                f"還有 {missing} 個目標字元沒有候選來源字。\n"
                "請重新掃描、放寬候選排除條件，或在 char_manager.py 人工挑選其他候選。",
                parent=self,
            )

    def _all_pairs(self) -> list[MappingPair]:
        if self.context is None:
            raise UIFHelperError("尚未讀取 UIF 與報表。")
        return combine_pairs(self.context.existing_pairs + self.context.work_pairs, self.new_pairs)

    def _refresh(self) -> None:
        for item in self.pair_tree.get_children():
            self.pair_tree.delete(item)
        if self.context is None:
            return
        for index, pair in enumerate(self.new_pairs):
            self.pair_tree.insert("", "end", iid=f"n{index}", text=pair.source, values=(pair.target, "新增"))
        new_targets = {pair.target for pair in self.new_pairs}
        lines = [
            f"UIF：{self.context.uif_path}",
            f"替換功能啟用：{'是' if self.context.substitution_enabled else '否'}",
            f"UIF 既有映射：{len(self.context.existing_pairs)} 組（保留原順序）",
            f"char_manager 人工映射：{len(self.context.work_pairs)} 組（接續於 UIF 既有映射後）",
            "人工配對目標字：" + (
                "、".join(pair.target for pair in self.context.work_pairs) or "（無）"
            ),
            f"本次翻譯中已由 UIF 處理：{len(self.context.already_mapped_targets)} 字",
            "已處理目標字及出現次數：" + (
                "、".join(
                    f"{ch}（{self.context.target_counts.get(ch, 0)} 次）"
                    for ch in self.context.already_mapped_targets
                ) or "（無）"
            ),
            f"尚未設定替換：{len(self.context.target_characters)} 字",
            f"可用來源候選：{len(self.context.candidate_characters)} 字；新增配對：{len(self.new_pairs)} 組",
            f"未配對：{len([t for t in self.context.target_characters if t not in new_targets])} 字",
            f"repeat 清單：{len(self.context.repeat_characters)} 字（不併入雙行映射、不修改）",
            "",
        ] + self.context.warnings
        self._set_summary("\n".join(lines))
        try:
            payload = make_two_line_text(self._all_pairs())
        except UIFHelperError:
            payload = ""
        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", payload)

    def _save_mapping(self) -> None:
        try:
            pairs = self._all_pairs()
            value = filedialog.asksaveasfilename(
                title="匯出完整 UIF 兩行映射", initialfile="uif_replacement_mapping.txt",
                defaultextension=".txt", filetypes=(("文字檔", "*.txt"), ("所有檔案", "*.*")), parent=self
            )
            if not value:
                return
            write_two_line_file(Path(value), pairs)
            self.status_var.set(f"已匯出完整兩行映射：{value}")
        except (UIFHelperError, OSError) as exc:
            messagebox.showerror("匯出失敗", str(exc), parent=self)

    def _copy_mapping(self) -> None:
        try:
            payload = make_two_line_text(self._all_pairs())
            self.clipboard_clear()
            self.clipboard_append(payload.rstrip("\n"))
            self.update_idletasks()
            self.status_var.set("已複製完整兩行映射，可直接貼入 UIF 的兩個字串欄位。")
        except (UIFHelperError, tk.TclError) as exc:
            messagebox.showerror("複製失敗", str(exc), parent=self)

    def _replace(self) -> None:
        try:
            pairs = self._all_pairs()
            translation_text = self.translation_var.get().strip().strip('"')
            output_text = self.output_var.get().strip().strip('"')
            if not translation_text or not output_text:
                raise UIFHelperError("請指定翻譯來源資料夾及替換後輸出資料夾。")
            encoding = self.encoding_var.get().strip() or self.context.text_encoding
            result = replace_translation_files(
                Path(translation_text), Path(output_text), pairs, encoding
            )
            message = (
                f"處理檔案：{result['files_total']}\n"
                f"有替換的檔案：{result['files_changed']}\n"
                f"替換字元總數：{result['replacements']}\n"
                f"輸出位置：{result['output_root']}"
            )
            if result["ambiguous_targets"]:
                message += "\n\n以下目標字在 UIF 有多個來源，替換時採用第一個來源：\n" + "、".join(result["ambiguous_targets"])
            if result["errors"]:
                message += "\n\n未完整處理的項目：\n" + "\n".join(result["errors"][:30])
                messagebox.showwarning("替換完成，但有警告", message, parent=self)
            else:
                messagebox.showinfo("替換完成", message, parent=self)
            self.status_var.set(f"替換完成：共替換 {result['replacements']} 個字元。")
        except (UIFHelperError, OSError, UnicodeError, LookupError) as exc:
            messagebox.showerror("替換失敗", str(exc), parent=self)


def run_gui() -> int:
    root = tk.Tk()
    root.title("NekoSDKTools－UIF 字元配對與替換")
    root.geometry("980x760")
    root.minsize(780, 620)
    app = UIFCharHelperApp(root)
    app.pack(fill="both", expand=True)
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="讀取遊戲外部 uif_config.json、自動配對字元，並可另存替換翻譯文件。"
    )
    parser.add_argument("--uif", help="遊戲資料夾內的 uif_config.json")
    parser.add_argument("--report", help="char_manager 產生的 *_char_report 資料夾")
    parser.add_argument("--auto-pair", action="store_true", help="自動產生一對一配對")
    parser.add_argument("--export-two-line", help="輸出完整兩行映射的 TXT 路徑")
    parser.add_argument("--translations", help="要替換的翻譯資料夾或單一文字檔")
    parser.add_argument("--output", help="替換後輸出資料夾或檔案路徑")
    parser.add_argument("--encoding", help="覆寫翻譯檔編碼；預設使用 scan_metadata.json")
    parser.add_argument("--no-gui", action="store_true", help="命令列模式")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    has_cli = any((args.uif, args.report, args.export_two_line, args.translations, args.output, args.auto_pair, args.no_gui))
    if not has_cli:
        return run_gui()
    if not args.uif or not args.report:
        parser.error("命令列模式必須同時指定 --uif 與 --report。")
    try:
        context = load_context(Path(args.uif), Path(args.report))
        added = auto_pair(context) if args.auto_pair or args.export_two_line or args.translations else []
        pairs = combine_pairs(context.existing_pairs, added)
        print(f"既有映射：{len(context.existing_pairs)} 組")
        print(f"本次待替換：{len(context.target_characters)} 字")
        print(f"本次新增：{len(added)} 組")
        print(f"未配對：{max(0, len(context.target_characters) - len(added))} 字")
        for warning in context.warnings:
            print("警告：" + warning, file=sys.stderr)
        if args.export_two_line:
            write_two_line_file(Path(args.export_two_line), pairs)
            print(f"兩行映射：{Path(args.export_two_line)}")
        if args.translations:
            if not args.output:
                parser.error("指定 --translations 時，也必須指定 --output。")
            result = replace_translation_files(
                Path(args.translations), Path(args.output), pairs,
                args.encoding or context.text_encoding,
            )
            print(f"處理檔案：{result['files_total']}")
            print(f"有替換的檔案：{result['files_changed']}")
            print(f"替換字元總數：{result['replacements']}")
            print(f"輸出位置：{result['output_root']}")
            for error in result["errors"]:
                print("警告：" + error, file=sys.stderr)
        return 0
    except (UIFHelperError, OSError, UnicodeError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
