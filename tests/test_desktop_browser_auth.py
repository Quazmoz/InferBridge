from __future__ import annotations

import asyncio

from app.desktop_browser_auth import DesktopBrowserAuthBridgeMiddleware
from app.desktop_browser_auth_ui import DESKTOP_BROWSER_AUTH_JS


def _scope(
    *,
    path: str,
    method: str = "GET",
    client: str = "127.0.0.1",
    host: str = "127.0.0.1:8123",
    cookie: str = "",
    authorization: str = "",
):
    headers = [(b"host", host.encode("ascii"))]
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if authorization:
        headers.append((b"authorization", authorization.encode("ascii")))
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": (client, 50000),
        "server": ("127.0.0.1", 8123),
    }


def _run(scope):
    captured = {"request_headers": {}, "response_headers": []}

    async def downstream(current, _receive, send):
        captured["request_headers"] = dict(current.get("headers", ()))
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = DesktopBrowserAuthBridgeMiddleware(
        downstream,
        api_key="server-api-key-that-must-stay-private",
        ui_token="random-desktop-ui-token",
    )

    async def execute():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                captured["response_headers"] = list(message.get("headers", ()))

        await middleware(scope, receive, send)

    asyncio.run(execute())
    return captured


def test_loopback_document_get_issues_httponly_session_cookie_without_api_key():
    captured = _run(_scope(path="/"))
    cookies = [
        value.decode("ascii")
        for key, value in captured["response_headers"]
        if key.lower() == b"set-cookie"
    ]

    assert len(cookies) == 1
    assert "inferbridge_desktop_ui=random-desktop-ui-token" in cookies[0]
    assert "HttpOnly" in cookies[0]
    assert "SameSite=Strict" in cookies[0]
    assert "server-api-key-that-must-stay-private" not in cookies[0]


def test_valid_loopback_ui_cookie_bridges_server_managed_key_to_v1_request():
    captured = _run(
        _scope(
            path="/v1/desktop/operations/restart-server",
            method="POST",
            cookie="inferbridge_desktop_ui=random-desktop-ui-token",
        )
    )

    assert (
        captured["request_headers"][b"authorization"]
        == b"Bearer server-api-key-that-must-stay-private"
    )


def test_valid_loopback_ui_cookie_replaces_stale_browser_authorization():
    captured = _run(
        _scope(
            path="/internal/connection-hub/self-test",
            method="POST",
            cookie="inferbridge_desktop_ui=random-desktop-ui-token",
            authorization="Bearer stale-browser-value",
        )
    )

    assert (
        captured["request_headers"][b"authorization"]
        == b"Bearer server-api-key-that-must-stay-private"
    )


def test_remote_lan_client_never_gets_desktop_cookie_auth_bridge():
    captured = _run(
        _scope(
            path="/v1/models",
            client="192.168.1.50",
            host="192.168.1.20:8123",
            cookie="inferbridge_desktop_ui=random-desktop-ui-token",
        )
    )

    assert b"authorization" not in captured["request_headers"]


def test_loopback_sdk_without_ui_cookie_keeps_normal_api_key_contract():
    captured = _run(
        _scope(
            path="/v1/models",
            authorization="Bearer explicit-sdk-key",
        )
    )

    assert captured["request_headers"][b"authorization"] == b"Bearer explicit-sdk-key"


def test_cookie_bridge_does_not_attach_api_key_to_unprotected_routes():
    captured = _run(
        _scope(
            path="/health/live",
            cookie="inferbridge_desktop_ui=random-desktop-ui-token",
        )
    )

    assert b"authorization" not in captured["request_headers"]


def test_packaged_auth_ui_masks_managed_key_without_persisting_secret():
    assert "localStorage.removeItem(LEGACY_KEY)" in DESKTOP_BROWSER_AUTH_JS
    assert "localStorage.setItem" not in DESKTOP_BROWSER_AUTH_JS
    assert "sessionStorage.removeItem(SESSION_KEY)" in DESKTOP_BROWSER_AUTH_JS
    assert "field.value = MASK" in DESKTOP_BROWSER_AUTH_JS
    assert "server-api-key" not in DESKTOP_BROWSER_AUTH_JS


def test_comma_separated_server_keys_use_one_valid_bearer_token():
    captured = {"headers": {}}

    async def downstream(current, _receive, send):
        captured["headers"] = dict(current.get("headers", ()))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = DesktopBrowserAuthBridgeMiddleware(
        downstream,
        api_key="first-valid-key, second-valid-key",
        ui_token="random-desktop-ui-token",
    )

    async def execute():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            return None

        await middleware(
            _scope(
                path="/v1/models",
                cookie="inferbridge_desktop_ui=random-desktop-ui-token",
            ),
            receive,
            send,
        )

    asyncio.run(execute())
    assert captured["headers"][b"authorization"] == b"Bearer first-valid-key"
