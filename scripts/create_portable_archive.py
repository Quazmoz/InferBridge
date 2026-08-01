"""Create and structurally verify InferBridge portable ZIP archives without staging copies."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path, PurePosixPath

_ARCHIVE_ROOT_RE = re.compile(r"^InferBridge-[0-9A-Za-z.+-]+$")


def _validate_archive_root(value: str) -> str:
    if not _ARCHIVE_ROOT_RE.fullmatch(value):
        raise ValueError("archive root must use the form InferBridge-<version>")
    return value


def _source_files(source_root: Path, archive_root: str) -> dict[str, int]:
    files: dict[str, int] = {}
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_symlink():
            raise RuntimeError(f"symlink is not allowed in portable output: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source_root).as_posix()
        files[f"{archive_root}/{relative}"] = path.stat().st_size
    return files


def create_archive(source_root: Path, output: Path, archive_root: str) -> None:
    """Archive source_root under archive_root without copying or extracting it."""

    archive_root = _validate_archive_root(archive_root)
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"portable source directory is missing: {source_root}")
    required = (source_root / "InferBridge.exe", source_root / "portable.flag")
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"portable source is missing required file: {path.name}")

    files = _source_files(source_root, archive_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for archive_name in files:
            relative = archive_name.removeprefix(f"{archive_root}/")
            archive.write(source_root / Path(relative), archive_name)


def verify_archive(path: Path, archive_root: str, source_root: Path | None = None) -> None:
    """Verify root layout and source-file parity without extracting the archive."""

    archive_root = _validate_archive_root(archive_root)
    prefix = f"{archive_root}/"
    required = {f"{prefix}InferBridge.exe", f"{prefix}portable.flag"}
    seen: dict[str, int] = {}
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            normalized = item.filename.replace("\\", "/")
            pure = PurePosixPath(normalized)
            if pure.is_absolute() or ".." in pure.parts or not normalized.startswith(prefix):
                raise RuntimeError(f"unexpected portable archive path: {item.filename}")
            if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise RuntimeError(f"symlink is not allowed in portable archive: {item.filename}")
            if item.is_dir():
                continue
            if normalized in seen:
                raise RuntimeError(f"duplicate portable archive path: {item.filename}")
            seen[normalized] = item.file_size

    missing = sorted(required - set(seen))
    if missing:
        raise RuntimeError("portable archive is missing required files: " + ", ".join(missing))

    if source_root is not None:
        source_root = source_root.resolve()
        expected = _source_files(source_root, archive_root)
        missing_files = sorted(set(expected) - set(seen))
        extra_files = sorted(set(seen) - set(expected))
        if missing_files:
            raise RuntimeError(f"portable archive omitted source file: {missing_files[0]}")
        if extra_files:
            raise RuntimeError(f"portable archive contains unexpected file: {extra_files[0]}")
        for name, size in expected.items():
            if seen[name] != size:
                raise RuntimeError(f"portable archive size mismatch: {name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--archive-root", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--path", type=Path, required=True)
    verify.add_argument("--archive-root", required=True)
    verify.add_argument("--source-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        create_archive(args.source_root, args.output, args.archive_root)
    else:
        verify_archive(args.path, args.archive_root, args.source_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
