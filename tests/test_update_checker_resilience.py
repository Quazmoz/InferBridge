from datetime import UTC, datetime, timedelta

from app.update_checker import UpdateChecker, UpdatePreferences, UpdateStore, check_due


def test_future_cache_timestamp_is_due_after_clock_correction():
    now = datetime(2026, 7, 29, 12, tzinfo=UTC)

    assert check_due(now + timedelta(hours=2), now) is True


def test_naive_and_aware_timestamps_compare_safely():
    aware_now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    naive_previous = datetime(2026, 7, 28, 11)

    assert check_due(naive_previous, aware_now) is True


def test_checker_normalizes_naive_clock_and_clamps_timeout(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))
    calls = []

    def offline(_request, timeout):
        calls.append(timeout)
        raise OSError("offline")

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=offline,
        now=lambda: datetime(2026, 7, 29, 12),
        timeout_seconds=0,
    ).check(force=True)

    assert result.status == "offline"
    assert result.checked_at == datetime(2026, 7, 29, 12, tzinfo=UTC)
    assert calls == [0.1, 0.1]
