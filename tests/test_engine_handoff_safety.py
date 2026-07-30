import asyncio
from contextlib import asynccontextmanager

import pytest

from app.engine_handoff_safety import install_engine_handoff_safety
from app.model_manager import ModelManager
from app.model_manager_core import ModelNotLoaded

install_engine_handoff_safety()


class FakeHandle:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.error = None

    def next_chunk(self):
        return next(self._chunks, None)

    def request_stop(self):
        return None

    def wait_closed(self):
        return True


class FakeEngine:
    def __init__(self, model_id: str, label: str):
        self.model_id = model_id
        self.device = "CPU"
        self.label = label
        self.generate_calls = []
        self.stream_calls = []

    def generate(self, prompt, params):
        self.generate_calls.append((prompt, params))
        return self.label

    def stream(self, prompt, params):
        self.stream_calls.append((prompt, params))
        return FakeHandle([self.label])


class FakeManager:
    def __init__(self, engine: FakeEngine | None, *, managed: bool = True):
        self.engines = {engine.model_id: engine} if engine is not None else {}
        self.catalog = {"demo": object()} if managed else {}
        self.locks = {"demo": asyncio.Lock()}
        self.tracking_started = asyncio.Event()

    def get_lock(self, model_id: str):
        return self.locks.setdefault(model_id, asyncio.Lock())

    @asynccontextmanager
    async def _track_generation(self):
        self.tracking_started.set()
        yield

    async def _finish_stream_handle(self, _engine, handle, _loop):
        handle.request_stop()
        handle.wait_closed()
        return None

    async def _recover_cancelled_npu_engine(self, _engine, _loop):
        return None


def test_queued_generation_rebinds_to_replacement_engine():
    async def scenario():
        old_engine = FakeEngine("demo", "old")
        new_engine = FakeEngine("demo", "new")
        manager = FakeManager(old_engine)
        old_lock = manager.locks["demo"]
        await old_lock.acquire()

        task = asyncio.create_task(ModelManager.generate(manager, old_engine, "hello", object()))
        await manager.tracking_started.wait()
        await asyncio.sleep(0)

        manager.engines["demo"] = new_engine
        manager.locks["demo"] = asyncio.Lock()
        old_lock.release()

        assert await task == "new"
        assert old_engine.generate_calls == []
        assert len(new_engine.generate_calls) == 1

    asyncio.run(scenario())


def test_queued_stream_rebinds_to_replacement_engine():
    async def scenario():
        old_engine = FakeEngine("demo", "old")
        new_engine = FakeEngine("demo", "new")
        manager = FakeManager(old_engine)
        old_lock = manager.locks["demo"]
        await old_lock.acquire()

        stream = ModelManager.stream(manager, old_engine, "hello", object())
        first_chunk = asyncio.create_task(anext(stream))
        await manager.tracking_started.wait()
        await asyncio.sleep(0)

        manager.engines["demo"] = new_engine
        manager.locks["demo"] = asyncio.Lock()
        old_lock.release()

        assert await first_chunk == "new"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert old_engine.stream_calls == []
        assert len(new_engine.stream_calls) == 1

    asyncio.run(scenario())


def test_managed_request_fails_cleanly_after_unload():
    async def scenario():
        engine = FakeEngine("demo", "old")
        manager = FakeManager(None)

        with pytest.raises(ModelNotLoaded, match="no longer loaded"):
            await ModelManager.generate(manager, engine, "hello", object())

    asyncio.run(scenario())


def test_unmanaged_temporary_engine_remains_supported():
    async def scenario():
        engine = FakeEngine("demo", "temporary")
        manager = FakeManager(None, managed=False)

        assert await ModelManager.generate(manager, engine, "hello", object()) == "temporary"

    asyncio.run(scenario())


def test_unload_is_rejected_while_model_lock_is_busy():
    async def scenario():
        engine = FakeEngine("demo", "old")
        manager = FakeManager(engine)
        lock = manager.locks["demo"]
        await lock.acquire()
        try:
            with pytest.raises(ValueError, match="Wait for the request to finish"):
                ModelManager.unload(manager, "demo")
        finally:
            lock.release()

    asyncio.run(scenario())
