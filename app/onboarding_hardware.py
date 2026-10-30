"""Hardware scan presentation and conservative NPU readiness classification."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Mapping
from typing import Any

from app.hardware_advisor.common import utc_now
from app.onboarding_models import (
    ItemStatus,
    NpuReadinessResponse,
    NpuState,
    SystemItem,
    SystemScanResponse,
)

INTEL_NPU_SUPPORT_URL = (
    "https://www.intel.com/content/www/us/en/download/794734/intel-npu-driver-windows.html"
)


def _text(value: Any, fallback: str = "Unknown") -> str:
    rendered = str(value or "").strip()
    return rendered or fallback


def _available_bases(snapshot: Mapping[str, Any]) -> set[str]:
    return {str(item).split(".", 1)[0].upper() for item in snapshot.get("available_devices", [])}


def _device(snapshot: Mapping[str, Any], base: str) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in snapshot.get("devices", [])
            if str(item.get("base") or item.get("device") or "").upper().startswith(base)
        ),
        None,
    )


def detect_windows_npu_hardware() -> list[dict[str, str]]:
    """Return matching Windows PnP devices using a fixed, non-user-controlled query."""

    if os.name != "nt":
        return []
    command = (
        "$items = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FriendlyName -match "
        "'(?i)(neural processing unit|intel.*npu|intel.*ai boost)' }; "
        "$items | Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json -Compress"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = parsed if isinstance(parsed, list) else [parsed]
    return [
        {
            "name": _text(item.get("FriendlyName")),
            "status": _text(item.get("Status")),
            "instance_id": _text(item.get("InstanceId")),
        }
        for item in rows
        if isinstance(item, dict)
    ]


def classify_npu_readiness(
    snapshot: Mapping[str, Any], *, pnp_devices: list[dict[str, str]] | None = None
) -> NpuReadinessResponse:
    mock = bool(snapshot.get("runtime", {}).get("mock"))
    available = [str(item) for item in snapshot.get("available_devices", [])]
    bases = _available_bases(snapshot)
    npu = _device(snapshot, "NPU")
    driver = _text(npu.get("driver_version"), "") if npu else ""
    fallback = "GPU" if "GPU" in bases else "CPU" if "CPU" in bases else None

    if mock:
        return NpuReadinessResponse(
            state=NpuState.MOCK,
            usable=False,
            title="Mock mode does not validate NPU support",
            explanation=(
                "The deterministic mock engine is active. It can validate the setup flow, "
                "but it cannot prove that an Intel NPU or its driver works."
            ),
            available_devices=available,
            fallback_device=fallback,
            driver_version=driver or None,
            support_url=INTEL_NPU_SUPPORT_URL,
            mock=True,
        )

    if "NPU" in bases:
        return NpuReadinessResponse(
            state=NpuState.USABLE,
            usable=True,
            title="NPU is visible to OpenVINO",
            explanation=(
                "OpenVINO reports an NPU device. Final compatibility still depends on the "
                "selected model, driver, and successful measured generation."
            ),
            available_devices=available,
            fallback_device=fallback,
            driver_version=driver or None,
            support_url=INTEL_NPU_SUPPORT_URL,
        )

    pnp = detect_windows_npu_hardware() if pnp_devices is None else pnp_devices
    if pnp:
        return NpuReadinessResponse(
            state=NpuState.HARDWARE_PLUGIN_UNAVAILABLE,
            usable=False,
            title="NPU hardware appears present but OpenVINO cannot use it",
            explanation=(
                "Windows reports NPU-class hardware, but the active OpenVINO runtime does not "
                "list an NPU device. Check the Intel NPU driver, supported platform, and the "
                "installed OpenVINO release, then rescan."
            ),
            available_devices=available,
            fallback_device=fallback,
            driver_version=None,
            support_url=INTEL_NPU_SUPPORT_URL,
        )

    cpu_name = _text(snapshot.get("cpu", {}).get("name"), "").lower()
    if "core ultra" in cpu_name:
        return NpuReadinessResponse(
            state=NpuState.DRIVER_UNKNOWN,
            usable=False,
            title="NPU readiness could not be confirmed",
            explanation=(
                "This processor family can include an Intel NPU, but neither OpenVINO nor the "
                "system device query confirmed a usable NPU. Driver or platform details may be unavailable."
            ),
            available_devices=available,
            fallback_device=fallback,
            driver_version=None,
            support_url=INTEL_NPU_SUPPORT_URL,
        )

    system = str(snapshot.get("os", {}).get("system") or platform.system()).lower()
    state = NpuState.NOT_DETECTED if system == "windows" else NpuState.NOT_EXPECTED
    return NpuReadinessResponse(
        state=state,
        usable=False,
        title="No supported Intel NPU was detected",
        explanation=(
            "Continue with the available CPU or Intel GPU. Installing a driver does not add NPU "
            "hardware or guarantee compatibility."
        ),
        available_devices=available,
        fallback_device=fallback,
        driver_version=None,
        support_url=INTEL_NPU_SUPPORT_URL,
    )


def build_system_scan(snapshot: Mapping[str, Any]) -> SystemScanResponse:
    os_info = snapshot.get("os", {})
    cpu = snapshot.get("cpu", {})
    memory = snapshot.get("memory", {})
    disk = snapshot.get("disk", {})
    runtime = snapshot.get("runtime", {})
    available = [str(item) for item in snapshot.get("available_devices", [])]
    items: list[SystemItem] = []

    def add(
        key: str,
        label: str,
        value: Any,
        status: ItemStatus,
        detail: str | None = None,
    ) -> None:
        items.append(SystemItem(key=key, label=label, value=value, status=status, detail=detail))

    windows = str(os_info.get("system") or "").lower() == "windows"
    linux = str(os_info.get("system") or "").lower() == "linux"
    add(
        "windows",
        "Operating system",
        f"{_text(os_info.get('system'))} {_text(os_info.get('release'), '')}".strip(),
        ItemStatus.READY if windows else ItemStatus.WARNING,
        _text(os_info.get("version"), "Version unavailable"),
    )
    add("architecture", "Architecture", _text(cpu.get("architecture")), ItemStatus.READY)
    add("cpu", "CPU", _text(cpu.get("name")), ItemStatus.READY)
    add(
        "cores",
        "CPU cores",
        f"{int(cpu.get('physical_cores') or 0)} physical, {int(cpu.get('logical_cores') or 0)} logical",
        ItemStatus.READY if cpu.get("logical_cores") else ItemStatus.UNKNOWN,
    )
    add(
        "ram-total",
        "Installed RAM",
        f"{float(memory.get('total_gb') or 0):.1f} GB",
        ItemStatus.READY if memory.get("total_gb") else ItemStatus.UNKNOWN,
    )
    add(
        "ram-available",
        "Available RAM",
        f"{float(memory.get('available_gb') or 0):.1f} GB",
        ItemStatus.READY if memory.get("available_gb") else ItemStatus.UNKNOWN,
    )
    add(
        "disk-free",
        "Free model storage",
        f"{float(disk.get('free_gb') or 0):.1f} GB",
        ItemStatus.READY if disk.get("free_gb") else ItemStatus.UNKNOWN,
    )
    add(
        "openvino",
        "OpenVINO",
        _text(runtime.get("openvino"), "Not available"),
        ItemStatus.READY if runtime.get("openvino") else ItemStatus.UNAVAILABLE,
    )
    add(
        "openvino-genai",
        "OpenVINO GenAI",
        _text(runtime.get("openvino_genai"), "Not available"),
        ItemStatus.READY if runtime.get("openvino_genai") else ItemStatus.UNAVAILABLE,
    )
    add(
        "devices",
        "OpenVINO devices",
        ", ".join(available) if available else "None reported",
        ItemStatus.READY if available else ItemStatus.UNAVAILABLE,
    )

    for item in snapshot.get("devices", []):
        base = _text(item.get("base") or item.get("device"), "device").lower()
        full_name = _text(item.get("full_name"), _text(item.get("device")))
        detail_parts = [
            str(value)
            for value in (item.get("architecture"), item.get("driver_version"))
            if value not in (None, "")
        ]
        add(
            f"device-{base}",
            f"{base.upper()} details",
            full_name,
            ItemStatus.READY,
            " | ".join(detail_parts) or "Additional device properties unavailable",
        )

    warnings: list[str] = []
    if linux and not bool(runtime.get("mock")):
        warnings.append(
            "Linux desktop support is experimental. Validate CPU inference before GPU or NPU use."
        )
    elif not windows and not bool(runtime.get("mock")):
        warnings.append("Windows 11 is the primary supported desktop distribution target.")
    if not available and not bool(runtime.get("mock")):
        warnings.append("OpenVINO did not report a usable inference device.")
    return SystemScanResponse(
        generated_at=str(snapshot.get("generated_at") or utc_now()),
        fingerprint=_text(snapshot.get("fingerprint"), "unknown"),
        mock=bool(runtime.get("mock")),
        items=items,
        hardware=dict(snapshot),
        warnings=warnings,
    )
