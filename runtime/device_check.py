"""OpenVINO device discovery and validation.

Safe to import anywhere: if OpenVINO is not installed every function degrades to
an empty / "unavailable" result instead of raising.
"""

from __future__ import annotations

import atexit
import importlib.util
import logging
import re
import threading
from dataclasses import dataclass

logger = logging.getLogger("ov-llm.device")

DEFAULT_DEVICE = "NPU"
PHYSICAL_DEVICE_BASES = ("CPU", "GPU", "NPU")
META_DEVICES = ("AUTO", "MULTI", "HETERO")
SUPPORTED_DEVICE_EXAMPLES = (
    "CPU",
    "GPU",
    "NPU",
    "AUTO",
    "AUTO:NPU,GPU,CPU",
    "AUTO:GPU,NPU,CPU",
    "MULTI:GPU,CPU",
    "HETERO:GPU,CPU",
)

_PHYSICAL_RE = re.compile(r"^(CPU|GPU|NPU)(?:\.\d+)?$")


class DeviceValidationError(ValueError):
    """Raised when a user-supplied OpenVINO device expression is invalid."""


@dataclass(frozen=True)
class DeviceExpression:
    """Parsed OpenVINO device target such as ``GPU`` or ``AUTO:NPU,GPU,CPU``."""

    kind: str
    devices: tuple[str, ...] = ()

    @property
    def normalized(self) -> str:
        if self.devices:
            return f"{self.kind}:{','.join(self.devices)}"
        return self.kind

    @property
    def is_composite(self) -> bool:
        return bool(self.devices)


def is_openvino_available() -> bool:
    """True if the OpenVINO GenAI runtime can be imported."""
    return importlib.util.find_spec("openvino_genai") is not None


def normalize_device(device: str | None) -> str:
    return parse_device_expression(device, default=DEFAULT_DEVICE).normalized


def parse_device_expression(device: str | None, *, default: str | None = None) -> DeviceExpression:
    """Parse and normalize an OpenVINO device expression.

    ``default`` is only for internal configuration paths that historically treat
    a missing value as the default device. For explicit user input, pass no
    default so empty strings are rejected.
    """
    if device is None:
        if default is None:
            raise DeviceValidationError("Device value is required.")
        device = default

    raw = str(device).strip()
    if not raw:
        if default is None:
            raise DeviceValidationError("Device value cannot be empty.")
        raw = default

    text = raw.upper()
    if ":" not in text:
        if text == "AUTO":
            return DeviceExpression("AUTO")
        if _is_physical_token(text):
            return DeviceExpression(text)
        if text in {"MULTI", "HETERO"}:
            raise DeviceValidationError(
                f"Device '{text}' requires priorities, for example {text}:GPU,CPU."
            )
        raise DeviceValidationError(f"Unsupported OpenVINO device '{raw}'.")

    meta, priorities = (part.strip() for part in text.split(":", 1))
    if meta not in META_DEVICES:
        raise DeviceValidationError(f"Unsupported OpenVINO device mode '{meta or raw}'.")
    if not priorities:
        raise DeviceValidationError(f"Device '{meta}:' must include at least one physical device.")

    devices = tuple(part.strip() for part in priorities.split(","))
    if any(not part for part in devices):
        raise DeviceValidationError(f"Device '{raw}' contains an empty priority entry.")

    for token in devices:
        if not _is_physical_token(token):
            raise DeviceValidationError(
                f"Unsupported OpenVINO physical device '{token}' in '{raw}'."
            )
    if len(set(devices)) != len(devices):
        raise DeviceValidationError(f"Device '{raw}' contains duplicate priority entries.")
    return DeviceExpression(meta, devices)


def validate_device_expression(device: str | None, available: list[str] | None = None) -> str:
    """Return a normalized expression or raise with a user-facing message."""
    try:
        normalized = parse_device_expression(device).normalized
    except DeviceValidationError as exc:
        raise DeviceValidationError(_device_error_message(str(exc), available)) from exc

    if available is not None and not is_device_available(normalized, available):
        raise DeviceValidationError(
            _device_error_message(f"Device '{normalized}' is not available.", available)
        )
    return normalized


def _is_physical_token(device: str) -> bool:
    return bool(_PHYSICAL_RE.fullmatch(device))


_core_lock = threading.Lock()
_core_instance = None
_cached_devices: list[str] | None = None
_cached_details: list[dict] | None = None


def cleanup_cached_core() -> None:
    """Clear the cached OpenVINO Core and device discovery results.

    Releases Core resources at exit and gives tests a way to fully reset
    discovery state between cases.
    """
    global _core_instance, _cached_devices, _cached_details
    _core_instance = None
    _cached_devices = None
    _cached_details = None


atexit.register(cleanup_cached_core)


def _get_core():
    """Get or create the global thread-safe openvino.Core instance."""
    global _core_instance
    if _core_instance is None:
        with _core_lock:
            if _core_instance is None:
                import openvino as ov

                _core_instance = ov.Core()
    return _core_instance


def get_core():
    """Public accessor for the cached OpenVINO Core singleton.

    Prefer this over the internal ``_get_core()`` in modules outside
    ``device_check`` (e.g. telemetry) to avoid coupling to private internals.
    """
    return _get_core()


