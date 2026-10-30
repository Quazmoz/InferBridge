"""Build a Linux x86_64 application using a relocatable, private CPython runtime.

Requires uv 0.11.21 and a native x86_64 glibc Linux build host. No freezing, venv
relocation, model download, root access, or user-installed Python is involved.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.distribution import validate_version  # noqa: E402
from app.version import __version__  # noqa: E402

PYTHON = "3.12.12"
UV_VERSION = "0.11.21"


def run(*command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, timeout=1200, **kwargs)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def managed_installation(directory: Path) -> Path:
    # uv also creates a minor-version symlink; move the concrete runtime only.
    installations = [
        path
        for path in directory.glob(f"cpython-{PYTHON}-*")
        if path.is_dir() and not path.is_symlink()
    ]
    if len(installations) != 1:
        raise RuntimeError("Expected exactly one managed CPython installation.")
    return installations[0]


def archive_bundle(bundle: Path, destination: Path, *, epoch: int) -> None:
    """Stable order, ownership and timestamps; retain private-runtime symlinks."""

    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mtime = epoch
        info.mode = 0o755 if info.isdir() or info.mode & 0o111 else 0o644
        return info

    with destination.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                archive.add(bundle, arcname=bundle.name, filter=normalize)


def build(output: Path) -> Path:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError(
            "Build on native Linux x86_64; macOS/ARM artifacts cannot qualify Linux."
        )
    if run("uv", "--version", capture_output=True, text=True).stdout.split()[1] != UV_VERSION:
        raise RuntimeError(f"Install the build tool uv=={UV_VERSION} first.")
    version = validate_version(__version__)
    if run("git", "status", "--porcelain", cwd=ROOT, capture_output=True, text=True).stdout.strip():
        raise RuntimeError("Commit qualified development changes before building a distribution.")
    commit = run(
        "git", "rev-parse", "HEAD", cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    epoch = int(
        run(
            "git", "show", "-s", "--format=%ct", "HEAD", cwd=ROOT, capture_output=True, text=True
        ).stdout
    )
    output.mkdir(parents=True, exist_ok=True)
    stage = ROOT / "build" / "linux"
    if stage.exists():
        shutil.rmtree(stage)
    bundle = stage / f"InferBridge-{version}-linux-x86_64"
    resources = bundle / "resources"
    resources.mkdir(parents=True)
    managed = stage / "managed-python"
    # uv verifies the managed CPython download and installs only inside this build.
    run("uv", "python", "install", "--no-config", "--no-bin", "--install-dir", str(managed), PYTHON)
    shutil.move(str(managed_installation(managed)), bundle / "python")
    python = bundle / "python" / "bin" / "python3.12"
    lock = ROOT / "requirements" / "linux.lock"
    run(
        "uv",
        "pip",
        "sync",
        "--no-config",
        "--python",
        str(python),
        "--system",
        # This is the copied interpreter inside build/linux, never the host Python.
        "--break-system-packages",
        "--require-hashes",
        "--only-binary",
        ":all:",
        "--link-mode",
        "copy",
        "--torch-backend",
        "cpu",
        str(lock),
    )
    run("uv", "pip", "check", "--python", str(python))
    notices = run(
        str(python),
        "-I",
        "-B",
        "-c",
        "import importlib.metadata as m; print('Bundled wheel licenses are retained under python/lib/python3.12/site-packages/*.dist-info. CPython and its bundled libraries retain their accompanying licenses.\\n'); print('\\n'.join(sorted(d.metadata['Name'] + '==' + d.version + ': ' + (d.metadata.get('License-Expression') or d.metadata.get('License') or 'See bundled license').splitlines()[0] for d in m.distributions())))",
        capture_output=True,
        text=True,
    ).stdout
    (resources / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")
    inventory = run(
        "uv", "pip", "freeze", "--python", str(python), capture_output=True, text=True
    ).stdout
    (resources / "dependencies.txt").write_text(inventory, encoding="utf-8")
    shutil.copy2(lock, resources / "requirements-linux.lock")
    # Copy only tracked application resources: ignored .env files and local caches
    # must never enter an artifact just because they happen to exist on a builder.
    tracked = run(
        "git",
        "ls-files",
        "-z",
        "--",
        "app",
        "runtime",
        "web",
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    for name in tracked.split("\0"):
        if name:
            target = resources / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
    for name in ("models.json", "model_library_manifest.json", "LICENSE"):
        shutil.copy2(ROOT / name, resources / name)
    shutil.copy2(ROOT / "docs" / "LINUX.md", bundle / "README.md")
    shutil.copy2(ROOT / "packaging" / "linux" / "linux_desktop.py", resources / "linux_desktop.py")
    shutil.copy2(ROOT / "packaging" / "linux" / "InferBridge", bundle / "InferBridge")
    (bundle / "InferBridge").chmod(0o755)
    # Wheel console scripts contain absolute build-time shebangs. Only the converter
    # command is needed; replace it with a sibling-interpreter wrapper after relocation.
    for script in python.parent.iterdir():
        if not script.name.startswith("python"):
            if script.is_dir():
                shutil.rmtree(script)
            else:
                script.unlink()
    optimum = python.parent / "optimum-cli"
    optimum.write_text(
        '#!/bin/sh\nexec "$(dirname -- "$(readlink -f -- "$0")")/python3.12" '
        "-P -s -B -c 'from optimum.commands.optimum_cli import main; main()' \"$@\"\n",
        encoding="utf-8",
    )
    optimum.chmod(0o755)
    # A cold application install must not contain build-machine bytecode paths/caches.
    for cache in bundle.rglob("__pycache__"):
        shutil.rmtree(cache)
    environment = {
        **os.environ,
        "PYTHONPATH": str(resources),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    run(
        str(python),
        "-P",
        "-s",
        "-B",
        "-c",
        "from app.tray_support import tray_icon; from app.tray_state import TrayPhase; import sys; tray_icon(TrayPhase.READY).save(sys.argv[1])",
        str(resources / "InferBridge.png"),
        env=environment,
    )
    metadata = {
        "application": "InferBridge",
        "version": version,
        "commit": commit,
        "platform": "linux",
        "architecture": "x86_64",
        "python": PYTHON,
        "uv": UV_VERSION,
        "requirements_sha256": digest(lock),
        "build_os": platform.freedesktop_os_release(),
        "real_inference_verified": False,
    }
    (resources / "linux-build.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    build_info = {
        "application_version": version,
        "source_commit": commit,
        "build_channel": "development",
        "build_date": datetime.fromtimestamp(epoch, UTC).isoformat(),
        "source_tree_clean": True,
        "dependency_inventory_filename": "dependencies.txt",
        "dependency_inventory_sha256": digest(resources / "dependencies.txt"),
    }
    (resources / "build-info.json").write_text(
        json.dumps(build_info, indent=2) + "\n", encoding="utf-8"
    )
    for link in bundle.rglob("*"):
        if link.is_symlink() and not link.resolve().is_relative_to(bundle.resolve()):
            raise RuntimeError("The bundle contains a symlink outside its private runtime.")
    archive = output / f"{bundle.name}-portable.tar.gz"
    archive_bundle(bundle, archive, epoch=epoch)
    (output / f"InferBridge-{version}-linux-SHA256SUMS.txt").write_text(
        f"{digest(archive)}  {archive.name}\n", encoding="utf-8"
    )
    print(archive)
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "linux")
    args = parser.parse_args()
    build(args.output.resolve())
