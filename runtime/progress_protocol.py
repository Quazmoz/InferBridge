"""Versioned JSON Lines protocol for model preparation progress.

The converter writes one compact JSON object per stdout line. Human-readable logs stay
on stderr. The server validates every record before using it, then assigns its own
operation identity and revision for API consumers.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
import uuid
from dataclasses import dataclass
from typing import TextIO

SCHEMA_VERSION = 1
EVENT_TYPE = "inferbridge.progress"
VALID_PHASES = frozenset(
    {
        "queued",
        "resolving",
        "downloading",
        "converting",
        "finalizing",
        "loading",
        "ready",
        "cancelled",
        "error",
    }
)
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL_ID_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,240}$")


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One validated converter progress event."""

    operation_id: str
    revision: int
    phase: str
    message: str
    percent: float | None
    model_id: str | None
    completed: int | None
    total: int | None
    timestamp: float

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "type": EVENT_TYPE,
            "operation_id": self.operation_id,
            "revision": self.revision,
            "phase": self.phase,
            "message": self.message,
            "percent": self.percent,
            "model_id": self.model_id,
            "completed": self.completed,
            "total": self.total,
            "timestamp": self.timestamp,
        }


def _bounded_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 and char not in "\t" for char in text):
        return None
    return text


def _optional_count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("progress counts must be non-negative integers")
    return value


def _optional_percent(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("progress percent must be numeric")
    percent = float(value)
    if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
        raise ValueError("progress percent must be between 0 and 100")
    return percent


def decode_progress_event(line: str) -> ProgressEvent | None:
    """Decode one strict protocol line.

    ``None`` means the line is ordinary human-readable output rather than a protocol
    event. A line claiming to be a protocol event but violating the schema raises
    ``ValueError`` so callers can reject it explicitly.
    """

    try:
        payload = json.loads(str(line or ""))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != EVENT_TYPE:
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported progress schema version")

    operation_id = _bounded_text(payload.get("operation_id"), limit=128)
    if operation_id is None or _OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise ValueError("invalid progress operation id")

    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("progress revision must be a positive integer")

    phase = _bounded_text(payload.get("phase"), limit=32)
    if phase not in VALID_PHASES:
        raise ValueError("invalid progress phase")

    message = _bounded_text(payload.get("message"), limit=500)
    if message is None:
        raise ValueError("invalid progress message")

    model_id_value = payload.get("model_id")
    model_id = None
    if model_id_value is not None:
        model_id = _bounded_text(model_id_value, limit=240)
        if model_id is None or _MODEL_ID_RE.fullmatch(model_id) is None:
            raise ValueError("invalid progress model id")

    completed = _optional_count(payload.get("completed"))
    total = _optional_count(payload.get("total"))
    if completed is not None and total is not None and completed > total:
        raise ValueError("progress completed count exceeds total")

    timestamp_value = payload.get("timestamp")
    if isinstance(timestamp_value, bool) or not isinstance(timestamp_value, int | float):
        raise ValueError("invalid progress timestamp")
    timestamp = float(timestamp_value)
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("invalid progress timestamp")

    return ProgressEvent(
        operation_id=operation_id,
        revision=revision,
        phase=phase,
        message=message,
        percent=_optional_percent(payload.get("percent")),
        model_id=model_id,
        completed=completed,
        total=total,
        timestamp=timestamp,
    )


class ProgressEventEmitter:
    """Emit ordered protocol records to a JSON Lines stream."""

    def __init__(
        self,
        *,
        operation_id: str | None = None,
        model_id: str | None = None,
        stream: TextIO | None = None,
    ) -> None:
        candidate = operation_id or f"converter-{uuid.uuid4().hex}"
        if _OPERATION_ID_RE.fullmatch(candidate) is None:
            raise ValueError("invalid progress operation id")
        if model_id is not None and _MODEL_ID_RE.fullmatch(model_id) is None:
            raise ValueError("invalid progress model id")
        self.operation_id = candidate
        self.model_id = model_id
        self.stream = stream or sys.stdout
        self.revision = 0

    def emit(
        self,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> ProgressEvent:
        next_revision = self.revision + 1
        event = ProgressEvent(
            operation_id=self.operation_id,
            revision=next_revision,
            phase=phase,
            message=message,
            percent=percent,
            model_id=self.model_id,
            completed=completed,
            total=total,
            timestamp=time.time(),
        )
        # Round-trip through the decoder before publishing. This keeps the emitter and
        # parser contract synchronized and prevents malformed records reaching stdout.
        encoded = json.dumps(event.as_dict(), ensure_ascii=True, separators=(",", ":"))
        decode_progress_event(encoded)
        print(encoded, file=self.stream, flush=True)
        self.revision = next_revision
        return event


__all__ = [
    "EVENT_TYPE",
    "ProgressEvent",
    "ProgressEventEmitter",
    "SCHEMA_VERSION",
    "VALID_PHASES",
    "decode_progress_event",
]
