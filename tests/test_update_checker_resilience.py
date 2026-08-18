import urllib.error
from datetime import UTC, datetime, timedelta

import app.update_checker as update_checker_module
from app.update_checker import (
    UpdateCache,
    UpdateChecker,
    UpdatePreferences,
    UpdateStore,
    check_due,
)


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


def test_channel_change_ignores_fresh_cache_and_old_etag(tmp_path, monkeypatch):
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True, channel="beta"))
    store.save_cache(
        UpdateCache(
            channel="stable",
            releases_etag='"stable-etag"',
            last_checked_at=now - timedelta(minutes=5),
        )
    )
    seen_etags = []

    def fetch_release_index(*, opener, timeout_seconds, etag):
        del opener, timeout_seconds
        seen_etags.append(etag)
        return [], '"beta-etag"'

    monkeypatch.setattr(
        update_checker_module,
        "_fetch_release_index",
        fetch_release_index,
    )

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        now=lambda: now,
    ).check()

    assert result.status == "current"
    assert seen_etags == [None]
    cache = store.load_cache()
    assert cache.channel == "beta"
    assert cache.releases_etag == '"beta-etag"'


def test_not_modified_after_empty_release_cache_remains_current(tmp_path, monkeypatch):
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True, channel="stable"))
    store.save_cache(
        UpdateCache(
            channel="stable",
            releases_etag='"stable-etag"',
            last_checked_at=now - timedelta(days=2),
            manifest=None,
        )
    )

    def not_modified(**_kwargs):
        raise urllib.error.HTTPError(
            "https://api.github.com/releases",
            304,
            "Not Modified",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(update_checker_module, "_fetch_release_index", not_modified)

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        now=lambda: now,
    ).check(force=True)

    assert result.status == "current"
    assert result.message is None
    assert store.load_cache().last_checked_at == now
