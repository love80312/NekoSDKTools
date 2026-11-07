#!/usr/bin/env python3
"""NekoSDK ADVSCRIPT2 text extractor and importer.

The input files are binary ADVSCRIPT2 streams even though their file names
usually end in .txt. This tool exports translatable dialogue/choice strings
as UTF-8 JSON and imports edited JSON back into the original script.

Default command/scene encoding is CP932. --dialogue-encoding defaults to
--encoding for backward compatibility. For the mixed CP932/GBK files where
Japanese scene-navigation strings are mirrored in the CP932
[テキスト表示] main field, use:
    --encoding cp932 --dialogue-encoding auto

In auto mode, a byte-for-byte match against the CP932 command body identifies
a CP932 text record. Other opcode-5 dialogue records use GBK. This avoids
guessing from whether a byte sequence happens to decode in both codecs.
Changing byte encoding does not make the game engine support that encoding.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"NEKOSDK_ADVSCRIPT2\0"
DEFAULT_ENCODING = "cp932"
TEXT_OPCODE = 5
SCENE_OPCODE = 1
RECORD_FIXED_SIZE = 16 + 32 * 4 + 4 + 16 * 4 + 4 + 32 * 4
CHOICE_MARKERS = ("[選択", "選択肢", "[セレクト")


class ScriptError(Exception):
    """Raised when an ADVSCRIPT2 file or translation JSON is invalid."""


@dataclass
class Record:
    header: tuple[int, int, int, int]
    nums32: tuple[int, ...]
    extra: int
    nums16: tuple[int, ...]
    main: bytes
    args: list[bytes]


@dataclass
class Script:
    records: list[Record]


@dataclass(frozen=True)
class ChoiceSpan:
    """A replaceable choice string range within a scene command."""

    start: int
    end: int
    value: str


def validate_encoding(encoding: str) -> str:
    """Resolve and validate a Python codec name."""
    name = encoding.strip()
    if not name:
        raise ScriptError("encoding name cannot be empty")
    try:
        resolved = codecs.lookup(name).name
    except LookupError as exc:
        raise ScriptError(f"unknown text encoding: {encoding!r}") from exc
    return "utf-8" if resolved == "utf-8-sig" else resolved


def validate_dialogue_encoding(encoding: str) -> str:
    """Validate a dialogue codec or the special mixed-encoding mode."""
    if encoding.strip().casefold() == "auto":
        return "auto"
    return validate_encoding(encoding)


def read_u32(data: bytes, offset: int, context: str) -> int:
    try:
        return struct.unpack_from("<I", data, offset)[0]
    except struct.error as exc:
        raise ScriptError(f"truncated {context} at 0x{offset:X}") from exc


def pack_u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ScriptError(f"value does not fit uint32: {value}")
    return struct.pack("<I", value)


def has_content(raw: bytes) -> bool:
    return bool(raw[:-1] if raw.endswith(b"\0") else raw)


def decode_field(raw: bytes, encoding: str, context: str) -> str:
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    try:
        return raw.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise ScriptError(f"cannot decode {context} with {encoding}: {exc}") from exc


def decode_candidates(
    raw: bytes, encodings: tuple[str, ...], context: str
) -> list[tuple[str, str]]:
    """Return successful decodings with their codec identities.

    Successful decoding by itself does not prove that a codec is correct:
    CP932 and GBK can both decode many of the same byte sequences.
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            results.append((decode_field(raw, encoding, context), encoding))
        except ScriptError:
            continue
    return results


def encode_field(text: str, encoding: str, keep_nul: bool) -> bytes:
    if "\0" in text:
        raise ScriptError("NUL characters are not allowed in script strings")
    try:
        raw = text.encode(encoding, errors="strict")
    except UnicodeEncodeError as exc:
        raise ScriptError(
            f"text cannot be encoded as {encoding}: {exc}. "
            "Use an encoding supported by the game, or resolve its font/encoding first."
        ) from exc
    if keep_nul or text:
        raw += b"\0"
    if len(raw) > 0xFFFFFFFF:
        raise ScriptError("script string is too large")
    return raw


def _without_terminal_nul(raw: bytes) -> bytes:
    return raw[:-1] if raw.endswith(b"\0") else raw


