from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.connection_hub import (
    ConnectionHubService,
    ConnectionSelfTestRequest,
    classify_lan_state,
    register_connection_hub_routes,
)


class DummyLock:
    def __init__(self, locked: bool = False) -> None:
        self._locked = locked

    def locked(self) -> bool:
        return self._locked


class DummyManager:
    def __init__(self, *, busy: bool = False, active: int = 0, loaded: bool = True) -> None:
        self.catalog = {
            "chat-model": SimpleNamespace(name="Chat Model", backend="openvino-genai"),
            "embed-model": SimpleNamespace(name="Embed Model", backend="openvino-embeddings"),
        }
        self.engines = {"chat-model": SimpleNamespace(model_id="chat-model")} if loaded else {}
        self.locks = {"chat-model": DummyLock(busy)}
        self._active_generations = active

    def catalog_entry(self, model_id: str) -> dict:
        return {
            "id": model_id,
            "name": self.catalog[model_id].name,
            "status": "loaded" if model_id in self.engines else "available",
        }


def settings(*, api_key: str | None = None, host: str = "127.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(api_key=api_key, host=host, port=8123)


def request_for(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/internal/connection-hub",
            "raw_path": b"/internal/connection-hub",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("127.0.0.1", 8123),
            "app": app,
        }
    )


def api_transport(
    *, api_key: str | None = None, fail: bool = False, calls: list[str] | None = None
):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(f"{request.method} {request.url.path}")
        if fail:
            raise httpx.ConnectError(
                r"C:\Users\private\InferBridge\secret.txt", request=request
            )
        auth = request.headers.get("Authorization", "")
        if api_key and auth != f"Bearer {api_key}":
            return httpx.Response(401, json={"detail": "Invalid or missing API key"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "chat-model", "object": "model"},
                        {"id": "embed-model", "object": "model"},
                    ],
                },
            )
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content.decode("utf-8"))
            if body.get("stream"):
                payload = {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "ok"},
                            "finish_reason": None,
                        }
                    ],
                }
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n".encode(),
                )
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def service(
    *, api_key: str | None = None, manager: DummyManager | None = None, transport=None
):
    app = FastAPI()
    app.state.shutting_down = False
    manager = manager or DummyManager()
    cfg = settings(api_key=api_key)
    app.state.settings = cfg
    app.state.manager = manager
    return (
        ConnectionHubService(app=app, settings=cfg, manager=manager, transport=transport),
        app,
    )


def statuses(response) -> dict[str, str]:
    return {item.id: item.status for item in response.tests}


def test_connection_metadata_uses_runtime_settings_and_never_returns_secret():
    hub, app = service(api_key="super-secret-value")
    payload = hub.metadata(request_for(app)).model_dump()

    assert payload["base_url"] == "http://127.0.0.1:8123/v1"
    assert payload["listen_host"] == "127.0.0.1"
    assert payload["port"] == 8123
    assert payload["authentication"]["enabled"] is True
    assert payload["authentication"]["label"] == "Authentication required"
    assert payload["authentication"]["api_key_placeholder"] == "YOUR_INFERBRIDGE_API_KEY"
    assert payload["loaded_model_ids"] == ["chat-model"]
    assert payload["usable_model_ids"] == ["chat-model"]
    assert "super-secret-value" not in json.dumps(payload)


def test_connection_hub_internal_route_is_loopback_ui_only_and_secret_free():
    app = FastAPI()
    app.state.settings = settings(api_key="route-secret")
    app.state.manager = DummyManager()
    app.state.shutting_down = False
    register_connection_hub_routes(app)

    with TestClient(app) as client:
        assert client.get("/internal/connection-hub").status_code == 403
        response = client.get(
            "/internal/connection-hub", headers={"X-OV-LLM-UI": "1"}
        )

    assert response.status_code == 200
    assert "route-secret" not in response.text
    assert response.json()["authentication"]["required"] is True


def test_lan_classification_keeps_loopback_safe_and_flags_unauthenticated_lan():
    local = classify_lan_state("127.0.0.1", 8000, authentication_enabled=False)
    assert local.enabled is False
    assert local.classification == "loopback"
    assert local.security_attention is False

    wildcard = classify_lan_state("0.0.0.0", 8000, authentication_enabled=False)
    assert wildcard.enabled is True
    assert wildcard.classification == "unspecified"
    assert wildcard.url is None
    assert wildcard.security_attention is True

    lan = classify_lan_state("192.168.1.20", 8000, authentication_enabled=True)
    assert lan.enabled is True
    assert lan.url == "http://192.168.1.20:8000/v1"
    assert lan.security_attention is False


def test_self_test_covers_models_generation_stream_cancellation_followup_and_open_auth():
    hub, app = service(transport=api_transport())
    response = asyncio.run(
        hub.run_self_test(
            request_for(app), ConnectionSelfTestRequest(model_id="chat-model")
        )
    )

    assert statuses(response) == {
        "models": "passed",
        "non_streaming": "passed",
        "streaming": "passed",
        "cancellation": "passed",
        "authentication": "passed",
    }
    cancellation = next(item for item in response.tests if item.id == "cancellation")
    assert "follow-up request" in cancellation.detail


def test_authentication_self_test_uses_server_side_key_and_rejects_invalid_key():
    hub, app = service(
        api_key="configured-key", transport=api_transport(api_key="configured-key")
    )
    response = asyncio.run(
        hub.run_self_test(
            request_for(app), ConnectionSelfTestRequest(model_id="chat-model")
        )
    )

    assert statuses(response)["authentication"] == "passed"
    assert "configured-key" not in response.model_dump_json()


def test_no_model_and_busy_model_skip_generation_without_fighting_normal_work():
    no_model_hub, no_model_app = service(
        manager=DummyManager(loaded=False), transport=api_transport()
    )
    no_model = asyncio.run(
        no_model_hub.run_self_test(request_for(no_model_app), ConnectionSelfTestRequest())
    )
    no_model_status = statuses(no_model)
    assert no_model_status["models"] == "passed"
    assert no_model_status["authentication"] == "passed"
    assert no_model_status["non_streaming"] == "skipped"
    assert no_model_status["streaming"] == "skipped"
    assert no_model_status["cancellation"] == "skipped"

    calls: list[str] = []
    busy_hub, busy_app = service(
        manager=DummyManager(busy=True, active=1),
        transport=api_transport(calls=calls),
    )
    busy = asyncio.run(
        busy_hub.run_self_test(
            request_for(busy_app), ConnectionSelfTestRequest(model_id="chat-model")
        )
    )
    busy_status = statuses(busy)
    assert busy_status["non_streaming"] == "skipped"
    assert busy_status["streaming"] == "skipped"
    assert busy_status["cancellation"] == "skipped"
    assert not any(path == "POST /v1/chat/completions" for path in calls)


def test_failures_are_sanitized_before_the_browser_receives_them():
    hub, app = service(transport=api_transport(fail=True))
    response = asyncio.run(
        hub.run_self_test(
            request_for(app), ConnectionSelfTestRequest(model_id="chat-model")
        )
    )
    serialized = response.model_dump_json()

    assert "C:\\Users" not in serialized
    assert "secret.txt" not in serialized
    assert statuses(response)["models"] == "failed"
    assert statuses(response)["authentication"] == "failed"
