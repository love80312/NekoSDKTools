#!/usr/bin/env python3
"""Convert NekoSDK/Waffle BMP+ALP pairs to/from ordinary RGBA images.

Extract:
  py -3 bmp_alp.py -e image.bmp image.alp image.png
  py -3 bmp_alp.py -e extracted_folder png_folder

Create:
  py -3 bmp_alp.py -c image.png image.bmp image.alp
  py -3 bmp_alp.py -c png_folder bmp_alp_folder

ALP stores one 8-bit alpha value per pixel. Extraction accepts both tightly
packed rows and rows padded to a 4-byte boundary. Creation writes tightly
packed rows, matching the compact WaffleBmp layout.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from PIL import Image

IMAGE_EXTENSIONS = {
    ".png", ".tga", ".bmp", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"
}


class BmpAlpError(Exception):
    """Raised when a BMP/ALP pair or image cannot be converted."""


def path_key(path: Path) -> str:
    """Return a case-insensitive resolved path key for collision checks."""
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def ensure_distinct_paths(paths: list[tuple[Path, str]]) -> None:
    """Reject output paths that would overwrite an input or each other."""
    seen: dict[str, tuple[Path, str]] = {}
    for path, label in paths:
        key = path_key(path)
        previous = seen.get(key)
        if previous is not None:
            previous_path, previous_label = previous
            raise BmpAlpError(
                f"{label} path conflicts with {previous_label}: "
                f"{path} / {previous_path}"
            )
        seen[key] = (path, label)


def make_temp_path(destination: Path) -> Path:
    """Create a unique temporary file beside its final destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=destination.suffix + ".tmp",
        dir=destination.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    return temporary


def validate_target_paths(targets: list[Path]) -> None:
    """Reject duplicate targets and file/directory ancestor conflicts."""
    seen: dict[str, Path] = {}
    resolved_targets: list[Path] = []

    for target in targets:
        resolved = target.resolve(strict=False)
        key = path_key(resolved)
        previous = seen.get(key)
        if previous is not None:
            raise BmpAlpError(
                f"multiple outputs map to the same path: {previous} / {target}"
            )
        if resolved.exists() and resolved.is_dir():
            raise BmpAlpError(f"output target is a directory: {resolved}")

        seen[key] = resolved
        resolved_targets.append(resolved)

    for target in resolved_targets:
        parent = target.parent
        while parent != parent.parent:
            if path_key(parent) in seen:
                raise BmpAlpError(f"output file/directory path conflict: {target}")
            if parent.exists() and not parent.is_dir():
                raise BmpAlpError(f"output parent is not a directory: {parent}")
            parent = parent.parent


def preflight_batch_targets(
    output_folder: Path,
    relative_targets: list[Path],
) -> list[Path]:
    """Resolve and validate all planned outputs before batch conversion."""
    root = output_folder.resolve(strict=False)
    if root.exists() and not root.is_dir():
        raise BmpAlpError(f"output path is not a folder: {output_folder}")

    targets = [
        (root / relative).resolve(strict=False)
        for relative in relative_targets
    ]
    for target in targets:
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise BmpAlpError(
                f"output target escapes output folder: {target}"
            ) from exc

    validate_target_paths(targets)
    return targets


