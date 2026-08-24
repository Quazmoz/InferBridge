"""Benchmark runner compatibility layer with shared locking and safe failure details."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from typing import Any

from app.file_locks import path_lock
from runtime import benchmark_runner_core as _core, device_check
from runtime.benchmark_runner_core import *  # noqa: F401,F403 - preserve public API

_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_BENCHMARK_SCHEMA_VERSION = 1
_CORE_BENCHMARK_MODEL_DEVICE = _core.benchmark_model_device


def _safe_error(value: Any, *, limit: int = 500) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)
    text = _SECRET_RE.sub("[redacted]", text)
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)+", r"...\\", text)
    text = re.sub(r"/(?:[^/\s]+/){2,}", ".../", text)
    return text[:limit] or None


def _sanitize_run(run: dict[str, Any]) -> dict[str, Any]:
    safe = copy.deepcopy(run)
    results = safe.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and result.get("error") is not None:
                result["error"] = _safe_error(result.get("error"))
    return safe


def _sanitize_runs(runs: list[Any]) -> list[dict[str, Any]]:
    return [_sanitize_run(run) for run in runs if isinstance(run, dict)]


class BenchmarkStore(_core.BenchmarkStore):
    """JSON benchmark store serialized with advisor writes and sanitized failures."""

    def __init__(self, path, *, max_runs: int = 100) -> None:
        super().__init__(path, max_runs=max_runs)
        self._lock = path_lock(self.path)
        # Upgrade existing stores in place. Old builds persisted raw native exception
        # strings, which can contain local paths or credential-like values.
        with self._lock:
            data = self._read()
            safe_runs = _sanitize_runs(data["runs"])
            if safe_runs != data["runs"]:
                self._write({"schema_version": data["schema_version"], "runs": safe_runs})

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            return {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        schema = payload.get("schema_version", _BENCHMARK_SCHEMA_VERSION)
        if (
            isinstance(schema, bool)
            or not isinstance(schema, int)
            or schema != _BENCHMARK_SCHEMA_VERSION
        ):
            # Do not coerce Infinity, floats, strings, or a newer schema and do not
            # rewrite an incompatible file from this older reader.
            return {"schema_version": _BENCHMARK_SCHEMA_VERSION, "runs": []}
        return {"schema_version": schema, "runs": payload["runs"]}

    def list_runs(self) -> list[dict[str, Any]]:
        return _sanitize_runs(super().list_runs())

    def append(self, run: dict[str, Any]) -> None:
        super().append(_sanitize_run(run))


# Functions defined in the retained implementation resolve these globals at runtime.
_core.BenchmarkStore = BenchmarkStore


def _loaded_engine_matches_request(loaded_device: str, requested_device: str) -> bool:
    """Return whether a loaded engine represents the requested benchmark target."""

    try:
        loaded = device_check.parse_device_expression(loaded_device)
        requested = device_check.parse_device_expression(requested_device)
    except device_check.DeviceValidationError:
        return False
    direct = {"CPU", "GPU", "NPU"}
    if loaded.kind in direct and requested.kind in direct:
        return loaded.kind == requested.kind
    return device_check.normalize_device(loaded_device) == device_check.normalize_device(
        requested_device
    )


async def _stream_loaded_generation_once(
    manager: Any,
    engine: Any,
    prompt: str,
    params: Any,
    *,
    on_first_token=None,
) -> dict[str, Any]:
    """Measure a registered engine through the manager's normal generation locks."""

    started = time.perf_counter()
    first_token_at: float | None = None
    pieces: list[str] = []
    stream = manager.stream(engine, prompt, params)
    try:
        async for piece in stream:
            if first_token_at is None and piece:
                first_token_at = time.perf_counter()
                if on_first_token is not None:
                    on_first_token()
            pieces.append(piece)
    finally:
        await stream.aclose()

    latency_s = time.perf_counter() - started
    text = "".join(pieces)
    completion_tokens = await asyncio.to_thread(engine.count_tokens, text)
    ttft_s = None if first_token_at is None else first_token_at - started
    return {
        "ttft_s": ttft_s,
        "latency_s": latency_s,
        "completion_tokens": completion_tokens,
        "tokens_sec": (
            completion_tokens / latency_s
            if completion_tokens > 0 and latency_s > 0
            else None
        ),
        "decode_tokens_sec": _core._decode_tokens_sec(
            completion_tokens,
            latency_s,
            ttft_s,
        ),
    }


