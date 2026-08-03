"""Stage-aware watchdogs for model download, conversion, compilation, and loading.

Conversion stages use inactivity timeouts: long operations remain valid while the
converter continues publishing structured progress or useful human output. Loading
and native OpenVINO compilation use total stage deadlines because their parent-side
heartbeat messages do not prove that the native operation itself is advancing.

A watchdog timeout never deletes Hugging Face cache entries or incomplete OpenVINO
output. Existing loaded engines are also retained by the composed load lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

logger = logging.getLogger("ov-llm.preparation-watchdog")

_INSTALL_FLAG = "_MODEL_PREPARATION_TIMEOUTS_INSTALLED"
_REGISTRY_FLAG = "_MODEL_PREPARATION_TIMEOUT_SHAPE_INSTALLED"
_STATE_ATTR = "_preparation_watchdog_states"
_TIMEOUT_ATTR = "_preparation_timeout_records"
_CONFIG_ATTR = "_preparation_timeouts"

OperationKind = Literal["load", "convert"]
PreparationStage = Literal[
    "download",
    "conversion",
    "finalization",
    "compilation",
    "loading",
]
_ResultT = TypeVar("_ResultT")

_CONVERSION_STAGES = frozenset({"download", "conversion", "finalization"})
_LOAD_STAGES = frozenset({"compilation", "loading"})


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("Config: %s=%r is not numeric; using %.0f", name, raw, default)
        return default
    if not 1.0 <= value <= 86_400.0:
        logger.warning(
            "Config: %s=%r is outside 1-86400 seconds; using %.0f",
            name,
            raw,
            default,
        )
        return default
    return value


@dataclass(frozen=True, slots=True)
class PreparationTimeouts:
    """Timeout policy for each preparation stage.

    Download, conversion, and finalization values are inactivity thresholds. Loading
    and compilation values are total stage deadlines.
    """

    download_stall_seconds: float = 600.0
    conversion_stall_seconds: float = 900.0
    finalization_stall_seconds: float = 300.0
    loading_seconds: float = 1_800.0
    compilation_seconds: float = 1_800.0
    poll_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> PreparationTimeouts:
        return cls(
            download_stall_seconds=_env_seconds("OV_LLM_DOWNLOAD_STALL_TIMEOUT_SECONDS", 600.0),
            conversion_stall_seconds=_env_seconds("OV_LLM_CONVERSION_STALL_TIMEOUT_SECONDS", 900.0),
            finalization_stall_seconds=_env_seconds(
                "OV_LLM_FINALIZATION_STALL_TIMEOUT_SECONDS", 300.0
            ),
            loading_seconds=_env_seconds("OV_LLM_LOADING_TIMEOUT_SECONDS", 1_800.0),
            compilation_seconds=_env_seconds("OV_LLM_COMPILATION_TIMEOUT_SECONDS", 1_800.0),
            poll_seconds=_env_seconds("OV_LLM_PREPARATION_WATCHDOG_POLL_SECONDS", 1.0),
        )

    def timeout_for(self, stage: PreparationStage) -> float:
        return {
            "download": self.download_stall_seconds,
            "conversion": self.conversion_stall_seconds,
            "finalization": self.finalization_stall_seconds,
            "loading": self.loading_seconds,
            "compilation": self.compilation_seconds,
        }[stage]

    @staticmethod
    def is_inactivity_timeout(stage: PreparationStage) -> bool:
        return stage in _CONVERSION_STAGES


@dataclass(slots=True)
class PreparationHeartbeat:
    operation_id: str | None
    stage: PreparationStage
    stage_started_monotonic: float
    last_progress_monotonic: float
    last_progress_at: float


@dataclass(slots=True)
class PreparationTimeoutRecord:
    operation_kind: OperationKind
    operation_id: str | None
    stage: PreparationStage
    timeout_seconds: float
    elapsed_seconds: float
    last_progress_at: float
    timed_out_at: float
    inactivity_timeout: bool
    task_identity: int
    cleanup_pending: bool = True


def _states(manager: Any) -> dict[str, PreparationHeartbeat]:
    value = getattr(manager, _STATE_ATTR, None)
    if value is None:
        value = {}
        setattr(manager, _STATE_ATTR, value)
    return value


def _records(manager: Any) -> dict[str, PreparationTimeoutRecord]:
    value = getattr(manager, _TIMEOUT_ATTR, None)
    if value is None:
        value = {}
        setattr(manager, _TIMEOUT_ATTR, value)
    return value


def _active_operation(manager: Any, model_id: str) -> bool:
    load_task = getattr(manager, "load_tasks", {}).get(model_id)
    convert_task = getattr(manager, "convert_tasks", {}).get(model_id)
    return bool(
        (load_task is not None and not load_task.done())
        or (convert_task is not None and not convert_task.done())
    )


def preparation_timeouts(manager: Any) -> PreparationTimeouts:
    value = getattr(manager, _CONFIG_ATTR, None)
    if not isinstance(value, PreparationTimeouts):
        value = PreparationTimeouts.from_env()
        setattr(manager, _CONFIG_ATTR, value)
    return value


def _classify_stage(phase: str, message: str) -> PreparationStage | None:
    normalized_phase = str(phase or "").strip().lower()
    normalized_message = str(message or "").strip().lower()
    if normalized_phase in {"resolving", "downloading"}:
        return "download"
    if normalized_phase == "converting":
        return "conversion"
    if normalized_phase == "finalizing":
        return "finalization"
    if "compil" in normalized_message:
        return "compilation"
    if normalized_phase in {"queued", "loading"}:
        return "loading"
    return None


def record_preparation_heartbeat(
    manager: Any,
    model_id: str,
    phase: str,
    message: str,
    *,
    operation_id: str | None = None,
) -> None:
    """Record one server-observed preparation heartbeat using a monotonic clock."""

    stage = _classify_stage(phase, message)
    if stage is None:
        return
    now_monotonic = time.monotonic()
    now_epoch = time.time()
    current = _states(manager).get(model_id)
    if current is not None and current.stage == stage and current.operation_id == operation_id:
        stage_started = current.stage_started_monotonic
    else:
        stage_started = now_monotonic
    _states(manager)[model_id] = PreparationHeartbeat(
        operation_id=operation_id,
        stage=stage,
        stage_started_monotonic=stage_started,
        last_progress_monotonic=now_monotonic,
        last_progress_at=now_epoch,
    )


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration_label(seconds: float) -> str:
    total = max(1, int(round(seconds)))
    if total < 60:
        return f"{total} second{'s' if total != 1 else ''}"
    minutes, remaining = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {remaining:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def timeout_message(record: PreparationTimeoutRecord, model_name: str) -> str:
    stage_label = {
        "download": "download",
        "conversion": "conversion",
        "finalization": "conversion finalization",
        "compilation": "OpenVINO compilation",
        "loading": "model loading",
    }[record.stage]
    mode = "without new progress" if record.inactivity_timeout else "in this stage"
    preservation = (
        "Downloaded cache entries and incomplete OpenVINO output were preserved."
        if record.stage in _CONVERSION_STAGES
        else "Converted model files and any previously loaded engine were preserved."
    )
    return (
        f"{model_name} timed out during {stage_label} after "
        f"{_duration_label(record.elapsed_seconds)} {mode}. "
        f"Last successful progress: {_iso_timestamp(record.last_progress_at)}. "
        f"{preservation} Use Resume preparation or Retry failed stage after cleanup completes."
    )


def _progress_timeout_metadata(record: PreparationTimeoutRecord) -> dict[str, Any]:
    return {
        "stalled_stage": record.stage,
        "last_progress_at": int(record.last_progress_at),
        "last_progress_at_iso": _iso_timestamp(record.last_progress_at),
        "timeout_seconds": record.timeout_seconds,
        "timeout_kind": "inactivity" if record.inactivity_timeout else "stage_total",
        "resumable_files_preserved": True,
        "cleanup_pending": record.cleanup_pending,
    }


def _mark_cleanup_complete(
    manager: Any,
    model_id: str,
    record: PreparationTimeoutRecord,
) -> None:
    current = _records(manager).get(model_id)
    if current is not record:
        return
    record.cleanup_pending = False
    payload = getattr(manager, "progress", {}).get(model_id)
    if isinstance(payload, dict):
        payload["cleanup_pending"] = False


def _consume_watchdog_cancellation(task: asyncio.Task[Any]) -> bool:
    """Remove one watchdog cancellation while preserving any external request."""

    return task.uncancel() == 0


async def _watch_operation(
    manager: Any,
    model_id: str,
    operation_kind: OperationKind,
    target: asyncio.Task[Any],
    *,
    operation_id: str | None,
    on_timeout: Callable[[PreparationTimeoutRecord], None],
) -> None:
    config = preparation_timeouts(manager)
    poll_seconds = max(0.001, float(config.poll_seconds))

    while not target.done():
        await asyncio.sleep(poll_seconds)
        if target.done() or model_id in _records(manager):
            return

        heartbeat = _states(manager).get(model_id)
        if heartbeat is None:
            continue
        if operation_id and heartbeat.operation_id and heartbeat.operation_id != operation_id:
            return

        if operation_kind == "convert":
            if heartbeat.stage not in _CONVERSION_STAGES:
                continue
        elif heartbeat.stage not in _LOAD_STAGES:
            continue

        timeout_seconds = max(0.001, config.timeout_for(heartbeat.stage))
        now_monotonic = time.monotonic()
        inactivity_timeout = config.is_inactivity_timeout(heartbeat.stage)
        reference = (
            heartbeat.last_progress_monotonic
            if inactivity_timeout
            else heartbeat.stage_started_monotonic
        )
        elapsed = now_monotonic - reference
        if elapsed < timeout_seconds:
            continue

        record = PreparationTimeoutRecord(
            operation_kind=operation_kind,
            operation_id=heartbeat.operation_id or operation_id,
            stage=heartbeat.stage,
            timeout_seconds=timeout_seconds,
            elapsed_seconds=elapsed,
            last_progress_at=heartbeat.last_progress_at,
            timed_out_at=time.time(),
            inactivity_timeout=inactivity_timeout,
            task_identity=id(target),
        )
        _records(manager)[model_id] = record
        on_timeout(record)
        target.cancel()
        return


async def run_with_preparation_watchdog(
    manager: Any,
    model_id: str,
    operation_kind: OperationKind,
    operation: Callable[[], Awaitable[_ResultT]],
    *,
    on_timeout: Callable[[PreparationTimeoutRecord], None],
) -> _ResultT | None:
    """Run one lifecycle operation with the appropriate stage-aware watchdog."""

    current = asyncio.current_task()
    if current is None:  # pragma: no cover - asyncio always supplies a current task
        return await operation()
    target = asyncio.create_task(operation())

    progress = getattr(manager, "progress", {}).get(model_id, {})
    operation_id = (
        str(progress.get("operation_id"))
        if isinstance(progress, dict) and progress.get("operation_id")
        else None
    )
    watcher = asyncio.create_task(
        _watch_operation(
            manager,
            model_id,
            operation_kind,
            target,
            operation_id=operation_id,
            on_timeout=on_timeout,
        ),
        name=f"preparation-watchdog-{operation_kind}-{model_id}",
    )
    try:
        result = await target
        record = _records(manager).get(model_id)
        if record is not None and record.task_identity == id(target):
            _mark_cleanup_complete(manager, model_id, record)
        return result
    except asyncio.CancelledError:
        record = _records(manager).get(model_id)
        if record is None or record.task_identity != id(target):
            raise
        _mark_cleanup_complete(manager, model_id, record)
        if current.cancelling():
            raise
        return None
    finally:
        if not watcher.done():
            watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


def _install_registry_shape() -> None:
    from app import model_registry as registry

    if getattr(registry, _REGISTRY_FLAG, False):
        return
    original_normalize = registry._normalize_progress

    def normalize_timeout_progress(progress: dict | None, status: str, label: str) -> dict:
        payload = original_normalize(progress, status, label)
        raw = progress if isinstance(progress, dict) else {}
        stalled_stage = raw.get("stalled_stage")
        if stalled_stage not in {
            "download",
            "conversion",
            "finalization",
            "compilation",
            "loading",
        }:
            return payload
        payload.update(
            {
                "stalled_stage": stalled_stage,
                "last_progress_at": raw.get("last_progress_at"),
                "last_progress_at_iso": raw.get("last_progress_at_iso"),
                "timeout_seconds": raw.get("timeout_seconds"),
                "timeout_kind": raw.get("timeout_kind"),
                "resumable_files_preserved": bool(raw.get("resumable_files_preserved", True)),
                "cleanup_pending": bool(raw.get("cleanup_pending", False)),
            }
        )
        return payload

    registry._normalize_progress = normalize_timeout_progress
    setattr(registry, _REGISTRY_FLAG, True)


def install_model_preparation_timeouts() -> None:
    """Install stage-aware watchdogs on the fully composed model manager."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    original_init = manager_class.__init__
    original_set_status = manager_class._set_status
    original_clear_status = manager_class._clear_status
    original_set_progress = manager_class._set_progress
    original_clear_progress = manager_class._clear_progress
    original_load_task = manager_class._load_task
    original_convert_task = manager_class._convert_task

    def init_with_preparation_timeouts(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        setattr(self, _CONFIG_ATTR, PreparationTimeouts.from_env())
        _states(self)
        _records(self)

    def set_status_with_timeout_guard(
        self,
        model_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if model_id in _records(self):
            return
        original_set_status(self, model_id, status, error)

    def clear_status_with_timeout_guard(self, model_id: str) -> None:
        if model_id in _records(self):
            return
        original_clear_status(self, model_id)

    def set_progress_with_heartbeat(
        self,
        model_id: str,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        append_log: str | None = None,
    ) -> None:
        record = _records(self).get(model_id)
        if record is not None:
            if phase == "ready" and not _active_operation(self, model_id):
                _records(self).pop(model_id, None)
                _states(self).pop(model_id, None)
            else:
                return

        original_set_progress(
            self,
            model_id,
            phase,
            message,
            percent=percent,
            append_log=append_log,
        )
        payload = getattr(self, "progress", {}).get(model_id, {})
        operation_id = (
            str(payload.get("operation_id"))
            if isinstance(payload, dict) and payload.get("operation_id")
            else None
        )
        record_preparation_heartbeat(
            self,
            model_id,
            phase,
            message,
            operation_id=operation_id,
        )

    def clear_progress_with_timeout_reset(self, model_id: str) -> None:
        if model_id in _records(self) and _active_operation(self, model_id):
            return
        _records(self).pop(model_id, None)
        _states(self).pop(model_id, None)
        original_clear_progress(self, model_id)

    def publish_timeout(
        self,
        model_id: str,
        record: PreparationTimeoutRecord,
    ) -> None:
        cfg = getattr(self, "catalog", {}).get(model_id)
        model_name = getattr(cfg, "name", model_id)
        message = timeout_message(record, model_name)
        original_set_status(self, model_id, "error", message)
        original_set_progress(self, model_id, "error", message)
        payload = getattr(self, "progress", {}).get(model_id)
        if isinstance(payload, dict):
            payload.update(_progress_timeout_metadata(record))
        emit_event = getattr(self, "emit_event", None)
        if callable(emit_event):
            emit_event(
                "error",
                f"{model_name} timed out during {record.stage}; resumable files were preserved",
            )
        logger.error("Preparation watchdog stopped '%s': %s", model_id, message)

    async def load_task_with_stage_timeouts(
        self,
        model_id: str,
        device: str,
        draft_model_path: str | None = None,
    ) -> None:
        async def operation() -> None:
            await original_load_task(
                self,
                model_id,
                device,
                draft_model_path=draft_model_path,
            )

        await run_with_preparation_watchdog(
            self,
            model_id,
            "load",
            operation,
            on_timeout=lambda record: publish_timeout(self, model_id, record),
        )

    async def convert_task_with_stage_timeouts(
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
        async def operation() -> None:
            await original_convert_task(
                self,
                model_id,
                device,
                load_after,
                weight_format=weight_format,
                group_size=group_size,
                ratio=ratio,
                sym=sym,
                trust_remote_code=trust_remote_code,
            )

        await run_with_preparation_watchdog(
            self,
            model_id,
            "convert",
            operation,
            on_timeout=lambda record: publish_timeout(self, model_id, record),
        )

    manager_class.__init__ = init_with_preparation_timeouts
    manager_class._set_status = set_status_with_timeout_guard
    manager_class._clear_status = clear_status_with_timeout_guard
    manager_class._set_progress = set_progress_with_heartbeat
    manager_class._clear_progress = clear_progress_with_timeout_reset
    manager_class._load_task = load_task_with_stage_timeouts
    manager_class._convert_task = convert_task_with_stage_timeouts
    setattr(manager_class, _INSTALL_FLAG, True)
    _install_registry_shape()


__all__ = [
    "PreparationHeartbeat",
    "PreparationTimeoutRecord",
    "PreparationTimeouts",
    "install_model_preparation_timeouts",
    "record_preparation_heartbeat",
    "run_with_preparation_watchdog",
    "timeout_message",
]
