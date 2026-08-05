from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

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
        manager = client.app.state.manager
        monkeypatch.setattr(
            manager.advisor,
            "hardware_snapshot",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("advisor snapshot reached lightweight endpoint")
            ),
        )
        response = client.get("/v1/models/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert "models" in payload
    assert "memory" not in payload
    assert "disk" not in payload
    assert all("advisor" not in entry for entry in payload["models"]["available"])
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
    assert first.json()["model_advisor"]
    assert any("advisor" in entry for entry in legacy.json()["models"]["available"])
    assert legacy.json()["split_status"]["telemetry_ttl_seconds"] == 5.0
    assert calls == {"gpu": 1, "disk": 1, "devices": 1}


def test_advisor_collectors_run_off_the_event_loop(monkeypatch, tmp_path) -> None:
    threads: dict[str, int] = {}

    def memory():
        threads["event_loop"] = threading.get_ident()
        return {"total_gb": 1.0, "available_gb": 1.0, "used_percent": 0.0}

    def model_advisor(_manager):
        threads["model_advisor"] = threading.get_ident()
        return {}

    def advisor_summary(_manager):
        threads["advisor_summary"] = threading.get_ident()
        return {}

    monkeypatch.setattr(status_split, "memory_stats", memory)
    monkeypatch.setattr(status_split, "cpu_stats", lambda: {})
    monkeypatch.setattr(status_split, "gpu_stats", lambda: None)
    monkeypatch.setattr(
        status_split,
        "disk_stats",
        lambda _path, *, cache_seconds: {
            "models_gb": 0.0,
            "total_gb": 1.0,
            "free_gb": 1.0,
        },
    )
    monkeypatch.setattr(status_split, "_available_devices", lambda: ["CPU"])
    monkeypatch.setattr(status_split, "_model_advisor_snapshot", model_advisor)
    monkeypatch.setattr(status_split, "_advisor_summary_snapshot", advisor_summary)

    with _client(tmp_path) as client:
        response = client.get("/v1/system/telemetry")

    assert response.status_code == 200
    assert response.json()["metrics"]["advisor"] == {}
    assert threads["model_advisor"] != threads["event_loop"]
    assert threads["advisor_summary"] != threads["event_loop"]


def test_model_advisor_snapshot_skips_one_broken_model() -> None:
    manager = SimpleNamespace(catalog={"broken": object(), "healthy": object()})

    def catalog_entry(model_id: str):
        if model_id == "broken":
            raise RuntimeError("corrupt local evidence")
        return {"advisor": {"status": "ready"}}

    manager.catalog_entry = catalog_entry

    assert status_split._model_advisor_snapshot(manager) == {"healthy": {"status": "ready"}}


def test_request_metrics_remain_live_during_telemetry_cache_window(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(status_split, "gpu_stats", lambda: None)
    monkeypatch.setattr(
        status_split,
        "disk_stats",
        lambda _path, *, cache_seconds: {
            "models_gb": 0.0,
            "total_gb": 100.0,
            "free_gb": 90.0,
        },
    )
    monkeypatch.setattr(status_split, "_available_devices", lambda: ["CPU"])

    with _client(tmp_path) as client:
        manager = client.app.state.manager
        model_id = next(iter(manager.catalog))
        manager.record_request(model_id, 10, 5, 0.1)
        first = client.get("/v1/system/telemetry").json()
        manager.record_request(model_id, 20, 7, 0.2)
        second = client.get("/v1/system/telemetry").json()

    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert first["metrics"]["per_model"][model_id]["requests"] == 1
    assert second["metrics"]["per_model"][model_id]["requests"] == 2
    assert second["metrics"]["per_model"][model_id]["prompt_tokens"] == 30


def test_telemetry_refresh_serves_stale_cache_when_collector_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(status_split, "gpu_stats", lambda: {"device": "GPU.0"})
    monkeypatch.setattr(
        status_split,
        "disk_stats",
        lambda _path, *, cache_seconds: {
            "models_gb": 2.0,
            "total_gb": 100.0,
            "free_gb": 70.0,
        },
    )
    monkeypatch.setattr(status_split, "_available_devices", lambda: ["CPU", "GPU.0"])

    with _client(tmp_path) as client:
        initial = client.get("/v1/system/telemetry")
        monkeypatch.setattr(
            status_split,
            "gpu_stats",
            lambda: (_ for _ in ()).throw(RuntimeError("driver unavailable")),
        )
        stale = client.get("/v1/system/telemetry", params={"refresh": "true"})

    assert initial.status_code == 200
    assert stale.status_code == 200
    payload = stale.json()
    assert payload["cache"]["hit"] is True
    assert payload["cache"]["stale"] is True
    assert payload["disk"]["models_gb"] == 2.0


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


def test_event_ids_remain_unique_and_monotonic_under_concurrent_emission(tmp_path) -> None:
    with _client(tmp_path) as client:
        manager = client.app.state.manager
        baseline = manager.recent_events_page()["latest_cursor"]

        def emit(index: int) -> None:
            manager.emit_event("info", f"Concurrent event {index}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(emit, range(100)))

        page = manager.recent_events_page(cursor=0, limit=100)

    ids = [event["id"] for event in page["data"]]
    assert len(ids) == 50  # bounded manager event buffer
    assert ids == sorted(set(ids))
    assert page["latest_cursor"] == baseline + 100
    assert ids[-1] == page["latest_cursor"]


def test_split_routes_use_existing_api_key_policy(tmp_path) -> None:
    with _client(tmp_path, api_key="secret-key") as client:
        denied = client.get("/v1/models/status")
        allowed = client.get(
            "/v1/models/status",
            headers={"Authorization": "Bearer secret-key"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
