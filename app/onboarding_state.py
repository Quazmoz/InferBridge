"""Versioned, atomic persistence for operational onboarding state."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.onboarding_models import OnboardingStatusResponse
from runtime.device_check import normalize_device

SCHEMA_VERSION = 1
_STATE_READ_ATTEMPTS = 3
_STATE_READ_RETRY_SECONDS = 0.05


class UnsupportedStateVersion(ValueError):
    """The saved state belongs to a newer application schema and must be preserved."""


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
        "lan_access_enabled": False,
        "network_cors_origins": "",
    }


def _normalized_bool(value: Any, *, default: bool = False) -> bool:
    """Normalize persisted flags without treating arbitrary truthy values as enabled."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return default


def _normalized_text(value: Any, *, limit: int = 1024) -> str | None:
    if value in (None, ""):
        return None
    text = "".join(char for char in str(value) if char.isprintable()).strip()
    return text[:limit] or None


def migrate_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Onboarding state must be a JSON object.")
    version = int(raw.get("schema_version", 0) or 0)
    if version < 0:
        raise ValueError("Unsupported onboarding state version.")
    if version > SCHEMA_VERSION:
        # A rollback or older portable binary must never quarantine a valid state file
        # written by a newer InferBridge version. Failing closed preserves that state for
        # the newer version instead of silently resetting onboarding and network settings.
        raise UnsupportedStateVersion(
            f"Onboarding state schema {version} is newer than supported schema {SCHEMA_VERSION}."
        )

    state = default_state()
    for key in state:
        if key in raw:
            state[key] = raw[key]
    state["schema_version"] = SCHEMA_VERSION
    state["completed"] = _normalized_bool(state["completed"])
    state["restart_requested"] = _normalized_bool(state["restart_requested"])
    state["lan_access_enabled"] = _normalized_bool(state["lan_access_enabled"])
    for key in (
        "selected_model",
        "actual_device",
        "model_storage_location",
        "last_hardware_fingerprint",
        "last_benchmark_reference",
        "completed_app_version",
    ):
        state[key] = _normalized_text(state.get(key))
    state["network_cors_origins"] = (
        _normalized_text(state.get("network_cors_origins"), limit=2048) or ""
    )

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

    def _quarantine_corrupt_state(self) -> None:
        """Move malformed state aside so recovery is reported only once."""

        backup = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            self.path.replace(backup)
            return
        except OSError:
            pass

        # Some filesystems or security tools can block an atomic rename. Preserve a
        # best-effort copy and remove the source only after the backup is durable.
        with contextlib.suppress(OSError):
            backup.write_bytes(self.path.read_bytes())
            self.path.unlink()

    def _read_state_text(self) -> str | None:
        """Read state with short retries for transient Windows sharing violations."""

        last_error: OSError | None = None
        for attempt in range(_STATE_READ_ATTEMPTS):
            try:
                return self.path.read_text(encoding="utf-8-sig")
            except FileNotFoundError:
                return None
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _STATE_READ_ATTEMPTS:
                    time.sleep(_STATE_READ_RETRY_SECONDS)
        assert last_error is not None
        raise last_error

    def load(self) -> StateLoadResult:
        with self._lock:
            # The read is authoritative. Avoid Path.exists() here because supported
            # Python versions can suppress some filesystem OSErrors in exists() and
            # return False, which would misclassify an inaccessible state file as absent.
            text = self._read_state_text()
            if text is None:
                return StateLoadResult(default_state())

            try:
                raw = json.loads(text)
                return StateLoadResult(migrate_state(raw))
            except UnsupportedStateVersion:
                raise
            except (TypeError, ValueError):
                self._quarantine_corrupt_state()
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
        # Keep the complete read-modify-write transaction under one reentrant lock.
        # Separate load/save locks allow concurrent updates to overwrite each other.
        with self._lock:
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