def _normalize_raw_line_breaks(raw: bytes) -> bytes:
    """Normalize byte-level CR/LF while preserving all non-linebreak bytes."""
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip(b"\n")


def resolve_text_record_encoding(
    record: Record, command_encoding: str, dialogue_encoding: str, context: str
) -> str:
    """Resolve one opcode-5 record's codec in mixed CP932/GBK mode.

    The Japanese navigation records in the examined script duplicate their
    displayed text in the CP932 [テキスト表示] main field and args[1].
    Matching the raw byte body is stronger evidence than trying to decode
    the same bytes under both codecs.
    """
    if dialogue_encoding != "auto":
        return dialogue_encoding

    if len(record.args) != 32:
        raise ScriptError(f"{context}: expected exactly 32 argument fields")

    raw_message = _without_terminal_nul(record.args[1])
    raw_main = _without_terminal_nul(record.main)

    try:
        main_text = decode_field(record.main, command_encoding, f"{context} main")
    except ScriptError:
        return "gbk"

    header, separator, _body = main_text.partition("\n")
    if not separator or not header.startswith("[テキスト表示]"):
        return "gbk"

    raw_separator = raw_main.find(b"\n")
    if raw_separator < 0:
        return "gbk"
    raw_body = raw_main[raw_separator + 1:]

    message_normalized = _normalize_raw_line_breaks(raw_message)
    body_normalized = _normalize_raw_line_breaks(raw_body)
    if message_normalized and body_normalized.startswith(message_normalized):
        return command_encoding
    return "gbk"


def dialogue_choice_encoding(dialogue_encoding: str) -> str:
    """Use GBK for standalone choice payloads when auto mode is selected."""
    return "gbk" if dialogue_encoding == "auto" else dialogue_encoding


def read_script(path: Path) -> Script:
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise ScriptError(f"invalid ADVSCRIPT2 signature: {path}")

    pos = len(MAGIC)
    count = read_u32(data, pos, "record count")
    pos += 4

    available = len(data) - pos
    if count > available // RECORD_FIXED_SIZE:
        raise ScriptError(
            f"record count is impossible for file size: {count} records in {path}"
        )

    records: list[Record] = []
    for index in range(count):
        start = pos
        try:
            if pos + 16 > len(data):
                raise ScriptError(f"truncated record header #{index} in {path}")
            header = struct.unpack_from("<4I", data, pos)
            pos += 16

            if pos + 128 + 4 + 64 + 4 > len(data):
                raise ScriptError(f"truncated fixed record fields #{index} in {path}")
            nums32 = struct.unpack_from("<32I", data, pos)
            pos += 128
            extra = read_u32(data, pos, f"record #{index} extra field")
            pos += 4
            nums16 = struct.unpack_from("<16I", data, pos)
            pos += 64

            main_len = read_u32(data, pos, f"record #{index} main length")
            pos += 4
            if main_len > len(data) - pos:
                raise ScriptError(
                    f"main string exceeds end of file in record #{index}: {path}"
                )
            main = data[pos:pos + main_len]
            pos += main_len

            args: list[bytes] = []
            for arg_index in range(32):
                arg_len = read_u32(data, pos, f"record #{index} arg #{arg_index} length")
                pos += 4
                if arg_len > len(data) - pos:
                    raise ScriptError(
                        f"argument string exceeds end of file in record #{index}, "
                        f"arg #{arg_index}: {path}"
                    )
                args.append(data[pos:pos + arg_len])
                pos += arg_len
        except struct.error as exc:
            raise ScriptError(
                f"invalid record #{index} at 0x{start:X} in {path}"
            ) from exc

        records.append(Record(header, nums32, extra, nums16, main, args))

    if pos != len(data):
        raise ScriptError(
            f"unexpected trailing data in {path}: 0x{pos:X} != 0x{len(data):X}"
        )
    return Script(records)


