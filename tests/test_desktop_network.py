from __future__ import annotations

import os
import socket
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app import desktop_controller, desktop_launcher, desktop_server, paths
from app.config import Settings
from app.desktop_network import (
    DesktopApiKeyStore,
    DesktopNetworkService,
    DesktopNetworkUpdateRequest,
    detect_private_lan_ipv4,
    endpoint_url,
    normalize_cors_origins,
    resolve_desktop_network_settings,
)
from app.desktop_network_ui import DESKTOP_NETWORK_JS
from app.onboarding_state import OnboardingStateStore, migrate_state
from app.release_routes import _require_local_ui as require_release_local_ui

_NETWORK_ENV = ("OV_LLM_HOST", "OV_LLM_API_KEY", "OV_LLM_CORS_ORIGINS")
_STRONG_KEY = "ib_abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def clean_network_env(monkeypatch):
    for name in _NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _store(tmp_path) -> DesktopApiKeyStore:
    return DesktopApiKeyStore(tmp_path / "config")


def test_desktop_network_default_remains_loopback(clean_network_env, tmp_path):
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.host == "127.0.0.1"
    assert resolution.host_source == "default"


def test_explicit_environment_host_is_respected(clean_network_env, tmp_path):
    clean_network_env.setenv("OV_LLM_HOST", "0.0.0.0")
    clean_network_env.setenv("OV_LLM_API_KEY", _STRONG_KEY)
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={"lan_access_enabled": False},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.host == "0.0.0.0"
    assert resolution.host_source == "environment"
    assert resolution.api_key_source == "environment"


def test_persisted_lan_setting_resolves_to_wildcard_with_secure_key(clean_network_env, tmp_path):
    store = _store(tmp_path)
    store.set_key(_STRONG_KEY)
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={"lan_access_enabled": True},
        credential_store=store,
        env=os.environ,
    )
    assert resolution.settings.host == "0.0.0.0"
    assert resolution.api_key == _STRONG_KEY
    assert resolution.api_key_source == "secure_store"


def test_lan_off_overrides_stale_persisted_host_data(clean_network_env, tmp_path):
    state = migrate_state({"schema_version": 1, "lan_access_enabled": False, "host": "0.0.0.0"})
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state=state,
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.host == "127.0.0.1"
    assert "host" not in state


def test_environment_override_has_precedence_over_desktop_setting(clean_network_env, tmp_path):
    clean_network_env.setenv("OV_LLM_HOST", "127.0.0.1")
    store = _store(tmp_path)
    store.set_key(_STRONG_KEY)
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={"lan_access_enabled": True},
        credential_store=store,
        env=os.environ,
    )
    assert resolution.settings.host == "127.0.0.1"
    assert resolution.host_source == "environment"


def test_explicit_environment_lan_without_key_is_security_gated_to_loopback(
    clean_network_env, tmp_path
):
    clean_network_env.setenv("OV_LLM_HOST", "0.0.0.0")
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.host == "127.0.0.1"
    assert resolution.host_source == "security_fallback"
    assert "OV_LLM_HOST" in str(resolution.lan_blocked_reason)


def test_missing_secure_key_falls_back_to_loopback_instead_of_exposing_gui_lan(
    clean_network_env, tmp_path
):
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={"lan_access_enabled": True},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.host == "127.0.0.1"
    assert resolution.host_source == "security_fallback"
    assert resolution.lan_blocked_reason


def test_environment_wildcard_cors_without_key_is_disabled_by_security_gate(
    clean_network_env, tmp_path
):
    clean_network_env.setenv("OV_LLM_CORS_ORIGINS", "*")
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.cors_origins == ""
    assert resolution.cors_source == "security_fallback"
    assert "Wildcard CORS" in str(resolution.cors_blocked_reason)


def test_invalid_environment_cors_is_not_applied_in_packaged_mode(clean_network_env, tmp_path):
    clean_network_env.setenv("OV_LLM_CORS_ORIGINS", "*,http://192.168.1.50:3000")
    resolution = resolve_desktop_network_settings(
        Settings.from_env(),
        state={},
        credential_store=_store(tmp_path),
        env=os.environ,
    )
    assert resolution.settings.cors_origins == ""
    assert resolution.cors_source == "security_fallback"
    assert "OV_LLM_CORS_ORIGINS" in str(resolution.cors_blocked_reason)


