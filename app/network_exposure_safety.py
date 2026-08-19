"""Fail-closed network boundary for InferBridge LAN exposure."""

from __future__ import annotations

import functools
import ipaddress
import json
import secrets
from typing import Any

from fastapi import FastAPI

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME

_INSTALL_FLAG = "_inferbridge_network_exposure_safety_installed"
_TEST_CLIENTS = frozenset({"testclient"})


def host_is_loopback(value: Any) -> bool:
    host = str(value or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "ip6-localhost"}:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _configured_keys(settings: Any) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(getattr(settings, "api_key", "") or "").split(",")
        if item.strip()
    )


def _authorization_header(scope: dict[str, Any]) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            try:
                return value.decode("latin-1")
            except UnicodeError:
                return ""
    return ""


def _bearer_is_valid(scope: dict[str, Any], configured: tuple[str, ...]) -> bool:
    authorization = _authorization_header(scope)
    if not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ")
    return any(secrets.compare_digest(supplied, key) for key in configured)


async def _send_error(
    send: Any,
    *,
    status: int,
    detail: str,
    authenticate: bool = False,
) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store, max-age=0"),
        (b"x-content-type-options", b"nosniff"),
    ]
    if authenticate:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class UnauthenticatedRemoteAccessMiddleware:
    """Keep all non-loopback InferBridge HTTP exposure authenticated.

    Loopback UI, health probes, tray traffic, and local clients remain unchanged. A
    source/CLI or direct-Uvicorn wildcard bind with no API key rejects every remote HTTP
    client. When keys are configured, existing ``/v1`` route dependencies remain the
    authoritative API authentication path so their throttling/error behavior is
    preserved, while remote non-API surfaces such as ``/``, UI assets, and ``/health``
    require the same bearer key at this outer boundary.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = str(
            client[0] if isinstance(client, tuple | list) and client else ""
        ).strip()
        if client_host in _TEST_CLIENTS or host_is_loopback(client_host):
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        state = getattr(app, "state", None)
        settings = getattr(state, "settings", None)
        configured = _configured_keys(settings) if settings is not None else ()
        if not configured:
            await _send_error(
                send,
                status=403,
                detail=(
                    "Remote access requires an InferBridge API key. Configure OV_LLM_API_KEY "
                    "or use the packaged LAN access settings, then retry."
                ),
            )
            return

        path = str(scope.get("path") or "")
        if path == "/v1" or path.startswith("/v1/"):
            # Route-level auth intentionally remains authoritative for API calls. It also
            # owns repeated-failure throttling and the established 401 response contract.
            await self.app(scope, receive, send)
            return

        if str(scope.get("method") or "").upper() == "OPTIONS":
            # Let the CORS middleware answer preflight. No protected resource body is
            # exposed by an OPTIONS response.
            await self.app(scope, receive, send)
            return

        if not _bearer_is_valid(scope, configured):
            await _send_error(
                send,
                status=401,
                detail="Invalid or missing API key",
                authenticate=True,
            )
            return

        await self.app(scope, receive, send)


def install_network_exposure_safety() -> None:
    if getattr(FastAPI, _INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_network_guard(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            self.add_middleware(UnauthenticatedRemoteAccessMiddleware)

    FastAPI.__init__ = init_with_network_guard  # type: ignore[method-assign]
    setattr(FastAPI, _INSTALL_FLAG, True)


__all__ = [
    "UnauthenticatedRemoteAccessMiddleware",
    "host_is_loopback",
    "install_network_exposure_safety",
]
