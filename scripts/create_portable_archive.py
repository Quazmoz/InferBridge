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

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if path.is_symlink():
                raise RuntimeError(f"symlink is not allowed in portable output: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(source_root).as_posix()
            archive.write(path, f"{archive_root}/{relative}")


def verify_archive(path: Path, archive_root: str) -> None:
    """Verify root layout and required files without extracting the archive."""

    archive_root = _validate_archive_root(archive_root)
    prefix = f"{archive_root}/"
    required = {f"{prefix}InferBridge.exe", f"{prefix}portable.flag"}
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            normalized = item.filename.replace("\\", "/")
            pure = PurePosixPath(normalized)
            if pure.is_absolute() or ".." in pure.parts or not normalized.startswith(prefix):
                raise RuntimeError(f"unexpected portable archive path: {item.filename}")
            if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise RuntimeError(f"symlink is not allowed in portable archive: {item.filename}")
            if not item.is_dir():
                seen.add(normalized)
    missing = sorted(required - seen)
    if missing:
        raise RuntimeError("portable archive is missing required files: " + ", ".join(missing))


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        create_archive(args.source_root, args.output, args.archive_root)
    else:
        verify_archive(args.path, args.archive_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
