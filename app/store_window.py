"""Launch the bundled WinUI frontend from the authoritative desktop engine.

This is a native Windows-only dispatch; a missing WinUI executable is an error,
never permission to open a browser or revive the retired WebView2 shell.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("ov-llm.native.window")


def _validate_port(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Native window port must be an integer between 1 and 65535.")


def _store_runtime_available() -> bool:
    """Native Windows launch applies to installed, portable and Store editions."""
    return sys.platform == "win32"


def _native_client() -> Path | None:
    candidate = Path(sys.executable).resolve().parent / "native" / "InferBridge.Native.exe"
    return candidate if candidate.is_file() else None


def open_store_window(port: int, instance_nonce: str) -> bool:
    """Activate WinUI; it re-verifies session metadata and owns its single window."""
    _validate_port(port)
    if not isinstance(instance_nonce, str) or not 1 <= len(instance_nonce) <= 512:
        return False
    if not _store_runtime_available():
        return False
    native = _native_client()
    if native is None:
        logger.error("The packaged WinUI frontend is missing; refusing browser fallback")
        return False
    try:
        subprocess.Popen(
            [str(native)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(native.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError:
        logger.exception("Failed to activate the bundled WinUI frontend")
        return False
