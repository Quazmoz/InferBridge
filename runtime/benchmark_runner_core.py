"""Hardware benchmark runner and local result store.

The runner intentionally uses :class:`app.model_manager.ModelManager` and the
shared :class:`runtime.openvino_engine.BaseEngine` interface, so API and CLI
benchmarks exercise the same prompt formatting, device validation, engine
factory, and streaming bridge as normal chat serving.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import __version__, chat_format, model_registry
from app.config import BASE_DIR, Settings
from app.model_manager import ModelManager
from runtime import device_check
from runtime.openvino_engine import BaseEngine, GenParams

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard runtime dependency
    psutil = None  # type: ignore[assignment]

DEFAULT_BENCHMARK_PROMPT = (
    "You are running a local hardware benchmark. Reply with two concise bullet "
    "points about why measuring this exact machine matters."
)
DEFAULT_BENCHMARK_DEVICES = ("CPU", "GPU", "NPU", "AUTO")
BENCHMARK_METHODOLOGY_VERSION = 2
BENCHMARK_CAVEAT = (
    "AUTO, MULTI, and HETERO are OpenVINO routing modes. Requested and actual "
    "devices are reported separately. Results describe only this local run and "
    "are not a general speed guarantee."
)


@dataclass
class BenchmarkResult:
    run_id: str
    model_id: str
    source_model: str
    backend: str
    weight_format: str
    requested_device: str
    actual_device: str | None
    load_time_ms: float | None
    time_to_first_token_ms: float | None
    total_latency_ms: float | None
    prompt_tokens: int
    completion_tokens: int
    tokens_sec: float | None
    success: bool
    error: str | None
    timestamp: str
    runs: int = 1
    score: float | None = None
    decode_tokens_sec: float | None = None
    prefill_tokens_sec: float | None = None
    peak_process_ram_mb: float | None = None
    warmup_runs: int = 0
    samples: list[dict[str, Any]] | None = None
    statistics: dict[str, Any] | None = None
    stability: dict[str, Any] | None = None
    parameter_count_b: float | None = None
    architecture_type: str | None = None
    active_parameters_b: float | None = None
    num_experts: int | None = None
    active_experts: int | None = None
    estimated_weight_footprint_gb: float | None = None
    synthetic: bool = False


@dataclass
class ContextDepthResult:
    """Certification-safe facts for one deterministic context-depth trial."""

    model_id: str
    requested_device: str
    actual_device: str | None
    requested_context: int
    prompt_tokens: int
    tokens_generated: int
    configured_max_context: int
    reserved_output_tokens: int
    beyond_requested_context: int
    beyond_rejected: bool
    passed: bool
    error: str | None
    timestamp: str


class BenchmarkStore:
    """Small JSON-backed store for benchmark runs.

    The outer store intentionally remains schema version 1. Benchmark Lab adds
    additive run/result metadata and a methodology version so existing stores
    continue to be readable by older InferBridge builds.
    """

    def __init__(self, path: str | Path, *, max_runs: int = 100) -> None:
        self.path = Path(path)
        self.max_runs = max_runs
        self._lock = threading.Lock()

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["runs"])

    def latest(self) -> dict[str, Any] | None:
        runs = self.list_runs()
        return runs[-1] if runs else None

    def append(self, run: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data["runs"].append(run)
            if self.max_runs > 0:
                data["runs"] = data["runs"][-self.max_runs :]
            self._write(data)

    def clear(self) -> int:
        with self._lock:
            count = len(self._read()["runs"])
            self._write({"schema_version": 1, "runs": []})
            return count

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "runs": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "runs": []}
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            return {"schema_version": 1, "runs": []}
        return {"schema_version": int(data.get("schema_version", 1)), "runs": data["runs"]}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class _ProcessMemorySampler:
    """Best-effort peak process RSS sampler for one benchmark combination."""

    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process = None
        self._peak_bytes = 0

    def start(self) -> None:
        if psutil is None:
            return
        try:
            self._process = psutil.Process()
            self._sample()
        except Exception:
            self._process = None
            return
        self._thread = threading.Thread(
            target=self._run,
            name="inferbridge-benchmark-memory",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> float | None:
        if self._process is None:
            return None
        self._sample()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_seconds * 4, 0.2))
        self._sample()
        return round(self._peak_bytes / (1024**2), 2) if self._peak_bytes else None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        try:
            if self._process is not None:
                self._peak_bytes = max(self._peak_bytes, int(self._process.memory_info().rss))
        except Exception:
            pass


async def run_benchmark_suite(
    manager: ModelManager,
    *,
    model_ids: list[str],
    devices: list[str],
    prompt: str = DEFAULT_BENCHMARK_PROMPT,
    max_tokens: int = 64,
    runs: int = 1,
    warmup_runs: int | None = None,
) -> dict[str, Any]:
    """Run every requested model/device combination and return one persisted run."""

    suite_lock = getattr(manager, "_benchmark_suite_lock", None)
    if suite_lock is None:
        suite_lock = asyncio.Lock()
        setattr(manager, "_benchmark_suite_lock", suite_lock)

    async with suite_lock:
        return await _run_benchmark_suite_locked(
            manager,
            model_ids=model_ids,
            devices=devices,
            prompt=prompt,
            max_tokens=max_tokens,
            runs=runs,
            warmup_runs=warmup_runs,
        )


async def _run_benchmark_suite_locked(
    manager: ModelManager,
    *,
    model_ids: list[str],
    devices: list[str],
    prompt: str,
    max_tokens: int,
    runs: int,
    warmup_runs: int | None,
) -> dict[str, Any]:
    run_id = f"bench-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    started_at = _utc_now()
    normalized_models = _dedupe(model_ids)
    normalized_devices = _dedupe(
        [device_check.validate_device_expression(device) for device in devices]
    )
    measured_runs = max(int(runs), 1)
    resolved_warmups = (
        _infer_warmup_runs(measured_runs)
        if warmup_runs is None
        else max(int(warmup_runs), 0)
    )
    preset = _benchmark_preset(measured_runs, int(max_tokens), resolved_warmups)
    environment = _safe_benchmark_environment(manager)
    hardware_fingerprint = environment.get("hardware_fingerprint")
    total_combinations = len(normalized_models) * len(normalized_devices)
    results: list[dict[str, Any]] = []

    _emit_benchmark_progress(
        manager,
        f"starting {total_combinations} combination(s) · {preset} preset",
    )

    combination = 0
    for model_id in normalized_models:
        for device in normalized_devices:
            combination += 1
            result = await benchmark_model_device(
                manager,
                run_id=run_id,
                model_id=model_id,
                device=device,
                prompt=prompt,
                max_tokens=max_tokens,
                runs=measured_runs,
                warmup_runs=resolved_warmups,
                combination_index=combination,
                combination_total=total_combinations,
            )
            results.append(asdict(result))

    recommendation = score_benchmark_results(results, mock=manager.force_mock)
    leaders = summarize_benchmark_results(results)
    return {
        "run_id": run_id,
        "benchmark_schema_version": 2,
        "methodology_version": BENCHMARK_METHODOLOGY_VERSION,
        "created_at": started_at,
        "finished_at": _utc_now(),
        "prompt": prompt,
        "models": normalized_models,
        "devices": normalized_devices,
        "preset": preset,
        "max_tokens": int(max_tokens),
        "runs_per_combo": measured_runs,
        "warmup_runs": resolved_warmups,
        "mock": manager.force_mock,
        "synthetic": manager.force_mock,
        "automatic": False,
        "hardware_fingerprint": hardware_fingerprint,
        "environment": environment,
        "methodology": {
            "version": BENCHMARK_METHODOLOGY_VERSION,
            "preset": preset,
            "warmup_runs": resolved_warmups,
            "measured_runs": measured_runs,
            "output_token_target": int(max_tokens),
            "aggregation": "median",
            "legacy_tokens_sec": (
                "completion tokens divided by complete generation latency, including TTFT"
            ),
            "decode_tokens_sec": (
                "post-first-token output tokens divided by time after first token"
            ),
            "prefill_tokens_sec": (
                "unavailable unless the runtime exposes a reliable prompt-processing boundary"
            ),
            "memory": "peak InferBridge process resident set size (RSS), not device memory",
        },
        "results": results,
        "leaders": leaders,
        "recommendation": recommendation,
        "caveat": BENCHMARK_CAVEAT,
    }


async def benchmark_model_device(
    manager: ModelManager,
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
) -> BenchmarkResult:
    """Benchmark one model/device pair, continuing failures as result rows."""

    timestamp = _utc_now()
    engine: BaseEngine | None = None
    load_time_ms: float | None = None
    prompt_tokens = 0
    peak_process_ram_mb: float | None = None
    cfg = manager.config_for(model_id)
    identity = _model_identity(manager, cfg)
    memory_sampler = _ProcessMemorySampler()

    try:
        if cfg is None:
            raise ValueError(f"Unknown model '{model_id}'.")
        if "embedding" in str(getattr(cfg, "backend", "")).lower():
            raise ValueError("Embedding models cannot be benchmarked as generation models.")
        if not manager.force_mock and not model_registry.is_downloaded(cfg, BASE_DIR):
            raise ValueError(
                "Model is not prepared locally. Prepare it explicitly before benchmarking."
            )

        model_label = getattr(cfg, "name", model_id)
        prefix = _combination_prefix(
            combination_index,
            combination_total,
            model_label,
            device,
        )
        _emit_benchmark_progress(manager, f"{prefix} · preparing")
        memory_sampler.start()

        _emit_benchmark_progress(manager, f"{prefix} · loading")
        engine, load_time_s = await manager.build_temporary_engine(model_id, device)
        load_time_ms = _ms(load_time_s)
        max_prompt_len = cfg.max_prompt_len
        max_context_len = cfg.max_context_len

        _emit_benchmark_progress(manager, f"{prefix} · preparing prompt")
        loop = asyncio.get_running_loop()
        prompt_text, prompt_tokens = await loop.run_in_executor(
            None, _build_benchmark_prompt, engine, prompt, max_prompt_len
        )
        max_new_tokens = min(int(max_tokens), max(max_context_len - prompt_tokens - 8, 1))
        params = GenParams(
            max_new_tokens=max_new_tokens,
            temperature=0.0,
            top_p=1.0,
            do_sample=False,
        )

        for warmup_index in range(max(int(warmup_runs), 0)):
            _emit_benchmark_progress(
                manager,
                f"{prefix} · warming up {warmup_index + 1}/{warmup_runs}",
            )
            await _stream_generation_once(engine, prompt_text, params)

        samples: list[dict[str, Any]] = []
        for run_index in range(max(int(runs), 1)):
            _emit_benchmark_progress(
                manager,
                f"{prefix} · prefill · run {run_index + 1}/{runs}",
            )

            def on_first_token(
                *,
                _prefix: str = prefix,
                _run_index: int = run_index,
                _runs: int = runs,
            ) -> None:
                _emit_benchmark_progress(
                    manager,
                    f"{_prefix} · generating · run {_run_index + 1}/{_runs}",
                )

            generation = await _stream_generation_once(
                engine,
                prompt_text,
                params,
                on_first_token=on_first_token,
            )
            samples.append(_sample_payload(run_index + 1, generation))

        _emit_benchmark_progress(manager, f"{prefix} · finalizing")
        peak_process_ram_mb = memory_sampler.stop()
        aggregate = _aggregate_samples(samples)
        actual_device = _reported_actual_device(engine, device)
        _emit_benchmark_progress(manager, f"{prefix} · complete")

        return BenchmarkResult(
            run_id=run_id,
            model_id=model_id,
            **identity,
            requested_device=device,
            actual_device=actual_device,
            load_time_ms=load_time_ms,
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
    except Exception as exc:  # noqa: BLE001 - benchmark rows should capture failures
        peak_process_ram_mb = peak_process_ram_mb or memory_sampler.stop()
        _emit_benchmark_progress(
            manager,
            (
                f"{_combination_prefix(combination_index, combination_total, model_id, device)} "
                "· failed"
            ),
            level="warning",
        )
        return BenchmarkResult(
            run_id=run_id,
            model_id=model_id,
            **identity,
            requested_device=device,
            actual_device=_reported_actual_device(engine, device) if engine else None,
            load_time_ms=load_time_ms,
            time_to_first_token_ms=None,
            total_latency_ms=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            tokens_sec=None,
            decode_tokens_sec=None,
            prefill_tokens_sec=None,
            peak_process_ram_mb=peak_process_ram_mb,
            success=False,
            error=str(exc),
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
        if engine is not None:
            with contextlib.suppress(Exception):
                engine.close()


async def certify_context_depth(
    manager: ModelManager,
    *,
    model_id: str,
    device: str,
    requested_context: int,
) -> ContextDepthResult:
    """Generate one token after a deterministic prompt of the requested depth.

    This is a functional context-capacity check, not a performance benchmark.
    A pass requires an exact prompt token count, successful generation, and an
    actual device consistent with the requested direct device.
    """

    timestamp = _utc_now()
    engine: BaseEngine | None = None
    prompt_tokens = 0
    actual_device: str | None = None
    configured_max_context = 0
    reserved_output_tokens = 0
    beyond_requested_context = requested_context + 1
    beyond_rejected = False
    try:
        cfg = manager.config_for(model_id)
        if cfg is None:
            raise ValueError(f"Unknown model '{model_id}'.")
        configured_max_context = cfg.max_context_len
        reserved_output_tokens = cfg.max_output_tokens
        _validate_context_depth(requested_context, cfg.max_prompt_len)
        beyond_requested_context = cfg.max_prompt_len + 1
        try:
            _validate_context_depth(beyond_requested_context, cfg.max_prompt_len)
        except ValueError:
            beyond_rejected = True
        normalized_device = device_check.validate_device_expression(device)
        engine, _ = await manager.build_temporary_engine(model_id, normalized_device)
        actual_device = _reported_actual_device(engine, normalized_device)
        loop = asyncio.get_running_loop()
        prompt, prompt_tokens = await loop.run_in_executor(
            None, _build_exact_context_prompt, engine, requested_context
        )
        if prompt_tokens != requested_context:
            raise RuntimeError(
                f"Tokenizer could not construct exactly {requested_context} prompt tokens; "
                f"constructed {prompt_tokens}."
            )
        generation = await _stream_generation_once(
            engine,
            prompt,
            GenParams(max_new_tokens=1, temperature=0.0, top_p=1.0, do_sample=False),
        )
        tokens_generated = int(generation["completion_tokens"])
        if tokens_generated < 1:
            raise RuntimeError("Generation completed without producing a token.")
        if not _device_matches_request(normalized_device, actual_device):
            raise RuntimeError(
                f"Requested device {normalized_device} but runtime reported "
                f"{actual_device or 'unknown'}."
            )
        if requested_context == cfg.max_prompt_len and not beyond_rejected:
            raise RuntimeError("The first prompt depth beyond the configured maximum was accepted.")
        return ContextDepthResult(
            model_id=model_id,
            requested_device=normalized_device,
            actual_device=actual_device,
            requested_context=requested_context,
            prompt_tokens=prompt_tokens,
            tokens_generated=tokens_generated,
            configured_max_context=configured_max_context,
            reserved_output_tokens=reserved_output_tokens,
            beyond_requested_context=beyond_requested_context,
            beyond_rejected=beyond_rejected,
            passed=True,
            error=None,
            timestamp=timestamp,
        )
    except Exception as exc:  # noqa: BLE001 - certification retains a failed result
        return ContextDepthResult(
            model_id=model_id,
            requested_device=device,
            actual_device=actual_device,
            requested_context=requested_context,
            prompt_tokens=prompt_tokens,
            tokens_generated=0,
            configured_max_context=configured_max_context,
            reserved_output_tokens=reserved_output_tokens,
            beyond_requested_context=beyond_requested_context,
            beyond_rejected=beyond_rejected,
            passed=False,
            error=str(exc),
            timestamp=timestamp,
        )
    finally:
        if engine is not None:
            with contextlib.suppress(Exception):
                engine.close()


def score_benchmark_results(
    results: list[dict[str, Any]],
    *,
    mock: bool = False,
) -> dict[str, Any]:
    """Assign balanced scores and choose a practical recommendation."""

    successes = [r for r in results if r.get("success")]
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
            "caveat": BENCHMARK_CAVEAT,
        }

    max_tps = max(_benchmark_speed(r) for r in successes) or 1.0
    min_ttft = min(_latency_for_ttft(r) for r in successes)
    min_total = min(_positive(r.get("total_latency_ms")) for r in successes) or 1.0
    min_load = min(max(_positive(r.get("load_time_ms")), 1.0) for r in successes)

    for result in results:
        if not result.get("success"):
            result["score"] = -25.0
            continue

        tps_norm = _benchmark_speed(result) / max_tps
        ttft_norm = min_ttft / _latency_for_ttft(result)
        total_norm = min_total / (_positive(result.get("total_latency_ms")) or min_total)
        load_ms = max(_positive(result.get("load_time_ms")), 1.0)
        load_norm = min_load / load_ms
        high_load_penalty = 0.0
        if load_ms > 30_000:
            high_load_penalty = min((load_ms - 30_000) / 90_000, 1.0) * 0.20

        score = (
            (0.50 * tps_norm)
            + (0.30 * ttft_norm)
            + (0.10 * total_norm)
            + (0.10 * load_norm)
        )
        score = max(0.0, (score - high_load_penalty) * 100)
        result["score"] = round(score, 2)

    best = max(successes, key=lambda item: float(item.get("score") or 0.0))
    best_speed = _benchmark_speed(best)
    summary_prefix = (
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
        "summary": summary_prefix,
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
                else "Load time was unavailable."
            ),
        ],
        "caveat": BENCHMARK_CAVEAT,
    }


def summarize_benchmark_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Return measured leaders for the Benchmark Lab summary cards."""

    successful = [row for row in results if row.get("success")]
    if not successful:
        return {
            "fastest_generation": None,
            "fastest_first_token": None,
            "best_balanced": None,
        }

    generation_rows = [row for row in successful if _benchmark_speed(row) > 0]
    ttft_rows = [
        row for row in successful if _positive_or_none(row.get("time_to_first_token_ms")) is not None
    ]
    fastest_generation = (
        max(generation_rows, key=_benchmark_speed) if generation_rows else None
    )
    fastest_ttft = (
        min(ttft_rows, key=lambda row: float(row["time_to_first_token_ms"]))
        if ttft_rows
        else None
    )
    balanced = max(successful, key=lambda row: float(row.get("score") or 0.0))

    def leader(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "model_id": row.get("model_id"),
            "weight_format": row.get("weight_format"),
            "requested_device": row.get("requested_device"),
            "actual_device": row.get("actual_device"),
            "decode_tokens_sec": row.get("decode_tokens_sec"),
            "tokens_sec": row.get("tokens_sec"),
            "time_to_first_token_ms": row.get("time_to_first_token_ms"),
            "score": row.get("score"),
        }

    return {
        "fastest_generation": leader(fastest_generation),
        "fastest_first_token": leader(fastest_ttft),
        "best_balanced": leader(balanced),
    }


