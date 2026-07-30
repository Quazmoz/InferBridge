from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.rate_limit import RateLimitMiddleware


def test_rate_limiting_middleware():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=3)

    @app.get("/v1/test")
    def test_endpoint():
        return {"ok": True}

    @app.get("/other")
    def other_endpoint():
        return {"ok": True}

    client = TestClient(app)

    # Non-v1 path should be exempt from rate limiting
    for _ in range(5):
        resp = client.get("/other")
        assert resp.status_code == 200

    # /v1/ path should allow exactly 3 requests within the window
    for _ in range(3):
        resp = client.get("/v1/test")
        assert resp.status_code == 200

    # 4th request should get 429
    resp = client.get("/v1/test")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert resp.json()["detail"].startswith("Rate limit exceeded")


def test_rate_limit_disabled():
    app = FastAPI()
    # 0 disables rate limit
    app.add_middleware(RateLimitMiddleware, requests_per_minute=0)

    @app.get("/v1/test")
    def test_endpoint():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(10):
        resp = client.get("/v1/test")
        assert resp.status_code == 200


def test_cors_preflight_does_not_consume_api_quota():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=1)

    @app.options("/v1/test")
    def preflight():
        return {"ok": True}

    @app.get("/v1/test")
    def test_endpoint():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(3):
        assert client.options("/v1/test").status_code == 200

    assert client.get("/v1/test").status_code == 200
    assert client.get("/v1/test").status_code == 429


def test_streaming_response_contract_is_preserved():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=2)

    @app.get("/v1/stream")
    def stream_endpoint():
        return StreamingResponse(iter([b"one", b"-two"]), media_type="text/plain")

    client = TestClient(app)
    response = client.get("/v1/stream")
    assert response.status_code == 200
    assert response.text == "one-two"
    assert client.get("/v1/stream").status_code == 200
    assert client.get("/v1/stream").status_code == 429


def test_stale_clients_are_cleaned_during_recording():
    middleware = RateLimitMiddleware(lambda scope, receive, send: None, requests_per_minute=2)
    middleware._hits["stale"].append(1.0)
    middleware._last_cleanup = 1.0

    assert middleware._record_request("active", 62.0) is None
    assert "stale" not in middleware._hits
    assert list(middleware._hits["active"]) == [62.0]
