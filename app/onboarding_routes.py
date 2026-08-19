"""FastAPI routes for the packaged desktop first-run workflow."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.config import Settings
from app.diagnostics_privacy import sanitize_text
from app.local_request_security import require_safe_browser_origin
from app.onboarding_models import (
    CancelPreparationResponse,
    CompleteOnboardingRequest,
    ConnectionConfigurationResponse,
    ModelRecommendationResponse,
    NpuReadinessResponse,
    OnboardingStatusResponse,
    PreparationProgressResponse,
    PrepareModelRequest,
    RestartOnboardingResponse,
    SystemScanResponse,
)
from app.onboarding_service import OnboardingService


def _api_key_auth(settings: Settings):
    async def require_key(
        authorization: str | None = Header(default=None),
    ) -> None:
        configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
        if not configured:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        supplied = authorization.removeprefix("Bearer ")
        if not any(secrets.compare_digest(supplied, key) for key in configured):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return require_key


def _state_change_auth(settings: Settings):
    require_api_key = _api_key_auth(settings)

    async def require_key(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        require_safe_browser_origin(request)
        await require_api_key(authorization)

    return require_key


def _bad_request(exc: Exception) -> HTTPException:
    message = sanitize_text(exc, limit=300).strip() or "The onboarding request could not be completed."
    return HTTPException(status_code=400, detail=message)


def register_onboarding_routes(
    app: Any,
    *,
    service: OnboardingService,
    settings: Settings,
) -> None:
    router = APIRouter(
        prefix="/v1/onboarding",
        tags=["onboarding"],
        dependencies=[Depends(_api_key_auth(settings))],
    )
    mutation_auth = [Depends(_state_change_auth(settings))]

    @router.get("/documentation", include_in_schema=False)
    async def onboarding_documentation():
        return RedirectResponse(
            "https://github.com/Quazmoz/InferBridge/blob/main/docs/FIRST_RUN.md",
            status_code=307,
        )

    @router.get("/status", response_model=OnboardingStatusResponse)
    async def onboarding_status():
        return service.status()

    @router.get("/system-scan", response_model=SystemScanResponse)
    async def onboarding_system_scan(refresh: bool = Query(default=False)):
        try:
            return await _off_thread(service.system_scan, refresh=refresh)
        except Exception as exc:
            raise _bad_request(exc) from exc

    @router.get("/npu-readiness", response_model=NpuReadinessResponse)
    async def onboarding_npu_readiness(refresh: bool = Query(default=False)):
        try:
            return await _off_thread(service.npu_readiness, refresh=refresh)
        except Exception as exc:
            raise _bad_request(exc) from exc

    @router.get("/recommendation", response_model=ModelRecommendationResponse)
    async def onboarding_recommendation(refresh: bool = Query(default=False)):
        try:
            return await _off_thread(service.recommendation, refresh=refresh)
        except Exception as exc:
            raise _bad_request(exc) from exc

    @router.post(
        "/prepare",
        response_model=PreparationProgressResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=mutation_auth,
    )
    async def onboarding_prepare(request: PrepareModelRequest):
        try:
            return await service.start_preparation(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=sanitize_text(exc, limit=300)) from exc
        except (KeyError, ValueError) as exc:
            raise _bad_request(exc) from exc

    @router.get("/preparation/{job_id}", response_model=PreparationProgressResponse)
    async def onboarding_preparation(job_id: str):
        try:
            return service.progress(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=sanitize_text(exc, limit=300)) from exc

    @router.post(
        "/preparation/{job_id}/cancel",
        response_model=CancelPreparationResponse,
        dependencies=mutation_auth,
    )
    async def onboarding_cancel(job_id: str):
        try:
            return await service.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=sanitize_text(exc, limit=300)) from exc

    @router.post(
        "/complete",
        response_model=ConnectionConfigurationResponse,
        dependencies=mutation_auth,
    )
    async def onboarding_complete(request: CompleteOnboardingRequest):
        try:
            return service.complete(request.job_id)
        except (KeyError, ValueError) as exc:
            raise _bad_request(exc) from exc

    @router.post(
        "/restart",
        response_model=RestartOnboardingResponse,
        dependencies=mutation_auth,
    )
    async def onboarding_restart():
        return service.restart()

    @router.get("/connection", response_model=ConnectionConfigurationResponse)
    async def onboarding_connection():
        try:
            return service.connection_configuration()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=sanitize_text(exc, limit=300)) from exc

    app.include_router(router)


async def _off_thread(function, /, **kwargs):
    import asyncio

    return await asyncio.to_thread(function, **kwargs)