def split_device_targets(raw: str) -> list[str]:
    """Split a CLI/UI device list while preserving commas inside META targets."""

    if ";" in raw:
        candidates = [part.strip() for part in raw.split(";")]
    else:
        tokens = [part.strip() for part in raw.split(",")]
        candidates: list[str] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if ":" in token:
                parts = [token]
                i += 1
                while i < len(tokens) and ":" not in tokens[i]:
                    parts.append(tokens[i])
                    i += 1
                candidates.append(",".join(parts))
            else:
                candidates.append(token)
                i += 1

    devices: list[str] = []
    for candidate in candidates:
        if not candidate:
            raise device_check.DeviceValidationError("Device list contains an empty entry.")
        devices.append(device_check.validate_device_expression(candidate))
    return devices


def _model_identity(manager: ModelManager, cfg: Any | None) -> dict[str, Any]:
    identity = {
        "source_model": cfg.source_model if cfg else "",
        "backend": cfg.backend if cfg else "",
        "weight_format": cfg.weight_format if cfg else "",
        "parameter_count_b": None,
        "architecture_type": getattr(cfg, "architecture_type", None) if cfg else None,
        "active_parameters_b": getattr(cfg, "active_parameters_b", None) if cfg else None,
        "num_experts": getattr(cfg, "num_experts", None) if cfg else None,
        "active_experts": getattr(cfg, "active_experts", None) if cfg else None,
        "estimated_weight_footprint_gb": None,
    }
    if cfg is None:
        return identity
    advisor = getattr(manager, "advisor", None)
    if advisor is None:
        return identity
    try:
        estimate = advisor.estimate_model(cfg)
        identity["parameter_count_b"] = estimate.get("parameter_count_b")
        identity["estimated_weight_footprint_gb"] = estimate.get("converted_size_gb")
    except Exception:
        pass
    return identity


