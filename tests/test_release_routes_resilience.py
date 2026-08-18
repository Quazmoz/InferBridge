from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.release_routes import register_release_routes
from app.update_checker import UpdateCache, UpdatePreferences, UpdateStore


def _client(tmp_path) -> tuple[TestClient, UpdateStore]:
    store = UpdateStore(tmp_path)
    app = FastAPI()
    register_release_routes(
        app,
        paths=SimpleNamespace(
            config_dir=tmp_path,
            resource_root=tmp_path,
            portable=False,
        ),
    )
    return TestClient(app), store


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
