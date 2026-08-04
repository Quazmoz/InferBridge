"""Coverage for stopping a running instance during upgrade and uninstall.

The tray launcher and its server child are windowed processes with no top-level window.
Restart Manager's graceful shutdown has nothing to send a close message to, so an upgrade
failed with "Setup was unable to automatically close all applications" and an uninstall
left the program directory and user data behind while reporting success.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.tray_menu import TrayMenuMixin
from app.tray_polling import TrayPollingMixin

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")


class _RecordingController:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _RecordingIcon:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _CommandStub(TrayPollingMixin, TrayMenuMixin):
    """Exercises the command-file handler with the real quit implementation."""

    def __init__(self, command_file: Path) -> None:
        self.command_file = command_file
        self.stop_event = threading.Event()
        self.controller = _RecordingController()
        self.icon = _RecordingIcon()
        self.started: list[bool] = []

    def _start_server(self, open_chat: bool = False) -> None:
        self.started.append(open_chat)

    def open_chat(self) -> None:
        self.started.append(True)


def _write_command(path: Path, name: str) -> None:
    path.write_text(json.dumps({"command": name}), encoding="utf-8")


# --- application side ------------------------------------------------------


def test_quit_command_performs_the_full_tray_shutdown(tmp_path):
    stub = _CommandStub(tmp_path / "tray-command.json")
    _write_command(stub.command_file, "quit")

    stub._handle_command_file()

    # Setting the stop event alone leaves the pystray message loop running, so the process
    # keeps holding every file the installer needs to replace.
    assert stub.stop_event.is_set()
    assert stub.controller.stopped
    assert stub.icon.stopped
    assert not stub.command_file.exists()


def test_quit_command_matches_the_tray_menu_action(tmp_path):
    menu_driven = _CommandStub(tmp_path / "menu.json")
    menu_driven.quit()

    command_driven = _CommandStub(tmp_path / "command.json")
    _write_command(command_driven.command_file, "quit")
    command_driven._handle_command_file()

    assert (
        command_driven.stop_event.is_set(),
        command_driven.controller.stopped,
        command_driven.icon.stopped,
    ) == (
        menu_driven.stop_event.is_set(),
        menu_driven.controller.stopped,
        menu_driven.icon.stopped,
    )


def test_other_commands_still_start_the_server(tmp_path):
    stub = _CommandStub(tmp_path / "tray-command.json")
    stub.controller.running = False
    _write_command(stub.command_file, "start")

    stub._handle_command_file()

    assert stub.started == [False]
    assert not stub.stop_event.is_set()
    assert not stub.icon.stopped


# --- installer side --------------------------------------------------------


def test_installer_forces_locked_files_closed():
    # Graceful Restart Manager shutdown cannot close a windowless process; force is the
    # backstop that releases the locks, scoped to processes holding files under {app}.
    assert "CloseApplications=force" in INSTALLER
    assert "CloseApplications=yes" not in INSTALLER


def test_installer_asks_a_running_instance_to_exit_before_touching_files():
    assert "function PrepareToInstall(" in INSTALLER
    assert "function InitializeUninstall(" in INSTALLER
    assert INSTALLER.count("StopRunningInstance();") >= 2
    assert '"command": "quit"' in INSTALLER
    assert "desktop-instance.lock" in INSTALLER


def test_installer_terminates_an_instance_that_will_not_exit():
    # The uninstaller performs no Restart Manager pass at all, so a release that ignores
    # the shutdown request would otherwise keep its files open through the whole uninstall.
    stop = INSTALLER.split("procedure StopRunningInstance();", 1)[1].split("\nfunction ", 1)[0]
    assert "TerminateImage('{#MyAppExeName}');" in stop
    assert "TerminateImage('{#MyLegacyAppExeName}');" in stop
    assert "taskkill.exe" in INSTALLER
    assert "'/F /T /IM \"' + ImageName + '\"'" in INSTALLER
    # Termination is reached only after the graceful request has been given time to work.
    assert stop.index("RequestGracefulShutdown();") < stop.index("TerminateImage(")


def test_installer_detects_a_live_instance_without_deleting_a_live_lock():
    probe = INSTALLER.split("function InstanceRunning(", 1)[1].split("end;", 1)[0]
    # A live tray holds the lock file open without delete sharing, so a failed delete is
    # the liveness signal and a stale lock is cleaned up instead of blocking forever.
    assert "if not DeleteFile(LockFile) then" in probe
    assert "Result := True" in probe


def test_uninstall_removes_the_emptied_program_directory():
    section = INSTALLER.split("[UninstallDelete]", 1)[1].split("[Files]", 1)[0]
    assert 'Type: dirifempty; Name: "{app}"' in section


def test_uninstall_removes_leftovers_and_reports_what_remains():
    assert "function RemoveTree(" in INSTALLER
    assert "RemoveTreeAttempts" in INSTALLER
    assert "attrib -r -s -h" in INSTALLER
    assert "RemoveStartupRegistration();" in INSTALLER
    assert "RegDeleteValue(HKCU, RunKey, 'InferBridge');" in INSTALLER
    assert "RegDeleteValue(HKCU, RunKey, 'OpenVINOWindowsLLM');" in INSTALLER
    assert "could not be removed completely" in INSTALLER
    # The previous uninstaller discarded every DelTree result and always claimed success.
    assert "DelTree(ExpandConstant('{localappdata}\\InferBridge'), True, True, True);" not in (
        INSTALLER
    )