def write_script(script: Script, path: Path) -> None:
    output = bytearray(MAGIC)
    output += pack_u32(len(script.records))

    for index, record in enumerate(script.records):
        if (
            len(record.header) != 4
            or len(record.nums32) != 32
            or len(record.nums16) != 16
        ):
            raise ScriptError(f"invalid fixed field count in record #{index}")
        if len(record.args) != 32:
            raise ScriptError(
                f"record #{index} must contain exactly 32 argument strings"
            )

        output += struct.pack("<4I", *record.header)
        output += struct.pack("<32I", *record.nums32)
        output += pack_u32(record.extra)
        output += struct.pack("<16I", *record.nums16)
        output += pack_u32(len(record.main))
        output += record.main
        for arg in record.args:
            output += pack_u32(len(arg))
            output += arg

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_bytes(output)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def decode_record_text(
    record: Record, encoding: str, context: str
) -> tuple[str, list[str]]:
    main = decode_field(record.main, encoding, f"{context} main")
    args = [
        decode_field(value, encoding, f"{context} arg {index}")
        for index, value in enumerate(record.args)
    ]
    return main, args


def is_text_record(record: Record) -> bool:
    return (
        record.header[3] == TEXT_OPCODE
        and len(record.args) == 32
        and has_content(record.args[1])
    )


def is_scene_record(record: Record) -> bool:
    return (
        record.header[3] == SCENE_OPCODE
        and (has_content(record.main) or has_content(record.args[31]))
    )


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def scene_choice_header_kind(line: str) -> str | None:
    """Identify the two scene-choice command forms seen in ADVSCRIPT2."""
    body, _ = split_line_ending(line)
    marker = body.lstrip(";:")
    if marker == "選択肢":
        return "lines"
    if marker == "選択肢へ":
        return "quoted"
    return None


def parse_quoted_choice_row(
    line: str, line_offset: int
) -> list[ChoiceSpan] | None:
    """Parse a slash-separated row such as 「A」/「B」 without losing delimiters."""
    pairs = {"「": "」", '"': '"', "『": "』"}
    spans: list[ChoiceSpan] = []
    pos = 0
    length = len(line)

    while pos < length:
        while pos < length and line[pos].isspace():
            pos += 1
        if pos >= length:
            break

        opener = line[pos]
        closer = pairs.get(opener)
        if closer is None:
            return None
        value_start = pos + 1
        value_end = line.find(closer, value_start)
        if value_end < 0:
            return None
        value = line[value_start:value_end]
        if not value:
            return None
        spans.append(
            ChoiceSpan(line_offset + value_start, line_offset + value_end, value)
        )
        pos = value_end + 1

        while pos < length and line[pos].isspace():
            pos += 1
        if pos == length:
            break
        if line[pos] not in ("/", "／"):
            return None
        pos += 1

    return spans if len(spans) >= 2 else None


def parse_scene_choice_spans(text: str) -> list[ChoiceSpan] | None:
    """Find editable option text while retaining prefixes, quotes and separators."""
    lines = text.splitlines(True)
    if not lines:
        return None

    kind = scene_choice_header_kind(lines[0])
    if kind is None:
        return None

    offset = len(lines[0])
    choices: list[ChoiceSpan] = []
    for line in lines[1:]:
        body, _ending = split_line_ending(line)
        if kind == "lines":
            if body:
                prefix_length = 1 if body[0] in ";:" else 0
                value = body[prefix_length:]
                if value:
                    choices.append(
                        ChoiceSpan(
                            offset + prefix_length,
                            offset + len(body),
                            value,
                        )
                    )
        else:
            parsed = parse_quoted_choice_row(body, offset)
            if parsed is not None:
                return parsed
        offset += len(line)

    return choices or None


def scene_choices(
    record: Record, encoding: str
) -> tuple[str, str, list[ChoiceSpan]] | None:
    """Locate a real choice block in arg[31] or main."""
    if not is_scene_record(record):
        return None

    for field_name, raw in (("arg31", record.args[31]), ("main", record.main)):
        if not has_content(raw):
            continue
        try:
            text = decode_field(raw, encoding, f"scene {field_name}")
        except ScriptError:
            continue
        parsed = parse_scene_choice_spans(text)
        if parsed is not None:
            return field_name, text, parsed
    return None


