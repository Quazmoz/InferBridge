"""Keep generation requests bound to the current managed engine.

A device switch compiles a replacement while the previous engine remains available.
Requests can already be queued on the previous engine's lock when that replacement is
installed. Revalidate the engine/lock pair after acquiring the lock so queued work can
never resume against an engine that has just been closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_INSTALL_FLAG = "_ENGINE_HANDOFF_SAFETY_INSTALLED"


class ModelBusyError(ValueError):
    """Raised when an unload would close an engine with queued or active work."""


@asynccontextmanager
async def current_engine_lease(manager: Any, requested_engine: Any) -> AsyncIterator[Any]:
    """Yield a stable current engine while holding its matching model lock.

    The engine and lock are read twice: once before waiting and once after acquiring the
    lock. If a device switch replaced either object while this request was queued, release
    the stale lock and retry against the replacement.
    """

    model_id = str(requested_engine.model_id)
    while True:
        current_engine = manager.engines.get(model_id)
        if current_engine is None:
            if model_id not in manager.catalog:
                # Preserve support for short-lived engines that are intentionally not
                # registered in the manager, such as isolated benchmark helpers.
                current_engine = requested_engine
            else:
                from app.model_manager_core import ModelNotLoaded

                raise ModelNotLoaded(f"Model '{model_id}' is no longer loaded")

        current_lock = manager.get_lock(model_id)
        await current_lock.acquire()

        latest_engine = manager.engines.get(model_id)
        latest_lock = manager.get_lock(model_id)
        unmanaged = model_id not in manager.catalog and latest_engine is None
        stable = (
            unmanaged and current_engine is requested_engine and latest_lock is current_lock
        ) or (latest_engine is current_engine and latest_lock is current_lock)
        if not stable:
            current_lock.release()
            continue

        try:
            yield current_engine
        finally:
            current_lock.release()
        return


def install_engine_handoff_safety() -> None:
    """Install stale-engine prevention and busy-unload protection."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    original_unload = manager_class.unload

    async def generate_with_current_engine(self, engine, prompt, params):
        async with self._track_generation():
            loop = asyncio.get_running_loop()
            async with current_engine_lease(self, engine) as active_engine:
                return await loop.run_in_executor(
                    None,
                    active_engine.generate,
                    prompt,
                    params,
                )

    async def stream_with_current_engine(self, engine, prompt, params):
        async with self._track_generation():
            loop = asyncio.get_running_loop()
            async with current_engine_lease(self, engine) as active_engine:
                handle = await loop.run_in_executor(
                    None,
                    active_engine.stream,
                    prompt,
                    params,
                )
                completed = False
                generation_failed = False
                pending_cancellation: asyncio.CancelledError | None = None
                try:
                    while True:
                        chunk = await loop.run_in_executor(None, handle.next_chunk)
                        if chunk is None:
                            completed = True
                            break
                        yield chunk
                    if handle.error is not None:
                        generation_failed = True
                        raise handle.error
                finally:
                    cleanup_cancellation = await self._finish_stream_handle(
                        active_engine,
                        handle,
                        loop,
                    )
                    pending_cancellation = pending_cancellation or cleanup_cancellation
                    if not completed or generation_failed:
                        recovery_cancellation = await self._recover_cancelled_npu_engine(
                            active_engine,
                            loop,
                        )
                        pending_cancellation = pending_cancellation or recovery_cancellation
                    if pending_cancellation is not None:
                        raise pending_cancellation

    def unload_when_idle(self, model_id: str) -> bool:
        # Shutdown must remain able to force cleanup after its bounded generation
        # drain timeout. Normal API requests still reject unloading a busy engine.
        if getattr(self, "_model_manager_shutting_down", False):
            return original_unload(self, model_id)

        lock = self.locks.get(model_id)
        if lock is not None and lock.locked():
            raise ModelBusyError(
                f"Model '{model_id}' is serving or waiting on a request. "
                "Wait for the request to finish before unloading it."
            )
        return original_unload(self, model_id)

    manager_class.generate = generate_with_current_engine
    manager_class.stream = stream_with_current_engine
    manager_class.unload = unload_when_idle
    setattr(manager_class, _INSTALL_FLAG, True)
