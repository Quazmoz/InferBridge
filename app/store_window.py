"""A dedicated WebView2 window for the Microsoft Store edition.

The server and tray remain authoritative. This process owns only the UI window.
Browser-based standalone and portable distributions are intentionally unchanged.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

from app.desktop_launcher import InstanceMetadata, verify_instance
from app.msix import is_packaged
from app.paths import resolve_runtime_paths

logger = logging.getLogger("ov-llm.store.window")


def _validate_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Store window port must be an integer between 1 and 65535.")


def open_store_window(port: int, instance_nonce: str) -> bool:
    """Open the packaged UI in its own process, never a system-browser tab."""
    _validate_port(port)
    if not isinstance(instance_nonce, str) or not instance_nonce:
        return False
    if sys.platform != "win32" or not is_packaged():
        return False
    # Reuse the bundled launcher; this avoids injecting a URL or command into a
    # shell, and the window child can only navigate to our loopback origin.
    try:
        subprocess.Popen(
            [
                sys.executable,
                "--window-child",
                "--port",
                str(port),
                "--instance-nonce",
                instance_nonce,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(sys.executable).resolve().parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError:
        logger.exception("Failed to start the packaged application window")
        return False


def run_store_window(port: int, instance_nonce: str) -> int:
    """Run WebView2 on this child process's main STA/GUI thread."""
    _validate_port(port)
    if sys.platform != "win32" or not is_packaged():
        raise RuntimeError("The Store window is available only to an installed MSIX package.")
    if not isinstance(instance_nonce, str) or not instance_nonce:
        raise RuntimeError("The Store window requires a valid server instance nonce.")
    instance = InstanceMetadata(
        pid=0,
        port=port,
        nonce=instance_nonce,
        executable="",
        started_at="",
    )
    if not verify_instance(instance):
        raise RuntimeError("The InferBridge server instance is no longer available.")
    # Import only in the window process; do not initialize .NET/WebView2 inside the
    # tray's worker threads or the OpenVINO server child.
    try:
        import webview

        storage = resolve_runtime_paths(portable=False, desktop=True).data_root / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        window = webview.create_window(
            "InferBridge",
            f"http://127.0.0.1:{port}/",
            width=1180,
            height=800,
            min_size=(760, 520),
        )
        # Close a stale window if the authoritative tray/server exits or its port
        # is reused; never keep rendering a different process at the same address.
        stop = threading.Event()
        window.events.closed += stop.set

        def watch_instance() -> None:
            while not stop.wait(2):
                if not verify_instance(instance):
                    logger.warning("Closing Store window after server instance changed")
                    window.destroy()
                    return

        # Force Chromium: silent fallback to legacy MSHTML cannot run the current UI.
        webview.start(
            watch_instance,
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(storage),
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - native GUI initialization boundary
        logger.exception("The InferBridge Store window failed")
        from app.desktop_shell import show_dialog

        show_dialog(
            "InferBridge",
            "The application window could not start. Ensure Microsoft Edge WebView2 "
            "Runtime is installed, then reopen InferBridge.\n\n"
            f"Details: {type(exc).__name__}: {str(exc)[:180]}",
            error=True,
        )
        return 2