def test_cors_normalization_and_wildcard_rules():
    assert (
        normalize_cors_origins("http://192.168.1.50:3000, https://example.test/")
        == "http://192.168.1.50:3000,https://example.test"
    )
    assert normalize_cors_origins("*") == "*"
    with pytest.raises(ValueError, match="Wildcard CORS"):
        normalize_cors_origins("*,http://192.168.1.50:3000")
    with pytest.raises(ValueError, match="cannot contain URL paths"):
        normalize_cors_origins("https://example.test/app")


def test_lan_ip_detection_filters_down_loopback_and_link_local_interfaces():
    addresses = {
        "Ethernet": [
            SimpleNamespace(family=socket.AF_INET, address="192.168.1.20"),
            SimpleNamespace(family=socket.AF_INET, address="169.254.10.4"),
        ],
        "VPN": [SimpleNamespace(family=socket.AF_INET, address="10.8.0.2")],
        "Down": [SimpleNamespace(family=socket.AF_INET, address="172.16.0.4")],
    }
    stats = {
        "Ethernet": SimpleNamespace(isup=True),
        "VPN": SimpleNamespace(isup=True),
        "Down": SimpleNamespace(isup=False),
    }
    assert detect_private_lan_ipv4(
        interface_addresses=addresses,
        interface_stats=stats,
        primary_address="10.8.0.2",
    ) == ("10.8.0.2", "192.168.1.20")
    assert (
        detect_private_lan_ipv4(interface_addresses={}, interface_stats={}, primary_address="")
        == ()
    )


def test_endpoint_display_never_uses_wildcard_bind_address():
    assert endpoint_url("127.0.0.1", 8123) == "http://127.0.0.1:8123/v1"
    assert endpoint_url("192.168.1.20", 8123) == "http://192.168.1.20:8123/v1"
    with pytest.raises(ValueError, match="not a client destination"):
        endpoint_url("0.0.0.0", 8123)
    with pytest.raises(ValueError, match="not a client destination"):
        endpoint_url("::", 8123)


def test_api_key_gating_and_restart_pending_state(clean_network_env, tmp_path):
    state_store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    store = _store(tmp_path)
    base = Settings.from_env().replace(port=8123)
    active = resolve_desktop_network_settings(
        base, state=state_store.load().state, credential_store=store, env=os.environ
    )
    service = DesktopNetworkService(
        active_resolution=active,
        base_settings=base,
        paths=SimpleNamespace(portable=False),
        state_store=state_store,
        credential_store=store,
        endpoint_port=8123,
        env=os.environ,
    )
    with pytest.raises(ValueError, match="requires an API key"):
        service.update(DesktopNetworkUpdateRequest(allow_lan=True))

    response = service.update(DesktopNetworkUpdateRequest(allow_lan=True, generate_api_key=True))
    assert response.generated_api_key
    assert response.status.api_key_configured is True
    assert response.status.restart_required is True
    assert state_store.load().state["lan_access_enabled"] is True
    assert response.generated_api_key not in state_store.path.read_text(encoding="utf-8")


def test_wildcard_cors_requires_key_and_explicit_confirmation(clean_network_env, tmp_path):
    state_store = OnboardingStateStore(tmp_path / "state.json")
    store = _store(tmp_path)
    base = Settings.from_env().replace(port=8123)
    active = resolve_desktop_network_settings(
        base, state=state_store.load().state, credential_store=store, env=os.environ
    )
    service = DesktopNetworkService(
        active_resolution=active,
        base_settings=base,
        paths=SimpleNamespace(portable=False),
        state_store=state_store,
        credential_store=store,
        endpoint_port=8123,
        env=os.environ,
    )
    with pytest.raises(ValueError, match="requires an API key"):
        service.update(DesktopNetworkUpdateRequest(allow_lan=False, cors_origins="*"))
    store.set_key(_STRONG_KEY)
    with pytest.raises(ValueError, match="Confirm"):
        service.update(DesktopNetworkUpdateRequest(allow_lan=False, cors_origins="*"))
    result = service.update(
        DesktopNetworkUpdateRequest(
            allow_lan=False,
            cors_origins="*",
            acknowledge_wildcard_cors=True,
        )
    )
    assert result.status.wildcard_cors is True
    assert any("Wildcard CORS" in warning for warning in result.status.warnings)


