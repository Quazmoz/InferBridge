"""Dynamic tray menu and user actions."""

from __future__ import annotations

import contextlib
import logging
import threading
import webbrowser
from collections.abc import Callable

from app import __version__
from app.desktop_shell import copy_to_clipboard, open_path, show_dialog
from app.support import GITHUB_ISSUES_URL, SUPPORT_URL, validate_support_url
from app.tray_state import (
    TrayPhase,
    TraySnapshot,
    connection_information,
    menu_state,
    snapshot_from_status,
)
from app.tray_support import APP_TITLE

logger = logging.getLogger("ov-llm.tray")


class TrayMenuMixin:
    def _build_menu(self, pystray):
        Menu = pystray.Menu
        Item = pystray.MenuItem
        return Menu(
            Item(
                "Open Chat",
                self._action(self.open_chat),
                default=True,
                enabled=self._enabled("open_chat"),
            ),
            Item(
                "Status",
                Menu(
                    Item(
                        lambda _item: f"Server: {self._snapshot().server_status}",
                        lambda *_args: None,
                        enabled=False,
                    ),
                    Item(
                        lambda _item: (
                            f"Model: {self._snapshot().active_model_name or self._snapshot().active_model_id or 'None'}"
                        ),
                        lambda *_args: None,
                        enabled=False,
                    ),
                    Item(
                        lambda _item: f"Device: {self._snapshot().actual_device or 'None'}",
                        lambda *_args: None,
                        enabled=False,
                    ),
                    Item(
                        lambda _item: f"Preparation: {self._preparation_label()}",
                        lambda *_args: None,
                        enabled=False,
                    ),
                ),
            ),
            Item(
                "Copy",
                Menu(
                    Item(
                        "API Base URL",
                        self._action(self.copy_api_url),
                        enabled=self._enabled("copy_connection"),
                    ),
                    Item(
                        "Chat URL",
                        self._action(self.copy_chat_url),
                        enabled=self._enabled("copy_connection"),
                    ),
                    Item(
                        "OpenAI Configuration",
                        self._action(self.copy_openai_configuration),
                        enabled=self._enabled("copy_connection"),
                    ),
                    Item(
                        "Diagnostics",
                        self._action(self.copy_diagnostics),
                    ),
                ),
            ),
            Item(
                "Server",
                Menu(
                    Item(
                        "Start Server",
                        self._action(self.start_server),
                        enabled=self._enabled("start_server"),
                    ),
                    Item(
                        "Stop Server",
                        self._action(self.stop_server),
                        enabled=self._enabled("stop_server"),
                    ),
                    Item(
                        "Restart Server",
                        self._action(self.restart_server),
                        enabled=self._enabled("restart_server"),
                    ),
                    Item(
                        "Run Hardware Scan",
                        self._action(self.run_hardware_scan),
                        enabled=self._enabled("run_hardware_scan"),
                    ),
                    Item(
                        "Run Short Benchmark",
                        self._action(self.run_short_benchmark),
                        enabled=self._enabled("run_benchmark"),
                    ),
                ),
            ),
            Item(
                "Folders",
                Menu(
                    Item(
                        "Open Model Folder",
                        self._action(self.open_model_folder),
                        enabled=self._enabled("open_model_folder"),
                    ),
                    Item(
                        "Open Log Folder",
                        self._action(self.open_log_folder),
                        enabled=self._enabled("open_log_folder"),
                    ),
                ),
            ),
            Item(
                "Export Diagnostics",
                self._action(self.export_diagnostics),
                enabled=self._enabled("export_diagnostics"),
            ),
            Item(
                "Start with Windows",
                self._toggle_startup,
                checked=lambda _item: self._startup_enabled(),
                enabled=self._enabled("start_with_windows"),
            ),
            Item("Check for Updates", self._action(self.open_updates)),
            Item(
                "Help",
                Menu(
                    Item("Send Feedback", self._action(self.open_feedback)),
                    Item("GitHub Issues", self._action(self.open_github_issues)),
                    Item("About", self._action(self.show_about)),
                ),
            ),
            Item("Quit", self._action(self.quit)),
        )

    def _action(self, callback: Callable[[], None]):
        def invoke(_icon=None, _item=None):
            threading.Thread(target=self._guarded_action, args=(callback,), daemon=True).start()

        return invoke

    def _guarded_action(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - user action boundary
            logger.exception("Tray action failed")
            show_dialog(APP_TITLE, str(exc)[:300], error=True)
        finally:
            self._refresh_icon()

    def _snapshot(self) -> TraySnapshot:
        with self.snapshot_lock:
            return self.snapshot

    def _set_snapshot(self, snapshot: TraySnapshot) -> None:
        with self.snapshot_lock:
            self.snapshot = snapshot
        self._refresh_icon()

    def _menu_state(self):
        return menu_state(
            self._snapshot(),
            models_dir=self.paths.models_dir,
            logs_dir=self.paths.logs_dir,
            diagnostics_dir=self.paths.diagnostics_dir,
            portable=self.paths.portable,
        )

    def _enabled(self, field: str):
        return lambda _item: bool(getattr(self._menu_state(), field))

    def _preparation_label(self) -> str:
        snapshot = self._snapshot()
        if not snapshot.preparation_stage:
            return "Idle"
        if snapshot.preparation_percent is None:
            return snapshot.preparation_stage
        return f"{snapshot.preparation_stage} ({snapshot.preparation_percent:.0f}%)"

    def _start_server(self, *, open_chat: bool) -> None:
        self._set_snapshot(
            snapshot_from_status(
                None,
                port=self.controller.port,
                process_running=False,
                starting=True,
            )
        )
        self.controller.start(open_chat=open_chat)
        self._poll_once()

    def start_server(self) -> None:
        self._start_server(open_chat=False)

    def stop_server(self) -> None:
        self.controller.stop()
        self._poll_once()

    def restart_server(self) -> None:
        snapshot = self._snapshot()
        if snapshot.benchmark_running:
            raise RuntimeError(
                "Wait for the short benchmark to finish before restarting the server."
            )
        if snapshot.phase is TrayPhase.PREPARING:
            raise RuntimeError("Cancel or finish model preparation before restarting the server.")
        self._set_snapshot(
            snapshot_from_status(
                None, port=self.controller.port, process_running=False, starting=True
            )
        )
        self.controller.restart(open_chat=False)
        self._poll_once()

    def open_chat(self) -> None:
        if not self.controller.running:
            self._start_server(open_chat=True)
            return
        if not self.controller.open_chat():
            raise RuntimeError(f"The browser could not be opened. Visit {self.controller.origin}/")

    def open_updates(self) -> None:
        if not self.controller.running:
            self._start_server(open_chat=False)
        if not webbrowser.open(f"{self.controller.origin}/?updates=1", new=2):
            raise RuntimeError(
                f"The browser could not be opened. Visit {self.controller.origin}/?updates=1"
            )

    def _open_external_support(self, url: str, label: str) -> None:
        target = validate_support_url(url)
        if not webbrowser.open(target, new=2):
            raise RuntimeError(f"The browser could not open {label}. Visit {target}")

    def open_feedback(self) -> None:
        self._open_external_support(SUPPORT_URL, "the InferBridge feedback page")

    def open_github_issues(self) -> None:
        self._open_external_support(GITHUB_ISSUES_URL, "GitHub Issues")

    def _connection(self) -> dict[str, str]:
        snapshot = self._snapshot()
        if not snapshot.port:
            raise RuntimeError("The local server port is unavailable.")
        return connection_information(
            snapshot.port,
            api_key_configured=snapshot.api_key_configured,
        )

    def copy_api_url(self) -> None:
        copy_to_clipboard(self._connection()["api_base_url"])

    def copy_chat_url(self) -> None:
        copy_to_clipboard(self._connection()["chat_url"])

    def copy_openai_configuration(self) -> None:
        copy_to_clipboard(self._connection()["openai_configuration"])

    def open_model_folder(self) -> None:
        if not open_path(self.paths.models_dir):
            raise RuntimeError("The model folder could not be opened.")

    def open_log_folder(self) -> None:
        if not open_path(self.paths.logs_dir):
            raise RuntimeError("The log folder could not be opened.")

    def run_hardware_scan(self) -> None:
        result = self.controller.run_hardware_scan()
        scan = result.get("scan") if isinstance(result, dict) else None
        count = len((scan or {}).get("items") or []) if isinstance(scan, dict) else 0
        show_dialog(APP_TITLE, f"Hardware scan completed with {count} reported items.")

    def run_short_benchmark(self) -> None:
        result = self.controller.run_short_benchmark()
        benchmark = result.get("benchmark") if isinstance(result, dict) else None
        rows = (benchmark or {}).get("results") if isinstance(benchmark, dict) else None
        successful = sum(1 for row in rows or [] if isinstance(row, dict) and row.get("success"))
        show_dialog(APP_TITLE, f"Short benchmark completed. Successful results: {successful}.")

    def _startup_enabled(self) -> bool:
        try:
            return self.startup.state().enabled
        except Exception:
            return False

    def _toggle_startup(self, _icon=None, _item=None) -> None:
        try:
            current = self.startup.state().enabled
            self.startup.set_enabled(not current)
        except Exception as exc:  # noqa: BLE001
            show_dialog(APP_TITLE, str(exc)[:300], error=True)
        self._refresh_icon()

    def show_about(self) -> None:
        show_dialog(
            APP_TITLE,
            f"{APP_TITLE} {__version__}\n\n"
            "A local-first OpenVINO GenAI server for Windows. The tray controls only the "
            "server process it started. Prompts and chat history are not included in diagnostics. "
            "Use Help > Send Feedback for the simple support form, or Help > GitHub Issues for "
            "technical discussion. Use Check for Updates to review optional stable or beta releases.",
        )

    def quit(self) -> None:
        self.stop_event.set()
        with contextlib.suppress(Exception):
            self.controller.stop()
        if self.icon is not None:
            self.icon.stop()
