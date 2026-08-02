"""Desktop-only onboarding hardening around the shared orchestration service."""

from __future__ import annotations

import asyncio
import contextlib
import platform
import re
import shutil
import time
from pathlib import Path

from app import errors, model_registry as registry
from app.config import BASE_DIR
from app.onboarding_models import (
    ItemStatus,
    NpuReadinessResponse,
    NpuState,
    OnboardingStatusResponse,
    PreparationProgressResponse,
    PreparationStage,
    PrepareModelRequest,
    SystemItem,
    SystemScanResponse,
)
from app.onboarding_service import OnboardingService, PreparationJob
from runtime import device_check

_COMPOSITE_DEVICE_KINDS = {"AUTO", "MULTI", "HETERO"}
_WINDOWS_11_MINIMUM_BUILD = 22000
_STAGE_TIMEOUT_SECONDS = {
    PreparationStage.PREPARING: 5 * 60,
    PreparationStage.DOWNLOADING: 6 * 60 * 60,
    PreparationStage.CONVERTING: 6 * 60 * 60,
    PreparationStage.VALIDATING: 15 * 60,
    PreparationStage.COMPILING: 2 * 60 * 60,
    PreparationStage.LOADING: 60 * 60,
    PreparationStage.BENCHMARKING: 15 * 60,
}


def _windows_build(version: str | None) -> int | None:
    parts = re.findall(r"\d+", str(version or ""))
    if not parts:
        return None
    candidate = parts[2] if len(parts) >= 3 else parts[-1]
    try:
        return int(candidate)
    except ValueError:
        return None


def augment_windows_scan(
    scan: SystemScanResponse,
    *,
    edition: str | None = None,
) -> SystemScanResponse:
    """Add Windows edition/build and device capability status without false failures."""

    os_info = dict(scan.hardware.get("os") or {})
    additions: list[SystemItem] = []
    warnings = list(scan.warnings)
    if str(os_info.get("system") or "").lower() == "windows":
        if edition is None:
            try:
                edition = platform.win32_edition()
            except (AttributeError, OSError):
                edition = None
        edition = str(edition or "").strip() or None
        version = str(os_info.get("version") or "").strip()
        build = _windows_build(version)
        build_status = (
            ItemStatus.UNKNOWN
            if build is None
            else ItemStatus.READY
            if build >= _WINDOWS_11_MINIMUM_BUILD
            else ItemStatus.WARNING
        )
        build_detail = (
            "Windows build information is unavailable."
            if build is None
            else "Windows 11 is the primary supported desktop target."
            if build >= _WINDOWS_11_MINIMUM_BUILD
            else "Windows 11 build 22000 or newer is the primary supported desktop target."
        )
        additions.extend(
            [
                SystemItem(
                    key="windows-edition",
                    label="Windows edition",
                    value=edition or "Unknown",
                    status=ItemStatus.READY if edition else ItemStatus.UNKNOWN,
                    detail=None if edition else "Windows edition information is unavailable.",
                ),
                SystemItem(
                    key="windows-build",
                    label="Windows version and build",
                    value=version or "Unknown",
                    status=build_status,
                    detail=build_detail,
                ),
            ]
        )
        if build is not None and build < _WINDOWS_11_MINIMUM_BUILD:
            message = "This Windows build is older than the primary Windows 11 desktop target."
            if message not in warnings:
                warnings.append(message)

    for device in scan.hardware.get("devices", []):
        capabilities = device.get("optimization_capabilities")
        if not capabilities:
            continue
        name = str(device.get("base") or device.get("device") or "device").upper()
        value = (
            ", ".join(str(item) for item in capabilities)
            if isinstance(capabilities, list)
            else str(capabilities)
        )
        additions.append(
            SystemItem(
                key=f"device-{name.lower()}-capabilities",
                label=f"{name} capabilities",
                value=value,
                status=ItemStatus.READY,
                detail="Capabilities reported by the active OpenVINO runtime.",
            )
        )

    keys = {item.key for item in scan.items}
    items = [*scan.items, *(item for item in additions if item.key not in keys)]
    return scan.model_copy(update={"items": items, "warnings": warnings})


