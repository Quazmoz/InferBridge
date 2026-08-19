"""FastAPI route extension for the curated model library.

Installed during configuration import so both source-server and packaged-desktop
entry points expose the same model-library contract without duplicating server
construction logic.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import require_safe_browser_origin
from app.model_library import (
    ConvertedModelImportRequest,
    ManifestValidationError,
    ModelDefinitionImportRequest,
    ModelLibraryService,
)


class _RouteModelLibraryService(ModelLibraryService):
    """Apply route-level policy to remotely maintained definitions."""

    def apply_official_definitions(self, manifest: dict[str, Any]) -> dict[str, Any]:
        safe_manifest = copy.deepcopy(manifest)
        for entry in safe_manifest.get("catalog", {}).values():
            definition = entry.get("definition") if isinstance(entry, dict) else None
            if isinstance(definition, dict):
                definition["trust_remote_code"] = False
        return super().apply_official_definitions(safe_manifest)


def _service(request: Request) -> ModelLibraryService:
    state = request.app.state
    service = getattr(state, "model_library_service", None)
    if service is None:
        manager = getattr(state, "manager", None)
        settings = getattr(state, "settings", None)
        if manager is None or settings is None:
            raise HTTPException(status_code=503, detail="Model library is not initialized.")
        service = _RouteModelLibraryService(settings, manager)
        state.model_library_service = service
    return service


async def _require_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        require_safe_browser_origin(request)
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    supplied = authorization.removeprefix("Bearer ")
    if not any(secrets.compare_digest(supplied, key) for key in configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def register_model_library_routes(app: FastAPI) -> None:
    if getattr(app.state, "model_library_routes_registered", False):
        return
    router = APIRouter(
        prefix="/v1/model-library",
        tags=["model-library"],
        dependencies=[Depends(_require_access)],
    )
    refresh_lock = asyncio.Lock()

    @router.get("")
    async def model_library(
        request: Request,
        profile: str = Query(
            default="balanced", pattern=r"^(fastest|balanced|best_quality|lowest_memory)$"
        ),
        query: str = Query(default="", max_length=160),
        include_all: bool = Query(default=False),
    ):
        try:
            return await asyncio.to_thread(
                _service(request).snapshot,
                profile=profile,
                query=query,
                include_all=include_all,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc

    @router.post("/refresh")
    async def refresh_model_library(request: Request):
        # Refresh downloads, catalog application, and fixed-name cache replacement form
        # one transaction. Serializing the route prevents overlapping requests from
        # racing on model-library-manifest.json.tmp or letting an older response replace
        # a newer cached manifest after it finishes later.
        async with refresh_lock:
            try:
                result = await _service(request).refresh_official()
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="The official model-library release manifest could not be downloaded.",
                ) from exc
            except ManifestValidationError as exc:
                raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
            snapshot = await asyncio.to_thread(_service(request).snapshot)
            return {"status": "refreshed", "result": result, "library": snapshot}

    @router.get("/export")
    async def export_model_definitions(
        request: Request,
        include_all: bool = Query(default=False),
    ):
        payload = await asyncio.to_thread(
            _service(request).export_definitions,
            include_all=include_all,
        )
        return JSONResponse(
            payload,
            headers={
                "Content-Disposition": "attachment; filename=openvino-model-definitions.json",
                "Cache-Control": "no-store",
            },
        )

    @router.post("/import-definitions")
    async def import_model_definitions(request: Request, body: ModelDefinitionImportRequest):
        manager = request.app.state.manager
        raw = (
            body.payload.get("models")
            if isinstance(body.payload.get("models"), dict)
            else body.payload
        )
        candidate_ids = set(raw) if isinstance(raw, dict) else set()
        active = [
            model_id
            for model_id in candidate_ids
            if model_id in manager.engines
            or (manager.load_tasks.get(model_id) and not manager.load_tasks[model_id].done())
            or (manager.convert_tasks.get(model_id) and not manager.convert_tasks[model_id].done())
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot replace active model definition '{active[0]}'.",
            )
        try:
            result = await asyncio.to_thread(_service(request).import_definitions, body)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:300]) from exc
        return {"status": "imported", **result}

    @router.post("/import-converted")
    async def import_converted_model(request: Request, body: ConvertedModelImportRequest):
        manager = request.app.state.manager
        model_id = body.model_id
        if body.overwrite:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Converted-model replacement is intentionally disabled. Import with a new "
                    "model ID, or unload and delete the managed copy first."
                ),
            )
        if model_id in manager.engines:
            raise HTTPException(status_code=409, detail="Unload the model before replacing it.")
        for tasks, label in (
            (manager.load_tasks, "loading"),
            (manager.convert_tasks, "converting"),
        ):
            task = tasks.get(model_id)
            if task is not None and not task.done():
                raise HTTPException(status_code=409, detail=f"Model is still {label}.")
        try:
            result = await asyncio.to_thread(_service(request).import_converted, body)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
        manager.emit_event("info", f"Imported converted OpenVINO model: {model_id}")
        return {"status": "imported", **result, "model": manager.catalog_entry(model_id)}

    app.include_router(router)
    app.state.model_library_routes_registered = True


def install_model_library_routes_extension() -> None:
    """Register model-library routes on OpenVINO Windows LLM FastAPI instances."""

    if getattr(FastAPI, "_ovllm_model_library_routes_installed", False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_model_library(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_model_library_routes(self)

    FastAPI.__init__ = init_with_model_library  # type: ignore[method-assign]
    FastAPI._ovllm_model_library_routes_installed = True  # type: ignore[attr-defined]