async def _benchmark_loaded_model_device(
    manager: Any,
    *,
    run_id: str,
    model_id: str,
    device: str,
    prompt: str,
    max_tokens: int,
    runs: int,
    warmup_runs: int = 0,
    combination_index: int = 1,
    combination_total: int = 1,
):
    """Benchmark an already-loaded model without constructing a duplicate engine."""

    timestamp = _core._utc_now()
    engine = manager.engines.get(model_id)
    cfg = manager.config_for(model_id)
    prompt_tokens = 0
    peak_process_ram_mb: float | None = None
    identity = _core._model_identity(manager, cfg)
    memory_sampler = _core._ProcessMemorySampler()

    try:
        if engine is None or cfg is None:
            return await _CORE_BENCHMARK_MODEL_DEVICE(
                manager,
                run_id=run_id,
                model_id=model_id,
                device=device,
                prompt=prompt,
                max_tokens=max_tokens,
                runs=runs,
                warmup_runs=warmup_runs,
                combination_index=combination_index,
                combination_total=combination_total,
            )
        if "embedding" in str(getattr(cfg, "backend", "")).lower():
            raise ValueError("Embedding models cannot be benchmarked as generation models.")

        loaded_device = str(manager.devices.get(model_id) or getattr(engine, "device", ""))
        requested_device = device_check.validate_device_expression(device)
        if not _loaded_engine_matches_request(loaded_device, requested_device):
            raise ValueError(
                f"Model '{model_id}' is already loaded on {loaded_device or 'an unknown target'}. "
                f"To avoid duplicating model memory, benchmark that target or unload the model "
                f"before benchmarking on {requested_device}."
            )

        model_label = getattr(cfg, "name", model_id)
        prefix = _core._combination_prefix(
            combination_index,
            combination_total,
            model_label,
            requested_device,
        )
        _core._emit_benchmark_progress(manager, f"{prefix} · using loaded engine")
        memory_sampler.start()

        loop = asyncio.get_running_loop()
        _core._emit_benchmark_progress(manager, f"{prefix} · preparing prompt")
        prompt_text, prompt_tokens = await loop.run_in_executor(
            None,
            _core._build_benchmark_prompt,
            engine,
            prompt,
            cfg.max_prompt_len,
        )
        max_new_tokens = min(
            int(max_tokens),
            max(cfg.max_context_len - prompt_tokens - 8, 1),
        )
        params = _core.GenParams(
            max_new_tokens=max_new_tokens,
            temperature=0.0,
            top_p=1.0,
            do_sample=False,
        )

        for warmup_index in range(max(int(warmup_runs), 0)):
            _core._emit_benchmark_progress(
                manager,
                f"{prefix} · warming up {warmup_index + 1}/{warmup_runs}",
            )
            await _stream_loaded_generation_once(manager, engine, prompt_text, params)

        samples: list[dict[str, Any]] = []
        for run_index in range(max(int(runs), 1)):
            _core._emit_benchmark_progress(
                manager,
                f"{prefix} · prefill · run {run_index + 1}/{runs}",
            )

            def on_first_token(
                *,
                _prefix: str = prefix,
                _run_index: int = run_index,
                _runs: int = runs,
            ) -> None:
                _core._emit_benchmark_progress(
                    manager,
                    f"{_prefix} · generating · run {_run_index + 1}/{_runs}",
                )

            generation = await _stream_loaded_generation_once(
                manager,
                engine,
                prompt_text,
                params,
                on_first_token=on_first_token,
            )
            samples.append(_core._sample_payload(run_index + 1, generation))

        _core._emit_benchmark_progress(manager, f"{prefix} · finalizing")
        peak_process_ram_mb = memory_sampler.stop()
        aggregate = _core._aggregate_samples(samples)
        actual_device = _core._reported_actual_device(engine, requested_device)
        _core._emit_benchmark_progress(manager, f"{prefix} · complete")
        return _core.BenchmarkResult(
            run_id=run_id,
            model_id=model_id,
            **identity,
            requested_device=requested_device,
            actual_device=actual_device,
            load_time_ms=None,
            time_to_first_token_ms=aggregate["time_to_first_token_ms"],
            total_latency_ms=aggregate["total_latency_ms"],
            prompt_tokens=prompt_tokens,
            completion_tokens=aggregate["completion_tokens"],
            tokens_sec=aggregate["tokens_sec"],
            decode_tokens_sec=aggregate["decode_tokens_sec"],
            prefill_tokens_sec=None,
            peak_process_ram_mb=peak_process_ram_mb,
            success=True,
            error=None,
            timestamp=timestamp,
            runs=max(int(runs), 1),
            warmup_runs=max(int(warmup_runs), 0),
            samples=samples,
            statistics=aggregate["statistics"],
            stability=aggregate["stability"],
            synthetic=manager.force_mock,
        )
    except Exception as exc:  # noqa: BLE001 - preserve per-combination failure isolation
        peak_process_ram_mb = peak_process_ram_mb or memory_sampler.stop()
        _core._emit_benchmark_progress(
            manager,
            (
                f"{_core._combination_prefix(combination_index, combination_total, model_id, device)} "
                "· failed"
            ),
            level="warning",
        )
        return _core.BenchmarkResult(
            run_id=run_id,
            model_id=model_id,
            **identity,
            requested_device=device,
            actual_device=_core._reported_actual_device(engine, device) if engine else None,
            load_time_ms=None,
            time_to_first_token_ms=None,
            total_latency_ms=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            tokens_sec=None,
            decode_tokens_sec=None,
            prefill_tokens_sec=None,
            peak_process_ram_mb=peak_process_ram_mb,
            success=False,
            error=_safe_error(exc),
            timestamp=timestamp,
            runs=max(int(runs), 1),
            warmup_runs=max(int(warmup_runs), 0),
            samples=[],
            statistics={},
            stability=None,
            score=-25.0,
            synthetic=manager.force_mock,
        )
    finally:
        if peak_process_ram_mb is None:
            memory_sampler.stop()


