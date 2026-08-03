from pathlib import Path

import pytest

from app import startup_registration
from app.startup_registration import (
    CURRENT_VALUE_NAME,
    LEGACY_VALUE_NAME,
    RUN_KEY,
    MemoryRegistryBackend,
    StartupRegistration,
    desktop_launcher_command_prefix,
    startup_command,
)


def test_enabled_legacy_value_migrates_to_inferbridge(tmp_path):
    backend = MemoryRegistryBackend()
    backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = (
        '"C:\\Program Files\\OpenVINO Windows LLM\\OpenVINOWindowsLLM.exe" --startup --no-browser'
    )
    registration = StartupRegistration(executable=tmp_path / "InferBridge.exe", backend=backend)
    state = registration.state()
    assert state.enabled
    assert backend.read(RUN_KEY, CURRENT_VALUE_NAME) == registration.expected_command
    assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is None


def test_absent_legacy_value_does_not_enable_startup(tmp_path):
    registration = StartupRegistration(
        executable=tmp_path / "InferBridge.exe",
        backend=MemoryRegistryBackend(),
    )
    assert not registration.state().enabled


def test_unrecognized_legacy_value_is_not_removed(tmp_path):
    backend = MemoryRegistryBackend()
    backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = '"C:\\Other\\unrelated.exe"'
    registration = StartupRegistration(executable=tmp_path / "InferBridge.exe", backend=backend)
    assert not registration.state().enabled
    assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is not None


def test_disabling_removes_current_and_legacy_values_only(tmp_path):
    backend = MemoryRegistryBackend()
    backend.values[(RUN_KEY, CURRENT_VALUE_NAME)] = "current"
    backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = "legacy"
    backend.values[(RUN_KEY, "OtherApplication")] = "preserve"
    registration = StartupRegistration(executable=tmp_path / "InferBridge.exe", backend=backend)
    registration.set_enabled(False)
    assert backend.read(RUN_KEY, CURRENT_VALUE_NAME) is None
    assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is None
    assert backend.read(RUN_KEY, "OtherApplication") == "preserve"


def test_portable_mode_cannot_enable_startup(tmp_path):
    registration = StartupRegistration(
        executable=tmp_path / "InferBridge.exe",
        portable=True,
        backend=MemoryRegistryBackend(),
    )
    with pytest.raises(RuntimeError, match="portable mode"):
        registration.set_enabled(True)


def test_source_desktop_startup_preserves_module_dispatch(monkeypatch, tmp_path):
    python = tmp_path / "Python Env" / "python.exe"
    monkeypatch.setattr(startup_registration.sys, "executable", str(python))
    monkeypatch.delattr(startup_registration.sys, "frozen", raising=False)

    prefix = desktop_launcher_command_prefix()
    registration = StartupRegistration.for_current_desktop_launcher(
        backend=MemoryRegistryBackend()
    )

    assert prefix == (str(python.resolve()), "-m", "app.desktop_launcher")
    assert registration.expected_command == startup_command(
        python,
        arguments=("-m", "app.desktop_launcher"),
        portable=False,
        open_browser=False,
    )
    assert '"-m"' not in registration.expected_command
    assert "-m app.desktop_launcher --startup --no-browser" in registration.expected_command


def test_frozen_desktop_startup_uses_executable_directly(monkeypatch, tmp_path):
    executable = tmp_path / "InferBridge" / "InferBridge.exe"
    monkeypatch.setattr(startup_registration.sys, "executable", str(executable))
    monkeypatch.setattr(startup_registration.sys, "frozen", True, raising=False)

    registration = StartupRegistration.for_current_desktop_launcher(
        backend=MemoryRegistryBackend()
    )

    assert desktop_launcher_command_prefix() == (str(executable.resolve()),)
    assert registration.arguments == ()
    assert registration.expected_command == startup_command(
        executable,
        portable=False,
        open_browser=False,
    )


def test_source_registration_repairs_previous_bare_python_command(tmp_path):
    backend = MemoryRegistryBackend()
    python = tmp_path / "Python Env" / "python.exe"
    backend.values[(RUN_KEY, CURRENT_VALUE_NAME)] = startup_command(
        python,
        portable=False,
        open_browser=False,
    )
    registration = StartupRegistration(
        executable=python,
        arguments=("-m", "app.desktop_launcher"),
        backend=backend,
    )

    state = registration.state()

    assert state.enabled
    assert state.command == registration.expected_command
    assert "-m app.desktop_launcher --startup --no-browser" in state.command
