"""Loopback-only tray controls and authenticated browser desktop operations routes."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.brand import DISPLAY_NAME
from app.config import Settings
from app.desktop_operations import DesktopOperationsService
from app.desktop_operations_models import (
    BenchmarkControlResponse,
    DesktopControlResponse,
    DesktopOperationsStatusResponse,
    DiagnosticsExportResponse,
    HardwareScanControlResponse,
)
from app.diagnostics import redact_path
from app.local_request_security import require_safe_browser_origin


def _state_change_auth(settings: Settings):
    async def require_key(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        require_safe_browser_origin(request)
        configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
        if not configured:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        supplied = authorization.removeprefix("Bearer ")
        if not any(secrets.compare_digest(supplied, key) for key in configured):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return require_key


def _require_loopback(request: Request) -> None:
    host = str(request.client.host if request.client else "")
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Desktop controls are loopback-only")


def _control_dependency(control_token: str):
    async def require_control(
        request: Request,
        x_desktop_control: str | None = Header(default=None),
    ) -> None:
        _require_loopback(request)
        if not x_desktop_control or not secrets.compare_digest(x_desktop_control, control_token):
            raise HTTPException(status_code=403, detail="Invalid desktop control token")

    return require_control


def register_desktop_operations_routes(
    app: Any,
    *,
    service: DesktopOperationsService,
    settings: Settings,
    instance_nonce: str,
    control_token: str,
) -> None:
    control = [Depends(_control_dependency(control_token))]
    mutation_auth = [Depends(_state_change_auth(settings))]

    @app.get("/desktop/instance", include_in_schema=False)
    async def desktop_instance(request: Request):
        _require_loopback(request)
        return {
            "application": DISPLAY_NAME,
            "instance_nonce": instance_nonce,
            "port": service.endpoint_port,
            "api_contract_version": "1",
        }

    @app.post(
        "/desktop/control/shutdown",
        response_model=DesktopControlResponse,
        include_in_schema=False,
        dependencies=control,
    )
    async def desktop_shutdown():
        callback = getattr(app.state, "shutdown_callback", None)
        if callback is None:
            raise HTTPException(status_code=409, detail="Desktop shutdown is unavailable")
        app.state.shutting_down = True
        service.manager._model_manager_shutting_down = True
        asyncio.get_running_loop().call_later(0.2, callback)
        return DesktopControlResponse(
            status="shutting_down",
            message="The server is draining active requests and shutting down.",
        )

    @app.get(
        "/desktop/control/status",
        response_model=DesktopOperationsStatusResponse,
        include_in_schema=False,
        dependencies=control,
    )
    async def desktop_control_status():
        return service.status().to_dict()

    @app.post(
        "/desktop/control/hardware-scan",
        response_model=HardwareScanControlResponse,
        include_in_schema=False,
        dependencies=control,
    )
    async def desktop_control_hardware_scan():
        scan = await asyncio.to_thread(service.hardware_scan)
        return HardwareScanControlResponse(scan=dict(scan))

    @app.post(
        "/desktop/control/benchmark",
        response_model=BenchmarkControlResponse,
        include_in_schema=False,
        dependencies=control,
    )
    async def desktop_control_benchmark():
        try:
            benchmark = await service.run_short_benchmark()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:300]) from exc
        return BenchmarkControlResponse(benchmark=dict(benchmark))

    router = APIRouter(prefix="/v1/desktop/operations", tags=["desktop-operations"])

    @router.get("/status", response_model=DesktopOperationsStatusResponse)
    async def browser_operations_status():
        return service.status().to_dict()

    @router.post(
        "/diagnostics/export",
        response_model=DiagnosticsExportResponse,
        dependencies=mutation_auth,
    )
    async def browser_export_diagnostics():
        try:
            result = await asyncio.to_thread(service.export_diagnostics)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc
        return DiagnosticsExportResponse(
            filename=result.path.name,
            path=redact_path(result.path),
            included_categories=list(result.included_categories),
            excluded_categories=list(result.excluded_categories),
            collection_errors=list(result.manifest.get("collection_errors") or []),
        )

    @router.post(
        "/restart-server",
        response_model=DesktopControlResponse,
        dependencies=mutation_auth,
    )
    async def browser_restart_server():
        current = service.status()
        if current.benchmark_running:
            raise HTTPException(
                status_code=409,
                detail="Wait for the short benchmark to finish before restarting the server.",
            )
        preparation = current.preparation or {}
        if preparation.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail="Cancel or finish model preparation before restarting the server.",
            )
        if not current.controller_available:
            raise HTTPException(
                status_code=409,
                detail="The tray controller is not available to restart this server.",
            )
        marker = service.paths.data_root / "restart-server.request"
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("restart\n", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail="Restart request could not be written."
            ) from exc
        callback = getattr(app.state, "shutdown_callback", None)
        if callback is None:
            raise HTTPException(status_code=409, detail="Desktop restart is unavailable")
        app.state.shutting_down = True
        service.manager._model_manager_shutting_down = True
        asyncio.get_running_loop().call_later(0.2, callback)
        return DesktopControlResponse(
            status="restarting",
            message="The tray controller will restart the local server once shutdown completes.",
        )

    app.include_router(router)
