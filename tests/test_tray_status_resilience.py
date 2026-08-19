from __future__ import annotations

import math

from app.tray_state import TrayPhase, snapshot_from_status


def test_non_list_events_do_not_break_tray_snapshot():
    snapshot = snapshot_from_status(
        {
            "live": True,
            "ready": True,
            "events": 123,
        },
        port=8123,
        process_running=True,
    )

    assert snapshot.phase is TrayPhase.READY
    assert snapshot.recent_events == ()


def test_recent_events_are_bounded_and_mapping_only():
    events = [{"id": index} for index in range(60)] + ["bad", 7]
    snapshot = snapshot_from_status(
        {"live": True, "ready": True, "events": events},
        port=8123,
        process_running=True,
    )

    assert len(snapshot.recent_events) == 48
    assert snapshot.recent_events[0]["id"] == 12
    assert snapshot.recent_events[-1]["id"] == 59


def test_truthy_strings_do_not_enable_server_actions():
    snapshot = snapshot_from_status(
        {
            "live": "false",
            "ready": "false",
            "benchmark_running": "false",
            "api_key_configured": "true",
        },
        port=8123,
        process_running=True,
    )

    assert snapshot.live is False
    assert snapshot.ready is False
    assert snapshot.benchmark_running is False
    assert snapshot.api_key_configured is False
    assert snapshot.phase is TrayPhase.WARNING


def test_nonfinite_and_boolean_preparation_percent_are_ignored():
    for raw_percent in (float("nan"), float("inf"), True):
        snapshot = snapshot_from_status(
            {
                "live": True,
                "ready": False,
                "preparation": {
                    "status": "running",
                    "stage": "converting",
                    "percent": raw_percent,
                },
            },
            port=8123,
            process_running=True,
        )

        assert snapshot.phase is TrayPhase.PREPARING
        assert snapshot.preparation_percent is None

    valid = snapshot_from_status(
        {
            "live": True,
            "ready": False,
            "preparation": {"status": "running", "percent": 120.5},
        },
        port=8123,
        process_running=True,
    )
    assert valid.preparation_percent == 100.0
    assert math.isfinite(valid.preparation_percent)