def sanitize_system_scan(scan: SystemScanResponse) -> SystemScanResponse:
    """Remove machine-specific writable paths from the browser-facing scan payload."""

    hardware = dict(scan.hardware)
    disk = dict(hardware.get("disk") or {})
    disk.pop("models_dir", None)
    hardware["disk"] = disk
    return scan.model_copy(update={"hardware": hardware})


def actual_device_is_unresolved(actual_device: str | None) -> bool:
    """Return true when a benchmark did not identify one concrete OpenVINO device."""

    if not str(actual_device or "").strip():
        return True
    try:
        parsed = device_check.parse_device_expression(str(actual_device))
    except device_check.DeviceValidationError:
        return True
    return parsed.kind in _COMPOSITE_DEVICE_KINDS


def _classify_native_runtime_failure(job: PreparationJob) -> bool:
    """Mark tokenizer DLL failures as non-device-recoverable desktop errors."""

    if job.status != "failed":
        return False
    detail = str(job.error_detail or job.message or "")
    if not errors.is_openvino_tokenizer_runtime_error(RuntimeError(detail)):
        return False
    message = errors.format_openvino_tokenizer_runtime_error()
    job.error_code = "native_runtime_unavailable"
    job.error_detail = message
    job.message = message
    return True


def _existing_volume_anchor(candidate: Path) -> Path | None:
    anchor = candidate
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    return anchor if anchor.exists() else None


