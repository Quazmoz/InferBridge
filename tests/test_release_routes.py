"""Desktop release metadata and update-preference routes.

These endpoints only register inside the packaged desktop process, so they are mounted
onto a bare FastAPI application here with a stub paths object. Every route is gated on
the local-UI header, and the settings endpoint persists user preferences, so malformed
input must be rejected rather than faulted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.release_routes import register_release_routes
from app.update_checker import UpdateCache, UpdateStore
from app.version import DATA_SCHEMA_VERSION

LOCAL_UI = {"X-OV-LLM-UI": "1"}


@pytest.fixture
def paths(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(config_dir=config_dir, portable=False, resource_root=tmp_path)


@pytest.fixture
def client(paths):
    app = FastAPI()
    register_release_routes(app, paths=paths)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --- status --------------------------------------------------------------------


def test_status_reports_build_and_schema_without_the_local_ui_header(client) -> None:
    """Read-only release metadata stays available to the packaged UI shell."""

    response = client.get("/desktop/release/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["installation_mode"] == "installed"
    assert payload["data_schema_version"] == DATA_SCHEMA_VERSION
    assert payload["build"]["application_version"]
    # A first run has never checked, so a check is due and nothing is cached.
    assert payload["check_due"] is True
    assert payload["latest_checked_version"] is None
    assert payload["last_update_check_time"] is None
    assert payload["cached_manifest"] is None
    assert payload["update_checks"] == {
        "enabled": False,
        "channel": "stable",
        "skipped_versions": [],
    }


def test_status_reports_portable_installations_distinctly(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app = FastAPI()
    register_release_routes(
        app,
        paths=SimpleNamespace(config_dir=config_dir, portable=True, resource_root=tmp_path),
    )

    with TestClient(app) as portable_client:
        assert portable_client.get("/desktop/release/status").json()["installation_mode"] == (
            "portable"
        )


def test_status_surfaces_a_recent_check_as_not_due(client, paths) -> None:
    store = UpdateStore(paths.config_dir)
    checked_at = datetime.now(UTC) - timedelta(hours=1)
    store.save_cache(
        UpdateCache(
            last_checked_at=checked_at,
            latest_checked_version="9.9.9",
            manifest={"version": "9.9.9"},
        )
    )

    payload = client.get("/desktop/release/status").json()

    assert payload["check_due"] is False
    assert payload["latest_checked_version"] == "9.9.9"
    assert payload["cached_manifest"] == {"version": "9.9.9"}


def test_status_treats_a_corrupt_cache_as_absent(client, paths) -> None:
    (paths.config_dir / "update-cache.json").write_text("{ not json", encoding="utf-8")

    payload = client.get("/desktop/release/status").json()

    assert payload["check_due"] is True
    assert payload["cached_manifest"] is None


# --- local UI gate -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/desktop/release/check", None),
        ("put", "/desktop/release/settings", {"enabled": True}),
    ],
)
def test_mutations_require_the_local_ui_header(client, method, path, body) -> None:
    response = getattr(client, method)(path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "This action requires the local application UI."


def test_a_wrong_local_ui_header_value_is_not_accepted(client) -> None:
    response = client.put(
        "/desktop/release/settings",
        json={"enabled": True},
        headers={"X-OV-LLM-UI": "0"},
    )

    assert response.status_code == 403


# --- settings ------------------------------------------------------------------


def test_valid_settings_round_trip_to_disk(client, paths) -> None:
    response = client.put(
        "/desktop/release/settings",
        json={"enabled": True, "channel": "beta", "skipped_versions": ["1.2.3"]},
        headers=LOCAL_UI,
    )

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "channel": "beta",
        "skipped_versions": ["1.2.3"],
    }
    stored = json.loads((paths.config_dir / "update-settings.json").read_text(encoding="utf-8"))
    assert stored["channel"] == "beta"
    assert UpdateStore(paths.config_dir).load_preferences().enabled is True


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"enabled": True, "skipped_versions": ["not-a-version"]}, id="bad-version"),
        pytest.param({"channel": "experimental"}, id="unknown-channel"),
        pytest.param({"enabled": "yes please"}, id="non-boolean"),
        pytest.param({"enabled": True, "unexpected": 1}, id="extra-field"),
        pytest.param(["enabled"], id="not-an-object"),
        pytest.param({"skipped_versions": [str(n) for n in range(60)]}, id="too-many-skips"),
    ],
)
def test_invalid_settings_are_rejected_as_unprocessable(client, paths, payload) -> None:
    response = client.put("/desktop/release/settings", json=payload, headers=LOCAL_UI)

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid update settings."
    # A rejected write must leave no partial preferences behind.
    assert not (paths.config_dir / "update-settings.json").exists()


def test_a_malformed_json_body_is_a_client_error_not_a_server_fault(client) -> None:
    """Decoding happens inside the validation guard, so a truncated body yields 422."""

    response = client.put(
        "/desktop/release/settings",
        content=b'{"enabled": true',
        headers={**LOCAL_UI, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid update settings."


def test_an_empty_body_is_rejected_without_crashing(client) -> None:
    response = client.put(
        "/desktop/release/settings",
        content=b"",
        headers={**LOCAL_UI, "Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_saving_settings_twice_replaces_rather_than_merges(client, paths) -> None:
    client.put(
        "/desktop/release/settings",
        json={"enabled": True, "channel": "nightly", "skipped_versions": ["1.0.0"]},
        headers=LOCAL_UI,
    )
    second = client.put("/desktop/release/settings", json={"enabled": False}, headers=LOCAL_UI)

    assert second.json() == {"enabled": False, "channel": "stable", "skipped_versions": []}
    assert UpdateStore(paths.config_dir).load_preferences().skipped_versions == []


# --- check ---------------------------------------------------------------------


def test_check_reports_disabled_until_the_user_opts_in(client) -> None:
    response = client.post("/desktop/release/check", headers=LOCAL_UI)

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_check_reports_offline_when_the_release_index_is_unreachable(client, monkeypatch) -> None:
    client.put(
        "/desktop/release/settings",
        json={"enabled": True},
        headers=LOCAL_UI,
    )

    # `UpdateChecker` binds `urllib.request.urlopen` as a default argument at import time,
    # so patching that name would leave the real network call in place. The fetch helper
    # is the boundary the checker actually calls.
    def refuse(**_kwargs):
        raise OSError("no network")

    monkeypatch.setattr("app.update_checker._fetch_release_index", refuse)
    response = client.post("/desktop/release/check", headers=LOCAL_UI)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "offline"
    assert payload["message"] == "Update check unavailable."


def test_check_never_contacts_the_network_while_updates_are_disabled(client, monkeypatch) -> None:
    """Opt-in is the privacy contract: a disabled checker must issue no request."""

    def fail(**_kwargs):
        raise AssertionError("update check performed a network request while disabled")

    monkeypatch.setattr("app.update_checker._fetch_release_index", fail)

    assert client.post("/desktop/release/check", headers=LOCAL_UI).json()["status"] == "disabled"
