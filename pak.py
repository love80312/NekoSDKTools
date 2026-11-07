#!/usr/bin/env python3
"""NekoSDK NEKOPACK4A / NEKOPACK4S extractor and NEKOPACK4A repacker.

Supported archive formats:
- NEKOPACK4A: indexed archive; supports extraction and repacking.
- NEKOPACK4S: single-entry archive; extraction only.

File names are stored as CP932. Each payload is a zlib stream followed by
an uncompressed-size footer; the first 0x20 bytes of the stored payload are
XOR-masked.

Usage:
  py -3.14 pak.py -u input.pak output_folder
  py -3.14 pak.py -p input_folder output.pak

Optional environment variables for packing:
  NEKO_PAK_LEVEL=0..9       zlib compression level (default: 9)
  NEKO_PAK_WORKERS=1..N     concurrent compression workers (default: 1)
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

SIGNATURE = b"NEKOPACK"
MAGIC = SIGNATURE + b"4A"
MAGIC_SINGLE = SIGNATURE + b"4S"
HEADER_SIZE = 14
CHUNK_SIZE = 1024 * 1024
UINT32_MAX = 0xFFFFFFFF
DEFAULT_ENCODING = "cp932"
DEFAULT_COMPRESSION_LEVEL = 9
# Keep CPU and memory use predictable on older computers.
DEFAULT_PACK_WORKERS = 1


class PakError(Exception):
    """Raised when a PAK archive is malformed or cannot be processed."""


@dataclass(frozen=True)
class Entry:
    name: str
    name_bytes: bytes
    offset: int
    stored_size: int


@dataclass(frozen=True)
class PreparedEntry:
    name: str
    name_bytes: bytes
    temp_path: Path
    compressed_size: int
    unpacked_size: int
    stored_size: int
    offset: int = 0


def read_u32(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<I", data, offset)[0]
    except struct.error as exc:
        raise PakError("unexpected end of data while reading uint32") from exc


def write_u32(value: int) -> bytes:
    if not 0 <= value <= UINT32_MAX:
        raise PakError(f"value does not fit uint32: {value}")
    return struct.pack("<I", value)


def signed_byte_sum(data: bytes) -> int:
    """Return the sum of signed 8-bit values, wrapped to uint32."""
    total = sum(value if value < 0x80 else value - 0x100 for value in data)
    return total & UINT32_MAX


def xor_u32(value: int, key: int) -> int:
    return (value & UINT32_MAX) ^ (key & UINT32_MAX)


def mask_payload_header(header: bytearray, stored_size: int) -> None:
    key = stored_size // 8 + 0x22
    for index in range(min(0x20, len(header))):
        header[index] ^= key & 0xFF
        key = (key << 3) & UINT32_MAX


def display_name(name_bytes: bytes, encoding: str) -> str:
    raw = name_bytes.split(b"\0", 1)[0]
    if not raw:
        raise PakError("empty entry name")
    return raw.decode(encoding, errors="strict")


def encoded_name(name: str, encoding: str) -> bytes:
    if not name or "\0" in name:
        raise PakError(f"invalid entry name: {name!r}")
    raw = name.encode(encoding, errors="strict") + b"\0"
    if len(raw) > 0x100:
        raise PakError(f"entry name is too long: {name!r}")
    return raw


def normalized_entry_name(name: str) -> str:
    return name.replace("\\", "/")


def read_archive_index(archive_path: Path) -> tuple[bytes, int, str]:
    """Read the index area for either NEKOPACK4A or NEKOPACK4S.

    NEKOPACK4A stores the total index size immediately after the 10-byte
    signature/type field. NEKOPACK4S instead stores a single filename length,
    followed by that filename and one obfuscated offset/length pair.
    Both index layouts can then be parsed by the same entry parser.
    """
    archive_size = archive_path.stat().st_size
    if archive_size < HEADER_SIZE:
        raise PakError("file is too small to be a NekoSDK archive")

    with archive_path.open("rb") as archive:
        header = archive.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or header[:8] != SIGNATURE:
            raise PakError("invalid NekoSDK archive signature (expected NEKOPACK)")

        archive_type = header[8:10]
        if archive_type == b"4A":
            index_size = read_u32(header, 10)
            if index_size > archive_size - HEADER_SIZE:
                raise PakError("index extends beyond end of archive")
            index = archive.read(index_size)
            if len(index) != index_size:
                raise PakError("truncated archive index")
            return index, HEADER_SIZE + index_size, "4A"

        if archive_type == b"4S":
            # In 4S, the uint32 at offset 10 is the filename length, not the
            # total index size. There is exactly one entry and no terminator.
            # The 14-byte header read above already includes this length at
            # offsets 10..13.
            name_size_raw = header[10:14]
            name_size = read_u32(name_size_raw, 0)
            if name_size == 0:
                raise PakError("NEKOPACK4S contains an empty filename")
            if name_size > 0x100:
                raise PakError(f"entry name is too long: {name_size} bytes")

            remaining_index_size = name_size + 8
            if remaining_index_size > archive_size - HEADER_SIZE:
                raise PakError("NEKOPACK4S index extends beyond end of archive")
            index_tail = archive.read(remaining_index_size)
            if len(index_tail) != remaining_index_size:
                raise PakError("truncated NEKOPACK4S index")

            index = name_size_raw + index_tail
            return index, HEADER_SIZE + remaining_index_size, "4S"

        try:
            type_name = archive_type.decode("ascii", errors="replace")
        except Exception:
            type_name = repr(archive_type)
        raise PakError(
            f"unsupported NekoSDK archive type: {type_name!r} "
            "(expected 4A or 4S)"
        )


def parse_entries(archive_path: Path, encoding: str = DEFAULT_ENCODING) -> list[Entry]:
    if not archive_path.is_file():
        raise PakError(f"input archive not found: {archive_path}")

    index, data_start, archive_type = read_archive_index(archive_path)
    index_size = len(index)
    archive_size = archive_path.stat().st_size

    entries: list[Entry] = []
    seen_names: set[str] = set()
    pos = 0

    while pos < index_size:
        if pos + 4 > index_size:
            raise PakError("truncated name length in index")
        name_size = read_u32(index, pos)
        pos += 4
        if name_size == 0:
            if archive_type == "4S":
                raise PakError("NEKOPACK4S contains an invalid empty entry")
            break
        if name_size > 0x100:
            raise PakError(f"entry name is too long: {name_size} bytes")
        if pos + name_size + 8 > index_size:
            raise PakError("truncated entry in index")

        name_bytes = index[pos : pos + name_size]
        pos += name_size
        key = signed_byte_sum(name_bytes)
        offset = xor_u32(read_u32(index, pos), key)
        stored_size = xor_u32(read_u32(index, pos + 4), key)
        pos += 8

        name = display_name(name_bytes, encoding)
        safe_key = normalized_entry_name(name).casefold()
        if safe_key in seen_names:
            raise PakError(f"duplicate archive entry path: {name}")
        seen_names.add(safe_key)

        if offset < data_start:
            raise PakError(f"entry points into archive header/index: {name}")
        if stored_size < 5:
            raise PakError(f"entry is too small to contain a zlib stream and footer: {name}")
        if offset > archive_size or stored_size > archive_size - offset:
            raise PakError(f"entry points outside archive: {name}")

        entries.append(Entry(name, name_bytes, offset, stored_size))

    if archive_type == "4S":
        if pos != index_size:
            raise PakError("unexpected trailing data in NEKOPACK4S index")
        if len(entries) != 1:
            raise PakError("NEKOPACK4S must contain exactly one entry")
    elif pos < index_size:
        # A-format archives may end their index with a zero-length terminator.
        # Preserve the existing behavior of accepting that terminator.
        pass

    if not entries:
        raise PakError("archive index is empty")

    # Prevent overlapping payload ranges, which can otherwise cause one entry
    # to read another entry's data when the index is damaged.
    ranges = sorted((entry.offset, entry.offset + entry.stored_size, entry.name) for entry in entries)
    previous_end = data_start
    previous_name = "archive index"
    for start, end, name in ranges:
        if start < previous_end:
            raise PakError(f"overlapping payload ranges: {previous_name} / {name}")
        previous_end = end
        previous_name = name

    return entries


def safe_output_path(root: Path, entry_name: str) -> Path:
    normalized = normalized_entry_name(entry_name)
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise PakError(f"unsafe entry path: {entry_name!r}")
    if any(":" in part for part in pure.parts):
        raise PakError(f"unsafe entry path: {entry_name!r}")

    root_resolved = root.resolve()
    output = root.joinpath(*pure.parts)
    output_resolved = output.resolve(strict=False)
    try:
        output_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PakError(f"unsafe entry path: {entry_name!r}") from exc
    return output


def decrypt_zlib_stream(archive: BinaryIO, entry: Entry, output: BinaryIO) -> int:
    """Decrypt and decompress one entry, including short-payload footers."""
    compressed_size = entry.stored_size - 4
    prefix_size = min(0x20, entry.stored_size)

    archive.seek(entry.offset)
    prefix = bytearray(archive.read(prefix_size))
    if len(prefix) != prefix_size:
        raise PakError(f"truncated data: {entry.name}")
    mask_payload_header(prefix, entry.stored_size)

    decompressor = zlib.decompressobj()
    written = 0

    def write_decompressed(data: bytes) -> None:
        nonlocal written
        if data:
            written += len(data)
            if written > UINT32_MAX:
                raise PakError(f"unpacked data exceeds uint32 limit: {entry.name}")
            output.write(data)

    def feed(chunk: bytes) -> None:
        pending = chunk
        while pending:
            data = decompressor.decompress(pending, CHUNK_SIZE)
            write_decompressed(data)
            pending = decompressor.unconsumed_tail
            if decompressor.unused_data:
                raise PakError(f"unexpected trailing bytes in zlib stream: {entry.name}")
            if decompressor.eof and pending:
                raise PakError(f"unexpected data after zlib stream: {entry.name}")

    compressed_prefix_size = min(prefix_size, compressed_size)
    if compressed_prefix_size:
        feed(bytes(prefix[:compressed_prefix_size]))

    remaining = compressed_size - compressed_prefix_size
    while remaining:
        chunk = archive.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise PakError(f"truncated compressed stream: {entry.name}")
        remaining -= len(chunk)
        feed(chunk)

    write_decompressed(decompressor.flush())
    if not decompressor.eof:
        raise PakError(f"incomplete zlib stream: {entry.name}")
    if decompressor.unused_data:
        raise PakError(f"unexpected trailing bytes in zlib stream: {entry.name}")

    # If the compressed stream is shorter than 0x20 bytes, some or all of the
    # footer was inside the masked prefix and is already decrypted above.
    if prefix_size > compressed_size:
        footer = bytes(prefix[compressed_size:])
    else:
        footer = archive.read(4)

    if len(footer) != 4:
        raise PakError(f"missing unpacked-size footer: {entry.name}")
    expected_size = read_u32(footer, 0)
    if written != expected_size:
        raise PakError(
            f"size mismatch for {entry.name}: got {written}, expected {expected_size}"
        )
    return written


def unpack_archive(archive_path: Path, output_dir: Path, encoding: str = DEFAULT_ENCODING) -> None:
    if not archive_path.is_file():
        raise PakError(f"input archive not found: {archive_path}")
    entries = parse_entries(archive_path, encoding)
    output_dir.mkdir(parents=True, exist_ok=True)

    with archive_path.open("rb") as archive:
        total = len(entries)
        for index, entry in enumerate(entries, 1):
            print(f"unpacking [{index}/{total}] {entry.name}")
            out_path = safe_output_path(output_dir, entry.name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = out_path.with_name(out_path.name + ".tmp")
            try:
                with temp_path.open("wb") as output:
                    decrypt_zlib_stream(archive, entry, output)
                os.replace(temp_path, out_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    print(f"unpacked {len(entries)} file(s) to {output_dir}")


def iter_source_names(source_dir: Path, encoding: str) -> list[str]:
    names: list[str] = []
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            raise PakError(f"symbolic links are not supported: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir).as_posix().replace("/", "\\")
        encoded_name(relative, encoding)
        names.append(relative)
    names.sort(key=str.casefold)
    if not names:
        raise PakError(f"no files found to pack in {source_dir}")
    return names


def source_path_for_name(source_dir: Path, name: str) -> Path:
    path = safe_output_path(source_dir, name)
    if not path.is_file():
        raise PakError(f"missing source file: {name}")
    return path


def compress_file(source: Path, destination: Path, level: int) -> tuple[int, int]:
    compressor = zlib.compressobj(level)
    unpacked_size = 0
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        while True:
            chunk = input_file.read(CHUNK_SIZE)
            if not chunk:
                break
            unpacked_size += len(chunk)
            if unpacked_size > UINT32_MAX:
                raise PakError(f"file is too large for NEKOPACK4A: {source}")
            output_file.write(compressor.compress(chunk))
        output_file.write(compressor.flush())

    compressed_size = destination.stat().st_size
    if compressed_size > UINT32_MAX:
        raise PakError(f"compressed file is too large for NEKOPACK4A: {source}")
    return compressed_size, unpacked_size


def prepare_entry(
    source_dir: Path,
    index: int,
    name: str,
    encoding: str,
    level: int,
    temp_dir: Path,
) -> PreparedEntry:
    name_bytes = encoded_name(name, encoding)
    source_path = source_path_for_name(source_dir, name)
    temp_path = temp_dir / f"{index:08d}.z"
    compressed_size, unpacked_size = compress_file(source_path, temp_path, level)
    stored_size = compressed_size + 4
    if stored_size > UINT32_MAX:
        raise PakError(f"compressed file is too large for NEKOPACK4A: {name}")
    return PreparedEntry(name, name_bytes, temp_path, compressed_size, unpacked_size, stored_size)


def build_index(entries: Iterable[PreparedEntry]) -> bytes:
    index = bytearray()
    for entry in entries:
        key = signed_byte_sum(entry.name_bytes)
        index += write_u32(len(entry.name_bytes))
        index += entry.name_bytes
        index += write_u32(xor_u32(entry.offset, key))
        index += write_u32(xor_u32(entry.stored_size, key))
    index += write_u32(0)
    if len(index) > UINT32_MAX:
        raise PakError("archive index is too large for NEKOPACK4A")
    return bytes(index)


def write_encrypted_payload(
    output: BinaryIO, temp_path: Path, stored_size: int, unpacked_size: int
) -> None:
    compressed_size = stored_size - 4
    footer = write_u32(unpacked_size)
    prefix_size = min(0x20, stored_size)
    with temp_path.open("rb") as input_file:
        compressed_prefix_size = min(prefix_size, compressed_size)
        first = bytearray(input_file.read(compressed_prefix_size))
        footer_prefix_size = prefix_size - compressed_prefix_size
        if footer_prefix_size:
            first += footer[:footer_prefix_size]
        mask_payload_header(first, stored_size)
        output.write(first)
        shutil.copyfileobj(input_file, output, CHUNK_SIZE)
    output.write(footer[footer_prefix_size:])


def prepare_entries(
    source_dir: Path,
    names: list[str],
    encoding: str,
    level: int,
    temp_dir: Path,
    workers: int,
) -> list[PreparedEntry]:
    prepared: list[PreparedEntry | None] = [None] * len(names)
    if workers <= 1 or len(names) == 1:
        for index, name in enumerate(names):
            entry = prepare_entry(source_dir, index, name, encoding, level, temp_dir)
            prepared[index] = entry
            print(f"packing [{index + 1}/{len(names)}] {name}")
        return [entry for entry in prepared if entry is not None]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                prepare_entry, source_dir, index, name, encoding, level, temp_dir
            ): index
            for index, name in enumerate(names)
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            entry = future.result()
            prepared[index] = entry
            completed += 1
            print(f"packing [{completed}/{len(names)}] {entry.name}")

    missing = [names[index] for index, item in enumerate(prepared) if item is None]
    if missing:
        raise PakError(f"failed to prepare {len(missing)} file(s)")
    return [entry for entry in prepared if entry is not None]


def assign_offsets(entries: list[PreparedEntry]) -> list[PreparedEntry]:
    index_size = sum(4 + len(entry.name_bytes) + 8 for entry in entries) + 4
    offset = HEADER_SIZE + index_size
    if index_size > UINT32_MAX:
        raise PakError("archive index is too large for NEKOPACK4A")

    assigned: list[PreparedEntry] = []
    for entry in entries:
        if offset > UINT32_MAX:
            raise PakError("archive offset exceeds uint32 range")
        assigned.append(
            PreparedEntry(
                entry.name,
                entry.name_bytes,
                entry.temp_path,
                entry.compressed_size,
                entry.unpacked_size,
                entry.stored_size,
                offset,
            )
        )
        offset += entry.stored_size
    if offset > UINT32_MAX:
        raise PakError("archive exceeds uint32 range")
    return assigned


def pack_archive(
    source_dir: Path,
    output_path: Path,
    encoding: str = DEFAULT_ENCODING,
    level: int = DEFAULT_COMPRESSION_LEVEL,
    workers: int = DEFAULT_PACK_WORKERS,
) -> None:
    if not source_dir.is_dir():
        raise PakError(f"source folder not found: {source_dir}")
    if not 0 <= level <= 9:
        raise PakError("compression level must be between 0 and 9")
    if workers < 1:
        raise PakError("worker count must be at least 1")

    source_resolved = source_dir.resolve()
    output_resolved = output_path.resolve(strict=False)
    try:
        output_resolved.relative_to(source_resolved)
    except ValueError:
        pass
    else:
        raise PakError("output PAK must be outside the source folder")

    names = iter_source_names(source_dir, encoding)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_output = output_path.with_name(output_path.name + ".tmp")
    try:
        with tempfile.TemporaryDirectory(prefix="neko_pak_") as temp_root:
            prepared = prepare_entries(source_dir, names, encoding, level, Path(temp_root), workers)
            assigned = assign_offsets(prepared)
            index = build_index(assigned)

            with temp_output.open("wb") as output:
                output.write(MAGIC)
                output.write(write_u32(len(index)))
                output.write(index)
                for entry in assigned:
                    write_encrypted_payload(
                        output, entry.temp_path, entry.stored_size, entry.unpacked_size
                    )
        os.replace(temp_output, output_path)
    finally:
        if temp_output.exists():
            temp_output.unlink()

    print(f"packed {len(names)} file(s) to {output_path}")


def env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise PakError(f"{name} must be an integer") from exc
    if value < 1:
        raise PakError(f"{name} must be a positive integer")
    return value


def env_compression_level() -> int:
    raw = os.environ.get("NEKO_PAK_LEVEL")
    if raw is None:
        return DEFAULT_COMPRESSION_LEVEL
    try:
        value = int(raw)
    except ValueError as exc:
        raise PakError("NEKO_PAK_LEVEL must be an integer from 0 to 9") from exc
    if not 0 <= value <= 9:
        raise PakError("NEKO_PAK_LEVEL must be an integer from 0 to 9")
    return value


def usage() -> str:
    return (
        "Usage:\n"
        "  pak.py -u input.pak output_folder\n"
        "  pak.py -p input_folder output.pak\n"
        "\n"
        "Extraction supports NEKOPACK4A and NEKOPACK4S.\n"
        "Packing outputs NEKOPACK4A.\n"
        "\n"
        "Packing options:\n"
        "  NEKO_PAK_LEVEL=0..9       compression level (default 9)\n"
        "  NEKO_PAK_WORKERS=1..N     workers (default 1)\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] not in ("-u", "-p"):
        print(usage(), file=sys.stderr, end="")
        return 2

    try:
        if args[0] == "-u":
            unpack_archive(Path(args[1]), Path(args[2]), DEFAULT_ENCODING)
        else:
            pack_archive(
                Path(args[1]),
                Path(args[2]),
                DEFAULT_ENCODING,
                env_compression_level(),
                env_positive_int("NEKO_PAK_WORKERS", DEFAULT_PACK_WORKERS),
            )
    except (OSError, UnicodeError, zlib.error, PakError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
