"""Fail-closed network boundary for unauthenticated InferBridge servers."""

from __future__ import annotations

import functools
import ipaddress
import json
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


def _authentication_configured(settings: Any) -> bool:
    configured = [
        item.strip()
        for item in str(getattr(settings, "api_key", "") or "").split(",")
        if item.strip()
    ]
    return bool(configured)


class UnauthenticatedRemoteAccessMiddleware:
    """Reject non-loopback HTTP clients when API authentication is disabled.

    This protects source/CLI deployments as well as users who invoke Uvicorn directly
    with ``--host 0.0.0.0``. The bind socket may exist, but no remote client can obtain
    UI, health, model, or API data until an API key is configured.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        state = getattr(app, "state", None)
        settings = getattr(state, "settings", None)
        if settings is None or _authentication_configured(settings):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = str(
            client[0] if isinstance(client, tuple | list) and client else ""
        ).strip()
        if client_host in _TEST_CLIENTS or host_is_loopback(client_host):
            await self.app(scope, receive, send)
            return

        body = json.dumps(
            {
                "detail": (
                    "Remote access requires an InferBridge API key. Configure OV_LLM_API_KEY "
                    "or use the packaged LAN access settings, then retry."
                )
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store, max-age=0"),
            (b"x-content-type-options", b"nosniff"),
        ]
        await send({"type": "http.response.start", "status": 403, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})


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
