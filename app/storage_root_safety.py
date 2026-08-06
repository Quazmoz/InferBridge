"""Reject managed storage roots that are symbolic links or Windows junctions."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app import model_recovery
from app.model_library_conversion import is_reparse_point
from app.storage_safety import (
    StorageConflict,
    TreeMeasurement,
    _path_exists,
    cleanup_capability,
)

_SOURCE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_INSTALL_FLAG = "_storage_root_safety_installed"


def _hub_cache_root(service: Any) -> Path:
    explicit = str(os.environ.get("HUGGINGFACE_HUB_CACHE") or "").strip()
    if explicit:
        return Path(explicit).expanduser().absolute()
    hf_home = str(os.environ.get("HF_HOME") or "").strip()
    if hf_home:
        return Path(hf_home).expanduser().absolute() / "hub"
    configured = getattr(service.paths, "huggingface_cache_dir", None)
    if configured is not None:
        return Path(configured).expanduser().absolute() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def install_storage_root_safety() -> None:
    """Patch the desktop storage service without widening its public API."""

    from app.storage_manager import StorageManagerService

    if getattr(StorageManagerService, _INSTALL_FLAG, False):
        return

    original_safe_model = StorageManagerService._safe_model_measurement
    original_source_rows = StorageManagerService._source_cache_rows
    original_remove_source = StorageManagerService._remove_source_cache

    def source_cache_path(self: Any, source_model: str) -> Path | None:
        source = str(source_model or "").strip()
        if not _SOURCE_MODEL_PATTERN.fullmatch(source):
            return None
        return _hub_cache_root(self) / f"models--{source.replace('/', '--')}"

    def safe_model_measurement(self: Any, model_id: str):
        models_root = Path(self.settings.models_dir)
        if is_reparse_point(models_root):
            cfg = self.manager.catalog[model_id]
            model_dir = cfg.abs_path(model_recovery._base_dir())
            return (
                model_dir,
                TreeMeasurement(present=_path_exists(model_dir), unsafe=True),
                True,
            )
        return original_safe_model(self, model_id)

    def source_cache_rows(
        self: Any,
        groups: dict[str, dict[str, Any]],
        *,
        hub_root: Path,
    ) -> tuple[list[dict[str, Any]], int, int]:
        lexical_root = _hub_cache_root(self)
        if not is_reparse_point(lexical_root):
            return original_source_rows(self, groups, hub_root=lexical_root)

        rows: list[dict[str, Any]] = []
        for group in groups.values():
            cleanup = cleanup_capability(reclaimable_bytes=0, unsafe=True)
            rows.append(
                {
                    "source_model": group["source_model"],
                    "model_ids": sorted(group["model_ids"]),
                    "model_names": sorted(group["model_names"]),
                    "size_bytes": 0,
                    "state": "unsafe_path",
                    "shared": len(group["model_ids"]) > 1,
                    "cleanup": {
                        "action": "remove_huggingface_cache",
                        "model_id": sorted(group["model_ids"])[0],
                        **cleanup,
                    },
                }
            )
        rows.sort(key=lambda item: item["source_model"])
        return rows, 0, 0

    async def remove_source_cache(self: Any, model_id: str) -> dict[str, Any]:
        if is_reparse_point(_hub_cache_root(self)):
            raise StorageConflict(
                "unsafe_path",
                (
                    "Refusing to remove the reusable Hugging Face source cache through "
                    "a symbolic link or Windows junction."
                ),
            )
        return await original_remove_source(self, model_id)

    StorageManagerService._source_cache_path = source_cache_path
    StorageManagerService._safe_model_measurement = safe_model_measurement
    StorageManagerService._source_cache_rows = source_cache_rows
    StorageManagerService._remove_source_cache = remove_source_cache
    setattr(StorageManagerService, _INSTALL_FLAG, True)


__all__ = ["install_storage_root_safety"]
