"""Shared logging, state-file, and icon helpers for the desktop tray."""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.brand import DISPLAY_NAME
from app.tray_state import TrayPhase

APP_TITLE = DISPLAY_NAME
POLL_SECONDS = 3.0


def guarded_tray_icon(pystray):
    """Return a ``pystray.Icon`` subclass that never rebuilds its menu on screen.

    On Windows the notification-area menu is a native ``HMENU`` that pystray
    builds once in ``_update_menu`` and shows with a blocking ``TrackPopupMenuEx``
    call on the UI thread. The tray's status poller lives on a separate thread and
    calls ``update_menu`` a few times a minute to refresh dynamic labels and
    enabled state. If that rebuild lands while the user has the menu open,
    ``DestroyMenu`` frees the handle Windows is actively drawing, which makes
    hovering, sub-menus, and item highlighting glitch.

    This subclass defers any rebuild requested while the menu is displayed until
    it closes, and serializes rebuilds under a lock so two threads can never
    destroy the same handle. On non-Windows backends ``_on_notify`` is never
    dispatched, so the guard is inert and behaviour matches stock pystray.
    """

    class GuardedIcon(pystray.Icon):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._menu_lock = threading.RLock()
            self._menu_on_screen = False
            self._menu_rebuild_pending = False

        def _on_notify(self, wparam, lparam):
            with self._menu_lock:
                self._menu_on_screen = True
            try:
                # Blocks on Win32 for the entire time the popup menu is shown.
                super()._on_notify(wparam, lparam)
            finally:
                with self._menu_lock:
                    self._menu_on_screen = False
                    pending = self._menu_rebuild_pending
                    self._menu_rebuild_pending = False
                if pending:
                    self.update_menu()

        def _update_menu(self):
            with self._menu_lock:
                if self._menu_on_screen:
                    # Rebuilding now would DestroyMenu the handle currently on
                    # screen; apply the refresh once the menu closes instead.
                    self._menu_rebuild_pending = True
                    return
                super()._update_menu()

    return GuardedIcon


def configure_logging(logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "tray.log"
    root = logging.getLogger()
    if not any(getattr(handler, "baseFilename", None) == str(path) for handler in root.handlers):
        handler = RotatingFileHandler(
            path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    return path


#: Backoff for transient Windows replace contention, mirroring ``app.storage_safety``.
_REPLACE_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.15, 0.3, 0.6)
#: Sharing and lock violations reach Python as a bare OSError rather than a dedicated
#: subclass; ERROR_ACCESS_DENIED arrives as PermissionError.
_TRANSIENT_REPLACE_WINERRORS = frozenset({32, 33})


def _is_transient_replace_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in _TRANSIENT_REPLACE_WINERRORS


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Command and heartbeat files can be written by separate desktop processes. Give
    # each writer its own staging file so atomic replace remains safe even when two
    # second-launch activations or installer coordination requests arrive together.
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # Independent staging files make the write atomic, but on Windows the replace
        # itself is not contention-free: MoveFileEx onto a destination that another
        # writer, an antivirus scanner, or a file-sync client currently holds open
        # fails with ERROR_ACCESS_DENIED or a sharing violation. Those are transient,
        # so retry briefly rather than let a lost tray command surface as a crash.
        last_error: OSError | None = None
        for delay in _REPLACE_RETRY_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                temporary.replace(path)
                return
            except OSError as exc:
                if not _is_transient_replace_error(exc):
                    raise
                last_error = exc
        raise last_error if last_error is not None else OSError("Atomic replace did not complete.")
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def tray_icon(phase: TrayPhase):
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    background = {
        TrayPhase.STARTING: (42, 108, 176, 255),
        TrayPhase.READY: (25, 132, 87, 255),
        TrayPhase.PREPARING: (139, 92, 26, 255),
        TrayPhase.WARNING: (176, 104, 24, 255),
        TrayPhase.ERROR: (174, 48, 48, 255),
        TrayPhase.STOPPED: (85, 91, 102, 255),
        TrayPhase.UNKNOWN: (91, 74, 132, 255),
    }[phase]
    draw.rounded_rectangle((4, 4, 60, 60), radius=15, fill=background, outline="white", width=3)
    if phase is TrayPhase.READY:
        draw.line((17, 33, 28, 44, 48, 20), fill="white", width=7, joint="curve")
    elif phase is TrayPhase.STARTING:
        draw.ellipse((17, 17, 47, 47), outline="white", width=5)
        draw.line((32, 32, 32, 21), fill="white", width=4)
        draw.line((32, 32, 43, 36), fill="white", width=4)
    elif phase is TrayPhase.PREPARING:
        for x, height in ((18, 18), (30, 30), (42, 23)):
            draw.rounded_rectangle((x, 48 - height, x + 7, 48), radius=2, fill="white")
    elif phase is TrayPhase.WARNING:
        draw.polygon(((32, 13), (53, 50), (11, 50)), outline="white", fill=None)
        draw.line((32, 25, 32, 38), fill="white", width=5)
        draw.ellipse((29, 42, 35, 48), fill="white")
    elif phase is TrayPhase.ERROR:
        draw.line((18, 18, 46, 46), fill="white", width=7)
        draw.line((46, 18, 18, 46), fill="white", width=7)
    elif phase is TrayPhase.STOPPED:
        draw.rectangle((20, 20, 44, 44), outline="white", width=6)
    else:
        draw.ellipse((16, 12, 48, 50), outline="white", width=4)
        draw.line((25, 25, 31, 19, 39, 21, 40, 28, 32, 34, 32, 39), fill="white", width=4)
        draw.ellipse((29, 44, 35, 50), fill="white")
    return image
