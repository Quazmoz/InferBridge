from __future__ import annotations

import asyncio
import threading

import pytest

from app.config import BASE_DIR, Settings
from app.model_manager import ModelManager
from runtime.benchmark_runner import (
    _stream_generation_once,
    _validate_benchmark_work,
    benchmark_model_device,
    score_benchmark_results,
)
from runtime.openvino_engine import GenParams, MockEngine

MODEL_ID = "tinyllama-1.1b-chat-fp16"


class _TrackingMockEngine(MockEngine):
    def __init__(self, model_id: str, *, device: str) -> None:
        super().__init__(model_id, device=device)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _TimeoutHandle:
    def __init__(self) -> None:
        self.error = None
        self.done = threading.Event()
        self.stop_requested = False

    def next_chunk(self):
        return None

    def request_stop(self) -> None:
        self.stop_requested = True

    def wait_closed(self, timeout: float | None = 30.0) -> bool:
        if timeout is None:
            return self.done.wait(2.0)
        return False


class _TimeoutEngine:
    model_id = MODEL_ID

    def __init__(self) -> None:
        self.handle = _TimeoutHandle()
        self.closed = threading.Event()

    def stream(self, _prompt, _params):
        return self.handle

    def count_tokens(self, _text: str) -> int:
        return 1

    def close(self) -> None:
        self.closed.set()


def _manager(tmp_path) -> ModelManager:
    return ModelManager(
        Settings(
            host="127.0.0.1",
            port=8000,
            device="CPU",
            models_file=BASE_DIR / "models.json",
            models_dir=BASE_DIR / "models" / "openvino",
            default_model=None,
            force_mock=True,
            benchmark_results_file=tmp_path / "benchmarks.json",
        )
    )


def test_loaded_model_is_reused_instead_of_allocating_duplicate_engine(tmp_path):
    async def scenario() -> None:
        manager = _manager(tmp_path)
        engine = _TrackingMockEngine(MODEL_ID, device="CPU")
        manager.engines[MODEL_ID] = engine
        manager.devices[MODEL_ID] = "CPU"
        manager.locks[MODEL_ID] = asyncio.Lock()

        async def temporary_engine_must_not_be_built(*_args, **_kwargs):
            raise AssertionError("loaded benchmark attempted a duplicate engine build")

        manager.build_temporary_engine = temporary_engine_must_not_be_built
        result = await benchmark_model_device(
            manager,
            run_id="bench-loaded",
            model_id=MODEL_ID,
            device="CPU",
            prompt="Return one short sentence.",
            max_tokens=32,
            runs=3,
            warmup_runs=1,
        )

        assert result.success is True
        assert result.actual_device == "CPU"
        assert result.load_time_ms is None
        assert result.decode_tokens_sec is not None
        assert len(result.samples or []) == 3
        assert manager.engines[MODEL_ID] is engine
        assert engine.closed is False

        mismatched = await benchmark_model_device(
            manager,
            run_id="bench-mismatch",
            model_id=MODEL_ID,
            device="GPU",
            prompt="Return one short sentence.",
            max_tokens=16,
            runs=1,
        )
        assert mismatched.success is False
        assert "avoid duplicating model memory" in (mismatched.error or "")
        assert engine.closed is False

        manager.unload(MODEL_ID)
        assert engine.closed is True

    asyncio.run(scenario())


def test_missing_load_time_is_excluded_from_balanced_score_instead_of_treated_as_instant():
    results = [
        {
            "model_id": "temporary",
            "requested_device": "CPU",
            "actual_device": "CPU",
            "success": True,
            "decode_tokens_sec": 20.0,
            "tokens_sec": 18.0,
            "time_to_first_token_ms": 100.0,
            "total_latency_ms": 1000.0,
            "load_time_ms": 1000.0,
        },
        {
            "model_id": "already-loaded",
            "requested_device": "CPU",
            "actual_device": "CPU",
            "success": True,
            "decode_tokens_sec": 20.0,
            "tokens_sec": 18.0,
            "time_to_first_token_ms": 100.0,
            "total_latency_ms": 1000.0,
            "load_time_ms": None,
        },
    ]

    score_benchmark_results(results)

    assert results[0]["score"] == 100.0
    assert results[1]["score"] == 100.0


def test_temporary_stream_timeout_defers_close_until_native_worker_exits():
    engine = _TimeoutEngine()
    params = GenParams(max_new_tokens=1, temperature=0.0, top_p=1.0, do_sample=False)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="cleanup was deferred"):
            await _stream_generation_once(engine, "prompt", params)
        assert engine.handle.stop_requested is True
        assert engine.closed.is_set() is False

        # The retained benchmark caller will close in its own finally block. Once the
        # stream helper transfers ownership, that close must remain a no-op until the
        # native worker actually reports completion.
        engine.close()
        assert engine.closed.is_set() is False

    asyncio.run(scenario())
    engine.handle.done.set()
    assert engine.closed.wait(1.0) is True


def test_benchmark_work_budget_allows_builtin_thorough_matrix():
    _validate_benchmark_work(
        model_ids=[f"model-{index}" for index in range(8)],
        devices=["CPU", "GPU", "NPU", "AUTO"],
        runs=8,
        max_tokens=128,
    )


def test_benchmark_work_budget_rejects_pathological_custom_matrix():
    with pytest.raises(ValueError, match="measured output-token budget"):
        _validate_benchmark_work(
            model_ids=[f"model-{index}" for index in range(8)],
            devices=["CPU", "GPU", "NPU", "AUTO"],
            runs=10,
            max_tokens=4096,
        )
