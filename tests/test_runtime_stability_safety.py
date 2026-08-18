from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.desktop_credential_safety import install_desktop_credential_safety
from app.desktop_network import DesktopApiKeyStore
from app.embedding_lifecycle_safety import _install_engine_class_safety


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
    # Request accounting can finish after a deferred close without touching native state.
    assert engine.count_tokens("hello") == 1


def test_desktop_key_removal_does_not_claim_success_when_unlink_fails(
    tmp_path, monkeypatch
):
    install_desktop_credential_safety()
    store = DesktopApiKeyStore(tmp_path / "config")
    store.key_path.parent.mkdir(parents=True, exist_ok=True)
    store.key_path.write_bytes(b"encrypted-placeholder")
    store._memory_key = "ib_abcdefghijklmnopqrstuvwxyz0123456789"

    original_unlink = Path.unlink

    def fail_target_unlink(path: Path, *args, **kwargs):
        if path == store.key_path:
            raise PermissionError("credential file is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with pytest.raises(PermissionError, match="credential file is locked"):
        store.remove()

    assert store._memory_key == "ib_abcdefghijklmnopqrstuvwxyz0123456789"
    assert store.key_path.exists()
