from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.desktop_operations_routes import register_desktop_operations_routes
from app.onboarding_routes import register_onboarding_routes

_API_KEY = "ib_test_key_that_is_long_enough_12345"


def _desktop_status() -> dict:
    return {
        "application_version": "test",
        "api_contract_version": "1",
        "installation_mode": "installed",
        "controller_available": True,
        "server_port": 8123,
        "live": True,
        "ready": True,
        "server_status": "ready",
        "active_model": None,
        "models": [],
        "preparation": None,
        "events": [],
        "benchmark": None,
        "benchmark_running": False,
        "api_key_configured": True,
        "start_with_windows": False,
        "data_directory": "<redacted>",
        "last_diagnostics_export": None,
        "hardware_fingerprint": "fingerprint",
        "npu_readiness": None,
        "mock": True,
        "warning": None,
        "error": None,
    }


def test_onboarding_reads_require_configured_api_key():
    service = SimpleNamespace(status=lambda: {"completed": False})
    app = FastAPI()
    register_onboarding_routes(
        app,
        service=service,
        settings=SimpleNamespace(api_key=_API_KEY),
    )

    with TestClient(app) as client:
        missing = client.get("/v1/onboarding/status")
        invalid = client.get(
            "/v1/onboarding/status",
            headers={"Authorization": "Bearer wrong-key"},
        )
        allowed = client.get(
            "/v1/onboarding/status",
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["completed"] is False


def test_onboarding_reads_remain_open_when_authentication_is_disabled():
    service = SimpleNamespace(status=lambda: {"completed": False})
    app = FastAPI()
    register_onboarding_routes(
        app,
        service=service,
        settings=SimpleNamespace(api_key=None),
    )

    response = TestClient(app).get("/v1/onboarding/status")

    assert response.status_code == 200


def test_desktop_operations_status_requires_configured_api_key():
    status = SimpleNamespace(to_dict=_desktop_status)
    service = SimpleNamespace(
        endpoint_port=8123,
        manager=SimpleNamespace(),
        status=lambda: status,
    )
    app = FastAPI()
    register_desktop_operations_routes(
        app,
        service=service,
        settings=SimpleNamespace(api_key=_API_KEY),
        instance_nonce="nonce",
        control_token="control-token",
    )

    with TestClient(app) as client:
        missing = client.get("/v1/desktop/operations/status")
        invalid = client.get(
            "/v1/desktop/operations/status",
            headers={"Authorization": "Bearer wrong-key"},
        )
        allowed = client.get(
            "/v1/desktop/operations/status",
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["hardware_fingerprint"] == "fingerprint"


def test_loopback_control_plane_keeps_separate_control_token_contract():
    status = SimpleNamespace(to_dict=_desktop_status)
    service = SimpleNamespace(
        endpoint_port=8123,
        manager=SimpleNamespace(),
        status=lambda: status,
    )
    app = FastAPI()
    register_desktop_operations_routes(
        app,
        service=service,
        settings=SimpleNamespace(api_key=_API_KEY),
        instance_nonce="nonce",
        control_token="control-token",
    )

    response = TestClient(app).get(
        "/desktop/control/status",
        headers={"X-Desktop-Control": "control-token"},
    )

    assert response.status_code == 200
