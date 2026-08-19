"""Startup and pystray runtime boundary for the desktop tray."""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from datetime import UTC, datetime

from app.desktop_launcher import _read_metadata, verify_instance
from app.desktop_shell import open_browser, show_dialog
from app.tray_state import TrayPhase, TraySnapshot, tooltip
from app.tray_support import APP_TITLE, atomic_json, guarded_tray_icon, tray_icon

logger = logging.getLogger("ov-llm.tray")


class TrayRuntimeMixin:
    def run(self) -> int:
        if not self.lock.acquire():
            self._activate_existing_instance()
            return 0
        try:
            # Command and restart markers are one-shot coordination files owned by a
            # specific tray session. If a prior tray exited before consuming them, they
            # must not trigger actions in this newly authoritative controller instance.
            for marker in (self.command_file, self.restart_request_file):
                with contextlib.suppress(OSError):
                    marker.unlink()
            if not self.args.start_stopped:
                try:
                    self._start_server(open_chat=not self.args.no_browser and not self.args.startup)
                except Exception as exc:  # noqa: BLE001 - keep the tray available for recovery
                    message = str(exc)[:300] or "The local server could not be started."
                    logger.exception("Initial server startup failed")
                    self.snapshot = TraySnapshot(
                        phase=TrayPhase.ERROR,
                        server_status="Startup failed",
                        warning=message,
                    )
                    if not self.args.headless:
                        show_dialog(APP_TITLE, message, error=True)
            if self.args.headless:
                return self._run_headless()
            return self._run_tray()
        finally:
            self._shutdown_owned_resources()
            self.lock.release()

    def _activate_existing_instance(self) -> None:
        metadata = _read_metadata(self.paths.launcher_metadata_file)
        if metadata and verify_instance(metadata):
            if not self.args.no_browser:
                open_browser(f"http://127.0.0.1:{metadata.port}/")
            return
        try:
            atomic_json(
                self.command_file,
                {
                    "command": "start-open-chat" if not self.args.no_browser else "start",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
        except OSError as exc:
            show_dialog(
                APP_TITLE,
                f"The tray controller is already running, but it could not be activated: {exc}",
                error=True,
            )

    def _run_headless(self) -> int:
        deadline = (
            time.monotonic() + self.args.headless_seconds if self.args.headless_seconds else None
        )
        while not self.stop_event.wait(0.5):
            self._poll_once()
            if deadline and time.monotonic() >= deadline:
                break
        return 0

    def _run_linux_without_tray(self, reason: str) -> int:
        """Keep the local server alive when a Linux tray backend is unavailable.

        Linux desktop environments vary widely: GNOME may omit StatusNotifier/AppIndicator
        support, Wayland sessions may not expose a compatible backend, and headless or SSH
        sessions intentionally have no tray. The tray is a convenience layer, not the
        server's availability boundary, so a backend failure must not tear down a healthy
        InferBridge server.
        """

        controller = getattr(self, "controller", None)
        if controller is not None and not controller.running:
            logger.error("Linux tray unavailable and no local server is running: %s", reason)
            show_dialog(
                APP_TITLE,
                "The Linux system-tray icon is unavailable and the local server is not "
                "running. Start InferBridge with --headless or use ./start_server.sh to "
                "review the startup failure.",
                error=True,
            )
            return 6

        detail = str(reason or "system-tray backend unavailable").replace("\n", " ")[:200]
        logger.warning("Linux tray unavailable; continuing without tray icon: %s", detail)
        show_dialog(
            APP_TITLE,
            "The Linux system-tray icon is unavailable in this desktop session. "
            "InferBridge will keep running without a tray icon. Use the browser UI or "
            "stop the launcher process to exit.",
        )
        return self._run_headless()

    def _run_tray(self) -> int:
        try:
            import pystray
        except Exception as exc:
            if sys.platform.startswith("linux"):
                return self._run_linux_without_tray(str(exc))
            show_dialog(
                APP_TITLE,
                "The system-tray component could not initialize. Reinstall a complete desktop "
                f"build. Details: {str(exc)[:200]}",
                error=True,
            )
            return 6

        menu = self._build_menu(pystray)
        self.icon = guarded_tray_icon(pystray)(
            "OpenVINOWindowsLLM",
            tray_icon(self.snapshot.phase),
            tooltip(self.snapshot),
            menu,
        )

        def setup(icon):
            icon.visible = True
            self.poll_thread = threading.Thread(
                target=self._poll_loop,
                name="ovllm-tray-poll",
                daemon=True,
            )
            self.poll_thread.start()

        try:
            self.icon.run(setup=setup)
            return 0
        except Exception as exc:  # noqa: BLE001 - tray backend boundary
            logger.exception("Tray library failed")
            if sys.platform.startswith("linux"):
                # A pystray backend may fail after import but before setup starts. Do not
                # launch a second polling thread if setup already created one; in that
                # case, simply keep the controller process alive until it is stopped.
                if self.poll_thread and self.poll_thread.is_alive():
                    detail = str(exc).replace("\n", " ")[:200]
                    logger.warning(
                        "Linux tray backend failed after polling started; continuing headless: %s",
                        detail,
                    )
                    while not self.stop_event.wait(0.5):
                        pass
                    return 0
                return self._run_linux_without_tray(str(exc))
            show_dialog(
                APP_TITLE, f"The tray icon stopped unexpectedly: {str(exc)[:240]}", error=True
            )
            return 7
