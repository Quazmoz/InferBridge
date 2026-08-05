"""Live model lifecycle: loading, unloading, deleting, and serving engines.

Holds all mutable runtime state (which engines are loaded on which device, the
per-model generation locks, and transient load status) and orchestrates
background loads without blocking request handling. Heavy work (building an
``LLMPipeline``) runs in a thread-pool executor, serialized so two large models
don't load into memory at once.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import gc
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from app import errors, model_registry as registry
from app.config import BASE_DIR, Settings
from runtime import device_check
from runtime.openvino_engine import BaseEngine, GenParams, GenResult, StreamHandle, create_engine

logger = logging.getLogger("ov-llm.manager")

TASKKILL_TIMEOUT_SECONDS = 5.0
CONVERTER_EXIT_TIMEOUT_SECONDS = 5.0
# Backstop deadline for one native engine build. The stage-aware preparation watchdog
# owns the user-visible policy when installed; this only catches a build that hangs
# with no watchdog present.
DEFAULT_ENGINE_BUILD_TIMEOUT_SECONDS = 1800.0

_PROGRESS_PERCENT_RE = re.compile(r"(?<!\d)(100(?:\.0+)?|[1-9]?\d(?:\.\d+)?)\s*%")
_PROGRESS_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_PROGRESS_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# --- Resolution errors (mapped to HTTP status codes by the server) ---------


class ModelResolutionError(Exception):
    """Base class for failures resolving a model to a ready engine."""


class UnknownModel(ModelResolutionError):
    pass


class ModelNotLoaded(ModelResolutionError):
    pass


class ModelLoading(ModelResolutionError):
    pass


class NoModelsLoaded(ModelResolutionError):
    pass


class ModelManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog: dict[str, registry.ModelConfig] = registry.load_catalog(settings.models_file)
        # OpenVINO absent or explicitly disabled -> everything runs on the mock engine.
        self.force_mock: bool = settings.force_mock or not device_check.is_openvino_available()

        self.engines: dict[str, BaseEngine] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.devices: dict[str, str] = {}
        self.load_tasks: dict[str, asyncio.Task] = {}
        self.convert_tasks: dict[str, asyncio.Task] = {}
        self.status_overrides: dict[str, dict] = {}
        # Per-model preparation progress shown by the API/UI. This is intentionally
        # bounded and sanitized because converter output may include noisy CLI text.
        self.progress: dict[str, dict] = {}
        # Cumulative per-model request metrics (since server start).
        self.metrics: dict[str, dict] = {}
        # Bounded activity log for the UI (newest last).
        self._events: collections.deque[dict] = collections.deque(maxlen=50)
        self._load_lock = asyncio.Lock()
        self._convert_lock = asyncio.Lock()
        self._gen_lock = asyncio.Lock()
        self._active_generations = 0
        self._drain_event = asyncio.Event()
        self._drain_event.set()

    @contextlib.asynccontextmanager
    async def _track_generation(self):
        async with self._gen_lock:
            self._active_generations += 1
            self._drain_event.clear()
        try:
            yield
        finally:
            async with self._gen_lock:
                self._active_generations -= 1
                if self._active_generations == 0:
                    self._drain_event.set()

    # --- status helpers ----------------------------------------------------

    def _set_status(self, model_id: str, status: str, error: str | None = None) -> None:
        self.status_overrides[model_id] = {
            "status": status,
            "error": error,
            "updated_at": int(time.time()),
        }

    def _clear_status(self, model_id: str) -> None:
        self.status_overrides.pop(model_id, None)

    # --- progress helpers --------------------------------------------------

    def _sanitize_progress_line(self, text: str, *, limit: int = 240) -> str:
        line = _PROGRESS_CONTROL_RE.sub("", str(text or "")).strip()
        if not line:
            return ""
        line = _PROGRESS_SECRET_RE.sub("[redacted]", line)
        # Avoid exposing machine-specific full paths in user-facing progress. Keep
        # the most useful part of path-like strings, which is usually the filename.
        line = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)+", r"...\\", line)
        line = re.sub(r"/(?:[^/\s]+/){2,}", ".../", line)
        if len(line) > limit:
            return line[: limit - 1].rstrip() + "…"
        return line

    def _set_progress(
        self,
        model_id: str,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        append_log: str | None = None,
    ) -> None:
        now = int(time.time())
        previous = self.progress.get(model_id, {})
        started_at = previous.get("started_at") or now
        log_tail = list(previous.get("log_tail") or [])
        if append_log:
            safe_log = self._sanitize_progress_line(append_log)
            if safe_log:
                log_tail.append(safe_log)
                log_tail = log_tail[-10:]

        safe_message = (
            self._sanitize_progress_line(message, limit=180) or phase.replace("_", " ").title()
        )
        if percent is not None:
            percent = max(0.0, min(float(percent), 100.0))

        self.progress[model_id] = {
            "phase": phase,
            "message": safe_message,
            "percent": percent,
            "started_at": started_at,
            "updated_at": now,
            "log_tail": log_tail,
        }

    def _clear_progress(self, model_id: str) -> None:
        self.progress.pop(model_id, None)

    def _progress_from_converter_line(
        self, line: str, cfg: registry.ModelConfig
    ) -> tuple[str, str, float | None]:
        text = line.lower()
        percent: float | None = None
        match = _PROGRESS_PERCENT_RE.search(line)
        if match:
            with contextlib.suppress(ValueError):
                percent = float(match.group(1))

        if any(token in text for token in ("download", "fetch", "snapshot", "cache", "resolve")):
            return "downloading", f"Downloading model weights for {cfg.name}…", percent
        if any(token in text for token in ("quant", "compress", "int4", "int8")):
            return "converting", f"Quantizing {cfg.name} for OpenVINO…", percent
        if any(token in text for token in ("save", "write", "serializ")):
            return "converting", f"Saving OpenVINO IR for {cfg.name}…", percent
        if any(token in text for token in ("export", "openvino", "convert", "compile")):
            return "converting", f"Converting {cfg.name} to OpenVINO IR…", percent
        return "converting", f"Preparing {cfg.name}…", percent

    async def _read_conversion_stream(
        self,
        model_id: str,
        cfg: registry.ModelConfig,
        stream: asyncio.StreamReader | None,
    ) -> list[str]:
        if stream is None:
            return []

        lines: list[str] = []
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = self._sanitize_progress_line(raw.decode(errors="replace"))
            if not line:
                continue
            lines.append(line)
            phase, message, percent = self._progress_from_converter_line(line, cfg)
            self._set_progress(model_id, phase, message, percent=percent, append_log=line)
        return lines

    # --- activity events ---------------------------------------------------

    def emit_event(self, level: str, message: str) -> None:
        """Append an event to the bounded activity log.

        *level* is ``"info"``, ``"warning"``, or ``"error"``.
        """
        self._events.append({"timestamp": int(time.time()), "level": level, "message": message})

    def recent_events(self) -> list[dict]:
        """Return the activity log as a plain list (oldest first)."""
        return list(self._events)

    # --- queries -----------------------------------------------------------

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self.engines

    def get_lock(self, model_id: str) -> asyncio.Lock:
        return self.locks.setdefault(model_id, asyncio.Lock())

    def any_busy(self) -> bool:
        return any(lock.locked() for lock in self.locks.values())

    def loading_count(self) -> int:
        return sum(
            1
            for ov in self.status_overrides.values()
            if ov.get("status") in {"queued", "loading", "queued_convert", "converting"}
        )

    # --- request metrics ---------------------------------------------------

    def record_request(
        self, model_id: str, prompt_tokens: int, completion_tokens: int, latency_s: float
    ) -> None:
        """Accumulate one served request's token + latency totals for a model."""
        m = self.metrics.setdefault(
            model_id,
            {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_latency_s": 0.0},
        )
        m["requests"] += 1
        m["prompt_tokens"] += int(prompt_tokens)
        m["completion_tokens"] += int(completion_tokens)
        m["total_latency_s"] += max(float(latency_s), 0.0)

        cfg = self.catalog.get(model_id)
        name = cfg.name if cfg else model_id
        tps = round(completion_tokens / latency_s, 1) if latency_s > 0 else 0.0
        self.emit_event("info", f"Generated {completion_tokens} tokens using {name} ({tps} t/s)")

    def metrics_summary(self) -> dict:
        """Per-model and aggregate request metrics for the status endpoint."""
        per_model: dict[str, dict] = {}
        total_requests = total_prompt = total_completion = 0
        total_latency = 0.0
        for model_id, m in self.metrics.items():
            reqs = m["requests"]
            per_model[model_id] = {
                "requests": reqs,
                "prompt_tokens": m["prompt_tokens"],
                "completion_tokens": m["completion_tokens"],
                "avg_latency_ms": round(m["total_latency_s"] / reqs * 1000, 1) if reqs else 0.0,
            }
            total_requests += reqs
            total_prompt += m["prompt_tokens"]
            total_completion += m["completion_tokens"]
            total_latency += m["total_latency_s"]
        return {
            "totals": {
                "requests": total_requests,
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "avg_latency_ms": round(total_latency / total_requests * 1000, 1)
                if total_requests
                else 0.0,
            },
            "per_model": per_model,
        }

    def resolve_engine(self, model_id: str) -> BaseEngine:
        """Return the engine for a request, with sensible fallbacks.

        Raises a :class:`ModelResolutionError` subclass the server maps to a 4xx/5xx.
        """
        if model_id in self.engines:
            return self.engines[model_id]

        if model_id in self.catalog:
            override = self.status_overrides.get(model_id, {})
            if override.get("status") in {"queued", "loading", "queued_convert", "converting"}:
                raise ModelLoading(f"Model '{model_id}' is still loading")
            raise ModelNotLoaded(f"Model '{model_id}' is not loaded")

        # Unknown id: fall back to the configured default, then any loaded model.
        default = self.settings.default_model
        if default and default in self.engines:
            return self.engines[default]
        if self.engines:
            return next(iter(self.engines.values()))
        raise NoModelsLoaded("No models are loaded")

    def config_for(self, model_id: str) -> registry.ModelConfig | None:
        return self.catalog.get(model_id)

    # --- catalog entries (UI/API) -----------------------------------------

    def catalog_entry(self, model_id: str) -> dict:
        cfg = self.catalog[model_id]
        loaded = model_id in self.engines
        override = self.status_overrides.get(model_id, {})
        status = override.get("status")
        queued = status == "queued"
        loading = status == "loading"
        converting = status in {"queued_convert", "converting"}
        cancelled = status == "cancelled"
        error = override.get("error") if status == "error" else None
        downloaded = self.force_mock or registry.is_downloaded(cfg, BASE_DIR)
        lock = self.locks.get(model_id)
        busy = bool(lock and lock.locked())
        return registry.make_catalog_entry(
            cfg,
            loaded=loaded,
            queued=queued,
            loading=loading,
            converting=converting,
            cancelled=cancelled,
            downloaded=downloaded,
            device=self.devices.get(model_id),
            busy=busy,
            error=error,
            progress=self.progress.get(model_id),
        )

    def catalog_entries(self) -> list[dict]:
        return [self.catalog_entry(model_id) for model_id in self.catalog]

    # --- loading -----------------------------------------------------------

    def _resolve_draft_model_path(self, model_id: str, draft_model: str | None) -> str | None:
        if not draft_model:
            return None
        if draft_model == model_id:
            raise ValueError("Draft model must differ from the target model.")

        draft_cfg = self.catalog.get(draft_model)
        if draft_cfg is not None:
            if "embedding" in draft_cfg.backend.lower():
                raise ValueError(
                    f"Draft model '{draft_model}' is an embedding model; "
                    "speculative decoding requires a text-generation model."
                )
            if not self.force_mock and not registry.is_downloaded(draft_cfg, BASE_DIR):
                raise ValueError(f"Draft model '{draft_model}' is not converted locally.")
            return str(draft_cfg.abs_path(BASE_DIR))

        path = Path(draft_model).expanduser()
        path = path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()
        if not path.is_dir():
            raise ValueError(f"Draft model path does not exist or is not a directory: {path}")
        if not self.force_mock and not registry.is_openvino_model_dir(path):
            raise ValueError(f"Draft model path is not a converted OpenVINO model: {path}")
        return str(path)

    def _build_engine(
        self, model_id: str, device: str, draft_model_path: str | None = None
    ) -> BaseEngine:
        cfg = self.catalog[model_id]
        return create_engine(
            model_id=model_id,
            model_path=str(cfg.abs_path(BASE_DIR)),
            device=device,
            max_prompt_len=cfg.max_prompt_len,
            force_mock=self.force_mock,
            cache_dir=self.settings.cache_dir,
            backend=cfg.backend,
            draft_model_path=draft_model_path,
        )

    def _engine_build_timeout_seconds(self) -> float:
        """Return the backstop deadline for one native engine build.

        When the stage-aware preparation watchdog is installed its configured budget is
        authoritative, so this deadline stays above it instead of silently capping it.
        """

        config = getattr(self, "_preparation_timeouts", None)
        budgets = [
            float(value)
            for value in (
                getattr(config, "loading_seconds", None),
                getattr(config, "compilation_seconds", None),
            )
            if isinstance(value, int | float) and value > 0
        ]
        if not budgets:
            return DEFAULT_ENGINE_BUILD_TIMEOUT_SECONDS
        # A grace margin lets the watchdog publish its stage-specific error first.
        return max(budgets) + 60.0

    async def _load_task(
        self, model_id: str, device: str, draft_model_path: str | None = None
    ) -> None:
        cfg = self.catalog[model_id]
        try:
            try:
                async with self._load_lock:
                    if model_id in self.engines:
                        self._set_progress(
                            model_id, "ready", f"{cfg.name} is already loaded.", percent=100
                        )
                        self._clear_status(model_id)
                        return

                    self._set_status(model_id, "loading")
                    self._set_progress(
                        model_id, "loading", f"Checking local model files for {cfg.name}…"
                    )

                    # Validate device + on-disk model for real (non-mock) loads.
                    if not self.force_mock:
                        available = device_check.available_devices()
                        if not device_check.is_device_available(device, available):
                            raise RuntimeError(errors.format_device_error(device, available))
                        if not registry.is_downloaded(cfg, BASE_DIR):
                            if self.settings.auto_convert:
                                logger.info(
                                    "Model '%s' not found locally. Auto-convert is enabled. Starting conversion...",
                                    model_id,
                                )
                                self._set_progress(
                                    model_id,
                                    "downloading",
                                    f"{cfg.name} is not converted yet. Downloading and converting first…",
                                )
                                # Downloading and quantizing multi-gigabyte weights has no
                                # useful fixed deadline; the converter reports its own
                                # progress and is bounded by the preparation watchdog.
                                await self._convert_task(model_id, device, load_after=False)
                                self._set_status(model_id, "loading")
                                self._set_progress(
                                    model_id,
                                    "loading",
                                    f"Loading {cfg.name} on {device}…",
                                )
                                if not registry.is_downloaded(cfg, BASE_DIR):
                                    raise RuntimeError(
                                        f"Auto-conversion failed for '{cfg.name}'. Please check logs."
                                    )
                            else:
                                raise RuntimeError(
                                    errors.format_model_not_converted(
                                        cfg.name,
                                        str(cfg.abs_path(BASE_DIR)),
                                        cfg.source_model,
                                        weight_format=cfg.weight_format,
                                    )
                                )

                    self._set_progress(model_id, "loading", f"Loading {cfg.name} on {device}…")
                    loop = asyncio.get_running_loop()
                    async with asyncio.timeout(self._engine_build_timeout_seconds()):
                        engine = await loop.run_in_executor(
                            None, self._build_engine, model_id, device, draft_model_path
                        )

                    self.engines[model_id] = engine
                    self.locks[model_id] = asyncio.Lock()
                    self.devices[model_id] = engine.device
                    self._set_progress(
                        model_id,
                        "ready",
                        f"{cfg.name} is ready on {engine.device}.",
                        percent=100,
                    )
                    self._clear_status(model_id)
                    logger.info("Loaded '%s' on %s", model_id, engine.device)
                    self.emit_event("info", f"Loaded {cfg.name} on {engine.device}")
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a status
                self.engines.pop(model_id, None)
                self.locks.pop(model_id, None)
                self.devices.pop(model_id, None)
                message = errors.format_model_load_error(exc)
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", f"Load failed: {message}")
                logger.exception("Failed to load '%s': %s", model_id, message)
                self.emit_event("error", f"Failed to load {cfg.name}: {message}")
        finally:
            self.load_tasks.pop(model_id, None)

    def schedule_load(
        self,
        model_id: str,
        device: str | None = None,
        *,
        draft_model: str | None = None,
    ) -> asyncio.Task | None:
        if model_id not in self.catalog:
            logger.warning("Refusing to load unknown model '%s'", model_id)
            return None
        if model_id in self.engines:
            cfg = self.catalog[model_id]
            self._set_progress(model_id, "ready", f"{cfg.name} is already loaded.", percent=100)
            self._clear_status(model_id)
            return None

        existing = self.load_tasks.get(model_id)
        if existing and not existing.done():
            return existing

        draft_model_path = self._resolve_draft_model_path(model_id, draft_model)
        device = device_check.normalize_device(device or self.settings.device)
        cfg = self.catalog[model_id]
        self._set_status(model_id, "queued")
        self._set_progress(model_id, "queued", f"Queued {cfg.name} to load on {device}…")
        task = asyncio.create_task(
            self._load_task(model_id, device, draft_model_path=draft_model_path)
        )
        self.load_tasks[model_id] = task
        return task

    async def build_temporary_engine(self, model_id: str, device: str) -> tuple[BaseEngine, float]:
        """Build an engine for short-lived benchmark use without registering it.

        The same load lock, validation rules, and engine factory are used as the
        normal model lifecycle, but the returned engine is owned by the caller
        and must be closed by the caller.
        """
        if model_id not in self.catalog:
            raise UnknownModel(f"Unknown model '{model_id}'")

        cfg = self.catalog[model_id]
        device = device_check.normalize_device(device)
        async with self._load_lock:
            if not self.force_mock:
                available = device_check.available_devices()
                if not device_check.is_device_available(device, available):
                    raise RuntimeError(errors.format_device_error(device, available))
                if not registry.is_downloaded(cfg, BASE_DIR):
                    raise RuntimeError(
                        errors.format_model_not_converted(
                            cfg.name, str(cfg.abs_path(BASE_DIR)), cfg.source_model
                        )
                    )

            loop = asyncio.get_running_loop()
            start = time.perf_counter()
            engine = await loop.run_in_executor(None, self._build_engine, model_id, device)
            return engine, time.perf_counter() - start

    # --- conversion --------------------------------------------------------

    async def _convert_task(
        self,
        model_id: str,
        device: str,
        load_after: bool,
        weight_format: str | None = None,
        group_size: int | None = None,
        ratio: float | None = None,
        sym: bool | None = None,
        trust_remote_code: bool | None = None,
    ) -> None:
        cfg = self.catalog[model_id]
        proc: asyncio.subprocess.Process | None = None
        try:
            try:
                async with self._convert_lock:
                    if registry.is_downloaded(cfg, BASE_DIR) and not weight_format:
                        self._set_progress(
                            model_id, "ready", f"{cfg.name} is already converted.", percent=100
                        )
                        self._clear_status(model_id)
                        if load_after:
                            self.schedule_load(model_id, device)
                        return

                    if not cfg.source_model:
                        raise RuntimeError(
                            f"{cfg.name} has no Hugging Face source model configured."
                        )

                    self._set_status(model_id, "converting")
                    self._set_progress(
                        model_id,
                        "downloading",
                        f"Starting download and OpenVINO conversion for {cfg.name}…",
                    )
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "runtime.model_converter",
                        "--id",
                        model_id,
                        *(["--weight-format", weight_format] if weight_format else []),
                        *(["--group-size", str(group_size)] if group_size is not None else []),
                        *(["--ratio", str(ratio)] if ratio is not None else []),
                        *(["--sym"] if sym else []),
                        *(
                            ["--trust-remote-code"]
                            if trust_remote_code is True
                            else ["--no-trust-remote-code"]
                            if trust_remote_code is False
                            else []
                        ),
                        cwd=str(BASE_DIR),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        # Force UTF-8 stdio so tqdm/Transformers progress glyphs
                        # (e.g. U+258F) never abort a conversion on a legacy
                        # Windows code page. The converter also self-reconfigures.
                        env={**os.environ, "PYTHONIOENCODING": "utf-8:replace"},
                    )
                    stdout_task = asyncio.create_task(
                        self._read_conversion_stream(model_id, cfg, proc.stdout)
                    )
                    stderr_task = asyncio.create_task(
                        self._read_conversion_stream(model_id, cfg, proc.stderr)
                    )
                    return_code = await proc.wait()
                    stdout_lines, stderr_lines = await asyncio.gather(stdout_task, stderr_task)
                    output_lines = [*stdout_lines, *stderr_lines]

                    if return_code != 0:
                        tail = "\n".join(output_lines[-12:]).strip()
                        raise RuntimeError(tail or f"Conversion exited with code {return_code}")

                    self._set_progress(
                        model_id,
                        "ready",
                        f"Converted {cfg.name} to OpenVINO IR.",
                        percent=100,
                    )
                    self._clear_status(model_id)
                    logger.info("Converted '%s' to %s", model_id, cfg.abs_path(BASE_DIR))
                    self.emit_event("info", f"Converted {cfg.name} to OpenVINO IR")
                    if load_after:
                        self.schedule_load(model_id, device)
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    try:
                        await self._terminate_conversion_process(proc)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.error("Unexpected converter cleanup failure during cancellation.")
                self._set_status(model_id, "cancelled")
                self._set_progress(model_id, "cancelled", f"Conversion cancelled for {cfg.name}.")
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a statement/status
                message = errors.format_model_convert_error(exc)
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", f"Conversion failed: {message}")
                logger.exception("Failed to convert '%s': %s", model_id, message)
                self.emit_event("error", f"Conversion failed for {cfg.name}: {message}")
        finally:
            self.convert_tasks.pop(model_id, None)

    async def _terminate_conversion_process(self, proc: asyncio.subprocess.Process) -> None:
        """Terminate the converter and its Optimum child process tree."""

        if proc.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                exit_code = await asyncio.wait_for(killer.wait(), timeout=TASKKILL_TIMEOUT_SECONDS)
                if exit_code != 0:
                    self._safe_kill_process(proc)
            except (TimeoutError, OSError):
                self._safe_kill_process(proc)
        else:
            self._safe_kill_process(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=CONVERTER_EXIT_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Converter process did not exit within the cancellation timeout.")
        except (ProcessLookupError, OSError):
            logger.warning("Converter process cleanup failed after cancellation.")

    @staticmethod
    def _safe_kill_process(proc: asyncio.subprocess.Process) -> bool:
        """Best-effort kill that treats process disappearance as a normal race."""

        if proc.returncode is not None:
            return False
        try:
            proc.kill()
            return True
        except (ProcessLookupError, OSError):
            return False

    def schedule_convert(
        self,
        model_id: str,
        device: str | None = None,
        *,
        load_after: bool = True,
        weight_format: str | None = None,
        group_size: int | None = None,
        ratio: float | None = None,
        sym: bool | None = None,
        trust_remote_code: bool | None = None,
    ) -> asyncio.Task | None:
        if model_id not in self.catalog:
            logger.warning("Refusing to convert unknown model '%s'", model_id)
            return None
        if model_id in self.engines:
            raise ValueError(
                f"Model '{model_id}' is loaded. Unload it before converting or replacing its files."
            )

        existing = self.convert_tasks.get(model_id)
        if existing and not existing.done():
            return existing

        cfg = self.catalog[model_id]
        if registry.is_downloaded(cfg, BASE_DIR) and not weight_format:
            self._set_progress(model_id, "ready", f"{cfg.name} is already converted.", percent=100)
            if load_after:
                return self.schedule_load(model_id, device)
            return None

        device = device_check.normalize_device(device or self.settings.device)
        self._set_status(model_id, "queued_convert")
        self._set_progress(model_id, "queued", f"Queued {cfg.name} for OpenVINO conversion…")
        task = asyncio.create_task(
            self._convert_task(
                model_id,
                device,
                load_after,
                weight_format=weight_format,
                group_size=group_size,
                ratio=ratio,
                sym=sym,
                trust_remote_code=trust_remote_code,
            )
        )
        self.convert_tasks[model_id] = task
        return task

    # --- unload / delete ---------------------------------------------------

    def unload(self, model_id: str) -> bool:
        engine = self.engines.pop(model_id, None)
        self.locks.pop(model_id, None)
        self.devices.pop(model_id, None)
        self._clear_status(model_id)
        self._clear_progress(model_id)
        if engine is None:
            return False
        cfg = self.catalog.get(model_id)
        name = cfg.name if cfg else model_id
        with contextlib.suppress(Exception):
            engine.close()
        gc.collect()
        self.emit_event("info", f"Unloaded {name}")
        return True

    def delete(self, model_id: str) -> dict:
        """Delete a model's on-disk OpenVINO IR directory. Returns freed-space info."""
        from app.telemetry import dir_size_bytes

        cfg = self.catalog[model_id]
        model_dir = cfg.abs_path(BASE_DIR)
        freed = 0
        deleted = False

        if model_dir.exists():
            self._ensure_within_models_dir(model_dir)
            if model_dir.is_symlink():
                raise ValueError(
                    f"Refusing to delete symlink target: {model_dir}. Remove the symlink manually."
                )
            freed = dir_size_bytes(model_dir)
            shutil.rmtree(model_dir)
            deleted = True

        self._clear_status(model_id)
        self._clear_progress(model_id)
        self.load_tasks.pop(model_id, None)
        self.convert_tasks.pop(model_id, None)
        if deleted:
            freed_gb = round(freed / (1024**3), 2)
            self.emit_event("info", f"Deleted {cfg.name} ({freed_gb} GB freed)")
        return {"deleted": deleted, "freed_bytes": freed}

    def _ensure_within_models_dir(self, path: Path) -> None:
        root = self.settings.models_dir.resolve()
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"Refusing to delete a path outside {root}: {resolved}")

    # --- lifecycle ---------------------------------------------------------

    def reload_catalog(self) -> None:
        self.catalog = registry.load_catalog(self.settings.models_file)

    def register_model(self, req: Any) -> registry.ModelConfig:
        """Register a new custom model in the catalog and save to models.json."""
        if req.model_id in self.catalog:
            raise ValueError(f"Model ID '{req.model_id}' is already registered in the catalog.")

        # Build new config
        cfg = registry.ModelConfig(
            id=req.model_id,
            name=req.name,
            description=req.description
            or f"Custom model registered via Web UI. Source: {req.source_model}",
            backend=getattr(req, "backend", "openvino-genai"),
            model_path=f"models/openvino/{req.model_id}",
            source_model=req.source_model,
            weight_format=req.weight_format,
            recommended_device=req.recommended_device,
            max_context_len=req.max_context_len,
            max_output_tokens=req.max_output_tokens,
            trust_remote_code=getattr(req, "trust_remote_code", False),
        )

        self.catalog[req.model_id] = cfg
        registry.save_catalog(self.settings.models_file, self.catalog)
        self.emit_event("info", f"Registered new custom model: {cfg.name} ({cfg.id})")
        return cfg

    async def startup(self) -> None:
        mode = "mock engine" if self.force_mock else f"device={self.settings.device}"
        self.emit_event("info", f"Server started ({mode}, {len(self.catalog)} models in catalog)")
        if self.settings.default_model:
            if self.settings.default_model in self.catalog:
                self.schedule_load(self.settings.default_model)
            else:
                logger.warning(
                    "Default model '%s' is not in the catalog; skipping auto-load",
                    self.settings.default_model,
                )

    async def shutdown(self) -> None:
        for task in list(self.load_tasks.values()):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for task in list(self.convert_tasks.values()):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Gracefully wait for in-flight requests to complete
        if self._active_generations > 0:
            logger.info(
                "Waiting for %d in-flight generation requests to drain...", self._active_generations
            )
            try:
                await asyncio.wait_for(self._drain_event.wait(), timeout=10.0)
                logger.info("All in-flight requests drained.")
            except TimeoutError:
                logger.warning(
                    "Timeout waiting for in-flight requests to drain. Proceeding with shutdown."
                )

        for model_id in list(self.engines):
            self.unload(model_id)
        self.load_tasks.clear()
        self.convert_tasks.clear()
        self.status_overrides.clear()
        self.progress.clear()

    # --- generation bridges (sync engine -> asyncio) -----------------------

    async def generate(self, engine: BaseEngine, prompt: str, params: GenParams) -> GenResult:
        async with self._track_generation():
            loop = asyncio.get_running_loop()
            lock = self.get_lock(engine.model_id)
            async with lock:
                return await loop.run_in_executor(None, engine.generate, prompt, params)

    async def stream(self, engine: BaseEngine, prompt: str, params: GenParams):
        """Async generator yielding text chunks; holds the model lock throughout."""
        async with self._track_generation():
            loop = asyncio.get_running_loop()
            lock = self.get_lock(engine.model_id)
            async with lock:
                handle: StreamHandle = await loop.run_in_executor(
                    None, engine.stream, prompt, params
                )
                try:
                    while True:
                        chunk = await loop.run_in_executor(None, handle.next_chunk)
                        if chunk is None:
                            break
                        yield chunk
                    if handle.error is not None:
                        raise handle.error
                finally:
                    # If the consumer stopped early (client disconnect), signal the
                    # worker and wait for it to finish before releasing the lock, so a
                    # later request can't call generate() on the same pipeline while a
                    # stale worker is still running on it.
                    handle.request_stop()
                    await loop.run_in_executor(None, handle.wait_closed)
