#!/usr/bin/env python3
"""Convert NekoSDK/Waffle BMP+ALP pairs to/from ordinary RGBA images.

Extract:
  py -3.13 bmp_alp.py -e image.bmp image.alp image.png
  py -3.13 bmp_alp.py -e extracted_folder png_folder

Create:
  py -3.13 bmp_alp.py -c image.png image.bmp image.alp
  py -3.13 bmp_alp.py -c png_folder bmp_alp_folder

ALP stores one 8-bit alpha value per pixel. Extraction accepts both tightly
packed rows and rows padded to a 4-byte boundary. Creation writes tightly
packed rows, matching the compact WaffleBmp layout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from PIL import Image

IMAGE_EXTENSIONS = {
    ".png", ".tga", ".bmp", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"
}


class BmpAlpError(Exception):
    """Raised when a BMP/ALP pair or image cannot be converted."""


def alpha_stride(width: int, height: int, alpha_size: int) -> int:
    if width <= 0 or height <= 0:
        raise BmpAlpError(f"invalid image size: {width}x{height}")
    compact_stride = width
    padded_stride = (width + 3) & ~3
    compact_size = compact_stride * height
    padded_size = padded_stride * height

    if alpha_size == compact_size:
        return compact_stride
    if alpha_size == padded_size:
        return padded_stride
    raise BmpAlpError(
        f"ALP size {alpha_size} does not match {width}x{height}; "
        f"expected {compact_size} (compact) or {padded_size} (4-byte row padding)"
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def save_image(image: Image.Image, output_path: Path) -> None:
    suffix = output_path.suffix.lower()
    formats = {
        ".png": "PNG",
        ".tga": "TGA",
        ".bmp": "BMP",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".webp": "WEBP",
        ".tif": "TIFF",
        ".tiff": "TIFF",
    }
    image_format = formats.get(suffix)
    if image_format is None:
        raise BmpAlpError(f"unsupported output image extension: {suffix or '(none)'}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.stem + ".tmp" + output_path.suffix)
    try:
        save_source = image
        if image_format == "JPEG":
            # JPEG has no alpha channel; retain RGB content and discard alpha.
            save_source = image.convert("RGB")
        save_source.save(temp_path, format=image_format)
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def combine_bmp_alp(bmp_path: Path, alp_path: Path, output_path: Path) -> None:
    if not bmp_path.is_file():
        raise BmpAlpError(f"BMP file not found: {bmp_path}")
    if not alp_path.is_file():
        raise BmpAlpError(f"ALP file not found: {alp_path}")

    try:
        with Image.open(bmp_path) as bmp_image:
            rgba = bmp_image.convert("RGBA")
    except Exception as exc:
        raise BmpAlpError(f"cannot open BMP image {bmp_path}: {exc}") from exc

    width, height = rgba.size
    alpha = alp_path.read_bytes()
    stride = alpha_stride(width, height, len(alpha))

    pixels = bytearray(rgba.tobytes())
    for y in range(height):
        alpha_row = y * stride
        pixel_row = y * width * 4
        for x in range(width):
            pixels[pixel_row + x * 4 + 3] = alpha[alpha_row + x]

    output = Image.frombytes("RGBA", (width, height), bytes(pixels))
    save_image(output, output_path)
    print(f"extracted: {bmp_path} + {alp_path} -> {output_path}")


def split_image(image_path: Path, bmp_path: Path, alp_path: Path) -> None:
    if not image_path.is_file():
        raise BmpAlpError(f"image file not found: {image_path}")

    try:
        with Image.open(image_path) as source:
            rgba = source.convert("RGBA")
    except Exception as exc:
        raise BmpAlpError(f"cannot open image {image_path}: {exc}") from exc

    bmp_path.parent.mkdir(parents=True, exist_ok=True)
    rgba.convert("RGB").save(bmp_path, format="BMP")
    atomic_write_bytes(alp_path, rgba.getchannel("A").tobytes())
    print(f"created: {image_path} -> {bmp_path} + {alp_path}")


def iter_files(root: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions),
        key=lambda path: path.as_posix().casefold(),
    )


def build_case_insensitive_file_map(root: Path, extension: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in iter_files(root, {extension.lower()}):
        relative_key = path.relative_to(root).with_suffix("").as_posix().casefold()
        if relative_key in result:
            raise BmpAlpError(f"duplicate case-insensitive file name: {relative_key}")
        result[relative_key] = path
    return result


def extract_folder(input_folder: Path, output_folder: Path) -> None:
    if not input_folder.is_dir():
        raise BmpAlpError(f"input is not a folder: {input_folder}")

    bmp_files = iter_files(input_folder, {".bmp"})
    if not bmp_files:
        raise BmpAlpError(f"no BMP files found: {input_folder}")

    alp_map = build_case_insensitive_file_map(input_folder, ".alp")
    extracted = 0
    missing: list[Path] = []
    failed: list[tuple[Path, str]] = []

    for bmp_path in bmp_files:
        relative = bmp_path.relative_to(input_folder)
        key = relative.with_suffix("").as_posix().casefold()
        alp_path = alp_map.get(key)
        if alp_path is None:
            missing.append(bmp_path)
            print(f"skip (matching ALP not found): {bmp_path}", file=sys.stderr)
            continue

        output_path = (output_folder / relative).with_suffix(".png")
        try:
            combine_bmp_alp(bmp_path, alp_path, output_path)
            extracted += 1
        except (OSError, BmpAlpError, ValueError) as exc:
            failed.append((bmp_path, str(exc)))
            print(f"error: {bmp_path}: {exc}", file=sys.stderr)

    print(
        f"extract summary: {extracted} converted, {len(missing)} missing ALP, "
        f"{len(failed)} failed"
    )
    if extracted == 0:
        raise BmpAlpError("no BMP/ALP pairs could be extracted")


def create_folder(image_folder: Path, output_folder: Path) -> None:
    if not image_folder.is_dir():
        raise BmpAlpError(f"input is not a folder: {image_folder}")

    image_files = iter_files(image_folder, IMAGE_EXTENSIONS)
    if not image_files:
        raise BmpAlpError(f"no supported image files found: {image_folder}")

    # Different source formats with the same relative stem would overwrite
    # the same BMP/ALP output pair. Stop rather than silently lose an image.
    seen: set[str] = set()
    for image_path in image_files:
        relative = image_path.relative_to(image_folder).with_suffix("").as_posix().casefold()
        if relative in seen:
            raise BmpAlpError(f"multiple input images map to the same output name: {relative}")
        seen.add(relative)

    created = 0
    for image_path in image_files:
        relative = image_path.relative_to(image_folder)
        split_image(
            image_path,
            (output_folder / relative).with_suffix(".bmp"),
            (output_folder / relative).with_suffix(".alp"),
        )
        created += 1
    print(f"create summary: {created} image(s) converted")


def usage() -> str:
    return (
        "Usage:\n"
        "  bmp_alp.py -e in.bmp in.alp out.png\n"
        "  bmp_alp.py -e bmp_alp_folder out_folder\n"
        "  bmp_alp.py -c in.png out.bmp out.alp\n"
        "  bmp_alp.py -c image_folder out_folder\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 4 and args[0] == "-e":
            combine_bmp_alp(Path(args[1]), Path(args[2]), Path(args[3]))
        elif len(args) == 3 and args[0] == "-e":
            extract_folder(Path(args[1]), Path(args[2]))
        elif len(args) == 4 and args[0] == "-c":
            split_image(Path(args[1]), Path(args[2]), Path(args[3]))
        elif len(args) == 3 and args[0] == "-c":
            create_folder(Path(args[1]), Path(args[2]))
        else:
            print(usage(), file=sys.stderr, end="")
            return 2
    except (OSError, BmpAlpError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
