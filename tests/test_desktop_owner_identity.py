from __future__ import annotations

from types import SimpleNamespace

from app.desktop_server import owner_process_matches


def _process(*, running=True, created_at=123.0):
    return SimpleNamespace(
        is_running=lambda: running,
        create_time=lambda: created_at,
    )


def test_unowned_standalone_server_does_not_require_tray_identity():
    assert owner_process_matches(0, 0.0, process_factory=lambda _pid: None) is True


def test_positive_owner_pid_requires_creation_timestamp():
    calls = []

    def factory(pid):
        calls.append(pid)
        return _process()

    assert owner_process_matches(1234, 0.0, process_factory=factory) is False
    assert owner_process_matches(1234, -1.0, process_factory=factory) is False
    assert calls == []


def test_owner_identity_requires_live_process_and_matching_creation_time():
    assert (
        owner_process_matches(
            1234,
            123.0,
            process_factory=lambda _pid: _process(running=False, created_at=123.0),
        )
        is False
    )
    assert (
        owner_process_matches(
            1234,
            123.0,
            process_factory=lambda _pid: _process(created_at=999.0),
        )
        is False
    )
    assert (
        owner_process_matches(
            1234,
            123.0,
            process_factory=lambda _pid: _process(created_at=123.5),
        )
        is True
    )


def test_owner_identity_contains_process_lookup_failures():
    def missing(_pid):
        raise RuntimeError("process disappeared")

    assert owner_process_matches(1234, 123.0, process_factory=missing) is False
