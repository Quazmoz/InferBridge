from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.network_exposure_safety import UnauthenticatedRemoteAccessMiddleware, host_is_loopback


def _app(api_key: str | None) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key=api_key)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(UnauthenticatedRemoteAccessMiddleware)
    return app


async def _get(app: FastAPI, client_host: str):
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://inferbridge") as client:
        return await client.get("/health")


def test_loopback_host_detection_is_strict():
    assert host_is_loopback("127.0.0.1") is True
    assert host_is_loopback("127.8.9.10") is True
    assert host_is_loopback("::1") is True
    assert host_is_loopback("[::1]") is True
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


def test_remote_client_is_allowed_to_reach_routes_when_authentication_is_configured():
    response = asyncio.run(_get(_app("configured-secret"), "192.168.1.50"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
