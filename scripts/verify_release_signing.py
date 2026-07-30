"""Independently gate release signature claims before publication."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any


class SigningVerificationError(RuntimeError):
    """A release signature claim is inconsistent or could not be verified."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SigningVerificationError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise SigningVerificationError(f"{path.name} must contain a JSON object.")
    return value


def _artifact(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise SigningVerificationError("Release manifest artifacts must be a list.")
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("type") == kind]
    if len(matches) != 1:
        raise SigningVerificationError(f"Release manifest must contain one {kind} artifact.")
    return matches[0]


def _truth(value: Any) -> bool:
    return value is True


def _signtool() -> str:
    configured = os.environ.get("OV_LLM_SIGNTOOL_PATH", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path.resolve())
        raise SigningVerificationError("OV_LLM_SIGNTOOL_PATH does not identify a file.")
    found = shutil.which("signtool.exe")
    if found:
        return found
    raise SigningVerificationError(
        "Signed claims require signtool.exe or OV_LLM_SIGNTOOL_PATH for publisher verification."
    )


def _verify(tool: str, path: Path) -> None:
    if not path.is_file():
        raise SigningVerificationError(f"Signed artifact is missing: {path.name}")
    result = subprocess.run(
        [tool, "verify", "/pa", "/all", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode:
        raise SigningVerificationError(
            f"signtool verify /pa /all failed for {path.name} with exit code {result.returncode}."
        )


def verify_release_signing(
    artifact_directory: Path,
    version: str,
    *,
    require_signed: bool = False,
) -> bool:
    manifest = _load_object(artifact_directory / f"InferBridge-{version}-release-manifest.json")
    summary = _load_object(artifact_directory / f"InferBridge-{version}-release-summary.json")
    installer = _artifact(manifest, "installer")
    portable = _artifact(manifest, "portable")

    installer_claim = _truth(installer.get("signed")) and _truth(
        installer.get("signature_verified")
    )
    launcher_claim = _truth(portable.get("contained_launcher_signed")) and _truth(
        portable.get("contained_launcher_signature_verified")
    )
    claim_fields = (
        installer.get("signed"),
        installer.get("signature_verified"),
        portable.get("contained_launcher_signed"),
        portable.get("contained_launcher_signature_verified"),
    )
    if any(_truth(value) for value in claim_fields) and not (installer_claim and launcher_claim):
        raise SigningVerificationError(
            "A signed release must claim verified signatures for both installer and launcher."
        )
    if _truth(summary.get("installer_signature_verified")) != installer_claim:
        raise SigningVerificationError("Installer signature summary disagrees with the manifest.")
    if _truth(summary.get("launcher_signature_verified")) != launcher_claim:
        raise SigningVerificationError("Launcher signature summary disagrees with the manifest.")

    if not installer_claim:
        if require_signed:
            raise SigningVerificationError(
                "This release channel requires verified Authenticode signatures for both "
                "installer and launcher. Rebuild with -Sign before publishing."
            )
        print("Release metadata makes no signed claim; SignTool verification is not applicable.")
        return False

    tool = _signtool()
    installer_path = artifact_directory / str(installer.get("filename") or "")
    portable_path = artifact_directory / str(portable.get("filename") or "")
    _verify(tool, installer_path)
    if not portable_path.is_file():
        raise SigningVerificationError(f"Portable artifact is missing: {portable_path.name}")
    with tempfile.TemporaryDirectory(prefix="ovllm-signature-verify-") as temporary:
        with zipfile.ZipFile(portable_path) as archive:
            launchers = [
                name
                for name in archive.namelist()
                if Path(name.replace("\\", "/")).name.lower() == "inferbridge.exe"
            ]
            if len(launchers) != 1:
                raise SigningVerificationError(
                    "Portable ZIP must contain exactly one InferBridge.exe."
                )
            extracted = Path(temporary) / "InferBridge.exe"
            extracted.write_bytes(archive.read(launchers[0]))
        _verify(tool, extracted)
    print("Publisher independently verified installer and launcher Authenticode signatures.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--require-signed",
        action="store_true",
        help=(
            "Reject release metadata that does not claim verified installer and launcher signatures."
        ),
    )
    args = parser.parse_args(argv)
    try:
        verify_release_signing(
            args.artifact_directory.resolve(),
            args.version,
            require_signed=args.require_signed,
        )
    except (SigningVerificationError, zipfile.BadZipFile) as exc:
        print(f"Signature verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
