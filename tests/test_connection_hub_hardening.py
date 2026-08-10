from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.connection_hub_hardening import ConnectionHubHardeningMiddleware


def _settings(*, api_key: str | None = None, host: str = "127.0.0.1", port: int = 8123):
    return SimpleNamespace(api_key=api_key, host=host, port=port)


def _app(*, api_key: str | None = None, host: str = "127.0.0.1") -> FastAPI:
    app = FastAPI()
    app.state.settings = _settings(api_key=api_key, host=host)
    app.add_middleware(ConnectionHubHardeningMiddleware, owner=app)

    @app.get("/internal/connection-hub")
    async def metadata_probe(request: Request):
        return {
            "host": request.headers.get("host"),
            "server": list(request.scope.get("server") or ()),
        }

    @app.post("/internal/connection-hub/self-test")
    async def self_test_probe(request: Request):
        return {
            "host": request.headers.get("host"),
            "server": list(request.scope.get("server") or ()),
        }

    @app.get("/unrelated")
    async def unrelated(request: Request):
        return {"host": request.headers.get("host")}

    return app


def test_self_test_requires_normal_api_credential_when_authentication_is_enabled():
    app = _app(api_key="configured-secret")

    with TestClient(app) as client:
        missing = client.post(
            "/internal/connection-hub/self-test",
            headers={"Host": "127.0.0.1:9999"},
        )
        wrong = client.post(
            "/internal/connection-hub/self-test",
            headers={
                "Host": "127.0.0.1:9999",
                "Authorization": "Bearer wrong-secret",
            },
        )
        accepted = client.post(
            "/internal/connection-hub/self-test",
            headers={
                "Host": "127.0.0.1:9999",
                "Authorization": "Bearer configured-secret",
            },
        )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "Invalid or missing API key"}
    assert missing.headers["Cache-Control"] == "no-store"
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["host"] == "127.0.0.1:8123"
    assert accepted.json()["server"] == ["127.0.0.1", 8123]
    assert "configured-secret" not in accepted.text


def test_hub_host_header_is_pinned_to_configured_listener_port():
    app = _app()

    with TestClient(app) as client:
        response = client.get(
            "/internal/connection-hub",
            headers={"Host": "127.0.0.1:54321"},
        )
        unrelated = client.get(
            "/unrelated",
            headers={"Host": "127.0.0.1:54321"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "host": "127.0.0.1:8123",
        "server": ["127.0.0.1", 8123],
    }
    assert unrelated.json()["host"] == "127.0.0.1:54321"


def test_legitimate_localhost_origin_is_preserved_while_spoofed_port_is_removed():
    app = _app(host="127.0.0.1")

    with TestClient(app) as client:
        response = client.get(
            "/internal/connection-hub",
            headers={"Host": "localhost:54321"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "host": "localhost:8123",
        "server": ["localhost", 8123],
    }


def test_untrusted_hostname_is_never_copied_into_hub_callback_origin():
    app = _app(host="0.0.0.0")

    with TestClient(app) as client:
        response = client.get(
            "/internal/connection-hub",
            headers={"Host": "attacker.invalid:54321"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "host": "127.0.0.1:8123",
        "server": ["127.0.0.1", 8123],
    }


def test_authentication_disabled_keeps_local_self_test_available():
    app = _app(api_key=None)

    with TestClient(app) as client:
        response = client.post("/internal/connection-hub/self-test")

    assert response.status_code == 200
    assert response.json()["host"] == "127.0.0.1:8123"


def test_ipv6_loopback_listener_is_preserved_without_trusting_request_port():
    app = _app(host="::1")

    with TestClient(app) as client:
        response = client.get(
            "/internal/connection-hub",
            headers={"Host": "[::1]:9999"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "host": "[::1]:8123",
        "server": ["::1", 8123],
    }
