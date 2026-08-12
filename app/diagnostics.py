"""Privacy-safe local diagnostics collection shared by tray, browser, and support tools."""

from __future__ import annotations

import platform
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import __version__
from app.brand import DISPLAY_NAME
from app.diagnostics_privacy import (
    bounded_log_text,
    diagnostics_confirmation_summary,
    json_bytes,
    local_hardware_snapshot,
    redact_path as redact_path,
    safe_archive_name,
    safe_disk_payload,
    sanitize_text,
    sanitize_value,
    windows_edition,
)
from app.diagnostics_sections import DiagnosticsSectionsMixin
from app.paths import RuntimePaths

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DiagnosticsResult:
    path: Path
    manifest: Mapping[str, Any]
    included_categories: tuple[str, ...]
    excluded_categories: tuple[str, ...] = (
        "prompts and chat history",
        "API keys and Hugging Face tokens",
        "source images and request bodies",
        "model weights and OpenVINO IR files",
        "compiled model cache contents",
        "browser localStorage",
        "certificates and signing secrets",
    )


@dataclass
class DiagnosticsCollector(DiagnosticsSectionsMixin):
    paths: RuntimePaths
    runtime_snapshot: Mapping[str, Any] | None = None
    effective_configuration: Mapping[str, Any] | None = None
    hardware_snapshot: Mapping[str, Any] | None = None
    npu_readiness: Mapping[str, Any] | None = None
    benchmark_summaries: Iterable[Mapping[str, Any]] | None = None
    build_metadata: Mapping[str, Any] | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    collection_errors: list[str] = field(default_factory=list)
    redactions_applied: set[str] = field(default_factory=set)

    def support_summary(self) -> str:
        """Return a concise, sanitized block safe to paste into a support request.

        The summary deliberately uses the same allowlisted section producers as the
        local diagnostics ZIP. It never reads logs, environment variables, prompts,
        chat history, credentials, cookies, browser storage, or model-file contents.
        """

        application = sanitize_value(
            self._application_payload(), redactions=self.redactions_applied
        )
        hardware = sanitize_value(self._hardware_payload(), redactions=self.redactions_applied)
        runtime = sanitize_value(self._runtime_payload(), redactions=self.redactions_applied)
        configuration = sanitize_value(
            self._configuration_payload(), redactions=self.redactions_applied
        )

        application = application if isinstance(application, Mapping) else {}
        hardware = hardware if isinstance(hardware, Mapping) else {}
        runtime = runtime if isinstance(runtime, Mapping) else {}
        configuration = configuration if isinstance(configuration, Mapping) else {}

        os_info = hardware.get("os")
        os_info = os_info if isinstance(os_info, Mapping) else {}
        cpu = hardware.get("cpu")
        cpu = cpu if isinstance(cpu, Mapping) else {}
        memory = hardware.get("memory")
        memory = memory if isinstance(memory, Mapping) else {}
        runtime_versions = hardware.get("runtime")
        runtime_versions = runtime_versions if isinstance(runtime_versions, Mapping) else {}

        devices = [item for item in hardware.get("devices", []) if isinstance(item, Mapping)]
        available_devices = [str(item) for item in hardware.get("available_devices", []) if item]
        gpu_names = [
            str(item.get("full_name") or item.get("device"))
            for item in devices
            if str(item.get("device") or "").upper().startswith("GPU")
        ]
        npu_names = [
            str(item.get("full_name") or item.get("device"))
            for item in devices
            if str(item.get("device") or "").upper().startswith("NPU")
        ]

        device_state = runtime.get("device")
        if isinstance(device_state, Mapping):
            selected_device = (
                device_state.get("actual")
                or device_state.get("actual_device")
                or device_state.get("selected")
                or device_state.get("default")
            )
        else:
            selected_device = device_state
        selected_device = selected_device or configuration.get("device") or "unavailable"

        active_model = runtime.get("active_model")
        model_name = None
        model_format = None
        if isinstance(active_model, Mapping):
            model_name = (
                active_model.get("name") or active_model.get("id") or active_model.get("model_id")
            )
            model_format = active_model.get("weight_format") or active_model.get("precision")
        elif active_model:
            model_name = active_model

        if not model_name:
            models = runtime.get("models")
            if isinstance(models, Mapping):
                loaded = models.get("loaded")
                if isinstance(loaded, list) and loaded:
                    model_name = loaded[0]

        os_parts = [
            os_info.get("edition"),
            os_info.get("release"),
            os_info.get("version"),
        ]
        os_label = " ".join(str(part) for part in os_parts if part) or str(
            os_info.get("system") or "unavailable"
        )
        cpu_name = (
            cpu.get("name")
            or cpu.get("full_name")
            or cpu.get("brand")
            or cpu.get("model")
            or "unavailable"
        )
        ram_total = memory.get("total_gb")
        ram_label = (
            f"{ram_total} GB"
            if isinstance(ram_total, int | float) and ram_total > 0
            else "unavailable"
        )

        artifact_kind = application.get("build_metadata") or {}
        artifact_kind = artifact_kind if isinstance(artifact_kind, Mapping) else {}
        environment_label = (
            str(artifact_kind.get("artifact_kind") or "packaged")
            if application.get("packaged")
            else "development"
        )

        lines = [
            "InferBridge Diagnostics",
            f"Version: {application.get('application_version') or __version__}",
            f"Build: {artifact_kind.get('build_id') or artifact_kind.get('build_date') or 'unavailable'}",
            f"Environment: {environment_label}",
            f"Installation: {application.get('installation_mode') or 'unavailable'}",
            f"OS: {os_label}",
            f"Architecture: {application.get('architecture') or os_info.get('architecture') or 'unavailable'}",
            f"CPU: {cpu_name}",
            f"RAM: {ram_label}",
            f"GPU: {', '.join(gpu_names) if gpu_names else 'not detected'}",
            f"NPU: {', '.join(npu_names) if npu_names else 'not detected'}",
            f"OpenVINO: {runtime_versions.get('openvino') or 'unavailable'}",
            f"OpenVINO GenAI: {runtime_versions.get('openvino_genai') or 'unavailable'}",
            f"Available devices: {', '.join(available_devices) if available_devices else 'none detected'}",
            f"Selected device: {selected_device}",
            f"Model: {model_name or 'none loaded'}",
            f"Model format: {model_format or 'unavailable'}",
            f"Mock mode: {'yes' if runtime.get('mock') else 'no'}",
            "",
            "Privacy: credentials, prompts, chat history, browser storage, logs, and model files are not included.",
            "Paste this into the InferBridge feedback form or a GitHub issue.",
        ]
        return sanitize_text("\n".join(lines), redactions=self.redactions_applied, limit=16 * 1024)

    def export(self) -> DiagnosticsResult:
        self.paths.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self._assert_safe_output_root(self.paths.diagnostics_dir)
        created = self.now().astimezone(UTC)
        filename = f"inferbridge-diagnostics-{created.strftime('%Y%m%d-%H%M%S')}.zip"
        output = self.paths.diagnostics_dir / filename
        if output.exists():
            output = self.paths.diagnostics_dir / (
                f"inferbridge-diagnostics-{created.strftime('%Y%m%d-%H%M%S')}-"
                f"{created.microsecond:06d}.zip"
            )

        files: dict[str, bytes] = {}
        categories: list[str] = []
        self._collect_json(
            files, "application.json", self._application_payload, categories, "application"
        )
        self._collect_json(files, "hardware.json", self._hardware_payload, categories, "hardware")
        self._collect_json(files, "runtime.json", self._runtime_payload, categories, "runtime")
        self._collect_json(
            files,
            "configuration.json",
            self._configuration_payload,
            categories,
            "configuration",
        )
        self._collect_json(
            files, "benchmarks.json", self._benchmark_payload, categories, "benchmarks"
        )
        self._collect_json(files, "events.json", self._events_payload, categories, "events")
        self._collect_logs(files, categories)
        self._collect_certification_summaries(files, categories)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "application_version": __version__,
            "created_at": created.isoformat(),
            "installation_mode": "portable" if self.paths.portable else "installed",
            "files": sorted(files),
            "redactions_applied": sorted(self.redactions_applied),
            "collection_errors": list(self.collection_errors),
        }
        files["manifest.json"] = json_bytes(manifest)
        manifest["files"] = sorted(files)
        files["manifest.json"] = json_bytes(manifest)

        temp = output.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in sorted(files.items()):
                    safe_name = safe_archive_name(name)
                    archive.writestr(safe_name, content)
            temp.replace(output)
        except Exception as exc:  # noqa: BLE001 - support boundary
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"Diagnostics ZIP could not be created: {sanitize_text(exc)}"
            ) from exc

        return DiagnosticsResult(
            path=output,
            manifest=manifest,
            included_categories=tuple(dict.fromkeys(categories)),
        )

    def _assert_safe_output_root(self, directory: Path) -> None:
        resolved = directory.resolve()
        expected = self.paths.diagnostics_dir.resolve()
        if resolved != expected:
            raise RuntimeError(
                "Diagnostics output must remain inside the application diagnostics directory."
            )
        if directory.is_symlink():
            raise RuntimeError("Diagnostics directory cannot be a symbolic link.")
        probe = directory / ".diagnostics-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError("The diagnostics directory is not writable.") from exc

    def _collect_json(
        self,
        files: dict[str, bytes],
        name: str,
        producer: Callable[[], Any],
        categories: list[str],
        category: str,
    ) -> None:
        try:
            payload = sanitize_value(producer(), redactions=self.redactions_applied)
            files[safe_archive_name(name)] = json_bytes(payload)
            categories.append(category)
        except Exception as exc:  # noqa: BLE001 - best-effort collection
            self.collection_errors.append(f"{category}: {sanitize_text(exc)}")

    def _application_payload(self) -> Mapping[str, Any]:
        metadata = dict(self.build_metadata or {})
        return {
            "application_name": DISPLAY_NAME,
            "application_version": __version__,
            "packaging_version": metadata.get("packaging_version") or __version__,
            "build_metadata": {
                key: value
                for key, value in metadata.items()
                if key in {"packaging_version", "build_id", "build_date", "artifact_kind", "signed"}
            },
            "installation_mode": "portable" if self.paths.portable else "installed",
            "packaged": bool(self.paths.packaged),
            "python_version": platform.python_version(),
            "architecture": platform.machine() or "unknown",
            "api_contract_version": str(
                (self.runtime_snapshot or {}).get("api_contract_version") or "1"
            ),
        }

    def _hardware_payload(self) -> Mapping[str, Any]:
        snapshot = dict(self.hardware_snapshot or {})
        if not snapshot and self.runtime_snapshot:
            snapshot = dict(self.runtime_snapshot.get("hardware") or {})
        if not snapshot:
            snapshot = local_hardware_snapshot(self.paths.models_dir)
        npu = self.npu_readiness or (self.runtime_snapshot or {}).get("npu_readiness")
        if not npu:
            try:
                from app.onboarding_hardware import classify_npu_readiness

                npu = classify_npu_readiness(snapshot).model_dump(mode="json")
            except Exception:
                npu = {}
        return {
            "os": snapshot.get("os")
            or {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "edition": windows_edition(),
                "architecture": platform.machine(),
            },
            "cpu": snapshot.get("cpu") or {},
            "memory": snapshot.get("memory") or {},
            "disk": safe_disk_payload(snapshot.get("disk") or {}, self.paths.models_dir),
            "runtime": snapshot.get("runtime") or {},
            "available_devices": snapshot.get("available_devices") or [],
            "devices": snapshot.get("devices") or [],
            "hardware_fingerprint": snapshot.get("fingerprint")
            or (self.runtime_snapshot or {}).get("hardware_fingerprint"),
            "npu_readiness": npu or {},
        }


__all__ = [
    "DiagnosticsCollector",
    "DiagnosticsResult",
    "diagnostics_confirmation_summary",
    "safe_archive_name",
    "sanitize_text",
    "bounded_log_text",
]
