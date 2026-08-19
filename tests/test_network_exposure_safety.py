from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.network_exposure_safety import UnauthenticatedRemoteAccessMiddleware, host_is_loopback
from app.server import create_app


def _app(api_key: str | None) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key=api_key)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.options("/health")
    async def health_options():
        return {"status": "options"}

    app.add_middleware(UnauthenticatedRemoteAccessMiddleware)
    return app


async def _request(
    app: FastAPI,
    client_host: str,
    *,
    method: str = "GET",
    path: str = "/health",
    headers: dict[str, str] | None = None,
):
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://inferbridge") as client:
        return await client.request(method, path, headers=headers)


async def _get(
    app: FastAPI,
    client_host: str,
    *,
    path: str = "/health",
    headers: dict[str, str] | None = None,
):
    return await _request(app, client_host, path=path, headers=headers)


def test_loopback_host_detection_is_strict():
    assert host_is_loopback("127.0.0.1") is True
    assert host_is_loopback("127.8.9.10") is True
    assert host_is_loopback("::1") is True
    assert host_is_loopback("[::1]") is True
    assert host_is_loopback("::ffff:127.0.0.1") is True
    assert host_is_loopback("localhost") is True
    assert host_is_loopback("0.0.0.0") is False
    assert host_is_loopback("192.168.1.50") is False
    assert host_is_loopback("example.local") is False


def test_remote_client_is_blocked_when_authentication_is_disabled():
    response = asyncio.run(_get(_app(None), "192.168.1.50"))

    assert response.status_code == 403
    assert "requires an InferBridge API key" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_loopback_client_remains_available_without_api_key():
    response = asyncio.run(_get(_app(None), "127.0.0.1"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_remote_non_api_surface_requires_bearer_when_authentication_is_configured():
    app = _app("configured-secret")

    missing = asyncio.run(_get(app, "192.168.1.50"))
    invalid = asyncio.run(
        _get(
            app,
            "192.168.1.50",
            headers={"Authorization": "Bearer wrong-secret"},
        )
    )
    allowed = asyncio.run(
        _get(
            app,
            "192.168.1.50",
            headers={"Authorization": "Bearer configured-secret"},
        )
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok"}


def test_remote_non_ascii_bearer_fails_closed_without_exception():
    response = asyncio.run(
        _get(
            _app("configured-secret"),
            "192.168.1.50",
            headers={"Authorization": "Bearer sécuret"},
        )
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key"


def test_only_real_cors_preflight_bypasses_outer_bearer_check():
    app = _app("configured-secret")

    plain_options = asyncio.run(
        _request(app, "192.168.1.50", method="OPTIONS", path="/health")
    )
    preflight = asyncio.run(
        _request(
            app,
            "192.168.1.50",
            method="OPTIONS",
            path="/health",
            headers={
                "Origin": "http://client.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    )

    assert plain_options.status_code == 401
    assert preflight.status_code == 200
    assert preflight.json() == {"status": "options"}


def test_actual_inferbridge_app_installs_remote_guard():
    app = create_app(Settings(force_mock=True, api_key=None))

    response = asyncio.run(_get(app, "192.168.1.50"))

    assert response.status_code == 403


def test_actual_inferbridge_remote_health_requires_configured_bearer():
    app = create_app(Settings(force_mock=True, api_key="configured-secret"))

    missing = asyncio.run(_get(app, "192.168.1.50", path="/health/live"))
    allowed = asyncio.run(
        _get(
            app,
            "192.168.1.50",
            path="/health/live",
            headers={"Authorization": "Bearer configured-secret"},
        )
    )

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok"}


def test_remote_v1_calls_keep_existing_route_authentication_contract():
    app = create_app(Settings(force_mock=True, api_key="configured-secret"))

    missing = asyncio.run(_get(app, "192.168.1.50", path="/v1/models"))
    allowed = asyncio.run(
        _get(
            app,
            "192.168.1.50",
            path="/v1/models",
            headers={"Authorization": "Bearer configured-secret"},
        )
    )

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["object"] == "list"


def test_specific_non_loopback_bind_warns_without_api_key():
    warnings = Settings(host="192.168.1.50", api_key=None).validate()

    assert any("Non-loopback requests will be rejected" in warning for warning in warnings)
