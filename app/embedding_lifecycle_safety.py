"""Lifecycle safety for embedding engines.

Embedding inference runs in a worker thread, while model unload runs on the server event
loop. Without coordination, unload can clear the native OpenVINO pipeline while an
embedding call is still using it. Keep embedding calls serialized and defer close until
all already-started calls have completed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

_INSTALL_FLAG = "_OVLLM_EMBEDDING_LIFECYCLE_SAFETY_INSTALLED"
_STATE_ATTR = "_ovllm_embedding_lifecycle_state"


@dataclass
class _EmbeddingLifecycleState:
    guard: threading.RLock = field(default_factory=threading.RLock)
    operation: threading.Lock = field(default_factory=threading.Lock)
    active: int = 0
    close_pending: bool = False


def _state(engine: Any) -> _EmbeddingLifecycleState:
    value = getattr(engine, _STATE_ATTR, None)
    if not isinstance(value, _EmbeddingLifecycleState):
        value = _EmbeddingLifecycleState()
        setattr(engine, _STATE_ATTR, value)
    return value


def _install_engine_class_safety(engine_class: type[Any]) -> None:
    """Install deferred-close coordination on one embedding-engine class."""

    if getattr(engine_class, _INSTALL_FLAG, False):
        return

    original_init = engine_class.__init__
    original_embed = engine_class.embed
    original_close = engine_class.close
    original_count_tokens = engine_class.count_tokens

    def init_with_lifecycle_state(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _state(self)

    def embed_with_lifecycle_safety(self, texts: list[str]):
        lifecycle = _state(self)
        with lifecycle.guard:
            if lifecycle.close_pending or bool(getattr(self, "_closed", False)):
                raise RuntimeError(
                    f"Embedding engine for '{getattr(self, 'model_id', 'model')}' is unloading"
                )
            lifecycle.active += 1

        try:
            # OpenVINO embedding pipelines are not assumed to be safe for concurrent
            # native calls. Serialize requests even though FastAPI dispatches them to
            # separate executor threads.
            with lifecycle.operation:
                return original_embed(self, texts)
        finally:
            close_now = False
            with lifecycle.guard:
                lifecycle.active = max(0, lifecycle.active - 1)
                if lifecycle.active == 0 and lifecycle.close_pending:
                    # Leave close_pending set while the native close is running. That
                    # prevents a new request from entering between this decision and
                    # original_close() actually clearing the pipeline.
                    close_now = True
            if close_now:
                original_close(self)
                with lifecycle.guard:
                    lifecycle.close_pending = False

    def close_with_lifecycle_safety(self) -> None:
        lifecycle = _state(self)
        with lifecycle.guard:
            if bool(getattr(self, "_closed", False)):
                return
            # Mark the engine unavailable before releasing the guard. If an embedding
            # call is active, its finally block performs the real close. If not, this
            # call closes immediately without opening a race window for a new embed().
            lifecycle.close_pending = True
            if lifecycle.active > 0:
                # ModelManager.unload() is synchronous. Blocking the event loop here
                # until a worker thread finishes would stall unrelated API requests, so
                # defer the native close to the final in-flight embedding call instead.
                return
        original_close(self)
        with lifecycle.guard:
            lifecycle.close_pending = False

    def count_tokens_after_deferred_close(self, text: str) -> int:
        try:
            return original_count_tokens(self, text)
        except RuntimeError:
            # Embedding token accounting in both built-in engines is deliberately an
            # inexpensive estimate. An unload can complete after embed() returns but
            # before request metrics are calculated, so preserve that accounting without
            # touching a closed native pipeline.
            if bool(getattr(self, "_closed", False)):
                return max(1, len(text) // 4)
            raise

    engine_class.__init__ = init_with_lifecycle_state
    engine_class.embed = embed_with_lifecycle_safety
    engine_class.close = close_with_lifecycle_safety
    engine_class.count_tokens = count_tokens_after_deferred_close
    setattr(engine_class, _INSTALL_FLAG, True)


def install_embedding_lifecycle_safety() -> None:
    """Protect built-in embedding engines from concurrent inference/unload races."""

    from runtime.openvino_engine import MockEmbeddingEngine, OpenVINOEmbeddingEngine

    _install_engine_class_safety(MockEmbeddingEngine)
    _install_engine_class_safety(OpenVINOEmbeddingEngine)


__all__ = ["install_embedding_lifecycle_safety"]
