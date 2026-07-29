"""Keep late converter output from overwriting terminal preparation state."""

from __future__ import annotations

from typing import Any

_INSTALL_FLAG = "_CONVERSION_STREAM_SAFETY_INSTALLED"
_TERMINAL_STATES = {"cancelled", "error"}


async def read_conversion_stream_safely(
    manager: Any,
    model_id: str,
    cfg: Any,
    stream: Any,
) -> list[str]:
    """Drain converter output while preserving cancellation and error state.

    Converter stdout and stderr are consumed by independent tasks. During
    cancellation those readers can receive a final buffered line after the
    lifecycle task has already published a terminal state. Continue draining the
    pipes so the child can exit, but never let late output replace that state.
    """
    if stream is None:
        return []

    lines: list[str] = []
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = manager._sanitize_progress_line(raw.decode(errors="replace"))
        if not line:
            continue
        lines.append(line)

        status = getattr(manager, "status_overrides", {}).get(model_id, {}).get("status")
        phase = getattr(manager, "progress", {}).get(model_id, {}).get("phase")
        if status in _TERMINAL_STATES or phase in _TERMINAL_STATES:
            continue

        next_phase, message, percent = manager._progress_from_converter_line(line, cfg)
        manager._set_progress(
            model_id,
            next_phase,
            message,
            percent=percent,
            append_log=line,
        )
    return lines


def install_conversion_stream_safety() -> None:
    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    manager_class._read_conversion_stream = read_conversion_stream_safely
    setattr(manager_class, _INSTALL_FLAG, True)
