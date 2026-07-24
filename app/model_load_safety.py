"""Safe conversion profiles and device routing for OpenVINO LLM loads.

OpenVINO GenAI can terminate the host process when an INT4 artifact that was
exported with incompatible quantization settings is compiled for NPU. This
module records the settings used for each conversion and prevents unverified
artifacts from reaching the native NPU compiler.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import model_registry as registry
from runtime import device_check

LOAD_PROFILE_SCHEMA_VERSION = 1
LOAD_PROFILE_FILENAME = ".ovllm-load-profile.json"
_NPU_INT4_GROUP_SIZES = {-1, 128}


def load_profile_path(cfg: registry.ModelConfig, base_dir: Path) -> Path:
    return cfg.abs_path(base_dir) / LOAD_PROFILE_FILENAME


def resolve_conversion_profile(
    cfg: registry.ModelConfig,
    *,
    weight_format: str | None,
    group_size: int | None,
    ratio: float | None,
    sym: bool | None,
) -> tuple[str, int | None, float | None, bool | None]:
    """Return effective conversion settings with portable INT4 defaults.

    Intel's NPU guidance requires symmetric INT4/NF4 weights, a full 4-bit
    ratio, and either group-wise 128 or channel-wise -1 quantization. Those
    settings are also valid on CPU and GPU, so new INT4 artifacts default to a
    profile that remains eligible for later NPU use.
    """

    effective_format = str(weight_format or cfg.weight_format).lower()
    if effective_format != "int4":
        return effective_format, group_size, ratio, sym

    return (
        effective_format,
        128 if group_size is None else int(group_size),
        1.0 if ratio is None else float(ratio),
        True if sym is None else bool(sym),
    )


def is_npu_compatible_int4_profile(
    *,
    weight_format: str,
    group_size: int | None,
    ratio: float | None,
    sym: bool | None,
) -> bool:
    try:
        normalized_group_size = int(group_size) if group_size is not None else None
        normalized_ratio = float(ratio) if ratio is not None else None
    except (TypeError, ValueError):
        return False
    return (
        str(weight_format).lower() == "int4"
        and normalized_group_size in _NPU_INT4_GROUP_SIZES
        and normalized_ratio is not None
        and abs(normalized_ratio - 1.0) < 1e-9
        and sym is True
    )


def record_load_profile(
    cfg: registry.ModelConfig,
    base_dir: Path,
    *,
    weight_format: str,
    group_size: int | None,
    ratio: float | None,
    sym: bool | None,
) -> None:
    """Persist the conversion properties needed for safe future device loads."""

    model_dir = cfg.abs_path(base_dir)
    if not registry.is_openvino_model_dir(model_dir):
        return

    marker = {
        "schema_version": LOAD_PROFILE_SCHEMA_VERSION,
        "model_id": cfg.id,
        "source_model": cfg.source_model,
        "backend": cfg.backend,
        "weight_format": str(weight_format).lower(),
        "group_size": group_size,
        "ratio": ratio,
        "symmetric": sym,
        "npu_compatible_int4": is_npu_compatible_int4_profile(
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
        ),
    }
    path = load_profile_path(cfg, base_dir)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _read_profile(cfg: registry.ModelConfig, base_dir: Path) -> dict[str, Any] | None:
    path = load_profile_path(cfg, base_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != LOAD_PROFILE_SCHEMA_VERSION:
        return None
    expected = {
        "model_id": cfg.id,
        "source_model": cfg.source_model,
        "backend": cfg.backend,
        "weight_format": cfg.weight_format,
    }
    if any(str(value.get(key)) != str(expected_value) for key, expected_value in expected.items()):
        return None
    return value


def _base_device(value: str) -> str:
    return str(value or "").split(".", 1)[0].upper()


def _available_safe_devices(available: list[str]) -> tuple[str, ...]:
    bases = {_base_device(item) for item in available}
    return tuple(device for device in ("GPU", "CPU") if device in bases)


def safe_load_device(
    cfg: registry.ModelConfig,
    base_dir: Path,
    device: str,
    *,
    available: list[str] | None = None,
) -> str:
    """Return a crash-safe target or reject an unsafe direct NPU request.

    CPU and GPU loads are unchanged. A direct NPU request for an INT4 model is
    allowed only when this application recorded an NPU-compatible conversion
    profile. AUTO and composite expressions silently remove NPU when the local
    artifact is unverified, preserving availability without claiming NPU use.
    """

    normalized = device_check.normalize_device(device)
    if str(cfg.weight_format).lower() != "int4":
        return normalized

    profile = _read_profile(cfg, base_dir)
    verified = bool(
        profile
        and profile.get("npu_compatible_int4") is True
        and is_npu_compatible_int4_profile(
            weight_format=str(profile.get("weight_format") or ""),
            group_size=profile.get("group_size"),
            ratio=profile.get("ratio"),
            sym=profile.get("symmetric"),
        )
    )
    if verified:
        return normalized

    parsed = device_check.parse_device_expression(normalized)
    direct_base = _base_device(parsed.kind)
    if direct_base == "NPU" and not parsed.devices:
        raise RuntimeError(
            f"{cfg.name} was not converted with a verified NPU-compatible INT4 profile. "
            "Delete and reconvert it with the default INT4 settings "
            "(symmetric, ratio 1.0, group size 128) before loading it on NPU."
        )

    if parsed.devices:
        filtered = tuple(token for token in parsed.devices if _base_device(token) != "NPU")
        if filtered:
            return f"{parsed.kind}:{','.join(filtered)}"
        raise RuntimeError(
            f"{cfg.name} cannot use this device expression until it is reconverted with "
            "the verified NPU-compatible INT4 profile."
        )

    if parsed.kind == "AUTO":
        choices = _available_safe_devices(available or device_check.available_devices())
        if not choices:
            raise RuntimeError(
                f"{cfg.name} has an unverified INT4 artifact and no safe CPU or GPU target "
                "is available. Reconvert it with the default INT4 settings."
            )
        return choices[0] if len(choices) == 1 else f"AUTO:{','.join(choices)}"

    return normalized
