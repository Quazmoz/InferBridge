"""Compose recovery summaries into split model-status rows."""

from __future__ import annotations

from typing import Any

_INSTALL_FLAG = "_MODEL_RECOVERY_STATUS_INSTALLED"


def install_model_recovery_status_extension() -> None:
    """Attach compact recovery state without changing the core status collector."""

    from app import status_split

    if getattr(status_split, _INSTALL_FLAG, False):
        return
    original_lifecycle_entry = status_split._lifecycle_catalog_entry

    def lifecycle_entry_with_recovery(manager: Any, model_id: str) -> dict[str, Any]:
        entry = original_lifecycle_entry(manager, model_id)
        recovery_provider = getattr(manager, "model_recovery", None)
        if callable(recovery_provider):
            recovery = recovery_provider(model_id, include_details=False)
            if isinstance(recovery, dict):
                entry["recovery"] = recovery
        return entry

    status_split._lifecycle_catalog_entry = lifecycle_entry_with_recovery
    setattr(status_split, _INSTALL_FLAG, True)


__all__ = ["install_model_recovery_status_extension"]
