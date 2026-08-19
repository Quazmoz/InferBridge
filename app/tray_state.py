"""Pure tray-state derivation and menu availability rules."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.brand import DISPLAY_NAME


class TrayPhase(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    READY = "ready"
    PREPARING = "preparing"
    WARNING = "warning"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TraySnapshot:
    phase: TrayPhase = TrayPhase.UNKNOWN
    server_status: str = "Unknown"
    port: int | None = None
    live: bool = False
    ready: bool = False
    active_model_id: str | None = None
    active_model_name: str | None = None
    requested_device: str | None = None
    actual_device: str | None = None
    preparation_stage: str | None = None
    preparation_percent: float | None = None
    benchmark_running: bool = False
    api_key_configured: bool = False
    controller_available: bool = True
    warning: str | None = None
    error: str | None = None
    recent_events: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TrayMenuState:
    open_chat: bool
    start_server: bool
    stop_server: bool
    restart_server: bool
    run_hardware_scan: bool
    run_benchmark: bool
    copy_connection: bool
    open_model_folder: bool
    open_log_folder: bool
    export_diagnostics: bool
    start_with_windows: bool


def _boolean(value: Any, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _recent_events(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = payload.get("events")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value[-50:] if isinstance(item, Mapping))


def _first_loaded_model(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    explicit = payload.get("active_model")
    if isinstance(explicit, Mapping):
        return explicit
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    for item in models:
        if isinstance(item, Mapping) and item.get("is_loaded") is True:
            return item
    return None


def snapshot_from_status(
    payload: Mapping[str, Any] | None,
    *,
    port: int | None,
    process_running: bool,
    starting: bool = False,
    unexpected_exit: str | None = None,
) -> TraySnapshot:
    if unexpected_exit:
        return TraySnapshot(
            phase=TrayPhase.ERROR,
            server_status="Server exited unexpectedly",
            port=port,
            error=unexpected_exit,
        )
    if starting:
        return TraySnapshot(
            phase=TrayPhase.STARTING,
            server_status="Starting",
            port=port,
        )
    if not process_running:
        return TraySnapshot(
            phase=TrayPhase.STOPPED,
            server_status="Stopped",
            port=port,
        )
    if not isinstance(payload, Mapping):
        return TraySnapshot(
            phase=TrayPhase.UNKNOWN,
            server_status="Status unavailable",
            port=port,
            warning="The local server is running but status could not be read.",
        )

    live = _boolean(payload.get("live"))
    ready = _boolean(payload.get("ready"))
    preparing = payload.get("preparation")
    active = _first_loaded_model(payload)
    actual = None
    requested = None
    model_id = None
    model_name = None
    if active:
        model_id = str(active.get("id") or active.get("model_id") or "") or None
        model_name = str(active.get("name") or model_id or "") or None
        requested = str(active.get("requested_device") or "") or None
        actual = str(active.get("actual_device") or active.get("device") or "") or None

    recent_events = _recent_events(payload)
    stage = None
    percent = None
    if isinstance(preparing, Mapping) and preparing.get("status") == "running":
        stage = str(preparing.get("stage_label") or preparing.get("stage") or "Preparing model")
        raw_percent = preparing.get("percent")
        if isinstance(raw_percent, int | float) and not isinstance(raw_percent, bool):
            candidate = float(raw_percent)
            if math.isfinite(candidate):
                percent = max(0.0, min(candidate, 100.0))
        return TraySnapshot(
            phase=TrayPhase.PREPARING,
            server_status="Preparing model",
            port=port,
            live=live,
            ready=ready,
            active_model_id=model_id,
            active_model_name=model_name,
            requested_device=requested,
            actual_device=actual,
            preparation_stage=stage,
            preparation_percent=percent,
            benchmark_running=_boolean(payload.get("benchmark_running")),
            api_key_configured=_boolean(payload.get("api_key_configured")),
            controller_available=_boolean(payload.get("controller_available"), default=True),
            recent_events=recent_events,
        )

    error = str(payload.get("error") or "").strip() or None
    warning = str(payload.get("warning") or "").strip() or None
    if error:
        phase = TrayPhase.ERROR
        label = "Error"
    elif not live:
        phase = TrayPhase.WARNING
        label = "Server unavailable"
    elif not ready:
        phase = TrayPhase.WARNING
        label = "Server busy"
    elif warning:
        phase = TrayPhase.WARNING
        label = "Ready with warning"
    else:
        phase = TrayPhase.READY
        label = "Ready"

    return TraySnapshot(
        phase=phase,
        server_status=label,
        port=port,
        live=live,
        ready=ready,
        active_model_id=model_id,
        active_model_name=model_name,
        requested_device=requested,
        actual_device=actual,
        benchmark_running=_boolean(payload.get("benchmark_running")),
        api_key_configured=_boolean(payload.get("api_key_configured")),
        controller_available=_boolean(payload.get("controller_available"), default=True),
        warning=warning,
        error=error,
        recent_events=recent_events,
    )


def menu_state(
    snapshot: TraySnapshot,
    *,
    models_dir: Path | None,
    logs_dir: Path | None,
    diagnostics_dir: Path | None,
    portable: bool,
) -> TrayMenuState:
    running = snapshot.phase not in {TrayPhase.STOPPED, TrayPhase.ERROR} or snapshot.live
    server_usable = snapshot.live
    generation_model_loaded = bool(snapshot.active_model_id)
    return TrayMenuState(
        open_chat=server_usable,
        start_server=not running,
        stop_server=running,
        restart_server=running
        and not snapshot.benchmark_running
        and snapshot.phase is not TrayPhase.PREPARING,
        run_hardware_scan=server_usable,
        run_benchmark=server_usable
        and generation_model_loaded
        and not snapshot.benchmark_running
        and snapshot.phase is not TrayPhase.PREPARING,
        copy_connection=server_usable and snapshot.port is not None,
        open_model_folder=models_dir is not None,
        open_log_folder=logs_dir is not None,
        export_diagnostics=diagnostics_dir is not None,
        start_with_windows=not portable,
    )


def tooltip(snapshot: TraySnapshot) -> str:
    lines = [DISPLAY_NAME, snapshot.server_status]
    if snapshot.active_model_name or snapshot.active_model_id:
        lines.append(f"Model: {snapshot.active_model_name or snapshot.active_model_id}")
    if snapshot.actual_device:
        lines.append(f"Device: {snapshot.actual_device}")
    elif snapshot.requested_device:
        lines.append(f"Requested device: {snapshot.requested_device}")
    if snapshot.preparation_stage:
        detail = snapshot.preparation_stage
        if snapshot.preparation_percent is not None:
            detail += f" ({snapshot.preparation_percent:.0f}%)"
        lines.append(detail)
    return "\n".join(lines)[:127]


def connection_information(port: int, *, api_key_configured: bool) -> dict[str, str]:
    if not 1 <= int(port) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    base_url = f"http://127.0.0.1:{int(port)}/v1"
    chat_url = f"http://127.0.0.1:{int(port)}/"
    placeholder = "<configured-local-api-key>" if api_key_configured else "<not-required>"
    environment = f"OPENAI_BASE_URL={base_url}\nOPENAI_API_KEY={placeholder}"
    python = (
        "from openai import OpenAI\n\n"
        "client = OpenAI(\n"
        f'    base_url="{base_url}",\n'
        f'    api_key="{placeholder}",\n'
        ")"
    )
    return {
        "api_base_url": base_url,
        "chat_url": chat_url,
        "environment": environment,
        "python": python,
        "openai_configuration": f"{environment}\n\n{python}",
    }