def commit_staged_files(staged_pairs: list[tuple[Path, Path]]) -> None:
    """Commit staged files and restore replaced files if a commit fails."""
    if not staged_pairs:
        return

    targets = [target.resolve(strict=False) for _staged, target in staged_pairs]
    validate_target_paths(targets)

    # Keep backups on the same volume as the outputs so os.replace remains
    # atomic and does not fail with a cross-device error on Windows.
    common_parent = Path(os.path.commonpath([str(target.parent) for target in targets]))
    common_parent.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(tempfile.mkdtemp(
        prefix=".neko_bmp_backup_", dir=common_parent
    ))

    backups: list[tuple[Path, Path]] = []
    committed: list[Path] = []

    try:
        for index, target in enumerate(targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = backup_dir / f"{index:08d}.bak"
                os.replace(target, backup)
                backups.append((target, backup))

        for (staged, _original_target), target in zip(staged_pairs, targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            committed.append(target)

    except Exception as exc:
        rollback_errors: list[str] = []

        for target in reversed(committed):
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {target}: {rollback_exc}")

        for target, backup in reversed(backups):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    os.replace(backup, target)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {target}: {rollback_exc}")

        if rollback_errors:
            raise BmpAlpError(
                "output commit failed and rollback was incomplete; "
                f"recoverable backups remain at {backup_dir}: "
                + "; ".join(rollback_errors)
            ) from exc

        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    else:
        shutil.rmtree(backup_dir, ignore_errors=True)

def ensure_output_outside_input(output_folder: Path, input_folder: Path) -> None:
    output_resolved = output_folder.resolve(strict=False)
    input_resolved = input_folder.resolve(strict=False)
    try:
        output_resolved.relative_to(input_resolved)
    except ValueError:
        return
    raise BmpAlpError("output folder must be outside the input folder")


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
    temporary = make_temp_path(path)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

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
        raise BmpAlpError(
            f"unsupported output image extension: {suffix or '(none)'}"
        )

    temporary = make_temp_path(output_path)
    try:
        save_source = image
        if image_format == "JPEG":
            save_source = image.convert("RGB")
        save_source.save(temporary, format=image_format)
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

def combine_bmp_alp(bmp_path: Path, alp_path: Path, output_path: Path) -> None:
    ensure_distinct_paths([
        (bmp_path, "source BMP"),
        (alp_path, "source ALP"),
        (output_path, "output image"),
    ])
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
    ensure_distinct_paths([
        (image_path, "source image"),
        (bmp_path, "output BMP"),
        (alp_path, "output ALP"),
    ])
    validate_target_paths([bmp_path, alp_path])
    if not image_path.is_file():
        raise BmpAlpError(f"image file not found: {image_path}")

    try:
        with Image.open(image_path) as source:
            rgba = source.convert("RGBA")
    except Exception as exc:
        raise BmpAlpError(f"cannot open image {image_path}: {exc}") from exc

    staged_bmp = make_temp_path(bmp_path)
    staged_alp = make_temp_path(alp_path)
    try:
        rgba.convert("RGB").save(staged_bmp, format="BMP")
        staged_alp.write_bytes(rgba.getchannel("A").tobytes())
        commit_staged_files([
            (staged_bmp, bmp_path),
            (staged_alp, alp_path),
        ])
    finally:
        for staged in (staged_bmp, staged_alp):
            if staged.exists():
                staged.unlink()

    print(f"created: {image_path} -> {bmp_path} + {alp_path}")

def iter_files(root: Path, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BmpAlpError(f"symbolic links are not supported: {path}")
        if path.is_file() and path.suffix.lower() in extensions:
            files.append(path)
    return sorted(files, key=lambda path: path.as_posix().casefold())

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
    ensure_output_outside_input(output_folder, input_folder)

    bmp_files = iter_files(input_folder, {".bmp"})
    if not bmp_files:
        raise BmpAlpError(f"no BMP files found: {input_folder}")

    seen_bmp: set[str] = set()
    for bmp_path in bmp_files:
        key = bmp_path.relative_to(input_folder).with_suffix("").as_posix().casefold()
        if key in seen_bmp:
            raise BmpAlpError(f"duplicate case-insensitive BMP name: {key}")
        seen_bmp.add(key)

    alp_map = build_case_insensitive_file_map(input_folder, ".alp")
    matched: list[tuple[Path, Path, Path]] = []
    missing: list[Path] = []

    for bmp_path in bmp_files:
        relative = bmp_path.relative_to(input_folder)
        key = relative.with_suffix("").as_posix().casefold()
        alp_path = alp_map.get(key)
        if alp_path is None:
            missing.append(bmp_path)
            print(
                f"skip (matching ALP not found): {bmp_path}",
                file=sys.stderr,
            )
            continue
        matched.append((bmp_path, alp_path, relative.with_suffix(".png")))

    if not matched:
        raise BmpAlpError("no BMP/ALP pairs could be extracted")

    output_root = output_folder.resolve(strict=False)
    relative_targets = [relative for _bmp, _alp, relative in matched]
    preflight_batch_targets(output_root, relative_targets)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".neko_bmp_stage_", dir=output_root.parent
    ) as stage_name:
        stage_root = Path(stage_name)
        staged_pairs: list[tuple[Path, Path]] = []
        failed: list[tuple[Path, str]] = []

        for bmp_path, alp_path, relative in matched:
            staged = stage_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            try:
                combine_bmp_alp(bmp_path, alp_path, staged)
                staged_pairs.append((staged, output_root / relative))
            except (OSError, BmpAlpError, ValueError) as exc:
                failed.append((bmp_path, str(exc)))
                print(f"error: {bmp_path}: {exc}", file=sys.stderr)

        if failed:
            raise BmpAlpError(
                f"{len(failed)} BMP/ALP conversion(s) failed; "
                "no batch outputs were committed"
            )

        commit_staged_files(staged_pairs)

    print(
        f"extract summary: {len(matched)} converted, "
        f"{len(missing)} missing ALP, 0 failed"
    )

def create_folder(image_folder: Path, output_folder: Path) -> None:
    if not image_folder.is_dir():
        raise BmpAlpError(f"input is not a folder: {image_folder}")
    ensure_output_outside_input(output_folder, image_folder)

    image_files = iter_files(image_folder, IMAGE_EXTENSIONS)
    if not image_files:
        raise BmpAlpError(f"no supported image files found: {image_folder}")

    seen: set[str] = set()
    relative_pairs: list[tuple[Path, Path, Path]] = []
    relative_targets: list[Path] = []

    for image_path in image_files:
        relative = image_path.relative_to(image_folder)
        key = relative.with_suffix("").as_posix().casefold()
        if key in seen:
            raise BmpAlpError(
                f"multiple input images map to the same output name: {key}"
            )
        seen.add(key)

        bmp_relative = relative.with_suffix(".bmp")
        alp_relative = relative.with_suffix(".alp")
        relative_pairs.append((image_path, bmp_relative, alp_relative))
        relative_targets.extend((bmp_relative, alp_relative))

    output_root = output_folder.resolve(strict=False)
    preflight_batch_targets(output_root, relative_targets)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".neko_bmp_stage_", dir=output_root.parent
    ) as stage_name:
        stage_root = Path(stage_name)
        staged_pairs: list[tuple[Path, Path]] = []

        for image_path, bmp_relative, alp_relative in relative_pairs:
            staged_bmp = stage_root / bmp_relative
            staged_alp = stage_root / alp_relative
            split_image(image_path, staged_bmp, staged_alp)
            staged_pairs.append((staged_bmp, output_root / bmp_relative))
            staged_pairs.append((staged_alp, output_root / alp_relative))

        commit_staged_files(staged_pairs)

    print(f"create summary: {len(image_files)} image(s) converted")

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