class DesktopOnboardingService(OnboardingService):
    """Add packaged-desktop privacy and recovery guarantees without duplicating lifecycle logic."""

    def system_scan(self, *, refresh: bool = False) -> SystemScanResponse:
        scan = super().system_scan(refresh=refresh)
        return sanitize_system_scan(augment_windows_scan(scan))

    def npu_readiness(self, *, refresh: bool = False) -> NpuReadinessResponse:
        result = super().npu_readiness(refresh=refresh)
        if result.state is not NpuState.NOT_DETECTED:
            return result
        snapshot = self.manager.advisor.hardware_snapshot(refresh=False)
        cpu_name = str(snapshot.get("cpu", {}).get("name") or "").lower()
        if "intel" in cpu_name and "core ultra" in cpu_name:
            return result
        return result.model_copy(
            update={
                "state": NpuState.NOT_EXPECTED,
                "title": "This PC is not expected to expose a supported Intel NPU",
                "explanation": (
                    "Continue with an OpenVINO-visible CPU or Intel GPU. Installing an NPU "
                    "driver cannot add unsupported hardware or guarantee model compatibility."
                ),
            }
        )

    def recommendation(self, *, refresh: bool = False):
        snapshot = self.manager.advisor.hardware_snapshot(refresh=refresh)
        if not snapshot.get("runtime", {}).get("mock") and not snapshot.get("available_devices"):
            raise RuntimeError(
                "OpenVINO did not report a usable CPU, GPU, or NPU device. Review the runtime "
                "and driver diagnostics, then rescan before selecting a model."
            )
        return super().recommendation(refresh=refresh)

    def status(self) -> OnboardingStatusResponse:
        status = super().status()
        model_id = status.selected_model
        if not status.completed or not model_id:
            return status

        try:
            entry = self.manager.catalog_entry(model_id)
        except (KeyError, ValueError):
            entry = None
        if entry and entry.get("is_loaded"):
            return status
        if entry and entry.get("is_loading"):
            return status

        recovered = self.state_store.update(completed=False, restart_requested=True)
        return OnboardingStatusResponse(
            **recovered,
            state_recovered=True,
            recovery_message=(
                "The previously selected model did not reload successfully. Existing model files "
                "were retained and first-run setup was reopened."
            ),
        )

    def _validate_selected_storage_capacity(self, request: PrepareModelRequest) -> None:
        raw = str(request.model_storage_location or "").strip()
        cfg = self.manager.catalog.get(request.model_id)
        if not raw or cfg is None:
            return
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return
        anchor = _existing_volume_anchor(candidate)
        if anchor is None:
            raise ValueError("The selected model storage volume is unavailable.")
        downloaded = registry.is_downloaded(cfg, BASE_DIR)
        evaluation = self.manager.advisor.evaluate_model(
            cfg,
            downloaded=downloaded,
            profile="balanced",
        )
        required_gb = float(evaluation.get("required_free_disk_gb") or 0)
        free_gb = shutil.disk_usage(anchor).free / (1024**3)
        if required_gb and free_gb < required_gb:
            raise ValueError(
                f"The selected model volume has {free_gb:.1f} GB free, but about "
                f"{required_gb:.1f} GB is required."
            )

    async def start_preparation(self, request: PrepareModelRequest) -> PreparationProgressResponse:
        self._validate_selected_storage_capacity(request)
        progress = await super().start_preparation(request)
        job = self._jobs[progress.job_id]
        watchdog = asyncio.create_task(
            self._watch_preparation(job),
            name=f"onboarding-watchdog-{job.job_id}",
        )
        watchdog.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return progress

    async def _watch_preparation(self, job: PreparationJob) -> None:
        observed_stage = job.stage
        stage_started = time.monotonic()
        while job.status == "running":
            await asyncio.sleep(1)
            if job.stage != observed_stage:
                observed_stage = job.stage
                stage_started = time.monotonic()
            timeout = _STAGE_TIMEOUT_SECONDS.get(job.stage)
            if timeout is None or time.monotonic() - stage_started <= timeout:
                continue

            message = (
                f"{job.stage_label if hasattr(job, 'stage_label') else job.stage.value.replace('_', ' ').title()} "
                "exceeded its safety timeout. Retry the operation or choose another device."
            )
            job.cancel_requested.set()
            if job.task is not None and not job.task.done():
                job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job.task
            with contextlib.suppress(Exception):
                self.manager.unload(job.model_id)
            self.state_store.update(
                completed=False,
                actual_device=None,
                last_benchmark_reference=None,
            )
            job.error_detail = message
            job.terminal(
                PreparationStage.FAILED,
                message,
                error_code=f"{observed_stage.value}_timeout",
            )
            return

    def _reject_unresolved_actual_device(self, job: PreparationJob) -> None:
        if job.status != "ready" or not actual_device_is_unresolved(job.actual_device):
            return

        message = (
            "Generation succeeded, but OpenVINO did not report one concrete actual device. "
            "Choose a direct CPU, GPU, or NPU target and retry before completing setup."
        )
        with contextlib.suppress(Exception):
            self.manager.unload(job.model_id)
        self.state_store.update(
            completed=False,
            restart_requested=False,
            actual_device=None,
            last_benchmark_reference=None,
        )
        if job.benchmark is not None:
            job.benchmark = job.benchmark.model_copy(
                update={"success": False, "actual_device": None, "error": message}
            )
        job.error_detail = message
        job.terminal(
            PreparationStage.FAILED,
            message,
            error_code="actual_device_unresolved",
        )

    def progress(self, job_id: str):
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("Unknown onboarding preparation job.")
        self._reject_unresolved_actual_device(job)
        native_runtime_failure = _classify_native_runtime_failure(job)
        response = super().progress(job_id)
        if native_runtime_failure:
            response = response.model_copy(
                update={
                    "can_retry": False,
                    "can_fallback_to_cpu": False,
                    "error_code": job.error_code,
                    "error_detail": job.error_detail,
                    "message": job.message,
                }
            )
        return response

    def complete(self, job_id: str):
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("Unknown onboarding preparation job.")
        self._reject_unresolved_actual_device(job)
        return super().complete(job_id)