def _safe_benchmark_environment(manager: ModelManager) -> dict[str, Any]:
    """Return reproducibility metadata with no paths, host names, secrets, or serials."""

    environment: dict[str, Any] = {
        "inferbridge": __version__,
        "openvino": None,
        "openvino_genai": None,
        "hardware_fingerprint": None,
        "cpu": None,
        "ram_gb": None,
        "devices": [],
    }
    advisor = getattr(manager, "advisor", None)
    if advisor is None:
        return environment
    try:
        snapshot = advisor.hardware_snapshot()
    except Exception:
        return environment

    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    cpu = snapshot.get("cpu") if isinstance(snapshot.get("cpu"), dict) else {}
    memory = snapshot.get("memory") if isinstance(snapshot.get("memory"), dict) else {}
    environment["openvino"] = runtime.get("openvino")
    environment["openvino_genai"] = runtime.get("openvino_genai")
    environment["hardware_fingerprint"] = snapshot.get("fingerprint")
    environment["cpu"] = cpu.get("name")
    environment["ram_gb"] = memory.get("total_gb")

    safe_devices = []
    for item in snapshot.get("devices") or []:
        if not isinstance(item, dict):
            continue
        safe_devices.append(
            {
                "device": item.get("device") or item.get("base"),
                "base": item.get("base"),
                "driver_version": item.get("driver_version"),
            }
        )
    environment["devices"] = safe_devices
    return environment


