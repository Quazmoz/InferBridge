"""Exercise desktop IPC against real listeners, including hostile proxy/redirect settings."""

from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.desktop_controller import _json_request
from app.desktop_launcher import _http_json


@contextmanager
def _listener(respond):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            respond(self)

        do_POST = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _reply(request, payload):
    request.send_response(200)
    request.send_header("Content-Length", str(len(payload)))
    request.end_headers()
    request.wfile.write(payload)


@pytest.mark.parametrize("request_json", [_http_json, _json_request])
def test_desktop_ipc_ignores_proxy_and_preserves_control_request(monkeypatch, request_json):
    seen = []

    def respond(request):
        seen.append(
            (
                request.path,
                request.headers.get("X-Desktop-Control"),
                json.loads(request.rfile.read(int(request.headers["Content-Length"]))),
            )
        )
        _reply(request, b'{"status":"ok"}')

    # Force proxy discovery to return a dead proxy and disable bypass, even on macOS
    # where OS proxy discovery and bypass can override environment variables.
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:1"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)
    with _listener(respond) as origin:
        assert request_json(
            origin + "/desktop/control/status",
            method="POST",
            headers={"X-Desktop-Control": "private-control-token"},
            body={"action": "test"},
        ) == {"status": "ok"}
    assert seen == [("/desktop/control/status", "private-control-token", {"action": "test"})]


def test_desktop_ipc_refuses_redirect_without_forwarding_control_token():
    forwarded = []

    def destination(request):
        forwarded.append(request.headers.get("X-Desktop-Control"))
        _reply(request, b"{}")

    with _listener(destination) as other:

        def redirect(request):
            request.send_response(302)
            request.send_header("Location", other + "/capture")
            request.end_headers()

        with _listener(redirect) as origin:
            assert _json_request(origin, headers={"X-Desktop-Control": "private"}) is None
    assert forwarded == []


@pytest.mark.parametrize(
    "payload",
    [b"[]", b"not json", b"\xff", b"{}" + b" " * (512 * 1024)],
    ids=["array", "invalid-json", "invalid-utf8", "oversized"],
)
def test_desktop_ipc_rejects_invalid_or_oversized_responses(payload):
    with _listener(lambda request: _reply(request, payload)) as origin:
        assert _http_json(origin) is None


def test_desktop_ipc_tolerates_a_truncated_response():
    def truncated(request):
        request.send_response(200)
        request.send_header("Content-Length", "100")
        request.end_headers()
        request.wfile.write(b'{"status":"ok"}')

    with _listener(truncated) as origin:
        assert _http_json(origin) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://example.com:8000",
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1:invalid",
    ],
)
def test_desktop_ipc_rejects_nonlocal_or_invalid_urls(url):
    assert _http_json(url) is None
