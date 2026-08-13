"""Sanitized local connection metadata and server-side API self-tests."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import secrets
import time
import uuid
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import require_safe_browser_origin

logger = logging.getLogger("ov-llm.connection-hub")
_ROUTE_INSTALL_FLAG = "_ovllm_connection_hub_routes_installed"
_LOOPBACK_CLIENTS = {"127.0.0.1", "localhost", "::1", "testclient"}
_TEST_PROMPT = "InferBridge connection self-test. Reply briefly."
TestStatus = Literal["not_run", "running", "passed", "failed", "skipped"]
TestId = Literal["models", "non_streaming", "streaming", "cancellation", "authentication"]


class ConnectionHubModel(BaseModel):
    id: str
    name: str
    backend: str
    status: str
    loaded: bool
    generation_capable: bool
    busy: bool


class ConnectionAuthState(BaseModel):
    enabled: bool
    required: bool
    label: str
    api_key_placeholder: str


class ConnectionLanState(BaseModel):
    enabled: bool
    classification: Literal["loopback", "lan", "unspecified"]
    label: str
    detail: str
    configured_host: str
    url: str | None = None
    security_attention: bool = False


class ConnectionMetadataResponse(BaseModel):
    runtime_state: Literal["available", "shutting_down"]
    base_url: str
    api_root: str = "/v1"
    listen_host: str
    port: int
    authentication: ConnectionAuthState
    models: list[ConnectionHubModel]
    loaded_model_ids: list[str]
    usable_model_ids: list[str]
    lan: ConnectionLanState


class ConnectionSelfTestRequest(BaseModel):
    model_id: str | None = Field(default=None, min_length=1, max_length=128)


class ConnectionTestResult(BaseModel):
    id: TestId
    label: str
    status: TestStatus
    duration_ms: int | None = None
    detail: str


class ConnectionSelfTestResponse(BaseModel):
    model_id: str | None
    tests: list[ConnectionTestResult]


def _loopback(host: str) -> bool:
    return host.strip().lower().strip("[]") in {"127.0.0.1", "localhost", "::1"}


def _url_host(host: str) -> str:
    clean = host.strip().strip("[]")
    return f"[{clean}]" if ":" in clean else clean


def classify_lan_state(host: str, port: int, *, authentication_enabled: bool) -> ConnectionLanState:
    """Classify binding without enumerating machine interfaces."""
    host = (host or "127.0.0.1").strip()
    if _loopback(host):
        return ConnectionLanState(
            enabled=False,
            classification="loopback",
            label="Local only",
            detail="Other devices cannot connect to this listener.",
            configured_host=host,
        )
    if host in {"0.0.0.0", "::", "[::]"}:
        return ConnectionLanState(
            enabled=True,
            classification="unspecified",
            label="LAN access enabled",
            detail=(
                "The listener accepts available network interfaces. Authentication should be "
                "enabled, and firewall or network rules still determine reachability. Use this "
                "computer's current private address from the trusted client."
            ),
            configured_host=host,
            security_attention=not authentication_enabled,
        )
    return ConnectionLanState(
        enabled=True,
        classification="lan",
        label="LAN access enabled",
        detail=(
            "This listener may be reachable from the local network. Authentication should be "
            "enabled, and firewall or network rules still determine reachability."
        ),
        configured_host=host,
        url=f"http://{_url_host(host)}:{port}/v1",
        security_attention=not authentication_enabled,
    )


def is_generation_capable_backend(backend: str) -> bool:
    value = str(backend or "").strip().lower()
    return "embedding" not in value and value in {
        "openvino-genai",
        "openvino-vlm",
        "mock",
        "mock-vlm",
    }


def _keys(settings: Any) -> list[str]:
    return [
        part.strip()
        for part in str(getattr(settings, "api_key", "") or "").split(",")
        if part.strip()
    ]


def _shutting_down(app: FastAPI, manager: Any) -> bool:
    return bool(getattr(app.state, "shutting_down", False)) or bool(
        getattr(manager, "_model_manager_shutting_down", False)
    )


def _port(request: Request, settings: Any) -> int:
    return int(request.url.port or getattr(settings, "port", 8000))


def _origin(request: Request, settings: Any) -> str:
    host = str(request.url.hostname or "")
    if not _loopback(host):
        configured = str(getattr(settings, "host", "127.0.0.1") or "127.0.0.1")
        host = configured if _loopback(configured) else "127.0.0.1"
    return f"http://{_url_host(host)}:{_port(request, settings)}"


def _result(
    test_id: TestId,
    label: str,
    status: TestStatus,
    detail: str,
    start: float | None = None,
) -> ConnectionTestResult:
    duration = None if start is None else max(0, round((time.perf_counter() - start) * 1000))
    return ConnectionTestResult(
        id=test_id,
        label=label,
        status=status,
        duration_ms=duration,
        detail=detail,
    )


class ConnectionHubService:
    def __init__(
        self,
        *,
        app: FastAPI,
        settings: Any,
        manager: Any,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.manager = manager
        self.transport = transport
        self._test_lock = asyncio.Lock()

    def metadata(self, request: Request) -> ConnectionMetadataResponse:
        port = _port(request, self.settings)
        auth_enabled = bool(_keys(self.settings))
        models: list[ConnectionHubModel] = []
        for model_id, cfg in self.manager.catalog.items():
            loaded = model_id in self.manager.engines
            lock = self.manager.locks.get(model_id)
            entry = self.manager.catalog_entry(model_id)
            models.append(
                ConnectionHubModel(
                    id=model_id,
                    name=str(entry.get("name") or getattr(cfg, "name", model_id)),
                    backend=str(getattr(cfg, "backend", "unknown")),
                    status=str(entry.get("status") or "unknown"),
                    loaded=loaded,
                    generation_capable=is_generation_capable_backend(getattr(cfg, "backend", "")),
                    busy=bool(lock and lock.locked()),
                )
            )
        loaded = [item.id for item in models if item.loaded]
        usable = [item.id for item in models if item.loaded and item.generation_capable]
        auth = ConnectionAuthState(
            enabled=auth_enabled,
            required=auth_enabled,
            label="Authentication required" if auth_enabled else "Authentication disabled",
            api_key_placeholder="YOUR_INFERBRIDGE_API_KEY" if auth_enabled else "not-required",
        )
        return ConnectionMetadataResponse(
            runtime_state=(
                "shutting_down" if _shutting_down(self.app, self.manager) else "available"
            ),
            base_url=f"{_origin(request, self.settings)}/v1",
            listen_host=str(getattr(self.settings, "host", "127.0.0.1")),
            port=port,
            authentication=auth,
            models=models,
            loaded_model_ids=loaded,
            usable_model_ids=usable,
            lan=classify_lan_state(
                str(getattr(self.settings, "host", "127.0.0.1")),
                port,
                authentication_enabled=auth_enabled,
            ),
        )

    def _client(self, request: Request) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_origin(request, self.settings),
            timeout=httpx.Timeout(30.0),
            transport=self.transport,
            trust_env=False,
        )

    def _headers(self, prefix: str, *, auth: bool = True) -> dict[str, str]:
        headers = {"X-Request-ID": f"hub-{prefix}-{uuid.uuid4().hex[:12]}"}
        keys = _keys(self.settings)
        if auth and keys:
            headers["Authorization"] = f"Bearer {keys[0]}"
        return headers

    def _select_model(self, requested: str | None) -> tuple[str | None, str | None]:
        usable = [
            model_id
            for model_id, cfg in self.manager.catalog.items()
            if model_id in self.manager.engines
            and is_generation_capable_backend(getattr(cfg, "backend", ""))
        ]
        if requested:
            cfg = self.manager.catalog.get(requested)
            if cfg is None:
                return None, "The selected model is not registered. Refresh the Connection Hub."
            if requested not in self.manager.engines:
                return None, "The selected model is not loaded. Load it, then rerun the self-test."
            if not is_generation_capable_backend(getattr(cfg, "backend", "")):
                return (
                    None,
                    "The selected model is not generation-capable. Choose a loaded chat model.",
                )
            return requested, None
        if not usable:
            return (
                None,
                "No generation-capable model is loaded. Load a chat model, then rerun the self-test.",
            )
        if len(usable) > 1:
            return None, "Multiple generation-capable models are loaded. Select the model to test."
        return usable[0], None

    def _busy(self, model_id: str) -> str | None:
        if int(getattr(self.manager, "_active_generations", 0) or 0) > 0:
            return "Generation is already active. Wait for it to finish, then rerun the self-test."
        lock = self.manager.locks.get(model_id)
        if lock and lock.locked():
            return (
                "The selected model is busy. Wait for the current request to finish, then rerun "
                "the self-test."
            )
        return None

    async def _models(self, client: httpx.AsyncClient) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            response = await client.get("/v1/models", headers=self._headers("models"))
            if response.status_code != 200:
                return _result(
                    "models",
                    "Model listing",
                    "failed",
                    f"Model listing returned HTTP {response.status_code}.",
                    start,
                )
            body = response.json()
            data = (
                body.get("data")
                if isinstance(body, dict) and body.get("object") == "list"
                else None
            )
            if not isinstance(data, list) or not all(
                isinstance(item, dict) and isinstance(item.get("id"), str) for item in data
            ):
                raise ValueError("invalid model list")
            if not set(self.manager.catalog).issubset({item["id"] for item in data}):
                return _result(
                    "models",
                    "Model listing",
                    "failed",
                    "The API model list is missing one or more registered model IDs.",
                    start,
                )
            return _result(
                "models",
                "Model listing",
                "passed",
                f"The API returned {len(data)} model ID(s) with the expected OpenAI-compatible list structure.",
                start,
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            logger.exception("Connection Hub model-list self-test failed")
            return _result(
                "models",
                "Model listing",
                "failed",
                "The model-list response was unreachable or invalid. See server logs for the request ID.",
                start,
            )

    def _generation_body(self, model_id: str, *, stream: bool, max_tokens: int) -> dict[str, Any]:
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": _TEST_PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "seed": 1,
            "stream": stream,
        }

    async def _non_stream(self, client: httpx.AsyncClient, model_id: str) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            response = await client.post(
                "/v1/chat/completions",
                headers=self._headers("nonstream"),
                json=self._generation_body(model_id, stream=False, max_tokens=2),
            )
            if response.status_code != 200:
                return _result(
                    "non_streaming",
                    "Non-streaming generation",
                    "failed",
                    f"Non-streaming generation returned HTTP {response.status_code}.",
                    start,
                )
            body = response.json()
            choices = body.get("choices") if isinstance(body, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices else None
            if (
                body.get("object") != "chat.completion"
                or body.get("model") != model_id
                or not isinstance(message, dict)
                or "content" not in message
            ):
                raise ValueError("invalid completion")
            return _result(
                "non_streaming",
                "Non-streaming generation",
                "passed",
                "A small synthetic request completed with a valid chat completion response.",
                start,
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, AttributeError):
            logger.exception("Connection Hub non-streaming self-test failed")
            return _result(
                "non_streaming",
                "Non-streaming generation",
                "failed",
                "The generation response was unreachable or invalid. See server logs for the request ID.",
                start,
            )

    async def _stream(self, client: httpx.AsyncClient, model_id: str) -> ConnectionTestResult:
        start = time.perf_counter()
        chunks = 0
        done = False
        try:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers=self._headers("stream"),
                json=self._generation_body(model_id, stream=True, max_tokens=4),
            ) as response:
                if response.status_code != 200:
                    return _result(
                        "streaming",
                        "Streaming generation",
                        "failed",
                        f"Streaming generation returned HTTP {response.status_code}.",
                        start,
                    )
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        raise ValueError("invalid SSE framing")
                    data = line[6:]
                    if data == "[DONE]":
                        done = True
                        break
                    event = json.loads(data)
                    if (
                        event.get("object") != "chat.completion.chunk"
                        or event.get("model") != model_id
                    ):
                        raise ValueError("invalid stream event")
                    chunks += 1
            if chunks < 1 or not done:
                return _result(
                    "streaming",
                    "Streaming generation",
                    "failed",
                    "The streaming connection closed before a complete valid event sequence was received.",
                    start,
                )
            return _result(
                "streaming",
                "Streaming generation",
                "passed",
                f"Received {chunks} valid SSE chunk(s) and a clean stream terminator.",
                start,
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, AttributeError):
            logger.exception("Connection Hub streaming self-test failed")
            return _result(
                "streaming",
                "Streaming generation",
                "failed",
                "Streaming failed before a valid complete event sequence was received. See server logs for the request ID.",
                start,
            )

    async def _wait_released(self, model_id: str) -> bool:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            lock = self.manager.locks.get(model_id)
            active = int(getattr(self.manager, "_active_generations", 0) or 0)
            if not (lock and lock.locked()) and active == 0:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _cancel(self, client: httpx.AsyncClient, model_id: str) -> ConnectionTestResult:
        start = time.perf_counter()
        began = False
        try:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers=self._headers("cancel"),
                json=self._generation_body(model_id, stream=True, max_tokens=64),
            ) as response:
                if response.status_code != 200:
                    return _result(
                        "cancellation",
                        "Cancellation",
                        "failed",
                        f"The cancellable test stream returned HTTP {response.status_code}.",
                        start,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = json.loads(line[6:])
                    if event.get("object") == "chat.completion.chunk":
                        began = True
                        break
            if not began:
                return _result(
                    "cancellation",
                    "Cancellation",
                    "failed",
                    "The test stream ended before generation began, so cancellation could not be verified.",
                    start,
                )
            if not await self._wait_released(model_id):
                return _result(
                    "cancellation",
                    "Cancellation",
                    "failed",
                    "Cancellation timed out while waiting for the generation worker and model lock to release.",
                    start,
                )
            follow = await client.post(
                "/v1/chat/completions",
                headers=self._headers("after-cancel"),
                json=self._generation_body(model_id, stream=False, max_tokens=1),
            )
            body = follow.json() if follow.status_code == 200 else {}
            if follow.status_code != 200 or body.get("object") != "chat.completion":
                return _result(
                    "cancellation",
                    "Cancellation",
                    "failed",
                    "The worker stopped, but the follow-up request did not succeed after cancellation.",
                    start,
                )
            return _result(
                "cancellation",
                "Cancellation",
                "passed",
                "Closed only the self-test stream, confirmed the worker and lock released, and completed a follow-up request.",
                start,
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, AttributeError):
            logger.exception("Connection Hub cancellation self-test failed")
            return _result(
                "cancellation",
                "Cancellation",
                "failed",
                "Cancellation or its follow-up request failed. See server logs for the request ID.",
                start,
            )

    async def _auth(self, client: httpx.AsyncClient) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            if not _keys(self.settings):
                response = await client.get(
                    "/v1/models", headers=self._headers("auth-open", auth=False)
                )
                status: TestStatus = "passed" if response.status_code == 200 else "failed"
                detail = (
                    "Authentication is disabled and the API accepted the expected open-local request."
                    if status == "passed"
                    else f"Authentication is disabled, but the open-local request returned HTTP {response.status_code}."
                )
                return _result("authentication", "Authentication", status, detail, start)
            invalid_headers = {
                "X-Request-ID": self._headers("auth-invalid")["X-Request-ID"],
                "Authorization": f"Bearer invalid-{secrets.token_urlsafe(16)}",
            }
            invalid = await client.get("/v1/models", headers=invalid_headers)
            if invalid.status_code not in {401, 429}:
                return _result(
                    "authentication",
                    "Authentication",
                    "failed",
                    "The API did not reject an intentionally invalid credential as expected.",
                    start,
                )
            valid = await client.get("/v1/models", headers=self._headers("auth-valid"))
            if valid.status_code != 200:
                return _result(
                    "authentication",
                    "Authentication",
                    "failed",
                    "The API rejected the configured server-side credential.",
                    start,
                )
            return _result(
                "authentication",
                "Authentication",
                "passed",
                "Authenticated access succeeded and an invalid credential was rejected as expected.",
                start,
            )
        except httpx.HTTPError:
            logger.exception("Connection Hub authentication self-test failed")
            return _result(
                "authentication",
                "Authentication",
                "failed",
                "Authentication could not be verified through the local API. See server logs for the request ID.",
                start,
            )

    @staticmethod
    def _skip_generation(reason: str) -> list[ConnectionTestResult]:
        return [
            _result("non_streaming", "Non-streaming generation", "skipped", reason),
            _result("streaming", "Streaming generation", "skipped", reason),
            _result("cancellation", "Cancellation", "skipped", reason),
        ]

    async def run_self_test(
        self, request: Request, body: ConnectionSelfTestRequest
    ) -> ConnectionSelfTestResponse:
        model_id, reason = self._select_model(body.model_id)
        if _shutting_down(self.app, self.manager) or self._test_lock.locked():
            detail = (
                "The server is shutting down. Start InferBridge again before running the self-test."
                if _shutting_down(self.app, self.manager)
                else "A Connection Hub self-test is already running. Let it finish before starting another."
            )
            return ConnectionSelfTestResponse(
                model_id=model_id,
                tests=[
                    _result("models", "Model listing", "skipped", detail),
                    *self._skip_generation(detail),
                    _result("authentication", "Authentication", "skipped", detail),
                ],
            )
        async with self._test_lock, self._client(request) as client:
            tests = [await self._models(client)]
            if reason:
                tests.extend(self._skip_generation(reason))
            elif model_id:
                checks = (
                    ("non_streaming", "Non-streaming generation", self._non_stream),
                    ("streaming", "Streaming generation", self._stream),
                    ("cancellation", "Cancellation", self._cancel),
                )
                for test_id, label, check in checks:
                    busy = self._busy(model_id)
                    tests.append(
                        _result(test_id, label, "skipped", busy)
                        if busy
                        else await check(client, model_id)
                    )
            tests.append(await self._auth(client))
            return ConnectionSelfTestResponse(model_id=model_id, tests=tests)


async def _require_local_ui(
    request: Request,
    x_ov_llm_ui: str | None = Header(default=None),
) -> None:
    require_safe_browser_origin(request)
    client = str(request.client.host if request.client else "").strip().lower()
    if client not in _LOOPBACK_CLIENTS or x_ov_llm_ui != "1":
        raise HTTPException(
            status_code=403,
            detail="Connection Hub is available only to the local UI.",
        )


def _service(request: Request) -> ConnectionHubService:
    current = getattr(request.app.state, "connection_hub_service", None)
    if current is not None:
        return current
    settings = getattr(request.app.state, "settings", None)
    manager = getattr(request.app.state, "manager", None)
    if settings is None or manager is None:
        raise HTTPException(status_code=503, detail="Connection Hub is unavailable.")
    current = ConnectionHubService(app=request.app, settings=settings, manager=manager)
    request.app.state.connection_hub_service = current
    return current


def register_connection_hub_routes(app: FastAPI) -> None:
    if getattr(app.state, "connection_hub_routes_registered", False):
        return
    app.state.connection_hub_routes_registered = True
    local_ui = [Depends(_require_local_ui)]

    @app.get(
        "/internal/connection-hub",
        response_model=ConnectionMetadataResponse,
        include_in_schema=False,
        dependencies=local_ui,
    )
    async def connection_hub_metadata(request: Request):
        return _service(request).metadata(request)

    @app.post(
        "/internal/connection-hub/self-test",
        response_model=ConnectionSelfTestResponse,
        include_in_schema=False,
        dependencies=local_ui,
    )
    async def connection_hub_self_test(request: Request, body: ConnectionSelfTestRequest):
        return await _service(request).run_self_test(request, body)


def install_connection_hub_routes_extension() -> None:
    if getattr(FastAPI, _ROUTE_INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_connection_hub(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_connection_hub_routes(self)

    FastAPI.__init__ = init_with_connection_hub  # type: ignore[method-assign]
    setattr(FastAPI, _ROUTE_INSTALL_FLAG, True)


__all__ = [
    "ConnectionHubService",
    "ConnectionSelfTestRequest",
    "classify_lan_state",
    "install_connection_hub_routes_extension",
    "is_generation_capable_backend",
    "register_connection_hub_routes",
]
