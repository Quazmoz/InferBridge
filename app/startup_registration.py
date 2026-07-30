"""Per-user Windows startup registration with InferBridge legacy migration."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.brand import LEGACY_EXECUTABLE_BASENAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CURRENT_VALUE_NAME = "InferBridge"
LEGACY_VALUE_NAME = "OpenVINOWindowsLLM"
_RUN_KEY = RUN_KEY
_VALUE_NAME = CURRENT_VALUE_NAME


class RegistryBackend(Protocol):
    def read(self, key: str, name: str) -> str | None: ...

    def write(self, key: str, name: str, value: str) -> None: ...

    def delete(self, key: str, name: str) -> None: ...


class WinRegBackend:
    def read(self, key: str, name: str) -> str | None:
        if os.name != "nt":
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
                value, _kind = winreg.QueryValueEx(handle, name)
                return str(value)
        except FileNotFoundError:
            return None

    def write(self, key: str, name: str, value: str) -> None:
        if os.name != "nt":
            raise RuntimeError("Start with Windows is only available on Windows.")
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            key,
            0,
            winreg.KEY_SET_VALUE,
        ) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        if os.name != "nt":
            return
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key,
                0,
                winreg.KEY_SET_VALUE,
            ) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            return


@dataclass(frozen=True)
class StartupRegistrationState:
    enabled: bool
    command: str | None
    location: str = f"HKCU\\{RUN_KEY}\\{CURRENT_VALUE_NAME}"


def quote_windows_argument(value: str) -> str:
    text = str(value)
    if not text:
        return '""'
    if not any(char.isspace() or char in '"' for char in text):
        return text
    return subprocess_list2cmdline([text])


def subprocess_list2cmdline(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments)


def startup_command(executable: Path, *, portable: bool, open_browser: bool = False) -> str:
    executable = Path(executable).expanduser().resolve()
    command = [str(executable), "--startup"]
    if portable:
        command.append("--portable")
    if not open_browser:
        command.append("--no-browser")
    return subprocess_list2cmdline(command)


def _command_executable_name(command: str | None) -> str | None:
    value = str(command or "").strip()
    if not value:
        return None
    match = re.match(r'^\s*(?:"([^"]+)"|([^\s]+))', value)
    if not match:
        return None
    return Path(match.group(1) or match.group(2)).name.casefold()


def _recognized_legacy_command(command: str | None) -> bool:
    executable_name = _command_executable_name(command)
    return executable_name == f"{LEGACY_EXECUTABLE_BASENAME}.exe".casefold()


class StartupRegistration:
    def __init__(
        self,
        *,
        executable: Path | None = None,
        portable: bool = False,
        backend: RegistryBackend | None = None,
    ) -> None:
        self.executable = Path(executable or sys.executable).expanduser().resolve()
        self.portable = bool(portable)
        self.backend = backend or WinRegBackend()

    @property
    def expected_command(self) -> str:
        return startup_command(self.executable, portable=self.portable, open_browser=False)

    def _migrate_legacy_if_enabled(self) -> None:
        current = self.backend.read(RUN_KEY, CURRENT_VALUE_NAME)
        legacy = self.backend.read(RUN_KEY, LEGACY_VALUE_NAME)
        if current is None and _recognized_legacy_command(legacy):
            self.backend.write(RUN_KEY, CURRENT_VALUE_NAME, self.expected_command)
            self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
        elif current == self.expected_command and _recognized_legacy_command(legacy):
            self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)

    def state(self) -> StartupRegistrationState:
        self._migrate_legacy_if_enabled()
        current = self.backend.read(RUN_KEY, CURRENT_VALUE_NAME)
        return StartupRegistrationState(
            enabled=current == self.expected_command,
            command=current,
        )

    def set_enabled(self, enabled: bool) -> StartupRegistrationState:
        if enabled and self.portable:
            raise RuntimeError(
                "Start with Windows is disabled in portable mode. Install the application "
                "per-user before enabling automatic startup."
            )
        if enabled:
            self.backend.write(RUN_KEY, CURRENT_VALUE_NAME, self.expected_command)
            legacy = self.backend.read(RUN_KEY, LEGACY_VALUE_NAME)
            if _recognized_legacy_command(legacy):
                self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
        else:
            self.backend.delete(RUN_KEY, CURRENT_VALUE_NAME)
            self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
        return self.state()


class MemoryRegistryBackend:
    """Small deterministic registry substitute for unit tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def read(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))

    def write(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def delete(self, key: str, name: str) -> None:
        self.values.pop((key, name), None)