def _build_benchmark_prompt(
    engine: BaseEngine,
    prompt: str,
    max_prompt_len: int,
) -> tuple[str, int]:
    messages = [{"role": "user", "content": prompt}]
    return chat_format.build_prompt_within_budget(
        messages,
        engine.apply_chat_template,
        engine.count_tokens,
        max_prompt_len,
    )


def _build_exact_context_prompt(
    engine: BaseEngine,
    requested_context: int,
) -> tuple[str, int]:
    """Construct a deterministic chat prompt with an exact tokenizer count."""

    def render(characters: int) -> tuple[str, int]:
        messages = [{"role": "user", "content": "x" * characters}]
        prompt = engine.apply_chat_template(messages, add_generation_prompt=True)
        return prompt, engine.count_tokens(prompt)

    low = 0
    high = max(requested_context * 8, 64)
    while render(high)[1] < requested_context:
        high *= 2
        if high > requested_context * 256:
            break
    while low <= high:
        middle = (low + high) // 2
        _, count = render(middle)
        if count < requested_context:
            low = middle + 1
        elif count > requested_context:
            high = middle - 1
        else:
            return render(middle)
    candidates = [render(value) for value in range(max(high - 32, 0), low + 33)]
    exact = next((candidate for candidate in candidates if candidate[1] == requested_context), None)
    if exact is not None:
        return exact
    return max(
        (candidate for candidate in candidates if candidate[1] <= requested_context),
        key=lambda candidate: candidate[1],
        default=render(0),
    )


