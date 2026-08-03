"""Typed desktop operations shared by tray actions and browser support surfaces."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app import __version__
from app.diagnostics import DiagnosticsCollector, DiagnosticsResult, redact_path
from app.onboarding_service import OnboardingService
from app.paths import RuntimePaths
from app.startup_registration import StartupRegistration
from runtime.benchmark_runner import DEFAULT_BENCHMARK_PROMPT, run_benchmark_suite

_API_CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class DesktopOperationsStatus:
    application_version: str
    api_contract_version: str
    installation_mode: str
    controller_available: bool
    server_port: int
    live: bool
    ready: bool
    server_status: str
    active_model: Mapping[str, Any] | None
    models: tuple[Mapping[str, Any], ...]
    preparation: Mapping[str, Any] | None
    events: tuple[Mapping[str, Any], ...]
    benchmark: Mapping[str, Any] | None
    benchmark_running: bool
    api_key_configured: bool
    start_with_windows: bool
    data_directory: str
    last_diagnostics_export: str | None
    hardware_fingerprint: str | None
    npu_readiness: Mapping[str, Any] | None
    mock: bool
    warning: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_version": self.application_version,
            "api_contract_version": self.api_contract_version,
            "installation_mode": self.installation_mode,
            "controller_available": self.controller_available,
            "server_port": self.server_port,
            "live": self.live,
            "ready": self.ready,
            "server_status": self.server_status,
            "active_model": self.active_model,
            "models": list(self.models),
            "preparation": self.preparation,
            "events": list(self.events),
            "benchmark": self.benchmark,
            "benchmark_running": self.benchmark_running,
            "api_key_configured": self.api_key_configured,
            "start_with_windows": self.start_with_windows,
            "data_directory": self.data_directory,
            "last_diagnostics_export": self.last_diagnostics_export,
            "hardware_fingerprint": self.hardware_fingerprint,
            "npu_readiness": self.npu_readiness,
            "mock": self.mock,
            "warning": self.warning,
            "error": self.error,
        }


class DesktopOperationsService:
    def __init__(
        self,
        *,
        settings: Any,
        manager: Any,
        onboarding: OnboardingService,
        paths: RuntimePaths,
        endpoint_port: int,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.onboarding = onboarding
        self.paths = paths
        self.endpoint_port = int(endpoint_port)
        self._benchmark_running = False
        self._cached_npu: Mapping[str, Any] | None = None
        self._cached_npu_at = 0.0

    def _controller_available(self) -> bool:
        heartbeat = self.paths.data_root / "tray-heartbeat.json"
        try:
            if not heartbeat.is_file() or heartbeat.is_symlink():
                return False
            if time.time() - heartbeat.stat().st_mtime > 15:
                return False
            parsed = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
            return bool(isinstance(parsed, dict) and parsed.get("controller") == "tray")
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _npu_status(self, *, refresh: bool = False) -> Mapping[str, Any] | None:
        now = time.monotonic()
        if not refresh and self._cached_npu is not None and now - self._cached_npu_at < 60:
            return self._cached_npu
        try:
            value = self.onboarding.npu_readiness(refresh=refresh).model_dump(mode="json")
        except Exception:
            return self._cached_npu
        self._cached_npu = value
        self._cached_npu_at = now
        return value

    def _latest_preparation(self) -> Mapping[str, Any] | None:
        jobs = list(getattr(self.onboarding, "_jobs", {}).values())
        if not jobs:
            return None
        job = max(jobs, key=lambda item: float(getattr(item, "started_monotonic", 0.0)))
        try:
            return self.onboarding.progress(job.job_id).model_dump(mode="json")
        except Exception:
            return None

    def _models(self) -> tuple[Mapping[str, Any], ...]:
        rows = []
        for model_id in self.manager.catalog:
            try:
                entry = dict(self.manager.catalog_entry(model_id))
            except Exception:
                continue
            rows.append(
                {
                    "id": model_id,
                    "name": entry.get("name") or model_id,
                    "status": entry.get("status"),
                    "is_loaded": bool(entry.get("is_loaded")),
                    "is_loading": bool(entry.get("is_loading")),
                    "requested_device": (
                        self.onboarding.state_store.load().state.get("selected_device")
                        if entry.get("is_loaded")
                        else None
                    ),
                    "actual_device": entry.get("device") if entry.get("is_loaded") else None,
                    "progress": entry.get("progress"),
                    "error": entry.get("error"),
                }
            )
        return tuple(rows)

    @staticmethod
    def _active_model(models: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any] | None:
        return next((item for item in models if item.get("is_loaded")), None)

    def _latest_benchmark(self) -> Mapping[str, Any] | None:
        try:
            return self.onboarding.benchmark_store.latest()
        except Exception:
            return None

    def _last_diagnostics_export(self) -> str | None:
        try:
            candidates = [
                path
                for path in self.paths.diagnostics_dir.glob(
                    "openvino-windows-llm-diagnostics-*.zip"
                )
                if path.is_file() and not path.is_symlink()
            ]
            if not candidates:
                return None
            return redact_path(max(candidates, key=lambda path: path.stat().st_mtime))
        except OSError:
            return None

    def status(self) -> DesktopOperationsStatus:
        models = self._models()
        active = self._active_model(models)
        preparation = self._latest_preparation()
        snapshot = self.manager.advisor.hardware_snapshot()
        npu = self._npu_status()
        try:
            startup = (
                StartupRegistration.for_current_desktop_launcher(
                    portable=self.paths.portable,
                )
                .state()
                .enabled
            )
        except Exception:
            startup = False
        ready = self.manager.loading_count() == 0
        return DesktopOperationsStatus(
            application_version=__version__,
            api_contract_version=_API_CONTRACT_VERSION,
            installation_mode="portable" if self.paths.portable else "installed",
            controller_available=self._controller_available(),
            server_port=self.endpoint_port,
            live=True,
            ready=ready,
            server_status="ready" if ready else "busy",
            active_model=active,
            models=models,
            preparation=preparation,
            events=tuple(self.manager.recent_events()[-100:]),
            benchmark=self._latest_benchmark(),
            benchmark_running=self._benchmark_running,
            api_key_configured=bool(self.settings.api_key),
            start_with_windows=startup,
            data_directory=redact_path(self.paths.data_root),
            last_diagnostics_export=self._last_diagnostics_export(),
            hardware_fingerprint=snapshot.get("fingerprint"),
            npu_readiness=npu,
            mock=bool(self.manager.force_mock),
        )

    def hardware_scan(self) -> Mapping[str, Any]:
        scan = self.onboarding.system_scan(refresh=True).model_dump(mode="json")
        self._npu_status(refresh=True)
        return scan

    async def run_short_benchmark(self) -> Mapping[str, Any]:
        if self._benchmark_running:
            raise RuntimeError("A benchmark is already running.")
        active = self._active_model(self._models())
        if not active:
            raise RuntimeError("Load a generation model before running the short benchmark.")
        model_id = str(active.get("id") or "")
        cfg = self.manager.catalog.get(model_id)
        if not model_id or cfg is None or "embedding" in str(getattr(cfg, "backend", "")).lower():
            raise RuntimeError("The short benchmark requires a loaded generation model.")
        actual_device = str(active.get("actual_device") or "").strip()
        if not actual_device:
            raise RuntimeError("The active model did not report an actual device.")
        self._benchmark_running = True
        try:
            run = await run_benchmark_suite(
                self.manager,
                model_ids=[model_id],
                devices=[actual_device],
                prompt=DEFAULT_BENCHMARK_PROMPT,
                max_tokens=32,
                runs=1,
            )
            await asyncio.to_thread(self.onboarding.benchmark_store.append, run)
            self.manager.emit_event(
                "info", f"Tray short benchmark completed for {model_id} on {actual_device}"
            )
            return run
        finally:
            self._benchmark_running = False

    def export_diagnostics(self) -> DiagnosticsResult:
        runtime = self.status().to_dict()
        configuration = {
            "host": self.settings.host,
            "port": self.settings.port,
            "device": self.settings.device,
            "default_model": self.settings.default_model,
            "auto_convert": self.settings.auto_convert,
            "rate_limit": self.settings.rate_limit,
            "max_request_body_mb": self.settings.max_request_body_mb,
            "cors_configured": bool(self.settings.cors_origins),
            "api_key_configured": bool(self.settings.api_key),
            "models_file": self.settings.models_file,
            "models_dir": self.settings.models_dir,
            "cache_dir": self.settings.cache_dir,
            "benchmark_results_file": self.settings.benchmark_results_file,
        }
        try:
            benchmarks = list(reversed(self.onboarding.benchmark_store.list_runs()))[:20]
        except Exception:
            benchmarks = []
        result = DiagnosticsCollector(
            paths=self.paths,
            runtime_snapshot=runtime,
            effective_configuration=configuration,
            hardware_snapshot=self.manager.advisor.hardware_snapshot(),
            npu_readiness=self._npu_status(refresh=True) or {"state": "unknown"},
            benchmark_summaries=benchmarks,
            build_metadata={
                "packaging_version": __version__,
                "artifact_kind": "portable" if self.paths.portable else "installed",
                "signed": None,
            },
        ).export()
        self.manager.emit_event("info", "Created a local sanitized diagnostics ZIP")
        return result
