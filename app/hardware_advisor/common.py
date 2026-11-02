"""Shared constants and helpers for the hardware-aware model advisor."""

from __future__ import annotations

import re
from datetime import UTC, datetime

PROFILE_ORDER = ("fastest", "balanced", "best-quality", "lowest-memory", "lowest-power")
PROFILE_LABELS = {
    "fastest": "Fastest",
    "balanced": "Balanced",
    "best-quality": "Best quality",
    "lowest-memory": "Lowest memory",
    "lowest-power": "Lowest power",
}
_PROFILE_ALIASES = {
    "fast": "fastest",
    "speed": "fastest",
    "default": "balanced",
    "quality": "best-quality",
    "best": "best-quality",
    "best-quality": "best-quality",
    "memory": "lowest-memory",
    "low-memory": "lowest-memory",
    "power": "lowest-power",
    "low-power": "lowest-power",
}
_AUTO_RE = re.compile(r"^auto(?:[:/](?P<profile>[a-z0-9 _-]+))?$", re.IGNORECASE)
_SIZE_B_RE = re.compile(r"(?<![a-z0-9])([0-9]+(?:\.[0-9]+)?)\s*b(?:illion)?(?![a-z])", re.I)
_SIZE_M_RE = re.compile(r"(?<![a-z0-9])([0-9]+(?:\.[0-9]+)?)\s*m(?:illion)?(?![a-z])", re.I)
_KNOWN_PARAMETER_COUNTS_B = {
    "tinyllama": 1.1,
    "phi-3.5-mini": 3.8,
    "phi3.5-mini": 3.8,
    "phi-4-mini": 3.8,
    "phi4-mini": 3.8,
    "bge-small-en-v1.5": 0.033,
    "bge-reranker-base": 0.278,
    "bge-reranker-v2-m3": 0.568,
}
AUTO_BENCHMARK_TTL_SECONDS = 6 * 60 * 60
SNAPSHOT_TTL_SECONDS = 30.0
AUTOMATIC_PROMPT = (
    "Reply with one short sentence confirming this local OpenVINO benchmark completed."
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_profile(value: str | None, *, default: str = "balanced") -> str:
    text = str(value or default).strip().lower().replace("_", "-").replace(" ", "-")
    text = re.sub(r"-+", "-", text).strip("-")
    text = _PROFILE_ALIASES.get(text, text)
    if text not in PROFILE_ORDER:
        raise ValueError(
            f"Unknown advisor profile '{value}'. Supported profiles: {', '.join(PROFILE_ORDER)}."
        )
    return text


def parse_auto_model(value: str | None) -> str | None:
    match = _AUTO_RE.fullmatch(str(value or "").strip())
    return normalize_profile(match.group("profile") or "balanced") if match else None


def is_auto_model(value: str | None) -> bool:
    return parse_auto_model(value) is not None


def infer_parameter_count_b(*values: str) -> float:
    text = " ".join(str(value or "") for value in values).lower()
    for key, count in _KNOWN_PARAMETER_COUNTS_B.items():
        if key in text:
            return count
    billions = [float(match) for match in _SIZE_B_RE.findall(text)]
    if billions:
        return max(min(billions), 0.001)
    millions = [float(match) / 1000.0 for match in _SIZE_M_RE.findall(text)]
    return max(min(millions), 0.001) if millions else 1.0


def base_device(device: str | None) -> str:
    text = str(device or "CPU").upper()
    if ":" in text:
        priorities = text.split(":", 1)[1].split(",")
        return priorities[0].split(".", 1)[0] if priorities else "CPU"
    return text.split(".", 1)[0]


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(value, high))