def _validate_context_depth(requested_context: int, max_prompt_len: int) -> None:
    if requested_context < 1 or requested_context > max_prompt_len:
        raise ValueError(
            f"Requested context must be between 1 and {max_prompt_len} prompt tokens."
        )


async def _stream_generation_once(
    engine: BaseEngine,
    prompt: str,
    params: GenParams,
    *,
    on_first_token: Callable[[], None] | None = None,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    first_token_at: float | None = None
    pieces: list[str] = []
    handle = engine.stream(prompt, params)
    try:
        while True:
            piece = await loop.run_in_executor(None, handle.next_chunk)
            if piece is None:
                break
            if first_token_at is None and piece:
                first_token_at = time.perf_counter()
                if on_first_token is not None:
                    on_first_token()
            pieces.append(piece)
        if handle.error is not None:
            raise handle.error
    finally:
        handle.request_stop()
        await loop.run_in_executor(None, handle.wait_closed)

    latency_s = time.perf_counter() - started
    text = "".join(pieces)
    completion_tokens = await loop.run_in_executor(None, engine.count_tokens, text)
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
        "decode_tokens_sec": _decode_tokens_sec(
            completion_tokens,
            latency_s,
            ttft_s,
        ),
    }


def _sample_payload(run_number: int, generation: dict[str, Any]) -> dict[str, Any]:
    return {
        "run": run_number,
        "completion_tokens": int(generation.get("completion_tokens") or 0),
        "time_to_first_token_ms": _optional_ms(generation.get("ttft_s")),
        "total_latency_ms": _optional_ms(generation.get("latency_s")),
        "tokens_sec": _rounded_or_none(generation.get("tokens_sec")),
        "decode_tokens_sec": _rounded_or_none(generation.get("decode_tokens_sec")),
    }


