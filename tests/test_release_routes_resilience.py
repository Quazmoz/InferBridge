from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import release_routes
from app.release_routes import register_release_routes
from app.update_checker import (
    UpdateCache,
    UpdateCheckResult,
    UpdatePreferences,
    UpdateStore,
)


def _app(tmp_path) -> FastAPI:
    app = FastAPI()
    register_release_routes(
        app,
        paths=SimpleNamespace(
            config_dir=tmp_path,
            resource_root=tmp_path,
            portable=False,
        ),
    )
    return app


def _client(tmp_path) -> tuple[TestClient, UpdateStore]:
    return TestClient(_app(tmp_path)), UpdateStore(tmp_path)


def test_release_status_hides_cache_from_previous_channel(tmp_path):
    client, store = _client(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True, channel="beta"))
    store.save_cache(
        UpdateCache(
            channel="stable",
            releases_etag='"stable-etag"',
            last_checked_at=datetime.now(UTC),
            latest_checked_version="0.9.7",
            manifest={"version": "0.9.7"},
        )
    )

    response = client.get("/desktop/release/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_checked_version"] is None
    assert payload["last_update_check_time"] is None
    assert payload["cached_manifest"] is None
    assert payload["check_due"] is True


def test_release_status_keeps_cache_for_selected_channel(tmp_path):
    client, store = _client(tmp_path)
    checked_at = datetime.now(UTC)
    store.save_preferences(UpdatePreferences(enabled=True, channel="beta"))
    store.save_cache(
        UpdateCache(
            channel="beta",
            releases_etag='"beta-etag"',
            last_checked_at=checked_at,
            latest_checked_version="0.9.7b1",
            manifest={"version": "0.9.7b1"},
        )
    )

    response = client.get("/desktop/release/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_checked_version"] == "0.9.7b1"
    assert payload["last_update_check_time"] is not None
    assert payload["cached_manifest"] == {"version": "0.9.7b1"}
    assert payload["check_due"] is False


def test_release_check_offloads_blocking_checker(tmp_path, monkeypatch):
    calls = []

    async def fake_to_thread(function, /, *args, **kwargs):
        calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    monkeypatch.setattr(release_routes.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(release_routes, "_require_local_ui", lambda _request: None)
    monkeypatch.setattr(
        release_routes.UpdateChecker,
        "check",
        lambda _self, *, force=False: UpdateCheckResult(
            status="current",
            checked_at=datetime.now(UTC),
            message="forced" if force else "scheduled",
        ),
    )
    client, _store = _client(tmp_path)

    response = client.post("/desktop/release/check")

    assert response.status_code == 200
    assert response.json()["status"] == "current"
    assert response.json()["message"] == "forced"
    assert len(calls) == 1
    assert calls[0][2] == {"force": True}


def test_release_check_serializes_overlapping_requests(tmp_path, monkeypatch):
    active = 0
    max_active = 0
    counter_lock = threading.Lock()

    def slow_check(_self, *, force=False):
        nonlocal active, max_active
        assert force is True
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.04)
            return UpdateCheckResult(status="current", checked_at=datetime.now(UTC))
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(release_routes, "_require_local_ui", lambda _request: None)
    monkeypatch.setattr(release_routes.UpdateChecker, "check", slow_check)
    app = _app(tmp_path)

    async def run_requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await asyncio.gather(
                client.post("/desktop/release/check"),
                client.post("/desktop/release/check"),
            )

    responses = asyncio.run(run_requests())

    assert [response.status_code for response in responses] == [200, 200]
    assert max_active == 1
