from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import onboarding_service
from app.onboarding_models import PrepareModelRequest
from app.onboarding_service import OnboardingService, PreparationJob


def test_active_job_is_rejected_before_storage_catalog_mutation(monkeypatch):
    async def scenario() -> None:
        service = object.__new__(OnboardingService)
        service.manager = SimpleNamespace(
            catalog={"model": SimpleNamespace(trust_remote_code=False)},
            advisor=SimpleNamespace(
                evaluate_model=lambda *_args, **_kwargs: {
                    "compatibility": "compatible",
                    "requires_confirmation": False,
                }
            ),
        )
        service._jobs_lock = asyncio.Lock()
        service._jobs = {
            "active": PreparationJob(
                job_id="active",
                model_id="model",
                requested_device="CPU",
            )
        }
        storage_mutated = False

        def validate_storage(_raw, _model_id):
            nonlocal storage_mutated
            storage_mutated = True

        service._validate_storage_location = validate_storage
        monkeypatch.setattr(onboarding_service.registry, "is_downloaded", lambda *_args: False)
        request = PrepareModelRequest(
            model_id="model",
            device="CPU",
            model_storage_location="C:/should-not-be-applied",
            confirm_license=True,
            confirm_disk_requirement=True,
            acknowledge_warnings=True,
            trust_remote_code=False,
        )

        with pytest.raises(RuntimeError, match="already active"):
            await service.start_preparation(request)

        assert storage_mutated is False

    asyncio.run(scenario())
