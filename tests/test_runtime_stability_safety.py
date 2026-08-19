from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.desktop_credential_safety import install_desktop_credential_safety
from app.desktop_network import (
    DesktopApiKeyStore,
    DesktopNetworkService,
    DesktopNetworkUpdateRequest,
    resolve_desktop_network_settings,
)
from app.embedding_lifecycle_safety import (
    _install_engine_class_safety,
    _state,
    _wait_for_embedding_drain,
)
from app.onboarding_state import OnboardingStateStore

_STRONG_KEY = "ib_abcdefghijklmnopqrstuvwxyz0123456789"
_NETWORK_ENV = ("OV_LLM_HOST", "OV_LLM_API_KEY", "OV_LLM_CORS_ORIGINS")


def test_embedding_close_is_deferred_until_inflight_call_finishes():
    started = threading.Event()
    release = threading.Event()

    class BlockingEmbeddingEngine:
        def __init__(self) -> None:
            self.model_id = "blocking-embedding"
            self._closed = False

        def embed(self, texts: list[str]) -> list[list[float]]:
            if self._closed:
                raise RuntimeError("closed")
            started.set()
            assert release.wait(1.0)
            return [[float(len(text))] for text in texts]

        def count_tokens(self, text: str) -> int:
            if self._closed:
                raise RuntimeError("closed")
            return max(1, len(text) // 4)

        def close(self) -> None:
            self._closed = True

    _install_engine_class_safety(BlockingEmbeddingEngine)
    engine = BlockingEmbeddingEngine()
    result: list[list[list[float]]] = []

    worker = threading.Thread(target=lambda: result.append(engine.embed(["hello"])), daemon=True)
    worker.start()
    assert started.wait(0.5)
    assert _state(engine).idle.is_set() is False

    # Unload must not close the native pipeline out from under the worker, and it
    # must not block the caller while inference is still running.
    engine.close()
    assert engine._closed is False
    with pytest.raises(RuntimeError, match="unloading"):
        engine.embed(["new request"])

    release.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert result == [[[5.0]]]
    assert engine._closed is True
    assert _state(engine).idle.is_set() is True
    # Request accounting can finish after a deferred close without touching native state.
    assert engine.count_tokens("hello") == 1


def test_shutdown_drain_waits_for_active_embedding_worker():
    started = threading.Event()
    release = threading.Event()

    class BlockingEmbeddingEngine:
        def __init__(self) -> None:
            self.model_id = "shutdown-embedding"
            self._closed = False

        def embed(self, texts: list[str]) -> list[list[float]]:
            started.set()
            assert release.wait(1.0)
            return [[1.0] for _text in texts]

        def count_tokens(self, text: str) -> int:
            return max(1, len(text) // 4)

        def close(self) -> None:
            self._closed = True

    _install_engine_class_safety(BlockingEmbeddingEngine)
    engine = BlockingEmbeddingEngine()
    worker = threading.Thread(target=lambda: engine.embed(["hello"]), daemon=True)
    worker.start()
    assert started.wait(0.5)

    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        manager = SimpleNamespace(engines={engine.model_id: engine})
        assert asyncio.run(_wait_for_embedding_drain(manager, timeout=1.0)) is True
    finally:
        release.set()
        timer.cancel()
        worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert _state(engine).idle.is_set() is True


def test_desktop_key_removal_does_not_claim_success_when_unlink_fails(
    tmp_path, monkeypatch
):
    install_desktop_credential_safety()
    store = DesktopApiKeyStore(tmp_path / "config")
    store.key_path.parent.mkdir(parents=True, exist_ok=True)
    store.key_path.write_bytes(b"encrypted-placeholder")
    store._memory_key = _STRONG_KEY

    original_unlink = Path.unlink

    def fail_target_unlink(path: Path, *args, **kwargs):
        if path == store.key_path:
            raise PermissionError("credential file is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with pytest.raises(PermissionError, match="credential file is locked"):
        store.remove()

    assert store._memory_key == _STRONG_KEY
    assert store.key_path.exists()


def test_desktop_network_updates_do_not_interleave_state_and_key_writes(
    tmp_path, monkeypatch
):
    for name in _NETWORK_ENV:
        monkeypatch.delenv(name, raising=False)
    install_desktop_credential_safety()

    state_store = OnboardingStateStore(tmp_path / "onboarding" / "state.json")
    credential_store = DesktopApiKeyStore(tmp_path / "config")
    base = Settings.from_env().replace(port=8123)
    active = resolve_desktop_network_settings(
        base,
        state=state_store.load().state,
        credential_store=credential_store,
        env=os.environ,
    )
    service = DesktopNetworkService(
        active_resolution=active,
        base_settings=base,
        paths=SimpleNamespace(portable=False),
        state_store=state_store,
        credential_store=credential_store,
        endpoint_port=8123,
        env=os.environ,
    )

    sequence: list[str] = []
    sequence_lock = threading.Lock()
    key_write_started = threading.Event()
    release_key_write = threading.Event()
    second_state_write = threading.Event()
    original_state_update = state_store.update
    original_set_key = credential_store.set_key

    def tracked_state_update(**changes):
        if threading.current_thread().name == "network-update-b":
            second_state_write.set()
        with sequence_lock:
            sequence.append(f"state:{bool(changes.get('lan_access_enabled'))}")
        return original_state_update(**changes)

    def blocking_set_key(value: str):
        with sequence_lock:
            sequence.append("key:start")
        key_write_started.set()
        assert release_key_write.wait(1.0)
        result = original_set_key(value)
        with sequence_lock:
            sequence.append("key:end")
        return result

    monkeypatch.setattr(state_store, "update", tracked_state_update)
    monkeypatch.setattr(credential_store, "set_key", blocking_set_key)

    errors: list[BaseException] = []

    def first_update() -> None:
        try:
            service.update(DesktopNetworkUpdateRequest(allow_lan=True, api_key=_STRONG_KEY))
        except BaseException as exc:  # noqa: BLE001 - captured for thread assertion
            errors.append(exc)

    def second_update() -> None:
        try:
            service.update(
                DesktopNetworkUpdateRequest(
                    allow_lan=False,
                    remove_stored_api_key=True,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - captured for thread assertion
            errors.append(exc)

    first = threading.Thread(target=first_update, name="network-update-a", daemon=True)
    second = threading.Thread(target=second_update, name="network-update-b", daemon=True)
    first.start()
    assert key_write_started.wait(0.5)
    second.start()

    # While A is between the state write and the credential write, B must be blocked
    # outside the full transaction rather than committing its own state in the middle.
    assert second_state_write.wait(0.1) is False
    with sequence_lock:
        assert sequence == ["state:True", "key:start"]

    release_key_write.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)
    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert state_store.load().state["lan_access_enabled"] is False
    assert credential_store.get_key() is None