def available_devices() -> list[str]:
    """List OpenVINO device names (e.g. ['CPU', 'GPU', 'NPU']); [] if unavailable."""
    global _cached_devices
    if _cached_devices is not None:
        return list(_cached_devices)

    if importlib.util.find_spec("openvino") is None:
        return []
    try:
        core = _get_core()
        _cached_devices = list(core.available_devices)
        return list(_cached_devices)
    except Exception as exc:  # pragma: no cover - depends on local OpenVINO/drivers
        logger.warning("OpenVINO device discovery failed: %s", exc)
        return []


def device_details() -> list[dict]:
    """List devices with their human-readable full names."""
    global _cached_details
    if _cached_details is not None:
        return [dict(item) for item in _cached_details]

    if importlib.util.find_spec("openvino") is None:
        return []
    try:
        core = _get_core()
        details = []
        for dev in core.available_devices:
            try:
                full = core.get_property(dev, "FULL_DEVICE_NAME")
            except Exception:
                full = dev
            details.append({"device": dev, "full_name": str(full)})
        _cached_details = details
        return [dict(item) for item in _cached_details]
    except Exception as exc:  # pragma: no cover
        logger.warning("OpenVINO device discovery failed: %s", exc)
        return []


def is_device_available(device: str, available: list[str] | None = None) -> bool:
    """Whether ``device`` can be targeted.

    Plain ``AUTO`` always passes. Otherwise matches every requested physical
    target against discovered devices. Unindexed targets such as ``GPU`` match
    indexed devices such as ``GPU.0``; explicitly indexed targets require an
    exact match. If discovery returned nothing (OpenVINO absent), only CPU/AUTO
    are considered available; mock-mode server paths skip this hardware check.
    """
    try:
        parsed = parse_device_expression(device)
    except DeviceValidationError:
        return False
    if parsed.kind == "AUTO" and not parsed.devices:
        return True
    if available is None:
        available = available_devices()
    if not available:
        return parsed.kind == "CPU"
    tokens = parsed.devices or (parsed.kind,)
    return all(_physical_device_available(token, available) for token in tokens)


def supported_device_examples() -> list[str]:
    """Examples accepted by the parser, for help text and error messages."""
    return list(SUPPORTED_DEVICE_EXAMPLES)


def suggested_device_targets(available: list[str] | None = None) -> list[dict[str, object]]:
    """Suggest advanced targets from physical devices OpenVINO reports.

    Suggestions are intentionally descriptive rather than promises of speed.
    ``MULTI`` and ``HETERO`` remain marked experimental because the server still
    serializes generation per loaded model.
    """
    if available is None:
        available = available_devices()
    bases = _available_bases(available)
    suggestions: list[dict[str, object]] = []

    def add(device: str, *, experimental: bool, note: str) -> None:
        if is_device_available(device, available):
            suggestions.append({"device": device, "experimental": experimental, "note": note})

    if {"NPU", "GPU", "CPU"}.issubset(bases):
        add(
            "AUTO:NPU,GPU,CPU",
            experimental=False,
            note="Auto-select best device (prefers NPU > GPU > CPU). Actual device chosen by model compatibility.",
        )
        add(
            "AUTO:GPU,NPU,CPU",
            experimental=False,
            note="Auto-select best device (prefers GPU > NPU > CPU). Actual device chosen by model compatibility.",
        )
        add("MULTI:NPU,GPU,CPU", experimental=True, note="Experimental throughput routing.")
        add("HETERO:NPU,GPU,CPU", experimental=True, note="Experimental graph partitioning.")
    elif {"GPU", "CPU"}.issubset(bases):
        add(
            "AUTO:GPU,CPU",
            experimental=False,
            note="Auto-select best device (prefers GPU > CPU). Actual device chosen by model compatibility.",
        )
        add("MULTI:GPU,CPU", experimental=True, note="Experimental throughput routing.")
        add("HETERO:GPU,CPU", experimental=True, note="Experimental graph partitioning.")
    elif {"NPU", "CPU"}.issubset(bases):
        add(
            "AUTO:NPU,CPU",
            experimental=False,
            note="Auto-select best device (prefers NPU > CPU). Actual device chosen by model compatibility.",
        )
        add("MULTI:NPU,CPU", experimental=True, note="Experimental throughput routing.")
    return suggestions


def _physical_device_available(device: str, available: list[str]) -> bool:
    requested = str(device).strip().upper()
    normalized_available = [str(a).strip().upper() for a in available if str(a).strip()]
    if "." in requested:
        return requested in normalized_available
    base = requested.split(".", 1)[0]
    return any(a == base or a.startswith(f"{base}.") for a in normalized_available)


def _available_bases(available: list[str]) -> set[str]:
    bases: set[str] = set()
    for device in available:
        normalized = str(device).strip().upper()
        if not normalized:
            continue
        base = normalized.split(".")[0]
        if base in PHYSICAL_DEVICE_BASES:
            bases.add(base)
    return bases


def _device_error_message(reason: str, available: list[str] | None) -> str:
    if available is None:
        available = available_devices()
    avail = ", ".join(available) if available else "none detected"
    examples = ", ".join(SUPPORTED_DEVICE_EXAMPLES)
    return f"{reason} Detected devices: {avail}. Supported examples: {examples}."