async def benchmark_model_device(*args: Any, **kwargs: Any):
    manager = args[0] if args else kwargs.get("manager")
    model_id = kwargs.get("model_id")
    if manager is not None and model_id in getattr(manager, "engines", {}):
        result = await _benchmark_loaded_model_device(*args, **kwargs)
    else:
        result = await _CORE_BENCHMARK_MODEL_DEVICE(*args, **kwargs)
    if result.error is not None:
        result.error = _safe_error(result.error)
    return result


# Make the core suite use the safety wrapper above. The original function is retained in
# _CORE_BENCHMARK_MODEL_DEVICE so unloaded models continue through the established path.
_core.benchmark_model_device = benchmark_model_device


def score_benchmark_results(
    results: list[dict[str, Any]],
    *,
    mock: bool = False,
) -> dict[str, Any]:
    """Score measured dimensions without treating unavailable load time as instant."""

    successes = [row for row in results if row.get("success")]
    if not successes:
        for result in results:
            result["score"] = float(result.get("score") or -25.0)
        return {
            "model_id": None,
            "requested_device": None,
            "actual_device": None,
            "score": 0.0,
            "summary": "No successful benchmark run completed.",
            "rationale": ["Every requested model/device combination returned an error."],
            "caveat": _core.BENCHMARK_CAVEAT,
        }

    max_tps = max(_core._benchmark_speed(row) for row in successes) or 1.0
    min_ttft = min(_core._latency_for_ttft(row) for row in successes)
    min_total = min(_core._positive(row.get("total_latency_ms")) for row in successes) or 1.0
    measured_loads = [
        value
        for row in successes
        if (value := _core._positive_or_none(row.get("load_time_ms"))) is not None
        and value > 0
    ]
    min_load = min(measured_loads) if measured_loads else None

    for result in results:
        if not result.get("success"):
            result["score"] = -25.0
            continue

        components = [
            (0.50, _core._benchmark_speed(result) / max_tps),
            (0.30, min_ttft / _core._latency_for_ttft(result)),
            (
                0.10,
                min_total
                / (_core._positive(result.get("total_latency_ms")) or min_total),
            ),
        ]
        load_ms = _core._positive_or_none(result.get("load_time_ms"))
        high_load_penalty = 0.0
        if min_load is not None and load_ms is not None and load_ms > 0:
            components.append((0.10, min_load / load_ms))
            if load_ms > 30_000:
                high_load_penalty = min((load_ms - 30_000) / 90_000, 1.0) * 0.20

        total_weight = sum(weight for weight, _value in components)
        raw_score = sum(weight * value for weight, value in components) / total_weight
        result["score"] = round(max(0.0, (raw_score - high_load_penalty) * 100), 2)

    best = max(successes, key=lambda item: float(item.get("score") or 0.0))
    best_speed = _core._benchmark_speed(best)
    summary = (
        "Synthetic mock benchmark completed; rerun on Windows with OpenVINO hardware "
        "for real performance evidence."
        if mock
        else (
            f"Recommended {best['model_id']} on {best['requested_device']} "
            "from this measured benchmark run."
        )
    )
    return {
        "model_id": best["model_id"],
        "requested_device": best["requested_device"],
        "actual_device": best.get("actual_device"),
        "score": best.get("score"),
        "summary": summary,
        "rationale": [
            f"{best_speed:.2f} decode tokens/sec"
            if best_speed
            else "Decode throughput was unavailable.",
            (
                f"{best['time_to_first_token_ms']:.1f} ms first-token latency"
                if best.get("time_to_first_token_ms") is not None
                else "First-token latency was not measurable for this backend."
            ),
            (
                f"{best['load_time_ms']:.1f} ms load time"
                if best.get("load_time_ms") is not None
                else "Load time was not measured because the existing loaded engine was reused."
            ),
        ],
        "caveat": _core.BENCHMARK_CAVEAT,
    }


# Core suite functions resolve the scorer by module global at runtime.
_core.score_benchmark_results = score_benchmark_results


async def run_benchmark_suite(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _sanitize_run(await _core.run_benchmark_suite(*args, **kwargs))


async def certify_context_depth(*args: Any, **kwargs: Any):
    result = await _core.certify_context_depth(*args, **kwargs)
    if result.error is not None:
        result.error = _safe_error(result.error)
    return result


def __getattr__(name: str):
    return getattr(_core, name)


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(_core.main())