def replace_scene_choice_text(
    text: str,
    choices: list[str],
    parsed: list[ChoiceSpan] | None = None,
) -> str:
    spans = parsed if parsed is not None else parse_scene_choice_spans(text)
    if spans is None:
        raise ScriptError("internal error: record is not a scene choice block")
    if len(spans) != len(choices):
        raise ScriptError("internal error: scene choice count changed")

    output: list[str] = []
    cursor = 0
    for choice, span in zip(choices, spans):
        if "\r" in choice or "\n" in choice:
            raise ScriptError("a single choice item cannot contain a line break")
        if span.start < cursor or span.end < span.start or span.end > len(text):
            raise ScriptError("internal error: invalid scene choice span")
        output.append(text[cursor:span.start])
        output.append(choice)
        cursor = span.end
    output.append(text[cursor:])
    return "".join(output)


def choice_marker_arg_indices(
    record: Record, encoding: str, dialogue_encoding: str
) -> set[int]:
    """Return argument slots that contain command/choice markers."""
    marker_indices: set[int] = set()
    choice_encoding = dialogue_choice_encoding(dialogue_encoding)
    for index, raw in enumerate(record.args):
        if index in (30, 31) or not has_content(raw):
            continue
        candidates = decode_candidates(
            raw, (encoding, choice_encoding), f"choice arg {index}"
        )
        if any(
            any(marker in value for marker in CHOICE_MARKERS)
            for value, _enc in candidates
        ):
            marker_indices.add(index)
    return marker_indices


def is_choice_record(
    record: Record, encoding: str, dialogue_encoding: str
) -> bool:
    if record.header[3] in (TEXT_OPCODE, SCENE_OPCODE):
        return False

    choice_encoding = dialogue_choice_encoding(dialogue_encoding)
    main_candidates = decode_candidates(
        record.main, (encoding, choice_encoding), "choice main"
    )
    if any(
        value.startswith(("選択肢\r", "選択肢\n"))
        for value, _enc in main_candidates
    ):
        return True
    return bool(
        choice_marker_arg_indices(record, encoding, dialogue_encoding)
    )


def extract_choice_targets(
    record: Record, encoding: str, dialogue_encoding: str
) -> list[tuple[str, int, str, str, str]]:
    """Find editable choice values without exporting marker/metadata fields."""
    choice_encoding = dialogue_choice_encoding(dialogue_encoding)
    main_candidates = decode_candidates(
        record.main, (encoding, choice_encoding), "choice main"
    )
    for main, main_encoding in main_candidates:
        if main.startswith("選択肢\r\n"):
            return [("main", -1, main[5:], "選択肢\r\n", main_encoding)]
        if main.startswith("選択肢\n"):
            return [("main", -1, main[4:], "選択肢\n", main_encoding)]
        if main.startswith("選択肢\r"):
            return [("main", -1, main[4:], "選択肢\r", main_encoding)]

    marker_indices = choice_marker_arg_indices(
        record, encoding, dialogue_encoding
    )
    if not marker_indices:
        return []

    results: list[tuple[str, int, str, str, str]] = []
    for index, raw in enumerate(record.args):
        if index in (30, 31) or index in marker_indices or not has_content(raw):
            continue
        try:
            value = decode_field(raw, choice_encoding, f"choice arg {index}")
        except ScriptError:
            continue
        if value:
            results.append(
                ("arg", index, value, "", choice_encoding)
            )
    return results


def normalize_line_breaks_with_offsets(
    text: str,
) -> tuple[str, list[int]]:
    """Collapse adjacent line-break characters and retain source-end offsets."""
    normalized: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(text):
        if text[index] in "\r\n":
            end = index + 1
            while end < len(text) and text[end] in "\r\n":
                end += 1
            normalized.append("\n")
            offsets.append(end)
            index = end
        else:
            normalized.append(text[index])
            offsets.append(index + 1)
            index += 1
    return "".join(normalized), offsets


def trailing_line_break_units(text: str) -> tuple[int, int]:
    """Return (start, count) for trailing CR/LF units; CRLF is one unit."""
    index = len(text)
    count = 0
    while index > 0 and text[index - 1] in "\r\n":
        if index >= 2 and text[index - 2:index] == "\r\n":
            index -= 2
        else:
            index -= 1
        count += 1
    return index, count


def consume_line_break_units(text: str, start: int, count: int) -> int:
    """Consume an exact number of CRLF/CR/LF units from text[start:]."""
    index = start
    for _ in range(count):
        if text[index:index + 2] == "\r\n":
            index += 2
        elif index < len(text) and text[index] in "\r\n":
            index += 1
        else:
            raise ScriptError(
                "cannot safely locate the end of the original dialogue text; "
                "refusing to discard unknown trailing data"
            )
    return index


