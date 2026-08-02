"""Versioned, atomic persistence for operational onboarding state."""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.onboarding_models import OnboardingStatusResponse
from runtime.device_check import normalize_device

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StateLoadResult:
    state: dict[str, Any]
    recovered: bool = False
    recovery_message: str | None = None


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "completed": False,
        "restart_requested": False,
        "selected_model": None,
        "selected_device": None,
        "actual_device": None,
        "model_storage_location": None,
        "last_hardware_fingerprint": None,
        "last_benchmark_reference": None,
        "completed_app_version": None,
    }


def _normalized_text(value: Any, *, limit: int = 1024) -> str | None:
    if value in (None, ""):
        return None
    text = "".join(char for char in str(value) if char.isprintable()).strip()
    return text[:limit] or None


def migrate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Onboarding state must be a JSON object.")
    version = int(raw.get("schema_version", 0) or 0)
    if version < 0 or version > SCHEMA_VERSION:
        raise ValueError("Unsupported onboarding state version.")

    state = default_state()
    for key in state:
        if key in raw:
            state[key] = raw[key]
    state["schema_version"] = SCHEMA_VERSION
    state["completed"] = bool(state["completed"])
    state["restart_requested"] = bool(state["restart_requested"])
    for key in (
        "selected_model",
        "actual_device",
        "model_storage_location",
        "last_hardware_fingerprint",
        "last_benchmark_reference",
        "completed_app_version",
    ):
        state[key] = _normalized_text(state.get(key))

    selected_device = _normalized_text(state.get("selected_device"))
    if selected_device is not None:
        try:
            selected_device = normalize_device(selected_device)
        except ValueError:
            # A stale or manually edited device must not crash desktop startup.
            # Restart first-run selection while retaining models and all other data.
            selected_device = None
            state["completed"] = False
            state["restart_requested"] = True
    state["selected_device"] = selected_device
    return state


class OnboardingStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> StateLoadResult:
        with self._lock:
            if not self.path.exists():
                return StateLoadResult(default_state())
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
                return StateLoadResult(migrate_state(raw))
            except (OSError, TypeError, ValueError):
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                with contextlib.suppress(OSError):
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(self.path.read_bytes())
                return StateLoadResult(
                    default_state(),
                    recovered=True,
                    recovery_message=(
                        "The saved first-run state was unreadable. Existing models were retained "
                        "and the setup wizard was restarted."
                    ),
                )

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = migrate_state(state)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        return normalized

    def update(self, **changes: Any) -> dict[str, Any]:
        current = self.load().state
        current.update(changes)
        return self.save(current)

    def status(self) -> OnboardingStatusResponse:
        loaded = self.load()
        return OnboardingStatusResponse(
            **loaded.state,
            state_recovered=loaded.recovered,
            recovery_message=loaded.recovery_message,
        )

    def restart(self) -> OnboardingStatusResponse:
        state = self.update(completed=False, restart_requested=True)
        return OnboardingStatusResponse(**state)
