from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.onboarding_state import (
    SCHEMA_VERSION,
    OnboardingStateStore,
    UnsupportedStateVersion,
    migrate_state,
)


def test_state_migration_normalizes_device_and_sanitizes_text():
    state = migrate_state(
        {
            "completed": True,
            "selected_model": "  tiny\x00model  ",
            "selected_device": " auto:npu, gpu, cpu ",
        }
    )

    assert state["completed"] is True
    assert state["selected_model"] == "tinymodel"
    assert state["selected_device"] == "AUTO:NPU,GPU,CPU"


def test_invalid_selected_device_restarts_onboarding_without_dropping_model():
    state = migrate_state(
        {
            "completed": True,
            "restart_requested": False,
            "selected_model": "tinyllama-1.1b-chat-fp16",
            "selected_device": "NPU;invalid",
            "actual_device": "NPU",
        }
    )

    assert state["completed"] is False
    assert state["restart_requested"] is True
    assert state["selected_device"] is None
    assert state["selected_model"] == "tinyllama-1.1b-chat-fp16"
    assert state["actual_device"] == "NPU"


def test_transient_state_read_error_is_retried_without_quarantine(tmp_path, monkeypatch):
    store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    store.save(
        {
            "schema_version": SCHEMA_VERSION,
            "completed": True,
            "selected_model": "tinyllama-1.1b-chat-fp16",
            "selected_device": "CPU",
        }
    )
    original_read_text = Path.read_text
    attempts = 0

    def flaky_read_text(path: Path, *args, **kwargs):
        nonlocal attempts
        if path == store.path:
            attempts += 1
            if attempts < 3:
                raise PermissionError("temporary sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    loaded = store.load()

    assert attempts == 3
    assert loaded.state["completed"] is True
    assert loaded.state["selected_model"] == "tinyllama-1.1b-chat-fp16"
    assert not store.path.with_suffix(store.path.suffix + ".corrupt").exists()


def test_persistent_state_read_error_preserves_original_file(tmp_path, monkeypatch):
    store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    store.save(
        {
            "schema_version": SCHEMA_VERSION,
            "completed": True,
            "selected_device": "CPU",
        }
    )
    original_bytes = store.path.read_bytes()
    original_read_text = Path.read_text

    def blocked_read_text(path: Path, *args, **kwargs):
        if path == store.path:
            raise PermissionError("state file is locked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", blocked_read_text)

    with pytest.raises(PermissionError, match="state file is locked"):
        store.load()

    assert store.path.exists()
    assert store.path.read_bytes() == original_bytes
    assert not store.path.with_suffix(store.path.suffix + ".corrupt").exists()


def test_newer_state_schema_is_preserved_instead_of_quarantined(tmp_path):
    store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION + 1,
        "completed": True,
        "selected_model": "future-model",
    }
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedStateVersion, match="newer than supported"):
        store.load()

    assert json.loads(store.path.read_text(encoding="utf-8")) == payload
    assert not store.path.with_suffix(store.path.suffix + ".corrupt").exists()
