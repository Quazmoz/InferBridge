"""Lightweight Windows system-tray controller for the packaged desktop application."""

from __future__ import annotations

import argparse
import contextlib
import threading
from pathlib import Path
from typing import Any

from app.desktop_controller import DesktopServerController, ServerControllerOptions
from app.desktop_launcher import InstanceLock
from app.desktop_server import prepare_desktop_environment
from app.paths import ensure_data_root_writable, resolve_runtime_paths
from app.startup_registration import StartupRegistration
from app.tray_diagnostics_actions import TrayDiagnosticsActionsMixin
from app.tray_menu import TrayMenuMixin
from app.tray_polling import TrayPollingMixin
from app.tray_runtime import TrayRuntimeMixin
from app.tray_state import TrayPhase, TraySnapshot
from app.tray_support import configure_logging


class TrayApplication(
    TrayRuntimeMixin,
    TrayPollingMixin,
    TrayDiagnosticsActionsMixin,
    TrayMenuMixin,
):
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        prepare_desktop_environment(
            portable=args.portable,
            data_dir=args.data_dir,
            mock=args.mock,
        )
        self.paths = resolve_runtime_paths(portable=args.portable, desktop=True)
        ensure_data_root_writable(self.paths)
        self.log_path = configure_logging(self.paths.logs_dir)
        self.lock = InstanceLock(self.paths.launcher_lock_file)
        self.stop_event = threading.Event()
        self.snapshot = TraySnapshot(phase=TrayPhase.STOPPED, server_status="Stopped")
        self.snapshot_lock = threading.Lock()
        self.render_lock = threading.Lock()
        self.last_render_signature: tuple[Any, ...] | None = None
        self.icon = None
        self.poll_thread: threading.Thread | None = None
        self.last_status_payload: dict[str, Any] | None = None
        self.last_diagnostics_path: Path | None = None
        resolved_data_dir = str(self.paths.data_root) if args.data_dir else None
        self.controller = DesktopServerController(
            paths=self.paths,
            options=ServerControllerOptions(
                preferred_port=args.port,
                portable=args.portable,
                data_dir=resolved_data_dir,
                mock=args.mock,
            ),
            log_path=self.paths.logs_dir / "desktop.log",
        )
        self.startup = StartupRegistration.for_current_desktop_launcher(
            portable=self.paths.portable,
        )
        self.command_file = self.paths.data_root / "tray-command.json"
        self.heartbeat_file = self.paths.data_root / "tray-heartbeat.json"
        self.restart_request_file = self.paths.data_root / "restart-server.request"

    def _shutdown_owned_resources(self) -> None:
        self.stop_event.set()
        with contextlib.suppress(Exception):
            self.controller.stop()
        for marker in (self.heartbeat_file, self.command_file, self.restart_request_file):
            with contextlib.suppress(OSError):
                marker.unlink()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=5)


def run_tray_controller(args: argparse.Namespace) -> int:
    return TrayApplication(args).run()