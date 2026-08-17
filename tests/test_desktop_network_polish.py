from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.desktop_network import (
    DesktopApiKeyStore,
    DesktopNetworkService,
    DesktopNetworkUpdateRequest,
    resolve_desktop_network_settings,
)
from app.desktop_network_ui import DESKTOP_NETWORK_JS
from app.onboarding_state import OnboardingStateStore

_NETWORK_ENV = ("OV_LLM_HOST", "OV_LLM_API_KEY", "OV_LLM_CORS_ORIGINS")
_STRONG_KEY = "ib_abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def clean_network_env(monkeypatch):
    for name in _NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _service(tmp_path) -> tuple[DesktopNetworkService, OnboardingStateStore, DesktopApiKeyStore]:
    state_store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    credential_store = DesktopApiKeyStore(tmp_path / "config")
    base = Settings.from_env().replace(port=8123)
    active = resolve_desktop_network_settings(
        base,
        state=state_store.load().state,
        credential_store=credential_store,
        env=os.environ,
    )
    service = DesktopNetworkService(
        active_resolution=active,
        base_settings=base,
        paths=SimpleNamespace(portable=False),
        state_store=state_store,
        credential_store=credential_store,
        endpoint_port=8123,
        env=os.environ,
    )
    return service, state_store, credential_store


def test_state_write_failure_does_not_commit_new_api_key(clean_network_env, tmp_path, monkeypatch):
    service, state_store, credential_store = _service(tmp_path)

    def fail_state_update(**_changes):
        raise OSError("state unavailable")

    monkeypatch.setattr(state_store, "update", fail_state_update)

    with pytest.raises(OSError, match="state unavailable"):
        service.update(
            DesktopNetworkUpdateRequest(
                allow_lan=True,
                api_key=_STRONG_KEY,
            )
        )

    assert credential_store.get_key() is None


def test_key_write_failure_leaves_recoverable_network_state(
    clean_network_env, tmp_path, monkeypatch
):
    service, state_store, credential_store = _service(tmp_path)

    def fail_key_write(_value: str):
        raise OSError("credential store unavailable")

    monkeypatch.setattr(credential_store, "set_key", fail_key_write)

    with pytest.raises(OSError, match="credential store unavailable"):
        service.update(
            DesktopNetworkUpdateRequest(
                allow_lan=True,
                cors_origins="http://192.168.1.50:3000",
                api_key=_STRONG_KEY,
            )
        )

    persisted = state_store.load().state
    assert persisted["lan_access_enabled"] is True
    assert persisted["network_cors_origins"] == "http://192.168.1.50:3000"

    recovered = resolve_desktop_network_settings(
        service.base_settings,
        state=persisted,
        credential_store=credential_store,
        env=os.environ,
    )
    assert recovered.settings.host == "127.0.0.1"
    assert recovered.host_source == "security_fallback"
    assert recovered.lan_blocked_reason


def test_generate_key_keeps_listener_and_cors_edits_as_ui_drafts():
    generate_source = DESKTOP_NETWORK_JS.split("async function generate()", 1)[1].split(
        "async function removeKey()", 1
    )[0]

    assert "const draftLan = Boolean(lan.checked);" in generate_source
    assert "const draftCors = String(cors.value || '').trim();" in generate_source
    assert "allow_lan:Boolean(status?.lan_setting_enabled)" in generate_source
    assert "cors_origins:persistedCors" in generate_source
    assert "lan.checked=draftLan" in generate_source
    assert "cors.value=draftCors" in generate_source
    assert "allow_lan:Boolean(lan.checked)" not in generate_source


def test_remove_key_clears_saved_wildcard_cors_instead_of_dead_ending():
    remove_source = DESKTOP_NETWORK_JS.split("async function removeKey()", 1)[1].split(
        "function focusables()", 1
    )[0]

    assert "const persistedCors=String(status?.cors_origins || '').trim();" in remove_source
    assert "const nextCors=persistedCors === '*' ? '' : persistedCors;" in remove_source
    assert "cors_origins:nextCors" in remove_source
    assert "wildcard CORS cleared" in remove_source
