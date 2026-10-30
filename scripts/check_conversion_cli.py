"""Verify the pinned Optimum/OpenVINO export CLI without downloading a model.

Run in the exact release environment before freezing it with PyInstaller.
This catches missing Optimum registrations and removed argument flags that
pure-Python/mock endpoint tests do not exercise.
"""

from __future__ import annotations

import sysconfig
import subprocess
import sys
from pathlib import Path

# Script entrypoints invoked as python scripts/... by the release builder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.model_converter import build_export_command  # noqa: E402


def required_flags() -> set[str]:
    """Stay synchronized with InferBridge's actual conversion argument builder."""

    command = build_export_command(
        "inferbridge-validation/no-download",
        "unused-output",
        "int4",
        task="text-generation",
        trust_remote_code=True,
        group_size=128,
        ratio=0.8,
        sym=True,
    )
    return {argument for argument in command if argument.startswith("--")}


def validate_help(output: str) -> None:
    """Fail closed if the installed CLI cannot accept a supported export option."""

    if "usage:" not in output.lower() or "openvino" not in output.lower():
        raise RuntimeError("The installed Optimum CLI did not describe OpenVINO export.")
    missing = sorted(flag for flag in required_flags() if flag not in output)
    if missing:
        raise RuntimeError("Optimum export CLI is missing arguments: " + ", ".join(missing))


def main() -> int:
    # Use the console entrypoint from this exact interpreter's scripts folder.
    # Release scripts invoke the venv Python directly without activating its PATH.
    executable = Path(sysconfig.get_path("scripts")) / (
        "optimum-cli.exe" if sys.platform == "win32" else "optimum-cli"
    )
    if not executable.is_file():
        print("optimum-cli is missing from this Python environment.", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            [executable, "export", "openvino", "--help"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )
        output = result.stdout + "\n" + result.stderr
        if result.returncode:
            detail = " ".join(output.split())[-1200:]
            raise RuntimeError(f"Optimum export CLI exited {result.returncode}; {detail}")
        validate_help(output)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"OpenVINO conversion CLI preflight failed: {exc}", file=sys.stderr)
        return 1
    print("OpenVINO conversion CLI and supported flags verified without model download.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
