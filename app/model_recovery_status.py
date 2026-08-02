"""Compose recovery summaries and contain recovery-action failures."""

from __future__ import annotations

import logging
from typing import Any

from app.model_recovery import RecoveryConflict

_INSTALL_FLAG = "_MODEL_RECOVERY_STATUS_INSTALLED"
logger = logging.getLogger("ov-llm.recovery")


def install_model_recovery_status_extension() -> None:
    """Attach compact recovery state and a sanitized action error boundary."""

    from app import model_manager as manager_module, status_split

    if getattr(status_split, _INSTALL_FLAG, False):
        return
    original_lifecycle_entry = status_split._lifecycle_catalog_entry
    original_recover_model = manager_module.ModelManager.recover_model

    def lifecycle_entry_with_recovery(manager: Any, model_id: str) -> dict[str, Any]:
        entry = original_lifecycle_entry(manager, model_id)
        recovery_provider = getattr(manager, "model_recovery", None)
        if callable(recovery_provider):
            recovery = recovery_provider(model_id, include_details=False)
            if isinstance(recovery, dict):
                entry["recovery"] = recovery
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
    manager_module.ModelManager.recover_model = recover_model_safely
    setattr(status_split, _INSTALL_FLAG, True)


__all__ = ["install_model_recovery_status_extension"]