def make_text_main(
    name: str,
    old_name: str,
    message: str,
    old_message: str,
    old_main: str,
) -> str | None:
    """Update dialogue text while preserving the original command header and tail."""
    header, separator, old_body = old_main.partition("\n")
    if not header.startswith("[テキスト表示]") or not separator:
        return None

    normalized_body, body_offsets = normalize_line_breaks_with_offsets(old_body)
    normalized_message, _ = normalize_line_breaks_with_offsets(old_message)
    if not normalized_body.startswith(normalized_message):
        return None

    first = header
    if name != old_name:
        command = "[テキスト表示]"
        header_rest = header[len(command):]
        if old_name:
            speaker_pos = header.find(old_name, len(command))
            if speaker_pos < 0:
                return None
            first = (
                header[:speaker_pos]
                + name
                + header[speaker_pos + len(old_name):]
            )
        else:
            first = command + " " + name + header_rest

    trailing_start, trailing_count = trailing_line_break_units(old_message)
    if trailing_count:
        content_prefix, _ = normalize_line_breaks_with_offsets(
            old_message[:trailing_start]
        )
        normalized_content, _ = normalize_line_breaks_with_offsets(old_body)
        if not normalized_content.startswith(content_prefix):
            raise ScriptError(
                "cannot safely locate the original dialogue text in the command; "
                "refusing to discard unknown trailing data"
            )
        raw_content_end = (
            body_offsets[len(content_prefix) - 1] if content_prefix else 0
        )
        raw_end = consume_line_break_units(
            old_body, raw_content_end, trailing_count
        )
    else:
        raw_end = (
            body_offsets[len(normalized_message) - 1]
            if normalized_message
            else 0
        )
    tail = old_body[raw_end:]

    display_message = message.replace("\r\n", "\n").replace("\r", "\n")
    return f"{first}\n{display_message}{tail}"


