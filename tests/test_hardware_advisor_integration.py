from __future__ import annotations

import asyncio

import pytest

from app.model_manager import ModelManager, NoModelsLoaded, UnknownModel
from app.model_resolution_safety import install_model_resolution_safety
from app.ui_extension import inject_multimodal_ui

# Constructing Settings installs the safety stack, which patches
# ModelManager.resolve_engine for the whole process. Whether that had already happened
# used to depend on which test files ran first, so this module saw the bare advisor
# error in isolation and the composed one in a full run. Install it here so these tests
# always exercise the same stack the application runs.
install_model_resolution_safety()


class FakeAdvisor:
    def __init__(self, selected=None):
        self.selected = selected

    def select_loaded_model(self, profile, engines, devices):
        assert profile in {"fastest", "balanced", "best-quality", "lowest-memory", "lowest-power"}
        return self.selected


def bare_manager(selected=None):
    manager = ModelManager.__new__(ModelManager)
    manager.engines = {"text-model": object()}
    manager.devices = {"text-model": "NPU"}
    manager.advisor = FakeAdvisor(selected)
    return manager


def test_model_manager_resolves_auto_profile_to_loaded_engine():
    manager = bare_manager("text-model")
    assert manager.resolve_engine("auto:fastest") is manager.engines["text-model"]


def test_model_manager_rejects_auto_when_no_generation_model_is_loaded():
    manager = bare_manager(None)
    with pytest.raises(NoModelsLoaded, match="advisor profile 'balanced'"):
        manager.resolve_engine("auto")


def test_model_manager_rejects_unknown_auto_profile():
    manager = bare_manager(None)
    with pytest.raises(UnknownModel, match="Invalid advisor model selector"):
        manager.resolve_engine("auto:impossible")


def test_advisor_observes_the_composed_load_scheduler():
    async def exercise() -> None:
        class RecordingAdvisor:
            def __init__(self) -> None:
                self._tasks: set[asyncio.Task] = set()
                self.measured: list[object] = []
                self.benchmarked: list[tuple[str, float | None]] = []

            def measure_converted_size(self, cfg) -> None:
                self.measured.append(cfg)

            def schedule_auto_benchmark(self, manager, model_id, *, load_time_ms=None) -> None:
                self.benchmarked.append((model_id, load_time_ms))

        manager = ModelManager.__new__(ModelManager)
        cfg = object()
        manager.catalog = {"text-model": cfg}
        manager.engines = {}
        manager.devices = {}
        manager.force_mock = True
        manager._load_lock = asyncio.Lock()
        manager.advisor = RecordingAdvisor()

        async def finish_load() -> None:
            await asyncio.sleep(0)
            manager.engines["text-model"] = object()
            manager.devices["text-model"] = "NPU"

        def composed_schedule_load(model_id, device=None, *, draft_model=None):
            assert model_id == "text-model"
            assert device == "NPU"
            assert draft_model is None
            return asyncio.create_task(finish_load())

        manager.schedule_load = composed_schedule_load
        manager._install_advisor_load_hook()

        load_task = manager.schedule_load("text-model", "NPU")
        assert load_task is not None
        await load_task
        await asyncio.sleep(0)
        pending = list(manager.advisor._tasks)
        if pending:
            await asyncio.gather(*pending)

        assert manager.advisor.measured == [cfg]
        assert manager.advisor.benchmarked == [("text-model", None)]

    asyncio.run(exercise())


def test_ui_composes_vision_and_hardware_advisor_extensions_once():
    rendered = inject_multimodal_ui("<html><body></body></html>")
    rendered_twice = inject_multimodal_ui(rendered)

    assert 'id="ovllm-vision-extension"' in rendered
    assert 'id="ovllm-hardware-advisor-extension"' in rendered
    assert "Best model for this PC" in rendered
    assert "bodyData.model = `auto:${autoRoutingProfile}`" in rendered
    assert "short hardware benchmark" in rendered
    assert rendered_twice.count('id="ovllm-hardware-advisor-extension"') == 1
