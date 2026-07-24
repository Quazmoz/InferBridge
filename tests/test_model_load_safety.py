"""Regression coverage for NPU load preflight and stream recovery."""

from __future__ import annotations

import asyncio
import collections
import threading
import time

import pytest

from app import model_load_safety, model_manager_core
from app.model_manager import ModelManager
from app.model_registry import ModelConfig
from runtime.openvino_engine import BaseEngine, GenParams, StreamHandle


def _config(tmp_path, *, weight_format: str = "int4") -> ModelConfig:
    return ModelConfig(
        id="tinyllama-test",
        name="TinyLlama Test",
        description="",
        backend="openvino-genai",
        model_path=str(tmp_path / "tinyllama-test"),
        source_model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        weight_format=weight_format,
        recommended_device="CPU",
        max_context_len=2048,
        max_output_tokens=512,
    )


def _mark_converted(cfg: ModelConfig, tmp_path) -> None:
    model_dir = cfg.abs_path(tmp_path)
    model_dir.mkdir(parents=True)
    (model_dir / "openvino_model.xml").write_text("<xml />", encoding="utf-8")


def test_int4_conversion_defaults_are_npu_portable(tmp_path) -> None:
    cfg = _config(tmp_path)

    result = model_load_safety.resolve_conversion_profile(
        cfg,
        weight_format=None,
        group_size=None,
        ratio=None,
        sym=None,
    )

    assert result == ("int4", 128, 1.0, True)


def test_direct_npu_rejects_legacy_untracked_int4_before_native_load(tmp_path) -> None:
    cfg = _config(tmp_path)
    _mark_converted(cfg, tmp_path)

    with pytest.raises(RuntimeError, match="Delete and reconvert"):
        model_load_safety.safe_load_device(cfg, tmp_path, "NPU", available=["CPU", "NPU"])


def test_recorded_portable_int4_profile_allows_direct_npu(tmp_path) -> None:
    cfg = _config(tmp_path)
    _mark_converted(cfg, tmp_path)
    model_load_safety.record_load_profile(
        cfg,
        tmp_path,
        weight_format="int4",
        group_size=128,
        ratio=1.0,
        sym=True,
    )

    assert (
        model_load_safety.safe_load_device(cfg, tmp_path, "NPU", available=["CPU", "NPU"])
        == "NPU"
    )


def test_auto_excludes_npu_for_unverified_int4(tmp_path) -> None:
    cfg = _config(tmp_path)
    _mark_converted(cfg, tmp_path)

    assert (
        model_load_safety.safe_load_device(
            cfg,
            tmp_path,
            "AUTO",
            available=["CPU", "GPU", "NPU"],
        )
        == "AUTO:GPU,CPU"
    )