def export_items(
    script: Script,
    encoding: str,
    dialogue_encoding: str,
    source_name: str,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record_index, record in enumerate(script.records):
        context = f"{source_name} record {record_index}"
        if is_text_record(record):
            record_encoding = resolve_text_record_encoding(
                record, encoding, dialogue_encoding, context
            )
            name = decode_field(
                record.args[0], record_encoding, f"{context} speaker"
            )
            message = decode_field(
                record.args[1], record_encoding, f"{context} message"
            )
            if name:
                items.append({"name": name, "message": message})
            else:
                items.append({"message": message})
            continue

        parsed_scene = scene_choices(record, encoding)
        if parsed_scene is not None:
            for choice in parsed_scene[2]:
                items.append({"choice": choice.value})
            continue

        if is_choice_record(record, encoding, dialogue_encoding):
            targets = extract_choice_targets(
                record, encoding, dialogue_encoding
            )
            for _target, _arg_index, choice, _prefix, _field_encoding in targets:
                items.append({"choice": choice})
    return items


def take_item(
    items: list[dict[str, object]],
    index: int,
    required_key: str,
    source_name: str,
) -> dict[str, object]:
    if index >= len(items):
        raise ScriptError(
            f"not enough JSON entries for {source_name}; "
            f"expected {required_key} at item {index}"
        )
    item = items[index]
    if not isinstance(item, dict) or required_key not in item:
        raise ScriptError(
            f"JSON item {index} in {source_name} must contain '{required_key}'"
        )
    if not isinstance(item[required_key], str):
        raise ScriptError(
            f"JSON item {index} field '{required_key}' must be a string"
        )
    return item


def import_items(
    script: Script,
    items: list[dict[str, object]],
    encoding: str,
    dialogue_encoding: str,
    source_name: str,
) -> int:
    item_index = 0
    changed_records = 0

    for record_index, record in enumerate(script.records):
        context = f"{source_name} record {record_index}"
        record_changed = False

        if is_text_record(record):
            item = take_item(items, item_index, "message", source_name)
            record_encoding = resolve_text_record_encoding(
                record, encoding, dialogue_encoding, context
            )
            old_name = decode_field(
                record.args[0], record_encoding, f"{context} speaker"
            )
            old_message = decode_field(
                record.args[1], record_encoding, f"{context} message"
            )
            name = item.get("name", old_name)
            message = item["message"]
            if not isinstance(name, str) or not isinstance(message, str):
                raise ScriptError(
                    f"JSON item {item_index} in {source_name} has invalid text fields"
                )
            if "\r" in name or "\n" in name:
                raise ScriptError("speaker names cannot contain line breaks")

            if name != old_name:
                record.args[0] = encode_field(
                    name, record_encoding, record.args[0].endswith(b"\0")
                )
                record_changed = True
            if message != old_message:
                record.args[1] = encode_field(
                    message, record_encoding, record.args[1].endswith(b"\0")
                )
                record_changed = True

            if record_changed:
                main_text = decode_field(
                    record.main, encoding, f"{context} main"
                )
                new_main = make_text_main(
                    name, old_name, message, old_message, main_text
                )
                if new_main is not None:
                    record.main = encode_field(
                        new_main, encoding, record.main.endswith(b"\0")
                    )
            item_index += 1

        else:
            parsed_scene = scene_choices(record, encoding)
            if parsed_scene is not None:
                field_name, old_text, parsed = parsed_scene
                new_choices: list[str] = []
                for _span in parsed:
                    item = take_item(items, item_index, "choice", source_name)
                    new_choices.append(item["choice"])
                    item_index += 1

                updated = replace_scene_choice_text(
                    old_text, new_choices, parsed
                )
                if updated != old_text:
                    if field_name == "arg31":
                        raw = record.args[31]
                        record.args[31] = encode_field(
                            updated, encoding, raw.endswith(b"\0")
                        )
                        try:
                            main_text = decode_field(
                                record.main, encoding, f"{context} main"
                            )
                        except ScriptError:
                            main_text = ""
                        main_choices = parse_scene_choice_spans(main_text)
                        if (
                            main_choices is not None
                            and len(main_choices) == len(new_choices)
                        ):
                            main_updated = replace_scene_choice_text(
                                main_text, new_choices, main_choices
                            )
                            if main_updated != main_text:
                                record.main = encode_field(
                                    main_updated,
                                    encoding,
                                    record.main.endswith(b"\0"),
                                )
                    else:
                        raw = record.main
                        record.main = encode_field(
                            updated, encoding, raw.endswith(b"\0")
                        )
                        try:
                            arg_text = decode_field(
                                record.args[31],
                                encoding,
                                f"{context} arg 31",
                            )
                        except ScriptError:
                            arg_text = ""
                        arg_choices = parse_scene_choice_spans(arg_text)
                        if (
                            arg_choices is not None
                            and len(arg_choices) == len(new_choices)
                        ):
                            arg_updated = replace_scene_choice_text(
                                arg_text, new_choices, arg_choices
                            )
                            if arg_updated != arg_text:
                                record.args[31] = encode_field(
                                    arg_updated,
                                    encoding,
                                    record.args[31].endswith(b"\0"),
                                )
                    record_changed = True

            elif is_choice_record(record, encoding, dialogue_encoding):
                targets = extract_choice_targets(
                    record, encoding, dialogue_encoding
                )
                for (
                    target,
                    arg_index,
                    old_choice,
                    prefix,
                    target_encoding,
                ) in targets:
                    item = take_item(items, item_index, "choice", source_name)
                    new_choice = item["choice"]
                    if not isinstance(new_choice, str):
                        raise ScriptError(
                            f"JSON item {item_index} choice must be a string"
                        )
                    if "\r" in new_choice or "\n" in new_choice:
                        raise ScriptError(
                            "a single choice item cannot contain a line break"
                        )
                    if new_choice != old_choice:
                        if target == "main":
                            replacement = prefix + new_choice
                            record.main = encode_field(
                                replacement,
                                target_encoding,
                                record.main.endswith(b"\0"),
                            )
                        else:
                            raw = record.args[arg_index]
                            record.args[arg_index] = encode_field(
                                new_choice,
                                target_encoding,
                                raw.endswith(b"\0"),
                            )
                        record_changed = True
                    item_index += 1

        if record_changed:
            changed_records += 1

    if item_index != len(items):
        raise ScriptError(
            f"{len(items) - item_index} unused JSON item(s) in {source_name}"
        )
    return changed_records


def path_key(path: Path) -> str:
    """Return a stable case-insensitive key for Windows-style file paths."""
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def ensure_output_not_inside(
    output_path: Path, input_path: Path, label: str
) -> None:
    if (
        path_key(output_path) == path_key(input_path)
        or is_path_within(output_path, input_path)
    ):
        raise ScriptError(
            f"{label} must be outside the input path: {input_path}"
        )


def export_file(
    input_path: Path,
    output_path: Path,
    encoding: str,
    dialogue_encoding: str,
) -> None:
    if path_key(input_path) == path_key(output_path):
        raise ScriptError("JSON output path cannot overwrite the original script")
    script = read_script(input_path)
    items = export_items(
        script, encoding, dialogue_encoding, str(input_path)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    print(f"exported {len(items)} item(s): {input_path} -> {output_path}")


def load_translation(path: Path) -> list[dict[str, object]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ScriptError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, list):
        raise ScriptError(f"JSON root must be an array: {path}")
    if not all(isinstance(item, dict) for item in value):
        raise ScriptError(f"every JSON array item must be an object: {path}")
    return value


def import_file(
    original_path: Path,
    json_path: Path,
    output_path: Path,
    encoding: str,
    dialogue_encoding: str,
) -> None:
    if path_key(original_path) == path_key(output_path):
        raise ScriptError(
            "imported script output cannot overwrite the original script"
        )
    if path_key(json_path) == path_key(output_path):
        raise ScriptError(
            "imported script output cannot overwrite the translation JSON"
        )
    script = read_script(original_path)
    items = load_translation(json_path)
    changed = import_items(
        script, items, encoding, dialogue_encoding, str(json_path)
    )
    write_script(script, output_path)
    print(
        f"imported {changed} changed record(s): "
        f"{original_path} + {json_path} -> {output_path}"
    )


def iter_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda p: p.as_posix().casefold(),
    )


def export_path(
    input_path: Path,
    output_path: Path,
    encoding: str,
    dialogue_encoding: str,
) -> None:
    if input_path.is_dir():
        ensure_output_not_inside(
            output_path, input_path, "JSON output folder"
        )
        files = iter_files(input_path)
        if not files:
            raise ScriptError(f"no files found: {input_path}")

        # ---------------------------------------------------------
        # Pass 1: identify valid ADVSCRIPT2 scripts.
        #
        # Files without the ADVSCRIPT2 signature are ordinary
        # resources and must not participate in output-name checks.
        # A file with the signature but invalid internal structure
        # is a damaged script and must stop the operation.
        # ---------------------------------------------------------
        scripts: list[Path] = []

        for file_path in files:
            try:
                read_script(file_path)
            except ScriptError as exc:
                if str(exc).startswith("invalid ADVSCRIPT2 signature:"):
                    print(
                        f"skip non-script file: {file_path}",
                        file=sys.stderr,
                    )
                    continue
                raise

            scripts.append(file_path)

        if not scripts:
            raise ScriptError(
                f"no valid ADVSCRIPT2 files found: {input_path}"
            )

        # ---------------------------------------------------------
        # Pass 2: preflight output names for valid scripts only.
        #
        # This entire check finishes before the output directory is
        # created or any JSON file is written.
        # ---------------------------------------------------------
        targets: dict[str, Path] = {}

        for file_path in scripts:
            relative = file_path.relative_to(input_path)
            target = (output_path / relative).with_suffix(".json")
            key = path_key(target)

            previous = targets.get(key)
            if previous is not None:
                raise ScriptError(
                    "multiple valid ADVSCRIPT2 scripts map to the same "
                    "JSON output: "
                    f"{previous.relative_to(input_path)} and {relative}"
                )

            targets[key] = file_path

        # ---------------------------------------------------------
        # Pass 3: all checks passed; begin exporting.
        # ---------------------------------------------------------
        output_path.mkdir(parents=True, exist_ok=True)

        processed = 0
        for file_path in scripts:
            relative = file_path.relative_to(input_path)
            target = (output_path / relative).with_suffix(".json")

            export_file(
                file_path,
                target,
                encoding,
                dialogue_encoding,
            )
            processed += 1

        print(
            f"finished: exported {processed} valid ADVSCRIPT2 script(s)"
        )

    else:
        export_file(
            input_path,
            output_path,
            encoding,
            dialogue_encoding,
        )


def import_path(
    original_path: Path,
    json_path: Path,
    output_path: Path,
    encoding: str,
    dialogue_encoding: str,
) -> None:
    if original_path.is_dir():
        if not json_path.is_dir():
            raise ScriptError("folder import requires a JSON folder")

        ensure_output_not_inside(
            output_path, original_path, "import output folder"
        )

        if is_path_within(json_path, original_path):
            raise ScriptError(
                "translation JSON folder must be outside the original "
                "script folder"
            )

        if (
            is_path_within(output_path, json_path)
            or is_path_within(json_path, output_path)
        ):
            raise ScriptError(
                "import output folder and translation JSON folder "
                "must be separate"
            )

        files = iter_files(original_path)
        if not files:
            raise ScriptError(f"no files found: {original_path}")

        scripts: list[Path] = []
        non_scripts: list[Path] = []

        for file_path in files:
            try:
                read_script(file_path)
                scripts.append(file_path)
            except ScriptError as exc:
                if str(exc).startswith("invalid ADVSCRIPT2 signature:"):
                    non_scripts.append(file_path)
                    continue
                raise

        missing_json = [
            (
                json_path / file_path.relative_to(original_path)
            ).with_suffix(".json")
            for file_path in scripts
            if not (
                json_path / file_path.relative_to(original_path)
            ).with_suffix(".json").is_file()
        ]

        if missing_json:
            raise ScriptError(
                f"missing translation JSON for script: {missing_json[0]}"
            )

        output_path.mkdir(parents=True, exist_ok=True)

        processed = 0
        copied = 0

        for file_path in files:
            relative = file_path.relative_to(original_path)
            destination = output_path / relative

            if file_path in non_scripts:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(file_path.read_bytes())
                copied += 1
                continue

            source_json = (
                json_path / relative
            ).with_suffix(".json")

            import_file(
                file_path,
                source_json,
                destination,
                encoding,
                dialogue_encoding,
            )
            processed += 1

        if processed == 0:
            raise ScriptError(
                f"no valid ADVSCRIPT2 files found: {original_path}"
            )

        print(
            f"finished: imported {processed} script(s), "
            f"copied {copied} non-script file(s)"
        )

    else:
        import_file(
            original_path,
            json_path,
            output_path,
            encoding,
            dialogue_encoding,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract/import NekoSDK ADVSCRIPT2 dialogue and "
            "choice strings as UTF-8 JSON."
        )
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "-e",
        "--export",
        action="store_true",
        help="export script text to JSON",
    )
    mode.add_argument(
        "-i",
        "--import-json",
        action="store_true",
        help="import edited JSON into scripts",
    )

    parser.add_argument(
        "paths",
        nargs="+",
        help="export: input output; import: original JSON_folder output",
    )

    parser.add_argument(
        "--encoding",
        default=DEFAULT_ENCODING,
        help=f"command/scene encoding (default: {DEFAULT_ENCODING})",
    )

    parser.add_argument(
        "--dialogue-encoding",
        default=None,
        help=(
            "dialogue encoding; use 'auto' for mixed CP932/GBK opcode-5 text "
            "(default: same as --encoding)"
        ),
    )

    args = parser.parse_args(argv)

    try:
        encoding = validate_encoding(args.encoding)

        dialogue_encoding = (
            validate_dialogue_encoding(args.dialogue_encoding)
            if args.dialogue_encoding is not None
            else encoding
        )

        if args.export:
            if len(args.paths) != 2:
                parser.error(
                    "export requires exactly: input_path output_path"
                )

            export_path(
                Path(args.paths[0]),
                Path(args.paths[1]),
                encoding,
                dialogue_encoding,
            )

        else:
            if len(args.paths) != 3:
                parser.error(
                    "import requires exactly: original_path "
                    "json_folder output_path"
                )

            import_path(
                Path(args.paths[0]),
                Path(args.paths[1]),
                Path(args.paths[2]),
                encoding,
                dialogue_encoding,
            )

    except (OSError, UnicodeError, ScriptError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
    
