from __future__ import annotations

from fastapi.testclient import TestClient

from app import status_split
from app.config import BASE_DIR, Settings
from app.server import create_app


def _client(tmp_path, *, api_key: str | None = None) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        api_key=api_key,
        force_mock=True,
    )
    return TestClient(create_app(settings))


def test_model_status_does_not_collect_expensive_telemetry(monkeypatch, tmp_path) -> None:
    calls = {"gpu": 0, "disk": 0, "devices": 0}

    def gpu():
        calls["gpu"] += 1
        return None

    def disk(_path, *, cache_seconds):
        calls["disk"] += 1
        assert cache_seconds == 5.0
        return {"models_gb": 0.0, "total_gb": 10.0, "free_gb": 9.0}

    def devices():
        calls["devices"] += 1
        return ["CPU"]

    monkeypatch.setattr(status_split, "gpu_stats", gpu)
    monkeypatch.setattr(status_split, "disk_stats", disk)
    monkeypatch.setattr(status_split, "_available_devices", devices)

    with _client(tmp_path) as client:
        response = client.get("/v1/models/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert "models" in payload
    assert "memory" not in payload
    assert "disk" not in payload
    assert calls == {"gpu": 0, "disk": 0, "devices": 0}


def test_telemetry_requests_share_five_second_cache(monkeypatch, tmp_path) -> None:
    calls = {"gpu": 0, "disk": 0, "devices": 0}

    def gpu():
        calls["gpu"] += 1
        return {"device": "GPU.0"}

    def disk(_path, *, cache_seconds):
        calls["disk"] += 1
        assert cache_seconds == 5.0
        return {"models_gb": 1.5, "total_gb": 100.0, "free_gb": 80.0}

    def devices():
        calls["devices"] += 1
        return ["CPU", "GPU.0"]

    monkeypatch.setattr(status_split, "gpu_stats", gpu)
    monkeypatch.setattr(status_split, "disk_stats", disk)
    monkeypatch.setattr(status_split, "_available_devices", devices)

    with _client(tmp_path) as client:
        first = client.get("/v1/system/telemetry")
        second = client.get("/v1/system/telemetry")
        legacy = client.get("/v1/system/status")

    assert first.status_code == 200
    assert second.status_code == 200
    assert legacy.status_code == 200
    assert first.json()["cache"]["hit"] is False
    assert second.json()["cache"]["hit"] is True
    assert legacy.json()["cache"]["hit"] is True
    assert legacy.json()["split_status"]["telemetry_ttl_seconds"] == 5.0
    assert calls == {"gpu": 1, "disk": 1, "devices": 1}


def test_event_cursor_returns_only_new_events_and_detects_restart(tmp_path) -> None:
    with _client(tmp_path) as client:
        manager = client.app.state.manager
        manager.emit_event("info", "First event")
        manager.emit_event("warning", "Second event")

        initial = client.get("/v1/events", params={"cursor": 0, "limit": 50}).json()
        cursor = initial["next_cursor"]
        manager.emit_event("error", "Third event")
        incremental = client.get("/v1/events", params={"cursor": cursor, "limit": 50}).json()
        restarted = client.get("/v1/events", params={"cursor": 999999, "limit": 50}).json()

    assert [event["message"] for event in initial["data"]][-2:] == [
        "First event",
        "Second event",
    ]
    assert [event["message"] for event in incremental["data"]] == ["Third event"]
    assert incremental["next_cursor"] > cursor
    assert restarted["reset_required"] is True
    assert restarted["next_cursor"] == restarted["latest_cursor"]


def test_split_routes_use_existing_api_key_policy(tmp_path) -> None:
    with _client(tmp_path, api_key="secret-key") as client:
        denied = client.get("/v1/models/status")
        allowed = client.get(
            "/v1/models/status",
            headers={"Authorization": "Bearer secret-key"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
