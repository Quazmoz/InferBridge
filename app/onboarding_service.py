"""Desktop onboarding orchestration built on the existing advisor and lifecycle manager."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import __version__, model_registry as registry
from app.config import BASE_DIR, Settings
from app.onboarding_hardware import build_system_scan, classify_npu_readiness
from app.onboarding_models import (
    BenchmarkSummary,
    CancelPreparationResponse,
    ConnectionConfigurationResponse,
    ModelRecommendationResponse,
    PreparationProgressResponse,
    PreparationStage,
    PreparationStageSnapshot,
    PrepareModelRequest,
    RecommendationWarning,
    RestartOnboardingResponse,
)
from app.onboarding_state import OnboardingStateStore
from app.paths import RuntimePaths
from runtime import device_check
from runtime.benchmark_runner import (
    DEFAULT_BENCHMARK_PROMPT,
    BenchmarkStore,
    run_benchmark_suite,
)

_STAGE_LABELS = {
    PreparationStage.PREPARING: "Preparing",
    PreparationStage.DOWNLOADING: "Downloading model files",
    PreparationStage.CONVERTING: "Converting or quantizing to OpenVINO",
    PreparationStage.VALIDATING: "Validating converted files",
    PreparationStage.COMPILING: "Compiling for the selected device",
    PreparationStage.LOADING: "Loading the model",
    PreparationStage.BENCHMARKING: "Running a short benchmark",
    PreparationStage.READY: "Ready",
    PreparationStage.CANCELLED: "Cancelled",
    PreparationStage.FAILED: "Failed",
}
_STAGE_ORDER = [
    PreparationStage.PREPARING,
    PreparationStage.DOWNLOADING,
    PreparationStage.CONVERTING,
    PreparationStage.VALIDATING,
    PreparationStage.COMPILING,
    PreparationStage.LOADING,
    PreparationStage.BENCHMARKING,
    PreparationStage.READY,
]
_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|api[_-]?key\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_message(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)
    text = _SECRET_RE.sub("[redacted]", text)
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)+", r"...\\", text)
    text = re.sub(r"/(?:[^/\s]+/){2,}", ".../", text)
    return text[:limit]


@dataclass
class _StageState:
    stage: PreparationStage
    status: str = "pending"
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class PreparationJob:
    job_id: str
    model_id: str
    requested_device: str
    started_monotonic: float = field(default_factory=time.monotonic)
    stage: PreparationStage = PreparationStage.PREPARING
    status: str = "running"
    message: str = "Preparing model setup."
    percent: float | None = None
    determinate: bool = False
    actual_device: str | None = None
    benchmark: BenchmarkSummary | None = None
    error_code: str | None = None
    error_detail: str | None = None
    safe_log_tail: list[str] = field(default_factory=list)
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[Any] | None = None
    stages: dict[PreparationStage, _StageState] = field(
        default_factory=lambda: {stage: _StageState(stage) for stage in _STAGE_ORDER}
    )
    was_downloaded: bool = False

    def transition(
        self,
        stage: PreparationStage,
        message: str,
        *,
        percent: float | None = None,
        determinate: bool = False,
    ) -> None:
        now = _utc_now()
        previous = self.stages.get(self.stage)
        if previous and previous.status == "active" and previous.stage != stage:
            previous.status = "complete"
            previous.finished_at = now
        self.stage = stage
        self.message = _safe_message(message)
        self.percent = percent
        self.determinate = determinate and percent is not None
        current = self.stages.get(stage)
        if current:
            if current.started_at is None:
                current.started_at = now
            current.status = "active"

    def terminal(
        self, stage: PreparationStage, message: str, *, error_code: str | None = None
    ) -> None:
        now = _utc_now()
        previous = self.stages.get(self.stage)
        if previous and previous.status == "active":
            previous.status = "complete" if stage == PreparationStage.READY else str(stage.value)
            previous.finished_at = now
        self.stage = stage
        self.message = _safe_message(message)
        self.status = (
            "ready"
            if stage == PreparationStage.READY
            else "cancelled"
            if stage == PreparationStage.CANCELLED
            else "failed"
        )
        self.percent = 100.0 if stage == PreparationStage.READY else None
        self.determinate = stage == PreparationStage.READY
        self.error_code = error_code
        current = self.stages.get(stage)
        if current:
            current.status = "complete" if stage == PreparationStage.READY else str(stage.value)
            current.started_at = current.started_at or now
            current.finished_at = now


class OnboardingService:
    def __init__(
        self,
        *,
        settings: Settings,
        manager: Any,
        paths: RuntimePaths,
        state_store: OnboardingStateStore,
        endpoint_port: int,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.paths = paths
        self.state_store = state_store
        self.endpoint_port = int(endpoint_port)
        self.benchmark_store = BenchmarkStore(settings.benchmark_results_file)
        self._jobs: dict[str, PreparationJob] = {}
        self._jobs_lock = asyncio.Lock()

    def status(self):
        status = self.state_store.status()
        if status.completed and status.last_hardware_fingerprint:
            current = self.manager.advisor.hardware_snapshot().get("fingerprint")
            if current and current != status.last_hardware_fingerprint:
                return status.model_copy(
                    update={
                        "rerun_scan_recommended": True,
                        "recommendation_reason": (
                            "Hardware, drivers, or OpenVINO runtime details changed since setup. "
                            "Run the system scan again before relying on the prior recommendation."
                        ),
                    }
                )
        return status

    def system_scan(self, *, refresh: bool = False):
        snapshot = self.manager.advisor.hardware_snapshot(refresh=refresh)
        return build_system_scan(snapshot)

    def npu_readiness(self, *, refresh: bool = False):
        snapshot = self.manager.advisor.hardware_snapshot(refresh=refresh)
        return classify_npu_readiness(snapshot)

    def recommendation(self, *, refresh: bool = False) -> ModelRecommendationResponse:
        snapshot = self.manager.advisor.hardware_snapshot(refresh=refresh)
        candidates: list[tuple[float, Any, dict[str, Any]]] = []
        for cfg in self.manager.catalog.values():
            if "embedding" in str(cfg.backend).lower():
                continue
            downloaded = registry.is_downloaded(cfg, BASE_DIR)
            evaluation = self.manager.advisor.evaluate_model(
                cfg,
                downloaded=downloaded,
                profile="balanced",
                snapshot=snapshot,
            )
            if evaluation.get("compatibility") == "blocked":
                continue
            warnings = evaluation.get("warnings") or []
            warning_penalty = sum(
                140 if item.get("severity") == "warning" else 20
                for item in warnings
                if isinstance(item, dict)
            )
            benchmark_bonus = 180 if evaluation.get("benchmark") else 0
            downloaded_bonus = 220 if downloaded else 0
            fit = float(evaluation.get("fit_score") or 0)
            runtime = float(evaluation.get("runtime_memory_gb") or 999)
            download = float(evaluation.get("download_size_gb") or 999)
            first_load = float(evaluation.get("first_load_seconds") or 999)
            params = float(evaluation.get("parameter_count_b") or 999)
            compact_bonus = 120 if params <= 3.0 else 40 if params <= 4.5 else -180
            score = (
                fit * 7
                + downloaded_bonus
                + benchmark_bonus
                + compact_bonus
                - runtime * 18
                - download * 8
                - min(first_load, 600) * 0.4
                - warning_penalty
            )
            candidates.append((score, cfg, evaluation))

        if not candidates:
            raise RuntimeError(
                "No catalog model passed the current hardware preflight. Free disk or memory, "
                "rescan hardware, or choose an advanced model manually."
            )

        _score, cfg, evaluation = max(candidates, key=lambda item: item[0])
        warnings = [
            RecommendationWarning(
                code=str(item.get("code") or "warning"),
                severity=str(item.get("severity") or "warning"),
                message=_safe_message(item.get("message")),
            )
            for item in evaluation.get("warnings") or []
            if isinstance(item, dict)
        ]
        device = str(evaluation.get("recommended_device") or "CPU")
        reason = (
            f"{cfg.name} is the most conservative compatible starting model after considering "
            "available RAM and disk, OpenVINO-visible devices, preparation cost, and local benchmark evidence."
        )
        return ModelRecommendationResponse(
            profile="balanced",
            model_id=cfg.id,
            model_name=cfg.name,
            description=cfg.description,
            requested_device=device,
            expected_actual_device=device,
            precision=str(evaluation.get("precision") or cfg.weight_format),
            download_size_gb=evaluation.get("download_size_gb"),
            converted_size_gb=evaluation.get("converted_size_gb"),
            runtime_memory_gb=evaluation.get("runtime_memory_gb"),
            first_load_seconds=evaluation.get("first_load_seconds"),
            required_free_disk_gb=evaluation.get("required_free_disk_gb"),
            context_length=int(evaluation.get("recommended_context_len") or cfg.max_context_len),
            output_tokens=int(evaluation.get("recommended_output_tokens") or cfg.max_output_tokens),
            compatibility=str(evaluation.get("compatibility") or "caution"),
            fit_score=float(evaluation.get("fit_score") or 0),
            reason=reason,
            warnings=warnings,
            requires_confirmation=bool(evaluation.get("requires_confirmation")),
            trust_remote_code=bool(cfg.trust_remote_code),
        )

    def _validate_storage_location(self, raw: str | None, model_id: str) -> None:
        if not raw:
            return
        candidate = Path(raw).expanduser()
        if os.name == "nt" and not candidate.is_absolute():
            raise ValueError("Choose an absolute model storage location.")
        candidate = candidate.resolve()
        if candidate == candidate.parent:
            raise ValueError("The filesystem root cannot be used as model storage.")
        if candidate == self.paths.resource_root or self.paths.resource_root in candidate.parents:
            raise ValueError("The read-only application directory cannot be used as model storage.")
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".ovllm-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError("The selected model storage location is not writable.") from exc

        cfg = self.manager.catalog[model_id]
        current = cfg.abs_path(BASE_DIR)
        if registry.is_openvino_model_dir(current) and current.parent != candidate:
            raise ValueError(
                "This model already exists in its current location. Existing models are not moved automatically."
            )
        self._update_catalog_model_path(model_id, candidate / current.name)

    def _update_catalog_model_path(self, model_id: str, target: Path) -> None:
        path = Path(self.settings.models_file)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if model_id not in raw or not isinstance(raw[model_id], dict):
            raise ValueError("The selected model is not present in the writable catalog.")
        raw[model_id]["model_path"] = str(target.resolve())
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        self.manager.reload_catalog()

    async def start_preparation(self, request: PrepareModelRequest) -> PreparationProgressResponse:
        if request.model_id not in self.manager.catalog:
            raise ValueError("Unknown model ID.")
        device = device_check.validate_device_expression(request.device)
        if not request.confirm_license or not request.confirm_disk_requirement:
            raise ValueError("Confirm the model license and disk requirement before downloading.")
        cfg = self.manager.catalog[request.model_id]
        evaluation = self.manager.advisor.evaluate_model(
            cfg,
            downloaded=registry.is_downloaded(cfg, BASE_DIR),
            profile="balanced",
        )
        if evaluation.get("compatibility") == "blocked":
            blocking = next(
                (
                    item.get("message")
                    for item in evaluation.get("warnings") or []
                    if item.get("severity") == "blocking"
                ),
                "The selected model is blocked by preflight checks.",
            )
            raise ValueError(_safe_message(blocking))
        if evaluation.get("requires_confirmation") and not request.acknowledge_warnings:
            raise ValueError("Acknowledge the model preflight warnings before continuing.")
        if request.trust_remote_code and not bool(cfg.trust_remote_code):
            raise ValueError("Remote code is not enabled for this reviewed catalog model.")

        async with self._jobs_lock:
            for existing in self._jobs.values():
                if existing.status == "running":
                    raise RuntimeError("Another first-run model preparation is already active.")
            # Storage validation can rewrite the writable model catalog. Keep that mutation
            # inside the same critical section as the active-job check so a rejected second
            # request cannot retarget model files while another preparation is running.
            self._validate_storage_location(request.model_storage_location, request.model_id)
            job = PreparationJob(
                job_id=f"onboard-{uuid.uuid4().hex[:16]}",
                model_id=request.model_id,
                requested_device=device,
            )
            cfg = self.manager.catalog[request.model_id]
            job.was_downloaded = registry.is_downloaded(cfg, BASE_DIR)
            job.transition(PreparationStage.PREPARING, "Checking model and device preflight.")
            self._jobs[job.job_id] = job
            job.task = asyncio.create_task(
                self._run_job(job, trust_remote_code=request.trust_remote_code),
                name=f"onboarding-{job.job_id}",
            )
        return self.progress(job.job_id)

    async def _await_lifecycle_task(self, job: PreparationJob, task: asyncio.Task[Any]) -> None:
        while not task.done():
            if job.cancel_requested.is_set():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise asyncio.CancelledError
            manager_progress = self.manager.progress.get(job.model_id) or {}
            phase = str(manager_progress.get("phase") or "").lower()
            if phase == "downloading":
                job.transition(
                    PreparationStage.DOWNLOADING,
                    manager_progress.get("message") or "Downloading model files.",
                    percent=manager_progress.get("percent"),
                    determinate=manager_progress.get("percent") is not None,
                )
            elif phase == "converting":
                job.transition(
                    PreparationStage.CONVERTING,
                    manager_progress.get("message") or "Converting model to OpenVINO.",
                    percent=manager_progress.get("percent"),
                    determinate=manager_progress.get("percent") is not None,
                )
            for line in manager_progress.get("log_tail") or []:
                safe = _safe_message(line)
                if safe and (not job.safe_log_tail or job.safe_log_tail[-1] != safe):
                    job.safe_log_tail.append(safe)
                    job.safe_log_tail = job.safe_log_tail[-12:]
            await asyncio.sleep(0.25)
        await task

    async def _run_job(self, job: PreparationJob, *, trust_remote_code: bool) -> None:
        cfg = self.manager.catalog[job.model_id]
        try:
            if job.cancel_requested.is_set():
                raise asyncio.CancelledError

            if self.manager.force_mock:
                for stage, message in (
                    (PreparationStage.DOWNLOADING, "Mock mode is validating the download stage."),
                    (PreparationStage.CONVERTING, "Mock mode is validating the conversion stage."),
                    (PreparationStage.VALIDATING, "Validating mock model files."),
                    (PreparationStage.COMPILING, "Validating mock device compilation."),
                ):
                    job.transition(stage, message)
                    await asyncio.sleep(0)
            elif not registry.is_downloaded(cfg, BASE_DIR):
                job.transition(
                    PreparationStage.DOWNLOADING,
                    f"Downloading model files for {cfg.name}.",
                )
                task = self.manager.schedule_convert(
                    job.model_id,
                    job.requested_device,
                    load_after=False,
                    trust_remote_code=trust_remote_code,
                )
                if task is None:
                    raise RuntimeError("The model conversion task could not be started.")
                await self._await_lifecycle_task(job, task)

            if job.cancel_requested.is_set():
                raise asyncio.CancelledError
            job.transition(PreparationStage.VALIDATING, "Validating converted OpenVINO files.")
            if not self.manager.force_mock and not registry.is_downloaded(cfg, BASE_DIR):
                raise RuntimeError("Converted OpenVINO files did not pass validation.")

            job.transition(
                PreparationStage.COMPILING,
                f"Compiling {cfg.name} for {job.requested_device}. This stage is indeterminate.",
            )
            load_task = self.manager.schedule_load(job.model_id, job.requested_device)
            if load_task is not None:
                await self._await_lifecycle_task(job, load_task)
            entry = self.manager.catalog_entry(job.model_id)
            if not entry.get("is_loaded"):
                raise RuntimeError(
                    entry.get("error") or "The model did not reach the loaded state."
                )

            job.transition(PreparationStage.LOADING, "Confirming the loaded runtime state.")
            job.actual_device = str(
                entry.get("device") or self.manager.devices.get(job.model_id) or ""
            )
            if not job.actual_device:
                raise RuntimeError("The loaded runtime did not report an actual OpenVINO device.")

            if job.cancel_requested.is_set():
                raise asyncio.CancelledError
            job.transition(PreparationStage.BENCHMARKING, "Running a short local benchmark.")
            run = await run_benchmark_suite(
                self.manager,
                model_ids=[job.model_id],
                devices=[job.requested_device],
                prompt=DEFAULT_BENCHMARK_PROMPT,
                max_tokens=32,
                runs=1,
            )
            await asyncio.to_thread(self.benchmark_store.append, run)
            result = next(
                (item for item in run.get("results", []) if item.get("model_id") == job.model_id),
                None,
            )
            if not result or not result.get("success"):
                with contextlib.suppress(Exception):
                    self.manager.unload(job.model_id)
                detail = _safe_message((result or {}).get("error") or "Short benchmark failed.")
                raise RuntimeError(detail)
            job.actual_device = str(result.get("actual_device") or job.actual_device)
            job.benchmark = BenchmarkSummary(
                run_id=run.get("run_id"),
                model_id=job.model_id,
                requested_device=job.requested_device,
                actual_device=job.actual_device,
                load_time_ms=result.get("load_time_ms"),
                time_to_first_token_ms=result.get("time_to_first_token_ms"),
                tokens_sec=result.get("tokens_sec"),
                completion_tokens=int(result.get("completion_tokens") or 0),
                success=True,
                mock=bool(run.get("mock")),
            )
            fingerprint = self.manager.advisor.hardware_snapshot().get("fingerprint")
            self.state_store.update(
                completed=True,
                restart_requested=False,
                selected_model=job.model_id,
                selected_device=job.requested_device,
                actual_device=job.actual_device,
                model_storage_location=str(cfg.abs_path(BASE_DIR).parent),
                last_hardware_fingerprint=fingerprint,
                last_benchmark_reference=run.get("run_id"),
                completed_app_version=__version__,
            )
            job.terminal(
                PreparationStage.READY,
                f"{cfg.name} is ready on {job.actual_device}.",
            )
        except asyncio.CancelledError:
            await self._cleanup_cancelled_job(job)
            job.terminal(PreparationStage.CANCELLED, "Model preparation was cancelled.")
        except Exception as exc:  # noqa: BLE001 - converted to a sanitized recoverable status
            detail = _safe_message(exc)
            await self._cleanup_failed_job(job)
            job.error_detail = detail or "Model preparation failed."
            job.terminal(
                PreparationStage.FAILED,
                job.error_detail,
                error_code="preparation_failed",
            )

    async def _cleanup_cancelled_job(self, job: PreparationJob) -> None:
        task = self.manager.convert_tasks.get(job.model_id) or self.manager.load_tasks.get(
            job.model_id
        )
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if job.model_id in self.manager.engines:
            with contextlib.suppress(Exception):
                self.manager.unload(job.model_id)
        self._quarantine_partial_model(job)

    async def _cleanup_failed_job(self, job: PreparationJob) -> None:
        if job.model_id in self.manager.engines and not job.benchmark:
            with contextlib.suppress(Exception):
                self.manager.unload(job.model_id)
        self._quarantine_partial_model(job)

    def _quarantine_partial_model(self, job: PreparationJob) -> None:
        if job.was_downloaded:
            return
        cfg = self.manager.catalog.get(job.model_id)
        if cfg is None:
            return
        model_dir = cfg.abs_path(BASE_DIR)
        if not model_dir.exists() or registry.is_openvino_model_dir(model_dir):
            return
        root = Path(self.settings.models_dir).resolve()
        resolved = model_dir.resolve()
        if resolved != root and root not in resolved.parents:
            return
        quarantine = self.paths.diagnostics_dir / "incomplete-models"
        quarantine.mkdir(parents=True, exist_ok=True)
        target = quarantine / f"{model_dir.name}-{int(time.time())}"
        with contextlib.suppress(OSError):
            shutil.move(str(model_dir), str(target))

    def progress(self, job_id: str) -> PreparationProgressResponse:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("Unknown onboarding preparation job.")
        stage_rows = [
            PreparationStageSnapshot(
                stage=state.stage,
                label=_STAGE_LABELS[state.stage],
                status=state.status,
                started_at=state.started_at,
                finished_at=state.finished_at,
            )
            for state in job.stages.values()
        ]
        available = {
            str(item).split(".", 1)[0].upper() for item in device_check.available_devices()
        }
        return PreparationProgressResponse(
            job_id=job.job_id,
            model_id=job.model_id,
            requested_device=job.requested_device,
            actual_device=job.actual_device,
            stage=job.stage,
            stage_label=_STAGE_LABELS[job.stage],
            status=job.status,
            determinate=job.determinate,
            percent=job.percent,
            message=job.message,
            elapsed_seconds=max(0, int(time.monotonic() - job.started_monotonic)),
            can_cancel=job.status == "running" and job.stage not in {PreparationStage.BENCHMARKING},
            can_retry=job.status in {"failed", "cancelled"},
            can_fallback_to_cpu=job.status == "failed"
            and job.requested_device.split(".", 1)[0].upper() != "CPU"
            and "CPU" in available,
            stages=stage_rows,
            safe_log_tail=job.safe_log_tail,
            error_code=job.error_code,
            error_detail=job.error_detail,
            benchmark=job.benchmark,
        )

    async def cancel(self, job_id: str) -> CancelPreparationResponse:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("Unknown onboarding preparation job.")
        if job.status != "running" or job.stage == PreparationStage.BENCHMARKING:
            return CancelPreparationResponse(
                job_id=job_id,
                status="not_cancellable",
                message="This stage cannot be cancelled safely.",
            )
        job.cancel_requested.set()
        return CancelPreparationResponse(
            job_id=job_id,
            status="cancelling",
            message="Cancellation requested. Partial files will not be presented as a valid model.",
        )

    def complete(self, job_id: str) -> ConnectionConfigurationResponse:
        job = self._jobs.get(job_id)
        if job is None or job.status != "ready" or not job.actual_device:
            raise ValueError("Onboarding cannot complete until loading and benchmarking succeed.")
        return self.connection_configuration(
            active_model_id=job.model_id,
            actual_device=job.actual_device,
        )

    def restart(self) -> RestartOnboardingResponse:
        self.state_store.restart()
        return RestartOnboardingResponse(
            message="First-run setup will be shown again. Existing models and benchmark data were preserved."
        )

    def connection_configuration(
        self,
        *,
        active_model_id: str | None = None,
        actual_device: str | None = None,
    ) -> ConnectionConfigurationResponse:
        state = self.state_store.load().state
        model_id = active_model_id or state.get("selected_model")
        device = actual_device or state.get("actual_device")
        if not model_id or not device:
            raise ValueError("No successfully verified active model is available yet.")
        origin = f"http://127.0.0.1:{self.endpoint_port}"
        base_url = f"{origin}/v1"
        key_configured = bool(self.settings.api_key)
        placeholder = "<configured-local-api-key>" if key_configured else "<not-required>"
        return ConnectionConfigurationResponse(
            chat_url=origin,
            base_url=base_url,
            health_url=f"{origin}/health/ready",
            active_model_id=str(model_id),
            actual_device=str(device),
            api_key_state="configured" if key_configured else "disabled",
            api_key_placeholder=placeholder,
            openai_python=(
                "from openai import OpenAI\n\n"
                "client = OpenAI(\n"
                f'    base_url="{base_url}",\n'
                f'    api_key="{placeholder}",\n'
                ")"
            ),
            environment_variables=(f"OPENAI_BASE_URL={base_url}\nOPENAI_API_KEY={placeholder}"),
            open_webui={
                "base_url": base_url,
                "api_key": placeholder,
                "note": "Add an OpenAI-compatible connection and select the active model ID.",
            },
            n8n={
                "base_url": base_url,
                "api_key": placeholder,
                "note": "Use an OpenAI credential with a custom base URL. The Responses API is supported.",
            },
        )
