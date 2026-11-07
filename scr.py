#!/usr/bin/env python3
"""NekoSDK ADVSCRIPT2 text extractor and importer.

The input files are binary ADVSCRIPT2 streams even though their file names
usually end in .txt. This tool exports translatable dialogue/choice strings
as UTF-8 JSON and can import the edited JSON back into the original script.

Default script encoding is CP932 (Shift-JIS compatible), matching the tested
archive. Use --encoding only when the target game is known to use another
encoding. Changing the byte encoding does not make the game engine support it.

Usage:
  py -3.13 scr.py -e extracted_folder json_folder
  py -3.13 scr.py -i extracted_folder json_folder imported_folder
  py -3.13 scr.py -e input.txt output.json --encoding cp932
  py -3.13 scr.py -i input.txt input.json output.txt --encoding cp932
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
    """Resolve an encoding name before any script files are processed."""
    name = encoding.strip()
    if not name:
        raise ScriptError("encoding name cannot be empty")
    try:
        return codecs.lookup(name).name
    except LookupError as exc:
        raise ScriptError(f"unknown text encoding: {encoding!r}") from exc


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


def read_script(path: Path) -> Script:
    try:
        data = path.read_bytes()
    except OSError:
        raise

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
                raise ScriptError(f"main string exceeds end of file in record #{index}: {path}")
            main = data[pos : pos + main_len]
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
                args.append(data[pos : pos + arg_len])
                pos += arg_len
        except struct.error as exc:
            raise ScriptError(f"invalid record #{index} at 0x{start:X} in {path}") from exc

        records.append(Record(header, nums32, extra, nums16, main, args))

    if pos != len(data):
        raise ScriptError(f"unexpected trailing data in {path}: 0x{pos:X} != 0x{len(data):X}")
    return Script(records)


def write_script(script: Script, path: Path) -> None:
    output = bytearray(MAGIC)
    output += pack_u32(len(script.records))

    for index, record in enumerate(script.records):
        if len(record.header) != 4 or len(record.nums32) != 32 or len(record.nums16) != 16:
            raise ScriptError(f"invalid fixed field count in record #{index}")
        if len(record.args) != 32:
            raise ScriptError(f"record #{index} must contain exactly 32 argument strings")

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


def decode_record_text(record: Record, encoding: str, context: str) -> tuple[str, list[str]]:
    main = decode_field(record.main, encoding, f"{context} main")
    args = [decode_field(value, encoding, f"{context} arg {index}") for index, value in enumerate(record.args)]
    return main, args


def is_text_record(record: Record) -> bool:
    return record.header[3] == TEXT_OPCODE and len(record.args) == 32 and has_content(record.args[1])


def is_scene_record(record: Record) -> bool:
    return record.header[3] == SCENE_OPCODE and (has_content(record.main) or has_content(record.args[31]))


def scene_source(record: Record, encoding: str) -> tuple[str, str] | None:
    """Return (field_name, decoded text), preferring argument 31 when present."""
    if has_content(record.args[31]):
        return "arg31", decode_field(record.args[31], encoding, "scene arg 31")
    if has_content(record.main):
        return "main", decode_field(record.main, encoding, "scene main")
    return None


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


def parse_quoted_choice_row(line: str, line_offset: int) -> list[ChoiceSpan] | None:
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
        spans.append(ChoiceSpan(line_offset + value_start, line_offset + value_end, value))
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
                        ChoiceSpan(offset + prefix_length, offset + len(body), value)
                    )
        else:
            parsed = parse_quoted_choice_row(body, offset)
            if parsed is not None:
                # A quoted row is a single option declaration; do not absorb later commands.
                return parsed
        offset += len(line)

    return choices or None


def scene_choices(record: Record, encoding: str) -> tuple[str, str, list[ChoiceSpan]] | None:
    """Locate a real choice block in arg[31] or main, not merely any nonempty field.

    arg[31] is the primary location in the tested scripts, but some variants
    store the command in main instead. Prefer arg[31] when both are valid.
    """
    if not is_scene_record(record):
        return None

    for field_name, raw in (("arg31", record.args[31]), ("main", record.main)):
        if not has_content(raw):
            continue
        text = decode_field(raw, encoding, f"scene {field_name}")
        parsed = parse_scene_choice_spans(text)
        if parsed is not None:
            return field_name, text, parsed
    return None


def replace_scene_choice_text(
    text: str, choices: list[str], parsed: list[ChoiceSpan] | None = None
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
        if span.start < cursor or span.end < span.start:
            raise ScriptError("internal error: invalid scene choice span")
        output.append(text[cursor:span.start])
        output.append(choice)
        cursor = span.end
    output.append(text[cursor:])
    return "".join(output)


def is_choice_record(record: Record, encoding: str) -> bool:
    if record.header[3] in (TEXT_OPCODE, SCENE_OPCODE):
        return False
    main = decode_field(record.main, encoding, "choice main")
    if main.startswith("選択肢\r") or main.startswith("選択肢\n"):
        return True
    for index, raw in enumerate(record.args):
        if index in (30, 31) or not has_content(raw):
            continue
        value = decode_field(raw, encoding, f"choice arg {index}")
        if any(marker in value for marker in ("[選択", "選択肢", "[セレクト")):
            return True
    return False


def extract_choice_targets(record: Record, encoding: str) -> list[tuple[str, int, str, str]]:
    main = decode_field(record.main, encoding, "choice main")
    if main.startswith("選択肢\r\n"):
        return [("main", -1, main[5:], "選択肢\r\n")]
    if main.startswith("選択肢\n"):
        return [("main", -1, main[4:], "選択肢\n")]
    if main.startswith("選択肢\r"):
        return [("main", -1, main[4:], "選択肢\r")]

    results: list[tuple[str, int, str, str]] = []
    for index, raw in enumerate(record.args):
        if index in (30, 31) or not has_content(raw):
            continue
        value = decode_field(raw, encoding, f"choice arg {index}")
        results.append(("arg", index, value, ""))
    return results


def normalize_line_breaks_with_offsets(text: str) -> tuple[str, list[int]]:
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
    name: str, old_name: str, message: str, old_message: str, old_main: str
) -> str | None:
    """Update dialogue text while preserving the original command header and tail."""
    header, separator, old_body = old_main.partition("\n")
    if not header.startswith("[テキスト表示]"):
        return None
    if not separator:
        raise ScriptError("cannot safely update dialogue: command body is missing")

    # Preserve the complete original header, including non-path voice IDs and spacing.
    # Only replace the exact speaker substring when the speaker was edited.
    first = header
    if name != old_name:
        command = "[テキスト表示]"
        header_rest = header[len(command):]
        if old_name:
            speaker_pos = header.find(old_name, len(command))
            if speaker_pos < 0:
                raise ScriptError(
                    "cannot safely locate the original speaker in the command header"
                )
            first = header[:speaker_pos] + name + header[speaker_pos + len(old_name):]
        else:
            first = command + " " + name + header_rest

    normalized_body, body_offsets = normalize_line_breaks_with_offsets(old_body)
    normalized_message, _message_offsets = normalize_line_breaks_with_offsets(old_message)
    if not normalized_body.startswith(normalized_message):
        raise ScriptError(
            "cannot safely locate the original dialogue text in the command; "
            "refusing to discard unknown trailing data"
        )

    # Most scripts differ only in whether internal line breaks are CR, LF or CRLF.
    # When args[1] itself ends in line breaks, consume exactly those units from main;
    # any additional line breaks belong to the command tail and must remain intact.
    trailing_start, trailing_count = trailing_line_break_units(old_message)
    if trailing_count:
        content_prefix, _ = normalize_line_breaks_with_offsets(old_message[:trailing_start])
        normalized_content, _ = normalize_line_breaks_with_offsets(old_body)
        if not normalized_content.startswith(content_prefix):
            raise ScriptError(
                "cannot safely locate the original dialogue text in the command; "
                "refusing to discard unknown trailing data"
            )
        raw_content_end = (
            body_offsets[len(content_prefix) - 1] if content_prefix else 0
        )
        raw_end = consume_line_break_units(old_body, raw_content_end, trailing_count)
    else:
        raw_end = body_offsets[len(normalized_message) - 1] if normalized_message else 0
    tail = old_body[raw_end:]

    # The source command uses LF even when the argument message contains CRLF.
    display_message = message.replace("\r\n", "\n").replace("\r", "\n")
    return f"{first}\n{display_message}{tail}"


def export_items(script: Script, encoding: str, source_name: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record_index, record in enumerate(script.records):
        context = f"{source_name} record {record_index}"
        if is_text_record(record):
            name = decode_field(record.args[0], encoding, f"{context} speaker")
            message = decode_field(record.args[1], encoding, f"{context} message")
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

        if is_choice_record(record, encoding):
            for _target, _arg_index, choice, _prefix in extract_choice_targets(record, encoding):
                items.append({"choice": choice})

    return items


def take_item(items: list[dict[str, object]], index: int, required_key: str, source_name: str) -> dict[str, object]:
    if index >= len(items):
        raise ScriptError(f"not enough JSON entries for {source_name}; expected {required_key} at item {index}")
    item = items[index]
    if not isinstance(item, dict) or required_key not in item:
        raise ScriptError(f"JSON item {index} in {source_name} must contain '{required_key}'")
    if not isinstance(item[required_key], str):
        raise ScriptError(f"JSON item {index} field '{required_key}' must be a string")
    return item


def import_items(script: Script, items: list[dict[str, object]], encoding: str, source_name: str) -> int:
    item_index = 0
    changed_records = 0

    for record_index, record in enumerate(script.records):
        context = f"{source_name} record {record_index}"
        record_changed = False

        if is_text_record(record):
            item = take_item(items, item_index, "message", source_name)
            old_name = decode_field(record.args[0], encoding, f"{context} speaker")
            old_message = decode_field(record.args[1], encoding, f"{context} message")
            name = item.get("name", old_name)
            message = item["message"]
            if not isinstance(name, str) or not isinstance(message, str):
                raise ScriptError(f"JSON item {item_index} in {source_name} has invalid text fields")
            if "\r" in name or "\n" in name:
                raise ScriptError("speaker names cannot contain line breaks")

            if name != old_name:
                record.args[0] = encode_field(name, encoding, record.args[0].endswith(b"\0"))
                record_changed = True
            if message != old_message:
                record.args[1] = encode_field(message, encoding, record.args[1].endswith(b"\0"))
                record_changed = True

            if record_changed:
                new_main = make_text_main(
                    name,
                    old_name,
                    message,
                    old_message,
                    decode_field(record.main, encoding, f"{context} main"),
                )
                if new_main is not None:
                    record.main = encode_field(new_main, encoding, record.main.endswith(b"\0"))
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

                updated = replace_scene_choice_text(old_text, new_choices, parsed)
                if updated != old_text:
                    if field_name == "arg31":
                        raw = record.args[31]
                        record.args[31] = encode_field(updated, encoding, raw.endswith(b"\0"))
                        # Some script records mirror the same scene command in main.
                        main_text = decode_field(record.main, encoding, f"{context} main")
                        main_choices = parse_scene_choice_spans(main_text)
                        if main_choices is not None and len(main_choices) == len(new_choices):
                            main_updated = replace_scene_choice_text(main_text, new_choices, main_choices)
                            if main_updated != main_text:
                                record.main = encode_field(
                                    main_updated, encoding, record.main.endswith(b"\0")
                                )
                    else:
                        raw = record.main
                        record.main = encode_field(updated, encoding, raw.endswith(b"\0"))
                        # Keep arg31 synchronized when it contains the same choice block.
                        arg_text = decode_field(record.args[31], encoding, f"{context} arg 31")
                        arg_choices = parse_scene_choice_spans(arg_text)
                        if arg_choices is not None and len(arg_choices) == len(new_choices):
                            arg_updated = replace_scene_choice_text(arg_text, new_choices, arg_choices)
                            if arg_updated != arg_text:
                                record.args[31] = encode_field(
                                    arg_updated, encoding, record.args[31].endswith(b"\0")
                                )
                    record_changed = True

            elif is_choice_record(record, encoding):
                targets = extract_choice_targets(record, encoding)
                for target, arg_index, old_choice, prefix in targets:
                    item = take_item(items, item_index, "choice", source_name)
                    new_choice = item["choice"]
                    if not isinstance(new_choice, str):
                        raise ScriptError(f"JSON item {item_index} choice must be a string")
                    if "\r" in new_choice or "\n" in new_choice:
                        raise ScriptError("a single choice item cannot contain a line break")
                    if new_choice != old_choice:
                        if target == "main":
                            replacement = prefix + new_choice
                            record.main = encode_field(replacement, encoding, record.main.endswith(b"\0"))
                        else:
                            raw = record.args[arg_index]
                            record.args[arg_index] = encode_field(new_choice, encoding, raw.endswith(b"\0"))
                        record_changed = True
                    item_index += 1

        if record_changed:
            changed_records += 1

    if item_index != len(items):
        raise ScriptError(f"{len(items) - item_index} unused JSON item(s) in {source_name}")
    return changed_records


def export_file(input_path: Path, output_path: Path, encoding: str) -> None:
    script = read_script(input_path)
    items = export_items(script, encoding, str(input_path))
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


def import_file(original_path: Path, json_path: Path, output_path: Path, encoding: str) -> None:
    script = read_script(original_path)
    items = load_translation(json_path)
    changed = import_items(script, items, encoding, str(json_path))
    write_script(script, output_path)
    print(f"imported {changed} changed record(s): {original_path} + {json_path} -> {output_path}")


def iter_files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.as_posix().casefold())


def export_path(input_path: Path, output_path: Path, encoding: str) -> None:
    if input_path.is_dir():
        files = iter_files(input_path)
        if not files:
            raise ScriptError(f"no files found: {input_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        processed = 0
        for file_path in files:
            relative = file_path.relative_to(input_path)
            try:
                export_file(file_path, (output_path / relative).with_suffix(".json"), encoding)
                processed += 1
            except ScriptError as exc:
                if str(exc).startswith("invalid ADVSCRIPT2 signature:"):
                    print(f"skip non-script file: {file_path}", file=sys.stderr)
                    continue
                raise
        if processed == 0:
            raise ScriptError(f"no valid ADVSCRIPT2 files found: {input_path}")
    else:
        export_file(input_path, output_path, encoding)


def import_path(original_path: Path, json_path: Path, output_path: Path, encoding: str) -> None:
    if original_path.is_dir():
        if not json_path.is_dir():
            raise ScriptError("folder import requires a JSON folder")
        files = iter_files(original_path)
        if not files:
            raise ScriptError(f"no files found: {original_path}")
        output_path.mkdir(parents=True, exist_ok=True)
        processed = 0
        copied = 0
        for file_path in files:
            relative = file_path.relative_to(original_path)
            destination = output_path / relative
            try:
                read_script(file_path)
            except ScriptError as exc:
                if str(exc).startswith("invalid ADVSCRIPT2 signature:"):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(file_path.read_bytes())
                    copied += 1
                    continue
                raise

            source_json = (json_path / relative).with_suffix(".json")
            if not source_json.is_file():
                raise ScriptError(f"missing translation JSON for script: {source_json}")
            import_file(file_path, source_json, destination, encoding)
            processed += 1

        if processed == 0:
            raise ScriptError(f"no valid ADVSCRIPT2 files found: {original_path}")
        print(f"finished: imported {processed} script(s), copied {copied} non-script file(s)")
    else:
        import_file(original_path, json_path, output_path, encoding)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract/import NekoSDK ADVSCRIPT2 dialogue and choice strings as UTF-8 JSON."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-e", "--export", action="store_true", help="export script text to JSON")
    mode.add_argument("-i", "--import-json", action="store_true", help="import edited JSON into scripts")
    parser.add_argument("paths", nargs="+", help="export: input output; import: original JSON_folder output")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help=f"script text encoding (default: {DEFAULT_ENCODING})")
    args = parser.parse_args(argv)

    try:
        encoding = validate_encoding(args.encoding)
        if args.export:
            if len(args.paths) != 2:
                parser.error("export requires exactly: input_path output_path")
            export_path(Path(args.paths[0]), Path(args.paths[1]), encoding)
        else:
            if len(args.paths) != 3:
                parser.error("import requires exactly: original_path json_path output_path")
            import_path(Path(args.paths[0]), Path(args.paths[1]), Path(args.paths[2]), encoding)
    except (OSError, UnicodeError, ScriptError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