def test_tampered_profile_is_revalidated_before_npu_load(tmp_path) -> None:
    cfg = _config(tmp_path)
    _mark_converted(cfg, tmp_path)
    model_load_safety.record_load_profile(
        cfg,
        tmp_path,
        weight_format="int4",
        group_size=128,
        ratio=1.0,
        sym=True,
    )
    path = model_load_safety.load_profile_path(cfg, tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace('"ratio": 1.0', '"ratio": 0.5'),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Delete and reconvert"):
        model_load_safety.safe_load_device(cfg, tmp_path, "NPU", available=["CPU", "NPU"])


def test_reconversion_invalidates_previous_npu_profile(tmp_path) -> None:
    cfg = _config(tmp_path)
    _mark_converted(cfg, tmp_path)
    model_load_safety.record_load_profile(
        cfg,
        tmp_path,
        weight_format="int4",
        group_size=128,
        ratio=1.0,
        sym=True,
    )

    model_load_safety.invalidate_load_profile(cfg, tmp_path)

    with pytest.raises(RuntimeError, match="Delete and reconvert"):
        model_load_safety.safe_load_device(cfg, tmp_path, "NPU", available=["CPU", "NPU"])


def test_manager_passes_portable_int4_defaults_to_converter(monkeypatch, tmp_path) -> None:
    cfg = _config(tmp_path)
    manager = object.__new__(ModelManager)
    manager.catalog = {cfg.id: cfg}
    manager.status_overrides = {}
    captured = {}

    async def fake_convert(
        self,
        model_id,
        device,
        load_after,
        weight_format=None,
        group_size=None,
        ratio=None,
        sym=None,
        trust_remote_code=None,
    ):
        captured.update(
            model_id=model_id,
            device=device,
            load_after=load_after,
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
            trust_remote_code=trust_remote_code,
        )
        self.status_overrides[model_id] = {"status": "error"}

    monkeypatch.setattr(
        model_manager_core.ModelManager, "_convert_task", fake_convert, raising=False
    )

    asyncio.run(ModelManager._convert_task(manager, cfg.id, "NPU", True))

    assert captured == {
        "model_id": cfg.id,
        "device": "NPU",
        "load_after": False,
        "weight_format": None,
        "group_size": 128,
        "ratio": 1.0,
        "sym": True,
        "trust_remote_code": None,
    }


def test_load_after_waits_for_npu_profile_marker(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from app import model_library

    cfg = _config(tmp_path)
    manager = object.__new__(ModelManager)
    manager.catalog = {cfg.id: cfg}
    manager.status_overrides = {}
    manager.settings = SimpleNamespace()
    manager.advisor = SimpleNamespace(measure_converted_size=lambda _cfg: None)
    scheduled = []

    async def fake_convert(
        self,
        model_id,
        device,
        load_after,
        **_kwargs,
    ):
        assert load_after is False
        _mark_converted(cfg, tmp_path)

    def schedule_load(model_id, device):
        assert model_load_safety.load_profile_path(cfg, tmp_path).is_file()
        scheduled.append((model_id, device))
        return None

    monkeypatch.setattr(
        model_manager_core.ModelManager, "_convert_task", fake_convert, raising=False
    )
    monkeypatch.setattr(model_library, "record_conversion_metadata", lambda *_args: None)
    manager.schedule_load = schedule_load

    asyncio.run(ModelManager._convert_task(manager, cfg.id, "NPU", True))

    assert scheduled == [(cfg.id, "NPU")]


class _SlowCancellationEngine(BaseEngine):
    backend = "openvino-genai"

    def __init__(self, release: threading.Event) -> None:
        self.model_id = "qwen-npu-test"
        self.model_path = ""
        self.device = "CPU"
        self.release = release

    def stream(self, prompt: str, params: GenParams) -> StreamHandle:
        handle = StreamHandle()

        def worker() -> None:
            handle.push("first")
            while not handle.should_stop():
                time.sleep(0.005)
            self.release.wait(timeout=2.0)
            handle.finish()

        threading.Thread(target=worker, daemon=True).start()
        return handle


def _bare_manager(engine: BaseEngine) -> ModelManager:
    manager = object.__new__(ModelManager)
    manager.force_mock = False
    manager.engines = {engine.model_id: engine}
    manager.locks = {}
    manager.devices = {engine.model_id: engine.device}
    manager.status_overrides = {}
    manager.progress = {}
    manager.catalog = {}
    manager._events = collections.deque(maxlen=50)
    manager._gen_lock = asyncio.Lock()
    manager._active_generations = 0
    manager._drain_event = asyncio.Event()
    manager._drain_event.set()
    return manager


def test_cancelled_stream_holds_model_lock_until_worker_closes() -> None:
    async def scenario() -> None:
        release = threading.Event()
        engine = _SlowCancellationEngine(release)
        manager = _bare_manager(engine)
        stream = manager.stream(engine, "prompt", GenParams(max_new_tokens=8))

        assert await anext(stream) == "first"
        blocked = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.05)
        blocked.cancel()
        await asyncio.sleep(0.05)

        lock = manager.get_lock(engine.model_id)
        assert lock.locked()
        assert not blocked.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        assert not lock.locked()

    asyncio.run(scenario())


def test_interrupted_npu_stream_rebuilds_pipeline_before_unlock() -> None:
    async def scenario() -> None:
        release = threading.Event()
        release.set()
        engine = _SlowCancellationEngine(release)
        engine.device = "NPU"
        replacement = _SlowCancellationEngine(release)
        replacement.device = "NPU"
        manager = _bare_manager(engine)
        manager.devices[engine.model_id] = "NPU"

        def build_replacement(*_args, **_kwargs):
            return replacement

        manager._build_engine = build_replacement  # type: ignore[method-assign]

        stream = manager.stream(engine, "prompt", GenParams(max_new_tokens=8))
        assert await anext(stream) == "first"
        await stream.aclose()

        assert manager.engines[engine.model_id] is replacement
        assert manager.devices[engine.model_id] == "NPU"
        assert not manager.get_lock(engine.model_id).locked()

    asyncio.run(scenario())