def _aggregate_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate measured samples only; callers keep warm-ups out of this list."""

    statistics_payload = {
        "decode_tokens_sec": _metric_stats(
            [sample.get("decode_tokens_sec") for sample in samples]
        ),
        "tokens_sec": _metric_stats([sample.get("tokens_sec") for sample in samples]),
        "time_to_first_token_ms": _metric_stats(
            [sample.get("time_to_first_token_ms") for sample in samples]
        ),
        "total_latency_ms": _metric_stats(
            [sample.get("total_latency_ms") for sample in samples]
        ),
        "completion_tokens": _metric_stats(
            [sample.get("completion_tokens") for sample in samples]
        ),
    }

    decode_stats = statistics_payload["decode_tokens_sec"]
    legacy_stats = statistics_payload["tokens_sec"]
    ttft_stats = statistics_payload["time_to_first_token_ms"]
    latency_stats = statistics_payload["total_latency_ms"]
    completion_stats = statistics_payload["completion_tokens"]
    stability_stats = decode_stats or legacy_stats

    return {
        "decode_tokens_sec": decode_stats.get("median") if decode_stats else None,
        "tokens_sec": legacy_stats.get("median") if legacy_stats else None,
        "time_to_first_token_ms": ttft_stats.get("median") if ttft_stats else None,
        "total_latency_ms": latency_stats.get("median") if latency_stats else None,
        "completion_tokens": (
            int(round(completion_stats["median"])) if completion_stats else 0
        ),
        "statistics": statistics_payload,
        "stability": _stability(stability_stats),
    }


def _metric_stats(values: list[Any]) -> dict[str, Any] | None:
    numeric = []
    for value in values:
        parsed = _positive_or_none(value)
        if parsed is not None:
            numeric.append(parsed)
    if not numeric:
        return None

    median = statistics.median(numeric)
    mean = statistics.fmean(numeric)
    stddev = statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
    cv_percent = (stddev / mean * 100.0) if mean > 0 else None
    return {
        "median": round(median, 3),
        "min": round(min(numeric), 3),
        "max": round(max(numeric), 3),
        "stddev": round(stddev, 3),
        "cv_percent": round(cv_percent, 2) if cv_percent is not None else None,
        "sample_count": len(numeric),
    }


def _stability(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stats:
        return None
    cv = stats.get("cv_percent")
    if cv is None:
        status = "unknown"
    elif cv <= 5:
        status = "stable"
    elif cv <= 12:
        status = "moderate"
    else:
        status = "variable"
    return {
        "status": status,
        "cv_percent": cv,
        "min": stats.get("min"),
        "max": stats.get("max"),
        "sample_count": stats.get("sample_count"),
    }


def _decode_tokens_sec(
    completion_tokens: int,
    latency_s: float,
    ttft_s: float | None,
) -> float | None:
    """Estimate steady decode throughput after the first emitted token.

    TTFT includes prompt processing and first-token decode. With the current
    stream contract, true prefill-only time is not observable. Excluding the
    first output token and TTFT is therefore the most defensible decode metric.
    """

    if completion_tokens < 2 or ttft_s is None:
        return None
    decode_seconds = latency_s - ttft_s
    if decode_seconds <= 0:
        return None
    return (completion_tokens - 1) / decode_seconds


def _infer_warmup_runs(measured_runs: int) -> int:
    if measured_runs <= 1:
        return 0
    if measured_runs <= 5:
        return 1
    return 2


def _benchmark_preset(
    measured_runs: int,
    max_tokens: int,
    warmup_runs: int,
) -> str:
    if measured_runs == 3 and max_tokens == 32 and warmup_runs == 1:
        return "quick"
    if measured_runs == 5 and max_tokens == 64 and warmup_runs == 1:
        return "standard"
    if measured_runs == 8 and max_tokens == 128 and warmup_runs == 2:
        return "thorough"
    return "custom"


def _combination_prefix(
    index: int,
    total: int,
    model_label: str,
    device: str,
) -> str:
    return (
        f"Benchmark Lab · combination {index}/{max(total, 1)} · "
        f"{model_label} · {device}"
    )


def _emit_benchmark_progress(
    manager: ModelManager,
    message: str,
    *,
    level: str = "info",
) -> None:
    emit = getattr(manager, "emit_event", None)
    if callable(emit):
        emit(level, message)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def _optional_ms(seconds: Any) -> float | None:
    try:
        return _ms(float(seconds)) if seconds is not None else None
    except (TypeError, ValueError):
        return None


def _rounded_or_none(value: Any) -> float | None:
    try:
        return round(float(value), 3) if value is not None else None
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> float:
    parsed = _positive_or_none(value)
    return parsed if parsed is not None else 0.0


def _positive_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _benchmark_speed(result: dict[str, Any]) -> float:
    decode = _positive(result.get("decode_tokens_sec"))
    return decode or _positive(result.get("tokens_sec"))


def _latency_for_ttft(result: dict[str, Any]) -> float:
    value = result.get("time_to_first_token_ms")
    if value is None:
        value = result.get("total_latency_ms")
    return max(_positive(value), 1.0)


def _reported_actual_device(engine: BaseEngine, requested_device: str) -> str | None:
    actual = getattr(engine, "actual_device", None)
    if actual:
        return str(actual)
    engine_device = getattr(engine, "device", None)
    if not engine_device:
        return None
    try:
        parsed = device_check.parse_device_expression(requested_device)
    except device_check.DeviceValidationError:
        return str(engine_device)
    if parsed.kind in {"AUTO", "MULTI", "HETERO"}:
        return None if str(engine_device) == requested_device else str(engine_device)
    return str(engine_device)


def _device_matches_request(requested_device: str, actual_device: str | None) -> bool:
    if not actual_device:
        return False
    parsed = device_check.parse_device_expression(requested_device)
    if parsed.kind in {"AUTO", "MULTI", "HETERO"}:
        return True
    return actual_device.split(".", 1)[0].upper() == parsed.kind


def _print_table(run: dict[str, Any]) -> None:
    headers = [
        "model",
        "device",
        "status",
        "load_ms",
        "ttft_ms",
        "latency_ms",
        "tokens",
        "decode_t/s",
        "score",
    ]
    rows = []
    for result in run["results"]:
        rows.append(
            [
                result["model_id"],
                result["requested_device"],
                "ok" if result["success"] else "fail",
                _fmt(result.get("load_time_ms")),
                _fmt(result.get("time_to_first_token_ms")),
                _fmt(result.get("total_latency_ms")),
                str(result.get("completion_tokens") or 0),
                _fmt(
                    result.get("decode_tokens_sec")
                    if result.get("decode_tokens_sec") is not None
                    else result.get("tokens_sec")
                ),
                _fmt(result.get("score")),
            ]
        )
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], min(len(cell), 48))

    def line(values: list[str]) -> str:
        clipped = [value if len(value) <= 48 else value[:45] + "..." for value in values]
        return "  ".join(value.ljust(widths[idx]) for idx, value in enumerate(clipped))

    print(line(headers))
    print(line(["-" * width for width in widths]))
    for row in rows:
        print(line(row))


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


async def _main_async(args: argparse.Namespace) -> int:
    settings = Settings.from_env().replace(
        default_model=None,
        force_mock=True if args.mock else None,
        benchmark_results_file=args.output or None,
    )
    manager = ModelManager(settings)
    devices = split_device_targets(args.benchmark_devices)
    run = await run_benchmark_suite(
        manager,
        model_ids=[args.benchmark_model],
        devices=devices,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        runs=args.runs,
    )
    BenchmarkStore(settings.benchmark_results_file).append(run)
    _print_table(run)
    rec = run["recommendation"]
    print()
    print(rec["summary"])
    print(rec["caveat"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark catalog models across OpenVINO devices."
    )
    parser.add_argument(
        "--benchmark-model",
        required=True,
        help="Catalog model id from models.json",
    )
    parser.add_argument(
        "--benchmark-devices",
        default=",".join(DEFAULT_BENCHMARK_DEVICES),
        help="Device targets, e.g. CPU,GPU,NPU,AUTO or CPU;AUTO:NPU,GPU,CPU",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_BENCHMARK_PROMPT,
        help="Prompt used for every run",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=64,
        help="Generated token limit per run",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Measured generation runs per model/device",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force the mock engine for route/CI validation",
    )
    parser.add_argument("--output", type=Path, help="Benchmark JSON store path")
    args = parser.parse_args(argv)

    if args.max_tokens < 1:
        parser.error("--max-tokens must be at least 1")
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    try:
        return asyncio.run(_main_async(args))
    except device_check.DeviceValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