def test_packaged_browser_auth_is_session_only_and_never_persisted_to_localstorage():
    assert "sessionStorage.setItem(SESSION_API_KEY" in DESKTOP_NETWORK_JS
    assert "localStorage.removeItem(LEGACY_API_KEY)" in DESKTOP_NETWORK_JS
    assert "localStorage.setItem(LEGACY_API_KEY" not in DESKTOP_NETWORK_JS
    assert "desktopSessionAuth" in DESKTOP_NETWORK_JS
    assert "nextHeaders.set('Authorization'" in DESKTOP_NETWORK_JS
    assert _STRONG_KEY not in DESKTOP_NETWORK_JS


def _request(*, client_host: str, host: str, origin: str | None = None) -> Request:
    headers = [(b"host", host.encode()), (b"x-ov-llm-ui", b"1")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/desktop/release/check",
            "raw_path": b"/desktop/release/check",
            "query_string": b"",
            "headers": headers,
            "client": (client_host, 50000),
            "server": (host.split(":")[0], 8123),
        }
    )


def test_release_mutations_remain_loopback_only_after_lan_support():
    require_release_local_ui(
        _request(
            client_host="127.0.0.1",
            host="127.0.0.1:8123",
            origin="http://127.0.0.1:8123",
        )
    )
    with pytest.raises(HTTPException) as remote:
        require_release_local_ui(
            _request(
                client_host="192.168.1.50",
                host="192.168.1.20:8123",
                origin="http://192.168.1.20:8123",
            )
        )
    assert remote.value.status_code == 403


def test_uvicorn_uses_resolved_desktop_host(monkeypatch):
    captured = {}
    fake_app = FastAPI()
    fake_app.state.settings = SimpleNamespace(host="0.0.0.0")

    class FakeConfig:
        def __init__(self, app, **kwargs):
            captured.update(kwargs)
            self.app = app

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.started = True

        def run(self):
            return None

    monkeypatch.setattr(desktop_server, "create_desktop_app", lambda **_kwargs: fake_app)
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(Config=FakeConfig, Server=FakeServer)
    )

    assert desktop_server.run_server(port=8123, instance_nonce="n", control_token="c") == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8123


def test_desktop_port_fallback_reserves_listener_compatible_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        occupied = listener.getsockname()[1]
        selected = desktop_controller.choose_available_listener_port(occupied)
    assert selected != occupied
    assert 1 <= selected <= 65535


def test_internal_launcher_health_probes_remain_loopback(monkeypatch):
    metadata = desktop_launcher.InstanceMetadata(
        pid=os.getpid(), port=8123, nonce="nonce", executable="InferBridge.exe", started_at="now"
    )
    seen = []

    def fake_http(url, **_kwargs):
        seen.append(url)
        if url.endswith("/desktop/instance"):
            return {"instance_nonce": "nonce"}
        if url.endswith("/health/live"):
            return {"status": "ok"}
        if url.endswith("/health/ready"):
            return {"status": "ready"}
        return None

    monkeypatch.setattr(desktop_launcher, "_http_json", fake_http)
    assert desktop_launcher.wait_for_readiness(metadata, timeout=1.0, is_alive=lambda: True)
    assert seen
    assert all(url.startswith("http://127.0.0.1:8123/") for url in seen)
    assert not any("0.0.0.0" in url for url in seen)


def test_installed_mode_keeps_network_credentials_under_local_app_data(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    resolved = paths.resolve_runtime_paths(
        portable=False,
        desktop=True,
        env={"LOCALAPPDATA": str(local_app_data)},
    )
    store = DesktopApiKeyStore(resolved.config_dir)
    assert resolved.data_root == (local_app_data / "InferBridge").resolve()
    assert store.key_path == resolved.config_dir / "api-key.dpapi"
    assert resolved.onboarding_file.is_relative_to(resolved.data_root)


def test_portable_mode_keeps_network_credentials_under_portable_data(monkeypatch, tmp_path):
    portable_root = tmp_path / "InferBridgePortable"
    monkeypatch.setattr(paths, "executable_dir", lambda: portable_root)
    resolved = paths.resolve_runtime_paths(portable=True, desktop=True, env={})
    store = DesktopApiKeyStore(resolved.config_dir)
    assert resolved.data_root == (portable_root / "data").resolve()
    assert store.key_path == resolved.config_dir / "api-key.dpapi"
    assert resolved.onboarding_file.is_relative_to(resolved.data_root)
