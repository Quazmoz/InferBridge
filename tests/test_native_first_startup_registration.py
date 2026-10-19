"""Native WinUI is the foreground target for Windows per-user autostart."""

from __future__ import annotations

import pytest

from app.startup_registration import (
    RUN_KEY,
    MemoryRegistryBackend,
    StartupRegistration,
    startup_command,
)


def test_winui_startup_command_is_not_an_engine_cli_command(tmp_path):
    native = tmp_path / "native" / "InferBridge.Native.exe"
    native.parent.mkdir()
    native.write_bytes(b"MZ")
    command = startup_command(native, portable=False)
    assert str(native) in command
    assert "--startup" not in command
    assert "--no-browser" not in command


def test_existing_engine_startup_registration_migrates_to_native(tmp_path):
    root = tmp_path / "InferBridge"
    native = root / "native" / "InferBridge.Native.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"MZ")
    backend = MemoryRegistryBackend()
    previous = startup_command(root / "InferBridge.exe", portable=False)
    backend.write(RUN_KEY, "InferBridge", previous)

    registration = StartupRegistration(
        executable=native, packaged=False, backend=backend
    )
    state = registration.state()
    assert state.enabled
    assert state.command == registration.expected_command
    assert backend.read(RUN_KEY, "InferBridge") == registration.expected_command
    assert "--no-browser" not in state.command


def test_missing_native_payload_never_registers_engine_fallback(tmp_path):
    backend = MemoryRegistryBackend()
    registration = StartupRegistration(
        executable=tmp_path / "native" / "InferBridge.Native.exe",
        packaged=False,
        backend=backend,
    )
    with pytest.raises(RuntimeError, match="Native WinUI executable is missing"):
        registration.set_enabled(True)
    assert backend.read(RUN_KEY, "InferBridge") is None
