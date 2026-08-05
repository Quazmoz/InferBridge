"""Bounded status polling and crash-command handling for the desktop tray."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime

from app import __version__
from app.tray_state import snapshot_from_status, tooltip
from app.tray_support import POLL_SECONDS, atomic_json, tray_icon

logger = logging.getLogger("ov-llm.tray")


class TrayPollingMixin:
    def _poll_loop(self) -> None:
        while not self.stop_event.wait(POLL_SECONDS):
            self._poll_once()

    def _poll_once(self) -> None:
        self._write_heartbeat()
        self._handle_command_file()
        restart_requested = self.restart_request_file.exists()
        crash = self.controller.poll_unexpected_exit()
        if restart_requested and not self.controller.running and not self.stop_event.is_set():
            with contextlib.suppress(OSError):
                self.restart_request_file.unlink()
            try:
                self._start_server(open_chat=False)
                crash = None
            except Exception as exc:  # noqa: BLE001
                self._set_snapshot(
                    snapshot_from_status(
                        None,
                        port=self.controller.port,
                        process_running=False,
                        unexpected_exit=f"Server restart failed: {str(exc)[:180]}",
                    )
                )
                return
        payload = self.controller.status_payload()
        if isinstance(payload, dict):
            server_version = str(payload.get("application_version") or "").strip()
            if server_version and server_version != __version__:
                payload = dict(payload)
                payload["warning"] = (
                    f"Tray version {__version__} does not match server version {server_version}. "
                    "Restart the application from a complete installation."
                )
        self.last_status_payload = payload
        self._set_snapshot(
            snapshot_from_status(
                payload,
                port=self.controller.port,
                process_running=self.controller.running,
                starting=self.controller.starting,
                unexpected_exit=crash,
            )
        )

    def _write_heartbeat(self) -> None:
        try:
            atomic_json(
                self.heartbeat_file,
                {
                    "controller": "tray",
                    "pid": os.getpid(),
                    "port": self.controller.port,
                    "state": self._snapshot().phase.value,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        except OSError:
            logger.warning("Could not write tray heartbeat")

    def _handle_command_file(self) -> None:
        try:
            if not self.command_file.is_file() or self.command_file.is_symlink():
                return
            command = json.loads(self.command_file.read_text(encoding="utf-8-sig"))
            self.command_file.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        name = command.get("command") if isinstance(command, dict) else None
        if name == "quit":
            # Setting the stop event alone leaves the pystray message loop running, so the
            # process keeps holding its installation files. An installer or upgrade asking
            # this instance to exit needs the same full shutdown the tray menu performs.
            self.quit()
        elif name in {"start", "start-open-chat"} and not self.controller.running:
            self._start_server(open_chat=name == "start-open-chat")
        elif name == "start-open-chat":
            self.open_chat()
        elif name == "open-chat":
            self.open_chat()

    def _refresh_icon(self) -> None:
        icon = self.icon
        if icon is None:
            return
        snapshot = self._snapshot()
        # The tooltip, glyph, and every menu label/enabled flag are a pure
        # function of the snapshot plus the startup-registration state. Skip the
        # native rebuild when nothing the user can see has changed: it avoids
        # re-registering the icon and rebuilding the menu handle every poll,
        # which is both wasteful and the source of on-screen menu glitches.
        signature = (snapshot, self._startup_enabled())
        with self.render_lock:
            if signature == self.last_render_signature:
                return
            self.last_render_signature = signature
        try:
            icon.title = tooltip(snapshot)
            icon.icon = tray_icon(snapshot.phase)
            icon.update_menu()
        except Exception:
            logger.exception("Could not refresh tray icon")
