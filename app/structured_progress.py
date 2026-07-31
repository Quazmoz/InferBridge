"""Server-owned model preparation operations and structured converter ingestion.

The converter owns only its JSON Lines producer stream. ``ModelManager`` assigns the
public operation ID and a monotonically increasing per-model revision, validates child
records, preserves cancellation/error terminal states, and exposes a stable API shape.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from runtime.progress_protocol import SCHEMA_VERSION, decode_progress_event

logger = logging.getLogger("ov-llm.progress")

_INSTALL_FLAG = "_STRUCTURED_PROGRESS_PROTOCOL_INSTALLED"
_TERMINAL_STATES = {"cancelled", "error"}
_CONVERSION_PHASES = {"downloading", "converting", "finalizing"}
_CONVERSION_STATUSES = {"queued_convert", "converting"}


def _ensure_state(manager: Any) -> None:
    if not hasattr(manager, "_progress_operation_meta"):
        manager._progress_operation_meta = {}
    if not hasattr(manager, "_progress_revision_counters"):
        manager._progress_revision_counters = {}
    if not hasattr(manager, "_producer_progress_revisions"):
        manager._producer_progress_revisions = {}
    if not hasattr(manager, "_structured_progress_models"):
        manager._structured_progress_models = set()


def _operation_type(manager: Any, model_id: str, phase: str) -> str:
    status = getattr(manager, "status_overrides", {}).get(model_id, {}).get("status")
    if status in _CONVERSION_STATUSES or phase in _CONVERSION_PHASES:
        return "convert"
    return "load"


def _new_operation(manager: Any, model_id: str, phase: str) -> dict[str, str]:
    _ensure_state(manager)
    operation_type = _operation_type(manager, model_id, phase)
    operation = {
        "operation_id": f"{operation_type}-{uuid.uuid4().hex}",
        "operation_type": operation_type,
    }
    manager._progress_operation_meta[model_id] = operation
    return operation


def _next_revision(manager: Any, model_id: str) -> int:
    _ensure_state(manager)
    revision = int(manager._progress_revision_counters.get(model_id, 0)) + 1
    manager._progress_revision_counters[model_id] = revision
    return revision


def _safe_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _install_registry_shape() -> None:
    from app import model_registry as registry

    if getattr(registry, "_STRUCTURED_PROGRESS_SHAPE_INSTALLED", False):
        return
    original_normalize = registry._normalize_progress

    def normalize_structured_progress(progress: dict | None, status: str, label: str) -> dict:
        payload = original_normalize(progress, status, label)
        raw = progress if isinstance(progress, dict) else {}
        revision = raw.get("revision", 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            revision = 0
        operation_type = raw.get("operation_type")
        if operation_type not in {"load", "convert"}:
            operation_type = None
        operation_id = raw.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            operation_id = None
        completed = _safe_count(raw.get("completed"))
        total = _safe_count(raw.get("total"))
        if completed is not None and total is not None and completed > total:
            completed = None
            total = None
        payload.update(
            {
                "schema_version": SCHEMA_VERSION,
                "operation_id": operation_id,
                "operation_type": operation_type,
                "revision": revision,
                "completed": completed,
                "total": total,
            }
        )
        return payload

    registry._normalize_progress = normalize_structured_progress
    registry._STRUCTURED_PROGRESS_SHAPE_INSTALLED = True


def install_structured_progress_protocol() -> None:
    """Install operation identity, revisioning, and JSON Lines stream handling."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    original_init = manager_class.__init__
    original_set_progress = manager_class._set_progress
    original_clear_progress = manager_class._clear_progress

    def init_with_progress_operations(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        _ensure_state(self)

    def set_progress_with_operation(
        self,
        model_id: str,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        append_log: str | None = None,
    ) -> None:
        _ensure_state(self)
        operation = self._progress_operation_meta.get(model_id)
        if operation is None:
            operation = _new_operation(self, model_id, phase)

        previous = getattr(self, "progress", {}).get(model_id, {})
        previous_completed = previous.get("completed")
        previous_total = previous.get("total")
        original_set_progress(
            self,
            model_id,
            phase,
            message,
            percent=percent,
            append_log=append_log,
        )
        payload = self.progress[model_id]
        payload.update(
            {
                "schema_version": SCHEMA_VERSION,
                "operation_id": operation["operation_id"],
                "operation_type": operation["operation_type"],
                "revision": _next_revision(self, model_id),
                "completed": _safe_count(previous_completed),
                "total": _safe_count(previous_total),
            }
        )

    def clear_progress_operation(self, model_id: str) -> None:
        _ensure_state(self)
        original_clear_progress(self, model_id)
        self._progress_operation_meta.pop(model_id, None)
        self._structured_progress_models.discard(model_id)
        stale_keys = [
            key for key in self._producer_progress_revisions if key[0] == model_id
        ]
        for key in stale_keys:
            self._producer_progress_revisions.pop(key, None)

    async def read_structured_conversion_stream(
        self,
        model_id: str,
        cfg: Any,
        stream: Any,
    ) -> list[str]:
        if stream is None:
            return []
        _ensure_state(self)

        lines: list[str] = []
        while True:
            raw = await stream.readline()
            if not raw:
                break
            decoded = raw.decode(errors="replace")
            line = self._sanitize_progress_line(decoded)
            if not line:
                continue

            try:
                event = decode_progress_event(decoded.strip())
            except ValueError as exc:
                logger.warning(
                    "Ignored malformed converter progress event for '%s': %s",
                    model_id,
                    exc,
                )
                lines.append("Ignored malformed structured progress event.")
                continue

            status = getattr(self, "status_overrides", {}).get(model_id, {}).get("status")
            current_phase = getattr(self, "progress", {}).get(model_id, {}).get("phase")
            if status in _TERMINAL_STATES or current_phase in _TERMINAL_STATES:
                continue

            if event is not None:
                if event.model_id is not None and event.model_id != model_id:
                    logger.warning(
                        "Ignored converter progress for model '%s' while preparing '%s'",
                        event.model_id,
                        model_id,
                    )
                    continue
                producer_key = (model_id, event.operation_id)
                previous_revision = self._producer_progress_revisions.get(producer_key, 0)
                if event.revision <= previous_revision:
                    continue
                self._producer_progress_revisions[producer_key] = event.revision
                self._structured_progress_models.add(model_id)
                safe_message = self._sanitize_progress_line(event.message, limit=180)
                if not safe_message:
                    safe_message = event.phase.replace("_", " ").title()
                lines.append(safe_message)
                self._set_progress(
                    model_id,
                    event.phase,
                    safe_message,
                    percent=event.percent,
                    append_log=safe_message,
                )
                payload = self.progress.get(model_id)
                if payload is not None:
                    payload["completed"] = event.completed
                    payload["total"] = event.total
                continue

            # Backward-compatible fallback for older converters and useful human logs.
            lines.append(line)
            if model_id in self._structured_progress_models:
                current = self.progress.get(model_id, {})
                self._set_progress(
                    model_id,
                    str(current.get("phase") or "converting"),
                    str(current.get("message") or f"Preparing {cfg.name}…"),
                    percent=current.get("percent"),
                    append_log=line,
                )
            else:
                next_phase, next_message, next_percent = self._progress_from_converter_line(
                    line, cfg
                )
                self._set_progress(
                    model_id,
                    next_phase,
                    next_message,
                    percent=next_percent,
                    append_log=line,
                )
        return lines

    manager_class.__init__ = init_with_progress_operations
    manager_class._set_progress = set_progress_with_operation
    manager_class._clear_progress = clear_progress_operation
    manager_class._read_conversion_stream = read_structured_conversion_stream
    setattr(manager_class, _INSTALL_FLAG, True)
    _install_registry_shape()


__all__ = ["install_structured_progress_protocol"]
