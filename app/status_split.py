"""Split model lifecycle polling from cached telemetry and cursor-based events.

The legacy ``/v1/system/status`` contract remains available, but it is composed from
lightweight model state, a coalesced telemetry cache, and the bounded event stream.
This keeps external clients compatible while allowing the WebGUI to poll each data
class at an appropriate cadence.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.telemetry import cpu_stats, disk_stats, gpu_stats, memory_stats
from runtime import device_check

logger = logging.getLogger("ov-llm.status")

_TELEMETRY_TTL_SECONDS = 5.0
_MANAGER_INSTALL_FLAG = "_STATUS_SPLIT_MANAGER_INSTALLED"
_ROUTE_INSTALL_FLAG = "_ovllm_status_split_routes_installed"


@dataclass
class _TelemetryCache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    payload: dict[str, Any] | None = None
    refreshed_monotonic: float = 0.0


def _available_devices() -> list[str]:
    """Isolate driver discovery so the lightweight endpoint never invokes it."""

    return device_check.available_devices()


def _core_manager_class():
    """Resolve the core manager lazily to avoid the app.config import cycle."""

    from app.model_manager_core import ModelManager as CoreModelManager

    return CoreModelManager


async def _require_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    supplied = authorization.removeprefix("Bearer ")
    if not any(secrets.compare_digest(supplied, key) for key in configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def install_status_manager_extension() -> None:
    """Add stable, thread-safe event cursors without changing existing consumers."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _MANAGER_INSTALL_FLAG, False):
        return

    original_init = manager_class.__init__
    original_emit_event = manager_class.emit_event
    original_recent_events = manager_class.recent_events

    @functools.wraps(original_init)
    def init_with_event_cursor(self, *args: Any, **kwargs: Any) -> None:
        self._event_sequence = 0
        self._event_cursor_lock = threading.RLock()
        original_init(self, *args, **kwargs)

    def event_lock(self) -> threading.RLock:
        lock = getattr(self, "_event_cursor_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._event_cursor_lock = lock
        return lock

    @functools.wraps(original_emit_event)
    def emit_event_with_cursor(self, level: str, message: str) -> None:
        # Event emission can originate from request threads, worker callbacks, and
        # asyncio tasks. Keep deque append, sequence increment, and ID assignment in
        # one critical section so cursor order always matches event order.
        with event_lock(self):
            original_emit_event(self, level, message)
            self._event_sequence = int(getattr(self, "_event_sequence", 0)) + 1
            if self._events:
                self._events[-1]["id"] = self._event_sequence

    @functools.wraps(original_recent_events)
    def recent_events_with_cursor(self) -> list[dict]:
        with event_lock(self):
            return [dict(event) for event in original_recent_events(self)]

    def recent_events_page(self, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        safe_cursor = max(int(cursor), 0)
        safe_limit = min(max(int(limit), 1), 100)
        with event_lock(self):
            events = [dict(event) for event in self._events]
            latest_cursor = int(getattr(self, "_event_sequence", 0))

        # Defensive normalization for managers created before this extension was
        # installed in a test process. Production managers receive IDs at emission.
        next_missing_id = max(latest_cursor - len(events) + 1, 1)
        for event in events:
            event_id = event.get("id")
            if not isinstance(event_id, int) or event_id < 1:
                event["id"] = next_missing_id
            next_missing_id = max(next_missing_id + 1, int(event["id"]) + 1)
        if events:
            latest_cursor = max(latest_cursor, int(events[-1]["id"]))

        first_cursor = int(events[0]["id"]) if events else latest_cursor + 1
        reset_required = safe_cursor > latest_cursor or (
            bool(events) and safe_cursor > 0 and safe_cursor < first_cursor - 1
        )
        candidates = (
            events
            if reset_required
            else [event for event in events if int(event["id"]) > safe_cursor]
        )
        page = candidates[:safe_limit]
        if page:
            next_cursor = int(page[-1]["id"])
        elif reset_required:
            next_cursor = latest_cursor
        else:
            next_cursor = safe_cursor
        return {
            "object": "list",
            "data": page,
            "cursor": safe_cursor,
            "next_cursor": next_cursor,
            "latest_cursor": latest_cursor,
            "has_more": len(candidates) > len(page),
            "reset_required": reset_required,
        }

    manager_class.__init__ = init_with_event_cursor
    manager_class.emit_event = emit_event_with_cursor
    manager_class.recent_events = recent_events_with_cursor
    manager_class.recent_events_page = recent_events_page
    setattr(manager_class, _MANAGER_INSTALL_FLAG, True)


def _lifecycle_catalog_entry(manager: Any, model_id: str) -> dict[str, Any]:
    """Build a lifecycle row without invoking the hardware advisor snapshot."""

    entry = _core_manager_class().catalog_entry(manager, model_id)
    capability = getattr(manager, "cancellation_capability", None)
    if callable(capability):
        entry.update(capability(model_id))
    return entry


def _lifecycle_catalog_entries(manager: Any) -> list[dict[str, Any]]:
    return [_lifecycle_catalog_entry(manager, model_id) for model_id in manager.catalog]


def _model_advisor_snapshot(manager: Any) -> dict[str, Any]:
    """Collect advisor metadata without allowing one model to fail the refresh."""

    advisors: dict[str, Any] = {}
    for model_id in list(manager.catalog):
        try:
            entry = manager.catalog_entry(model_id)
        except Exception:  # noqa: BLE001 - telemetry should degrade per model
            logger.exception("Could not collect advisor telemetry for '%s'", model_id)
            continue
        advisor = entry.get("advisor")
        if isinstance(advisor, dict):
            advisors[model_id] = advisor
    return advisors


def _advisor_summary_snapshot(manager: Any) -> dict[str, Any]:
    """Collect the aggregate advisor summary from stable runtime snapshots."""

    advisor = getattr(manager, "advisor", None)
    if advisor is None:
        return {}
    try:
        return advisor.summary(dict(manager.engines), dict(manager.devices))
    except Exception:  # noqa: BLE001 - request metrics remain useful without advisor data
        logger.exception("Could not collect aggregate advisor telemetry")
        return {}


def _merge_model_advisor(
    models: dict[str, Any],
    advisors: dict[str, Any] | None,
) -> dict[str, Any]:
    advisor_map = advisors if isinstance(advisors, dict) else {}
    available = []
    for raw in models.get("available", []):
        entry = dict(raw)
        advisor = advisor_map.get(entry.get("id"))
        if isinstance(advisor, dict):
            entry["advisor"] = copy.deepcopy(advisor)
        available.append(entry)
    return {**models, "available": available}


def _model_snapshot(request: Request) -> dict[str, Any]:
    manager = request.app.state.manager
    settings = request.app.state.settings
    entries = _lifecycle_catalog_entries(manager)
    return {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "device": {
            "default": settings.device,
            "mock": manager.force_mock,
            "loaded": dict(manager.devices),
            "busy": manager.any_busy(),
        },
        "models": {
            "loaded": list(manager.engines.keys()),
            "count": len(manager.engines),
            "loading_count": manager.loading_count(),
            "available": entries,
        },
    }


def _live_metrics(manager: Any, cached_metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Keep request counters live while retaining cached advisor aggregates."""

    metrics = _core_manager_class().metrics_summary(manager)
    cached = cached_metrics if isinstance(cached_metrics, dict) else {}
    advisor = cached.get("advisor")
    if isinstance(advisor, dict):
        metrics["advisor"] = copy.deepcopy(advisor)
    return metrics


def _cache_view(
    cache: _TelemetryCache,
    manager: Any,
    *,
    now: float,
    hit: bool,
    stale: bool = False,
) -> dict[str, Any]:
    if cache.payload is None:
        raise RuntimeError("Telemetry cache is empty.")
    payload = copy.deepcopy(cache.payload)
    payload["metrics"] = _live_metrics(manager, payload.get("metrics"))
    payload["cache"] = {
        "hit": hit,
        "stale": stale,
        "ttl_seconds": _TELEMETRY_TTL_SECONDS,
        "age_seconds": round(max(now - cache.refreshed_monotonic, 0.0), 3),
    }
    return payload


async def _telemetry_snapshot(request: Request, *, refresh: bool = False) -> dict[str, Any]:
    manager = request.app.state.manager
    settings = request.app.state.settings
    cache: _TelemetryCache = request.app.state.status_telemetry_cache
    now = time.monotonic()

    if (
        not refresh
        and cache.payload is not None
        and now - cache.refreshed_monotonic < _TELEMETRY_TTL_SECONDS
    ):
        return _cache_view(cache, manager, now=now, hit=True)

    async with cache.lock:
        now = time.monotonic()
        if (
            not refresh
            and cache.payload is not None
            and now - cache.refreshed_monotonic < _TELEMETRY_TTL_SECONDS
        ):
            return _cache_view(cache, manager, now=now, hit=True)

        try:
            gpu, disk, available, model_advisor, advisor_summary = await asyncio.gather(
                asyncio.to_thread(gpu_stats),
                asyncio.to_thread(
                    disk_stats,
                    settings.models_dir,
                    cache_seconds=_TELEMETRY_TTL_SECONDS,
                ),
                asyncio.to_thread(_available_devices),
                asyncio.to_thread(_model_advisor_snapshot, manager),
                asyncio.to_thread(_advisor_summary_snapshot, manager),
            )
            metrics = _core_manager_class().metrics_summary(manager)
            metrics["advisor"] = advisor_summary
            payload = {
                "schema_version": 1,
                "generated_at": int(time.time()),
                "memory": memory_stats(),
                "cpu": cpu_stats(),
                "gpu": gpu,
                "device": {
                    "default": settings.device,
                    "mock": manager.force_mock,
                    "available": available,
                    "suggestions": device_check.suggested_device_targets(available),
                },
                "disk": {
                    "models_dir": str(settings.models_dir.resolve()),
                    **disk,
                },
                "metrics": metrics,
                "model_advisor": model_advisor,
            }
        except Exception as exc:
            if cache.payload is not None:
                return _cache_view(cache, manager, now=now, hit=True, stale=True)
            raise HTTPException(
                status_code=503,
                detail="System telemetry is temporarily unavailable.",
            ) from exc

        cache.payload = payload
        cache.refreshed_monotonic = time.monotonic()
        return _cache_view(cache, manager, now=cache.refreshed_monotonic, hit=False)


def _events_page(request: Request, cursor: int, limit: int) -> dict[str, Any]:
    manager = request.app.state.manager
    if hasattr(manager, "recent_events_page"):
        return manager.recent_events_page(cursor, limit)
    events = [dict(event) for event in manager.recent_events()][-limit:]
    return {
        "object": "list",
        "data": events,
        "cursor": cursor,
        "next_cursor": cursor,
        "latest_cursor": cursor,
        "has_more": False,
        "reset_required": False,
    }


def register_status_split_routes(app: FastAPI) -> None:
    """Register split status APIs before the legacy server route is declared."""

    if getattr(app.state, "status_split_routes_registered", False):
        return
    app.state.status_telemetry_cache = _TelemetryCache()

    router = APIRouter(dependencies=[Depends(_require_access)])

    @router.get("/v1/models/status", tags=["models"])
    async def model_status(request: Request):
        return JSONResponse(_model_snapshot(request), headers={"Cache-Control": "no-store"})

    @router.get("/v1/system/telemetry", tags=["system"])
    async def system_telemetry(request: Request, refresh: bool = Query(default=False)):
        payload = await _telemetry_snapshot(request, refresh=refresh)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/v1/events", tags=["events"])
    async def events(
        request: Request,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return JSONResponse(
            _events_page(request, cursor, limit),
            headers={"Cache-Control": "no-store"},
        )

    # Compatibility route. Because this router is installed during FastAPI
    # construction, it precedes the historical route later declared by server.py.
    @router.get("/v1/system/status", tags=["system"])
    async def legacy_system_status(request: Request):
        model_payload = _model_snapshot(request)
        telemetry_payload = await _telemetry_snapshot(request)
        manager = request.app.state.manager
        models = _merge_model_advisor(
            model_payload["models"],
            telemetry_payload.get("model_advisor"),
        )
        return JSONResponse(
            {
                **telemetry_payload,
                "device": {
                    **telemetry_payload.get("device", {}),
                    **model_payload.get("device", {}),
                },
                "models": models,
                "events": manager.recent_events(),
                "split_status": {
                    "models_endpoint": "/v1/models/status",
                    "telemetry_endpoint": "/v1/system/telemetry",
                    "events_endpoint": "/v1/events",
                    "telemetry_ttl_seconds": _TELEMETRY_TTL_SECONDS,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)
    app.state.status_split_routes_registered = True


def install_status_split_routes_extension() -> None:
    """Install split status routes on InferBridge FastAPI applications."""

    if getattr(FastAPI, _ROUTE_INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_split_status(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_status_split_routes(self)

    FastAPI.__init__ = init_with_split_status  # type: ignore[method-assign]
    setattr(FastAPI, _ROUTE_INSTALL_FLAG, True)


__all__ = [
    "install_status_manager_extension",
    "install_status_split_routes_extension",
    "register_status_split_routes",
]
