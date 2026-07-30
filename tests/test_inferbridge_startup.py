from pathlib import Path

import pytest

from app.startup_registration import (
    CURRENT_VALUE_NAME,
    LEGACY_VALUE_NAME,
    RUN_KEY,
    MemoryRegistryBackend,
    StartupRegistration,
)


def test_enabled_legacy_value_migrates_to_inferbridge(tmp_path):
    backend = MemoryRegistryBackend()
    backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = (
        '"C:\\Program Files\\OpenVINO Windows LLM\\OpenVINOWindowsLLM.exe" '
        "--startup --no-browser"
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
