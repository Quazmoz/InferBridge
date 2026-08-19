"""Lifecycle safety for embedding engines.

Embedding inference runs in a worker thread, while model unload and shutdown run on the
server event loop. Without coordination, unload can clear the native OpenVINO pipeline
while an embedding call is still using it. Keep embedding calls serialized, defer close
until already-started calls finish, and let shutdown wait for those calls before normal
model teardown begins.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ov-llm.embedding-lifecycle")

_ENGINE_INSTALL_FLAG = "_OVLLM_EMBEDDING_LIFECYCLE_SAFETY_INSTALLED"
_MANAGER_INSTALL_FLAG = "_OVLLM_EMBEDDING_SHUTDOWN_SAFETY_INSTALLED"
_STATE_ATTR = "_ovllm_embedding_lifecycle_state"
_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 10.0


@dataclass
class _EmbeddingLifecycleState:
    guard: threading.RLock = field(default_factory=threading.RLock)
    operation: threading.Lock = field(default_factory=threading.Lock)
    idle: threading.Event = field(default_factory=threading.Event)
    active: int = 0
    close_pending: bool = False

    def __post_init__(self) -> None:
        self.idle.set()


def _state(engine: Any) -> _EmbeddingLifecycleState:
    value = getattr(engine, _STATE_ATTR, None)
    if not isinstance(value, _EmbeddingLifecycleState):
        value = _EmbeddingLifecycleState()
        setattr(engine, _STATE_ATTR, value)
    return value


def _install_engine_class_safety(engine_class: type[Any]) -> None:
    """Install deferred-close coordination on one embedding-engine class."""

    if getattr(engine_class, _ENGINE_INSTALL_FLAG, False):
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
            lifecycle.idle.clear()

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
                if lifecycle.active == 0:
                    close_now = lifecycle.close_pending
                    if not close_now:
                        lifecycle.idle.set()
            if close_now:
                try:
                    original_close(self)
                finally:
                    with lifecycle.guard:
                        lifecycle.close_pending = False
                        lifecycle.idle.set()

    def close_with_lifecycle_safety(self) -> None:
        lifecycle = _state(self)
        with lifecycle.guard:
            if bool(getattr(self, "_closed", False)):
                lifecycle.idle.set()
                return
            # Mark the engine unavailable before releasing the guard. If an embedding
            # call is active, its finally block performs the real close. If not, this
            # call closes immediately without opening a race window for a new embed().
            lifecycle.close_pending = True
            lifecycle.idle.clear()
            if lifecycle.active > 0:
                # ModelManager.unload() is synchronous. Blocking the event loop here
                # until a worker thread finishes would stall unrelated API requests, so
                # defer the native close to the final in-flight embedding call instead.
                return
        try:
            original_close(self)
        finally:
            with lifecycle.guard:
                lifecycle.close_pending = False
                lifecycle.idle.set()

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
    setattr(engine_class, _ENGINE_INSTALL_FLAG, True)


async def _wait_for_embedding_drain(
    manager: Any,
    *,
    timeout: float = _SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
) -> bool:
    """Wait for embedding workers attached to currently managed engines to become idle."""

    states: list[_EmbeddingLifecycleState] = []
    seen: set[int] = set()
    for engine in list(getattr(manager, "engines", {}).values()):
        lifecycle = getattr(engine, _STATE_ATTR, None)
        if not isinstance(lifecycle, _EmbeddingLifecycleState) or lifecycle.idle.is_set():
            continue
        identity = id(lifecycle)
        if identity in seen:
            continue
        seen.add(identity)
        states.append(lifecycle)

    if not states:
        return True

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    for lifecycle in states:
        remaining = deadline - loop.time()
        if remaining <= 0 or not await asyncio.to_thread(lifecycle.idle.wait, remaining):
            logger.warning(
                "Timeout waiting for in-flight embedding requests to drain during shutdown."
            )
            return False
    return True


def install_embedding_lifecycle_safety() -> None:
    """Protect built-in embedding engines from concurrent inference/unload races."""

    from app import model_manager as manager_module
    from runtime.openvino_engine import MockEmbeddingEngine, OpenVINOEmbeddingEngine

    _install_engine_class_safety(MockEmbeddingEngine)
    _install_engine_class_safety(OpenVINOEmbeddingEngine)

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _MANAGER_INSTALL_FLAG, False):
        return
    original_shutdown = manager_class.shutdown

    async def shutdown_after_embedding_drain(self) -> None:
        await _wait_for_embedding_drain(self)
        await original_shutdown(self)

    manager_class.shutdown = shutdown_after_embedding_drain
    setattr(manager_class, _MANAGER_INSTALL_FLAG, True)


__all__ = ["install_embedding_lifecycle_safety"]
