"""Compose recovery summaries and contain recovery-action failures."""

from __future__ import annotations

import logging
from typing import Any

from app.model_recovery import (
    RecoveryConflict,
    _inferred_record,
    _model_is_active,
    _summary_from_record,
)

_INSTALL_FLAG = "_MODEL_RECOVERY_STATUS_INSTALLED"
logger = logging.getLogger("ov-llm.recovery")


def _safe_record_text(manager: Any, value: Any, *, fallback: str = "") -> str:
    sanitize = getattr(manager, "_sanitize_progress_line", None)
    if callable(sanitize):
        try:
            return str(sanitize(value) or fallback)
        except Exception:  # noqa: BLE001 - corrupt recovery metadata must stay contained
            return fallback
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:240] or fallback


def _normalize_recovery_record(manager: Any, model_id: str, record: Any) -> dict[str, Any] | None:
    """Return a bounded recovery record safe for status and action processing.

    Recovery files survive crashes and may be hand-edited, truncated, or written by an
    older build. Never let permissive JSON values such as Infinity or an unexpected
    container shape break model-status polling or recovery actions.
    """

    if not isinstance(record, dict):
        return None

    interrupted_at = record.get("interrupted_at")
    if isinstance(interrupted_at, bool) or not isinstance(interrupted_at, int):
        interrupted_at = 0
    interrupted_at = max(interrupted_at, 0)

    recovery_id = record.get("recovery_id")
    if not isinstance(recovery_id, str) or not recovery_id.strip():
        recovery_id = f"recovery-{model_id}"[:128]
    else:
        recovery_id = "".join(char for char in recovery_id if char.isprintable()).strip()[:128]
        recovery_id = recovery_id or f"recovery-{model_id}"[:128]

    operation_id = record.get("operation_id")
    if not isinstance(operation_id, str):
        operation_id = None
    elif operation_id:
        operation_id = "".join(char for char in operation_id if char.isprintable()).strip()[:128] or None

    operation_type = record.get("operation_type")
    if operation_type not in {"load", "convert"}:
        operation_type = None

    terminal_state = record.get("terminal_state")
    if terminal_state not in {"error", "cancelled"}:
        terminal_state = "error"

    failed_stage = record.get("failed_stage")
    if failed_stage not in {"download", "conversion", "load"}:
        failed_stage = "conversion"

    last_completed = record.get("last_completed_stage")
    if last_completed not in {"none", "download", "conversion"}:
        last_completed = "none"

    raw_tail = record.get("log_tail")
    safe_tail: list[str] = []
    if isinstance(raw_tail, list):
        for line in raw_tail[-10:]:
            if not isinstance(line, str):
                continue
            safe = _safe_record_text(manager, line)
            if safe:
                safe_tail.append(safe)

    return {
        "schema_version": 1,
        "recovery_id": recovery_id,
        "model_id": model_id,
        "operation_id": operation_id,
        "operation_type": operation_type,
        "terminal_state": terminal_state,
        "interrupted_at": interrupted_at,
        "failed_stage": failed_stage,
        "last_completed_stage": last_completed,
        "message": _safe_record_text(
            manager,
            record.get("message"),
            fallback="Model preparation was interrupted.",
        ),
        "log_tail": safe_tail,
    }


def install_model_recovery_status_extension() -> None:
    """Attach compact recovery state and a sanitized action error boundary."""

    from app import model_manager as manager_module, status_split

    if getattr(status_split, _INSTALL_FLAG, False):
        return
    original_lifecycle_entry = status_split._lifecycle_catalog_entry
    original_model_recovery = manager_module.ModelManager.model_recovery
    original_recover_model = manager_module.ModelManager.recover_model

    def normalize_current_record(manager: Any, model_id: str) -> dict[str, Any] | None:
        records = getattr(manager, "_model_recovery_records", None)
        if not isinstance(records, dict):
            return None
        current = records.get(model_id)
        if current is None:
            return None
        normalized = _normalize_recovery_record(manager, model_id, current)
        if normalized is None:
            records.pop(model_id, None)
            return None
        records[model_id] = normalized
        return normalized

    def model_recovery_safely(
        self,
        model_id: str,
        *,
        include_details: bool = False,
    ) -> dict[str, Any] | None:
        normalize_current_record(self, model_id)
        try:
            return original_model_recovery(self, model_id, include_details=include_details)
        except (TypeError, ValueError, OverflowError):
            # Keep status APIs alive even if an older recovery shape contains a field
            # this build cannot interpret. Drop only the in-memory bad record; the
            # underlying incomplete model can still be inferred by the recovery layer.
            records = getattr(self, "_model_recovery_records", None)
            if isinstance(records, dict):
                records.pop(model_id, None)
            return original_model_recovery(self, model_id, include_details=include_details)

    def lifecycle_entry_with_recovery(manager: Any, model_id: str) -> dict[str, Any]:
        entry = original_lifecycle_entry(manager, model_id)
        if _model_is_active(manager, model_id):
            return entry
        records = getattr(manager, "_model_recovery_records", None)
        record = normalize_current_record(manager, model_id)
        if record is None:
            record = _inferred_record(manager, model_id)
            if isinstance(record, dict) and isinstance(records, dict):
                # Keep one operation identity for the current process. Persisting is
                # unnecessary here because incomplete output is inferred again after a
                # restart, but a stable ID prevents polling from invalidating UI actions.
                records[model_id] = record
        if isinstance(record, dict):
            entry["recovery"] = _summary_from_record(
                manager,
                model_id,
                record,
                include_details=False,
            )
        return entry

    async def recover_model_safely(
        self,
        model_id: str,
        recovery_id: str,
        action: str,
        *,
        device: str | None = None,
    ) -> dict[str, Any]:
        try:
            normalize_current_record(self, model_id)
            if model_id in self.catalog and action in {
                "resume",
                "retry_failed_stage",
                "restart_download",
                "remove_incomplete_files",
            }:
                cfg = self.catalog[model_id]
                model_dir = cfg.abs_path(manager_module.BASE_DIR).resolve()
                models_root = self.settings.models_dir.resolve()
                if model_dir == models_root:
                    raise RecoveryConflict(
                        "unsafe_output_path",
                        "Refusing to remove or replace the configured model-directory root.",
                        current_recovery_id=recovery_id,
                    )
            return await original_recover_model(
                self,
                model_id,
                recovery_id,
                action,
                device=device,
            )
        except RecoveryConflict:
            raise
        except Exception as exc:  # noqa: BLE001 - route receives only a sanitized conflict
            logger.exception("Model recovery failed for '%s'", model_id)
            sanitize = getattr(self, "_sanitize_progress_line", None)
            safe_message = sanitize(exc) if callable(sanitize) else ""
            raise RecoveryConflict(
                "recovery_failed",
                safe_message or "Model recovery could not be started safely.",
                current_recovery_id=recovery_id,
            ) from exc

    status_split._lifecycle_catalog_entry = lifecycle_entry_with_recovery
    manager_module.ModelManager.model_recovery = model_recovery_safely
    manager_module.ModelManager.recover_model = recover_model_safely
    setattr(status_split, _INSTALL_FLAG, True)


__all__ = ["install_model_recovery_status_extension"]
