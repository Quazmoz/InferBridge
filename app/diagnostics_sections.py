"""Diagnostics section producers and bounded local-file collectors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.diagnostics_privacy import (
    benchmark_summary,
    bounded_log_text,
    certification_summary,
    json_bytes,
    redact_path,
    safe_archive_name,
    sanitize_text,
    sanitize_value,
)
from app.model_library_conversion import is_reparse_point

ALLOWED_RUNTIME_KEYS = {
    "application_version",
    "api_contract_version",
    "packaging_version",
    "installation_mode",
    "controller_available",
    "server_port",
    "live",
    "ready",
    "server_status",
    "active_model",
    "models",
    "preparation",
    "events",
    "benchmark",
    "benchmark_running",
    "hardware_fingerprint",
    "npu_readiness",
    "device",
    "mock",
    "warning",
    "error",
}
MAX_CERTIFICATION_FILES = 8
MAX_CERTIFICATION_BYTES = 256 * 1024


class DiagnosticsSectionsMixin:
    def _runtime_payload(self) -> Mapping[str, Any]:
        raw = dict(self.runtime_snapshot or {})
        payload = {key: raw.get(key) for key in ALLOWED_RUNTIME_KEYS if key in raw}
        if isinstance(payload.get("benchmark"), Mapping):
            payload["benchmark"] = benchmark_summary(payload["benchmark"])
        return payload

    def _configuration_payload(self) -> Mapping[str, Any]:
        raw = dict(self.effective_configuration or {})
        allowed = {
            "host",
            "port",
            "device",
            "default_model",
            "auto_convert",
            "rate_limit",
            "max_request_body_mb",
            "cors_configured",
            "api_key_configured",
            "models_file",
            "models_dir",
            "cache_dir",
            "benchmark_results_file",
            "data_root",
            "logs_dir",
            "diagnostics_dir",
        }
        payload = {key: raw.get(key) for key in allowed if key in raw}
        payload.update(
            {
                "data_root": redact_path(self.paths.data_root, self.redactions_applied),
                "models_dir": redact_path(self.paths.models_dir, self.redactions_applied),
                "logs_dir": redact_path(self.paths.logs_dir, self.redactions_applied),
                "diagnostics_dir": redact_path(
                    self.paths.diagnostics_dir,
                    self.redactions_applied,
                ),
            }
        )
        return payload

    def _benchmark_payload(self) -> Mapping[str, Any]:
        if self.benchmark_summaries is not None:
            data = list(self.benchmark_summaries)
        else:
            runtime = self.runtime_snapshot or {}
            value = runtime.get("benchmark")
            data = [value] if isinstance(value, Mapping) else []
        return {
            "summaries": [
                benchmark_summary(item) for item in data[:20] if isinstance(item, Mapping)
            ]
        }

    def _events_payload(self) -> Mapping[str, Any]:
        events = list((self.runtime_snapshot or {}).get("events") or [])[-100:]
        return {"events": events}

    def _collect_logs(self, files: dict[str, bytes], categories: list[str]) -> None:
        if is_reparse_point(self.paths.logs_dir):
            self.collection_errors.append("logs: rejected symbolic link or Windows junction")
            return

        allowed = ("launcher.log", "desktop.log", "tray.log")
        included = False
        for name in allowed:
            path = self.paths.logs_dir / name
            try:
                if not path.is_file() or is_reparse_point(path):
                    continue
                resolved = path.resolve()
                if resolved.parent != self.paths.logs_dir.resolve():
                    self.collection_errors.append(f"logs/{name}: rejected path escape")
                    continue
                text = bounded_log_text(path)
                files[f"logs/{name}.txt"] = sanitize_text(
                    text,
                    redactions=self.redactions_applied,
                ).encode("utf-8")
                included = True
            except Exception as exc:  # noqa: BLE001 - best effort
                self.collection_errors.append(f"logs/{name}: {sanitize_text(exc)}")
        if included:
            categories.append("sanitized logs")

    def _collect_certification_summaries(
        self,
        files: dict[str, bytes],
        categories: list[str],
    ) -> None:
        if is_reparse_point(self.paths.diagnostics_dir):
            self.collection_errors.append(
                "certification: rejected symbolic link or Windows junction"
            )
            return

        included = False
        candidates = sorted(self.paths.diagnostics_dir.glob("*certification*.json"))[
            :MAX_CERTIFICATION_FILES
        ]
        for path in candidates:
            try:
                if is_reparse_point(path) or not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved.parent != self.paths.diagnostics_dir.resolve():
                    self.collection_errors.append(
                        f"certification/{path.name}: rejected path escape"
                    )
                    continue
                if path.stat().st_size > MAX_CERTIFICATION_BYTES:
                    self.collection_errors.append(
                        f"certification/{path.name}: file exceeded size limit"
                    )
                    continue
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
                summary = certification_summary(parsed)
                name = safe_archive_name(f"certification/{path.stem}-summary.json")
                files[name] = json_bytes(
                    sanitize_value(summary, redactions=self.redactions_applied)
                )
                included = True
            except Exception as exc:  # noqa: BLE001
                self.collection_errors.append(f"certification/{path.name}: {sanitize_text(exc)}")
        if included:
            categories.append("certification summaries")
