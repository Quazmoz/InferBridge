"""Defense-in-depth safeguards for Hugging Face model preparation.

The browser routes perform the primary interactive preflight. This extension also
protects internal conversion paths and keeps access metadata intact whenever the
model catalog is rewritten by registration, imports, or conversion bookkeeping.
"""

from __future__ import annotations

import contextlib
import functools
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

_ACCESS_TYPES = frozenset({"public", "gated", "unknown"})
_ACCESS_FIELDS = ("access_type", "model_url", "license_url")
_SAVE_LOCK = threading.RLock()


def _model_url(source_model: str) -> str:
    return f"https://huggingface.co/{quote(source_model, safe='/')}"


def _safe_huggingface_url(value: Any, source_model: str) -> str:
    text = str(value or "").strip()
    if text.startswith("https://huggingface.co/"):
        return text
    return _model_url(source_model)


def _read_access_metadata(path: Path) -> dict[str, tuple[str, dict[str, str]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    preserved: dict[str, tuple[str, dict[str, str]]] = {}
    for model_id, entry in raw.items():
        if not isinstance(model_id, str) or not isinstance(entry, dict):
            continue
        source_model = str(entry.get("source_model") or "").strip()
        access_type = str(entry.get("access_type") or "").strip().lower()
        if not source_model or access_type not in _ACCESS_TYPES:
            continue
        preserved[model_id] = (
            source_model,
            {
                "access_type": access_type,
                "model_url": _safe_huggingface_url(entry.get("model_url"), source_model),
                "license_url": _safe_huggingface_url(entry.get("license_url"), source_model),
            },
        )
    return preserved


def _install_catalog_persistence() -> None:
    from app import model_registry as registry

    if getattr(registry, "_inferbridge_hf_catalog_persistence_installed", False):
        return
    original_save = registry.save_catalog

    @functools.wraps(original_save)
    def save_catalog_with_access_metadata(models_file: Path, catalog: dict[str, Any]) -> None:
        path = Path(models_file)
        with _SAVE_LOCK:
            preserved = _read_access_metadata(path)
            stage = path.with_name(f".{path.name}.hf-stage")
            stage_temp = stage.with_suffix(stage.suffix + ".tmp")
            try:
                original_save(stage, catalog)
                data = json.loads(stage.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Serialized model catalog was not an object.")

                for model_id, entry in data.items():
                    if not isinstance(entry, dict):
                        continue
                    source_model = str(entry.get("source_model") or "").strip()
                    previous = preserved.get(model_id)
                    if previous is None or previous[0] != source_model:
                        for field in _ACCESS_FIELDS:
                            entry.pop(field, None)
                        continue
                    entry.update(previous[1])

                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(path.suffix + ".tmp")
                temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                temp.replace(path)
            finally:
                with contextlib.suppress(OSError):
                    stage.unlink()
                with contextlib.suppress(OSError):
                    stage_temp.unlink()

    registry.save_catalog = save_catalog_with_access_metadata
    registry._inferbridge_hf_catalog_persistence_installed = True


def _install_internal_conversion_preflight() -> None:
    from app import model_manager, model_registry as registry
    from app.config import BASE_DIR
    from app.huggingface_access import HuggingFaceAccessService

    cls = model_manager.ModelManager
    if getattr(cls, "_inferbridge_hf_internal_preflight_installed", False):
        return
    original_convert = cls._convert_task

    @functools.wraps(original_convert)
    async def convert_with_internal_preflight(
        self: Any,
        model_id: str,
        device: str,
        load_after: bool,
        weight_format: str | None = None,
        group_size: int | None = None,
        ratio: float | None = None,
        sym: bool | None = None,
        trust_remote_code: bool | None = None,
    ) -> Any:
        cfg = self.catalog[model_id]
        should_check = (
            not self.force_mock
            and bool(cfg.source_model)
            and not (registry.is_downloaded(cfg, BASE_DIR) and weight_format is None)
        )
        if should_check:
            service = getattr(self, "_hf_internal_access_service", None)
            if service is None:
                service = HuggingFaceAccessService(self._hf_credential_store)
                self._hf_internal_access_service = service
            access = self.catalog_entry(model_id).get("huggingface_access") or {}
            try:
                result = await service.preflight(
                    cfg.source_model,
                    access_type=str(access.get("access_type") or "unknown"),
                )
            except ValueError as exc:
                result = {
                    "code": "hf_model_id_invalid",
                    "message": str(exc),
                    "recoverable": True,
                }
            if result.get("code") != "hf_access_granted":
                message = str(
                    result.get("message")
                    or "Hugging Face access must be configured before conversion."
                )
                self._set_status(model_id, "error", error=message)
                self._set_progress(
                    model_id,
                    "error",
                    f"Hugging Face access check blocked conversion: {message}",
                )
                self.emit_event(
                    "warning",
                    f"Hugging Face access blocked conversion for {cfg.name}",
                )
                self.convert_tasks.pop(model_id, None)
                return None

        return await original_convert(
            self,
            model_id,
            device,
            load_after,
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
            trust_remote_code=trust_remote_code,
        )

    cls._convert_task = convert_with_internal_preflight
    cls._inferbridge_hf_internal_preflight_installed = True


def install_huggingface_manager_safety() -> None:
    """Install catalog persistence and manager-level access preflight once."""

    _install_catalog_persistence()
    _install_internal_conversion_preflight()


__all__ = ["install_huggingface_manager_safety"]
