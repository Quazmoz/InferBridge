"""Combined-device capability resolver (AUTO, AUTO:<list>, MULTI, HETERO).

Every rule here comes from a verified OpenVINO / OpenVINO GenAI fact (see
docs/DEVICE_SUPPORT.md, "Combined device modes"), not from guesswork:

* MULTI is deprecated and served by the AUTO plugin (``get_versions("MULTI")``
  reports ``openvino_auto_plugin``); its behaviour is AUTO with
  ``PERFORMANCE_HINT=CUMULATIVE_THROUGHPUT``. Saved MULTI choices are migrated to
  that equivalent, with a one-time notice.
* Plain ``AUTO`` never selects the NPU: OpenVINO excludes it from AUTO's default
  priority list, so it must be named explicitly.
* GenAI ``LLMPipeline`` uses its static NPU pipeline only when the device is exactly
  ``"NPU"``. Any combined string takes the paged-attention / stateful path, which the
  NPU was not designed for; ``VLMPipeline`` switches on its NPU code for any string
  containing ``NPU``. NPU members in HETERO or cumulative AUTO for chat are therefore
  refused, and an NPU member in an AUTO priority list is only experimental.
* Paged attention requires a single CPU, a single GPU or GPUs only; mixed CPU+GPU
  execution (HETERO:GPU,CPU, cumulative AUTO) falls back to GenAI's stateful backend.
* AUTO's CPU startup acceleration is disabled for stateful models (every GenAI LLM),
  so it is not offered for chat.
* HETERO supports ``MODEL_DISTRIBUTION_POLICY=PIPELINE_PARALLEL`` (a documented
  preview) for models that do not fit on the first device.
* GenAI's Python pipelines do not expose ``ov::execution_devices``; where AUTO landed
  is reported only when the engine can tell us.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.model_roles import role_for_backend
from app.multimodal import backend_supports_vision
from runtime import device_check

logger = logging.getLogger("ov-llm.device-modes")

SUPPORTED = "supported"
EXPERIMENTAL = "experimental"
UNSUPPORTED = "unsupported"
KINDS = ("chat", "vlm", "embedding", "rerank")
PERFORMANCE_HINTS = ("LATENCY", "THROUGHPUT", "CUMULATIVE_THROUGHPUT")
PIPELINE_PARALLEL = "pipeline_parallel"
_AUTO_DEFAULT_ORDER = ("GPU", "CPU")  # NPU is excluded from AUTO's default list
_MULTI_NOTICE = (
    "{old} uses OpenVINO's deprecated MULTI mode, which now runs on the AUTO plugin. "
    "InferBridge loads it as {new} with cumulative throughput, the documented equivalent."
)

_notified: set[str] = set()
_notified_lock = threading.Lock()


class DeviceModeError(ValueError):
    """A device expression that should be refused with HTTP 400 before compiling."""


def backend_kind(backend: object) -> str:
    role = role_for_backend(backend)
    if role == "chat" and backend_supports_vision(str(backend or "")):
        return "vlm"
    return role


def _base(token: str) -> str:
    return str(token).split(".", 1)[0].upper()


def migrate(device: str) -> tuple[str, str | None, str | None]:
    """Return ``(device, performance_hint, notice)``; MULTI becomes cumulative AUTO."""

    parsed = device_check.parse_device_expression(device)
    if parsed.kind != "MULTI":
        return parsed.normalized, None, None
    new = f"AUTO:{','.join(parsed.devices)}"
    return new, "CUMULATIVE_THROUGHPUT", _MULTI_NOTICE.format(old=parsed.normalized, new=new)


def notify_once(notice: str | None, emit: Any = None) -> bool:
    """Emit a migration notice once per process for each distinct message."""

    if not notice:
        return False
    with _notified_lock:
        if notice in _notified:
            return False
        _notified.add(notice)
    logger.warning(notice)
    if callable(emit):
        try:
            emit("warning", notice)
        except Exception:  # noqa: BLE001 - a notice must never block a load
            pass
    return True


def _members(kind: str, devices: tuple[str, ...], available: list[str] | None) -> list[str]:
    if devices:
        return list(devices)
    if kind != "AUTO":
        return [kind]
    bases = {_base(item) for item in available or []} or {"CPU"}
    return [base for base in _AUTO_DEFAULT_ORDER if base in bases]


def describe(
    device: str,
    *,
    kind: str = "chat",
    available: list[str] | None = None,
    performance_hint: str | None = None,
) -> dict[str, Any]:
    """Classify one device expression for one backend kind.

    ``available`` is OpenVINO's device list; ``None`` skips availability checks (mock).
    Raises :class:`device_check.DeviceValidationError` for malformed expressions.
    """

    original = device_check.parse_device_expression(device).normalized
    normalized, migrated_hint, notice = migrate(original)
    parsed = device_check.parse_device_expression(normalized)
    hint = (performance_hint or migrated_hint or "").upper() or None
    members = _members(parsed.kind, parsed.devices, available)
    bases = [_base(item) for item in members]
    has_npu = "NPU" in bases
    has_cpu_and_gpu = "CPU" in bases and "GPU" in bases
    missing = (
        []
        if available is None
        else [item for item in members if not device_check.is_device_available(item, available)]
    )

    if not parsed.devices and parsed.kind != "AUTO":
        mode = "single"
    elif parsed.kind == "HETERO":
        mode = "hetero"
    elif hint == "CUMULATIVE_THROUGHPUT":
        mode = "auto_cumulative"
    else:
        mode = "auto"

    support, reason = _classify(mode, kind, bases, has_npu, has_cpu_and_gpu)
    if missing:
        support = UNSUPPORTED
        reason = f"{', '.join(missing)} is not available on this PC."

    options: dict[str, list[str]] = {}
    recommended: dict[str, str] = {}
    if parsed.kind == "AUTO":
        options["performance_hint"] = list(PERFORMANCE_HINTS)
        recommended["PERFORMANCE_HINT"] = hint or "LATENCY"
    if mode == "hetero" and kind == "chat" and "GPU" in bases and not has_npu and len(bases) > 1:
        options["model_distribution"] = [PIPELINE_PARALLEL]

    return {
        "device": original,
        "normalized": normalized,
        "mode": mode,
        "members": members,
        "support": support,
        "reason": reason,
        "experimental": mode != "single",
        "recommended_hints": recommended,
        "options": options,
        "kv_cache_precision": "configurable"
        if mode == "single" and bases[0] in ("CPU", "GPU")
        else "auto_only",
        "may_use_npu": has_npu,
        "migrated_from": original if notice else None,
        "notice": notice,
        "missing": missing,
    }


def _classify(
    mode: str, kind: str, bases: list[str], has_npu: bool, cpu_gpu: bool
) -> tuple[str, str]:
    if mode == "single":
        if bases[0] == "NPU" and kind in ("embedding", "rerank"):
            return EXPERIMENTAL, "Embedding and reranker models are only verified on CPU and GPU."
        return SUPPORTED, "Direct device target."
    generative = kind in ("chat", "vlm")
    if has_npu and generative:
        if mode == "auto" and kind == "chat":
            return EXPERIMENTAL, (
                "OpenVINO GenAI uses its NPU-optimised pipeline only when you choose NPU "
                "directly. Through AUTO the NPU gets the general pipeline; if it cannot "
                "compile, AUTO moves on to the next device."
            )
        return UNSUPPORTED, (
            "The NPU cannot share a chat model with other devices: GenAI's NPU pipeline "
            "is static and only runs when NPU is the whole target. Choose NPU on its own."
        )
    if has_npu and mode == "hetero":
        return UNSUPPORTED, "Splitting a model between the NPU and other devices is not verified."
    if mode == "hetero":
        if len(bases) == 1:
            return (
                EXPERIMENTAL,
                "HETERO with one device behaves like that device; choose it directly.",
            )
        return EXPERIMENTAL, (
            "Splits the model's layers across devices. Useful when a model does not fit in "
            "GPU memory (turn on pipeline-parallel); otherwise usually slower than the GPU alone."
            if generative
            else "Splits the model's layers across devices; rarely faster for small models."
        )
    if mode == "auto_cumulative":
        detail = (
            " Mixed CPU and GPU execution also makes GenAI fall back from paged attention."
            if generative and cpu_gpu
            else ""
        )
        return EXPERIMENTAL, (
            "Runs copies of the model on every listed device for throughput. InferBridge "
            "serves one request at a time per model, so single chats rarely get faster." + detail
        )
    return SUPPORTED, (
        "OpenVINO picks the first listed device that can compile the model. "
        "It does not use several devices at once."
    )


def resolve_or_400(
    device: str,
    *,
    backend: object,
    available: list[str] | None,
    performance_hint: str | None = None,
    model_distribution: str | None = None,
    allow_unsupported: bool = False,
) -> dict[str, Any]:
    """Describe ``device`` for ``backend`` or raise :class:`DeviceModeError`."""

    try:
        info = describe(
            device,
            kind=backend_kind(backend),
            available=available,
            performance_hint=performance_hint,
        )
    except device_check.DeviceValidationError as exc:
        raise DeviceModeError(str(exc)) from exc
    if performance_hint and "performance_hint" not in info["options"]:
        raise DeviceModeError(
            f"performance_hint only applies to AUTO devices, not {info['normalized']}."
        )
    if model_distribution and "model_distribution" not in info["options"]:
        raise DeviceModeError(
            "model_distribution='pipeline_parallel' needs a chat model on HETERO with a GPU "
            "and at least one more non-NPU device, for example HETERO:GPU,CPU."
        )
    if info["missing"]:
        raise DeviceModeError(f"{info['normalized']}: {info['reason']}")
    if info["support"] == UNSUPPORTED and not allow_unsupported:
        raise DeviceModeError(
            f"{info['normalized']} is not supported for this model: {info['reason']} "
            "Set allow_resource_risk to try it anyway."
        )
    return info


def engine_properties(
    device: str, *, backend: object, options: dict[str, Any] | None = None
) -> tuple[str, dict[str, str], str | None]:
    """Return ``(device, plugin properties, notice)`` for a native engine build."""

    options = options or {}
    normalized, migrated_hint, notice = migrate(device)
    parsed = device_check.parse_device_expression(normalized)
    props: dict[str, str] = {}
    if parsed.kind == "AUTO":
        hint = options.get("performance_hint") or migrated_hint
        if hint is None and backend_kind(backend) in ("chat", "vlm"):
            hint = "LATENCY"  # AUTO's documented default, made explicit for chat
        if hint:
            props["PERFORMANCE_HINT"] = str(hint).upper()
    elif parsed.kind == "HETERO" and options.get("model_distribution") == PIPELINE_PARALLEL:
        props["MODEL_DISTRIBUTION_POLICY"] = "PIPELINE_PARALLEL"
    return normalized, props, notice


def may_use_npu(device: object) -> bool:
    """Whether a device expression can place work on the NPU (plain AUTO cannot)."""

    try:
        parsed = device_check.parse_device_expression(str(device or ""))
    except device_check.DeviceValidationError:
        return "NPU" in str(device or "").upper()
    tokens = parsed.devices or (parsed.kind,)
    return any(_base(token) == "NPU" for token in tokens)


def best_single_device(device: str, available: list[str] | None) -> str | None:
    """The first non-NPU member of a combined expression that is available."""

    try:
        parsed = device_check.parse_device_expression(device)
    except device_check.DeviceValidationError:
        return None
    if not parsed.devices and parsed.kind != "AUTO":
        return None
    for member in _members(parsed.kind, parsed.devices, available):
        base = _base(member)
        if base != "NPU" and (
            available is None or device_check.is_device_available(base, available)
        ):
            return base
    return None


def candidate_modes(available: list[str] | None) -> list[str]:
    """Combined expressions worth showing in a builder for this PC."""

    bases = [base for base in ("NPU", "GPU", "CPU") if base in {_base(a) for a in available or []}]
    if not bases:
        bases = ["CPU"]
    gpu_cpu = [base for base in bases if base != "NPU"]
    modes = ["AUTO"]
    if len(bases) > 1:
        modes.append(f"AUTO:{','.join(bases)}")
    if len(gpu_cpu) > 1:
        joined = ",".join(gpu_cpu)
        modes += [f"AUTO:{joined}", f"HETERO:{joined}"]
    if "NPU" in bases and len(bases) > 1:
        modes.append(f"HETERO:{','.join(bases)}")
    return list(dict.fromkeys(modes))


def modes_report(available: list[str] | None, *, mock: bool = False) -> dict[str, Any]:
    """Payload for ``GET /v1/devices/modes``."""

    check = None if mock else available
    rows = []
    for expression in candidate_modes(available):
        per_kind = {
            kind: {
                key: value
                for key, value in describe(expression, kind=kind, available=check).items()
                if key in ("support", "reason", "recommended_hints", "options")
            }
            for kind in KINDS
        }
        base = describe(expression, kind="chat", available=check)
        cumulative = None
        if base["mode"] == "auto" and len(base["members"]) > 1:
            cumulative = {
                kind: {
                    key: value
                    for key, value in describe(
                        expression,
                        kind=kind,
                        available=check,
                        performance_hint="CUMULATIVE_THROUGHPUT",
                    ).items()
                    if key in ("support", "reason")
                }
                for kind in KINDS
            }
        rows.append(
            {
                "device": expression,
                "mode": base["mode"],
                "members": base["members"],
                "kv_cache_precision": base["kv_cache_precision"],
                "may_use_npu": base["may_use_npu"],
                "backends": per_kind,
                "cumulative_throughput": cumulative,
            }
        )
    return {
        "available": list(available or []),
        "mock": mock,
        "modes": rows,
        "performance_hints": list(PERFORMANCE_HINTS),
        "migrations": {"MULTI": "AUTO with PERFORMANCE_HINT=CUMULATIVE_THROUGHPUT"},
        "execution_devices_reported": False,
        "notes": [
            "Plain AUTO never selects the NPU; name it explicitly, e.g. AUTO:NPU,GPU,CPU.",
            "KV cache precision is configurable only for one direct CPU or GPU target.",
            "AUTO's CPU startup acceleration is disabled by OpenVINO for stateful LLMs.",
        ],
    }


def placement(device: str | None, engine: object = None) -> dict[str, Any]:
    """Model-card / slot fields: where a loaded engine runs, e.g. ``AUTO → GPU``."""

    executed = list(getattr(engine, "execution_devices", None) or [])
    source = getattr(engine, "execution_devices_source", None) if executed else None
    combined = bool(device) and (":" in str(device) or str(device).upper() == "AUTO")
    if not device:
        label = None
    elif executed and combined:
        label = f"{device} \u2192 {', '.join(executed)}"
    else:
        label = str(device)
    return {
        "execution_devices": executed or None,
        "execution_devices_source": source,
        "resolved_device": label,
        "combined_device": combined,
    }
