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
from app.model_library_conversion import is_reparse_point
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
        # Path.is_symlink() does not reliably cover Windows directory junctions. Reject
        # all reparse points before resolving the path so diagnostics can never be
        # redirected outside the application-managed data root.
        if is_reparse_point(directory):
            raise RuntimeError(
                "Diagnostics directory cannot be a symbolic link or Windows junction."
            )
        resolved = directory.resolve()
        expected = self.paths.diagnostics_dir.resolve()
        if resolved != expected:
            raise RuntimeError(
                "Diagnostics output must remain inside the application diagnostics directory."
            )
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
