"""Packaged-browser authentication bridge for server-managed API keys.

The desktop server may require an API key for LAN clients while the bundled browser UI
must not persist that key in localStorage or page source. A random per-process HttpOnly
loopback cookie proves that a request came from the browser session opened against the
local desktop UI; only then is the configured server-side key attached internally.

Remote LAN requests never receive this cookie and must continue presenting the API key.
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any

from app.local_request_security import secret_matches

_COOKIE_NAME = "inferbridge_desktop_ui"
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_PROTECTED_INTERNAL = frozenset({"/internal/connection-hub/self-test"})


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


def _host_header_name(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end > 0 else ""
    if raw.count(":") == 1:
        return raw.rsplit(":", 1)[0]
    return raw.strip("[]")


def _trusted_loopback_scope(scope: dict[str, Any]) -> bool:
    client = scope.get("client")
    client_host = (
        str(client[0] if isinstance(client, (tuple, list)) and client else "").strip("[]").lower()
    )
    return client_host in _LOOPBACK and _host_header_name(_header(scope, b"host")) in _LOOPBACK


def _cookie_matches(scope: dict[str, Any], expected: str) -> bool:
    raw = _header(scope, b"cookie")
    if not raw:
        return False
    try:
        parsed = SimpleCookie()
        parsed.load(raw)
        morsel = parsed.get(_COOKIE_NAME)
        supplied = morsel.value if morsel is not None else ""
    except Exception:
        return False
    return bool(supplied and secret_matches(supplied, expected))


def _protected_path(path: str) -> bool:
    return path.startswith("/v1/") or path in _PROTECTED_INTERNAL


class DesktopBrowserAuthBridgeMiddleware:
    """Authorize the bundled loopback browser without exposing the configured API key."""

    def __init__(self, app: Any, *, api_key: str, ui_token: str) -> None:
        self.app = app
        keys = [item.strip() for item in str(api_key or "").split(",") if item.strip()]
        if not keys:
            raise ValueError("Desktop browser auth requires a configured API key.")
        # The core API supports comma-separated keys. Use one valid configured key rather
        # than forwarding the full configuration string as a single Bearer token.
        self.api_key = keys[0]
        self.ui_token = str(ui_token)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        trusted = _trusted_loopback_scope(scope)
        authorized_browser = trusted and _cookie_matches(scope, self.ui_token)

        forwarded = scope
        if authorized_browser and _protected_path(path):
            headers = [
                (key, value)
                for key, value in scope.get("headers", ())
                if key.lower() != b"authorization"
            ]
            headers.append((b"authorization", f"Bearer {self.api_key}".encode()))
            forwarded = dict(scope)
            forwarded["headers"] = headers

        issue_cookie = (
            trusted and path == "/" and str(scope.get("method") or "GET").upper() in {"GET", "HEAD"}
        )

        async def send_with_cookie(message: dict[str, Any]) -> None:
            if issue_cookie and message.get("type") == "http.response.start":
                headers = list(message.get("headers", ()))
                cookie = (
                    f"{_COOKIE_NAME}={self.ui_token}; Path=/; HttpOnly; SameSite=Strict"
                ).encode("ascii")
                headers.append((b"set-cookie", cookie))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(forwarded, receive, send_with_cookie)


__all__ = ["DesktopBrowserAuthBridgeMiddleware"]
