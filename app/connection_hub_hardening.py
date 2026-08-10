"""Security boundary for Connection Hub internal browser routes.

The Connection Hub metadata endpoint is intentionally secret-free, but the self-test
can make authenticated requests with InferBridge's configured server credential. This
middleware prevents that endpoint from becoming a localhost confused deputy and pins
its callback origin to the configured listener instead of trusting the request Host
header.
"""

from __future__ import annotations

import functools
import secrets
from typing import Any

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME

_INSTALL_FLAG = "_ovllm_connection_hub_hardening_installed"
_HUB_PATHS = frozenset({"/internal/connection-hub", "/internal/connection-hub/self-test"})
_SELF_TEST_PATH = "/internal/connection-hub/self-test"


def _configured_keys(settings: Any) -> list[str]:
    return [
        value.strip()
        for value in str(getattr(settings, "api_key", "") or "").split(",")
        if value.strip()
    ]


def _header_value(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers", ()):  # ASGI headers are lower-case by convention.
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


def _authorized(settings: Any, scope: dict[str, Any]) -> bool:
    keys = _configured_keys(settings)
    if not keys:
        return True
    authorization = _header_value(scope, b"authorization")
    if not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ")
    return any(secrets.compare_digest(supplied, key) for key in keys)


def _trusted_listener(settings: Any) -> tuple[str, int]:
    """Return a callback target derived only from server configuration."""

    configured = str(getattr(settings, "host", "127.0.0.1") or "127.0.0.1").strip()
    clean = configured.strip("[]").lower()
    if clean in {"::1", "::"}:
        host = "::1"
    elif clean in {"127.0.0.1", "localhost"}:
        host = clean
    else:
        # Wildcard and LAN listeners can accept a local callback through loopback when
        # the Hub itself is reachable locally. Never copy an untrusted request Host.
        host = "127.0.0.1"
    port = int(getattr(settings, "port", 8000))
    return host, port


def _trusted_host_header(host: str, port: int) -> bytes:
    rendered = f"[{host}]" if ":" in host else host
    return f"{rendered}:{port}".encode("ascii")


def _pin_request_origin(scope: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Return a shallow scope copy whose Host/server values cannot be spoofed."""

    host, port = _trusted_listener(settings)
    trusted_host = _trusted_host_header(host, port)
    headers: list[tuple[bytes, bytes]] = []
    host_replaced = False
    for key, value in scope.get("headers", ()):
        if key.lower() == b"host":
            if not host_replaced:
                headers.append((key, trusted_host))
                host_replaced = True
            continue
        headers.append((key, value))
    if not host_replaced:
        headers.append((b"host", trusted_host))

    pinned = dict(scope)
    pinned["headers"] = headers
    pinned["server"] = (host, port)
    return pinned


class ConnectionHubHardeningMiddleware:
    """Protect only Connection Hub internals without buffering normal API streams."""

    def __init__(self, app: Any, *, owner: FastAPI) -> None:
        self.app = app
        self.owner = owner

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") not in _HUB_PATHS:
            await self.app(scope, receive, send)
            return

        settings = getattr(self.owner.state, "settings", None)
        if settings is None:
            await self.app(scope, receive, send)
            return

        if scope.get("path") == _SELF_TEST_PATH and not _authorized(settings, scope):
            response = JSONResponse(
                {"detail": "Invalid or missing API key"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        await self.app(_pin_request_origin(scope, settings), receive, send)


def install_connection_hub_hardening() -> None:
    """Install the Hub security middleware on InferBridge FastAPI instances."""

    if getattr(FastAPI, _INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_connection_hub_hardening(
        self: FastAPI, *args: Any, **kwargs: Any
    ) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            self.add_middleware(ConnectionHubHardeningMiddleware, owner=self)

    FastAPI.__init__ = init_with_connection_hub_hardening  # type: ignore[method-assign]
    setattr(FastAPI, _INSTALL_FLAG, True)


__all__ = [
    "ConnectionHubHardeningMiddleware",
    "install_connection_hub_hardening",
]
