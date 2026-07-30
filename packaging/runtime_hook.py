"""Restore windowed streams and register the installed tray for update restarts."""

from __future__ import annotations

import os
import sys


def _restore_output(name: str, descriptor: int) -> None:
    if getattr(sys, name, None) is not None:
        return
    try:
        duplicate = os.dup(descriptor)
        stream = os.fdopen(
            duplicate,
            "w",
            buffering=1,
            encoding="utf-8",
            errors="backslashreplace",
        )
    except OSError:
        return
    setattr(sys, name, stream)


def _restore_input() -> None:
    if sys.stdin is not None:
        return
    try:
        sys.stdin = open(os.devnull, encoding="utf-8")
    except OSError:
        pass


def _register_for_update_restart() -> None:
    """Allow Restart Manager to relaunch only the installed tray after an update."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    arguments = set(sys.argv[1:])
    helper_modes = {"--server-child", "--convert-model", "--diagnostic", "--headless"}
    if arguments & helper_modes:
        return
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.exists(os.path.join(executable_dir, "portable.flag")):
        return
    try:
        import ctypes

        register = ctypes.windll.kernel32.RegisterApplicationRestart
        register.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        register.restype = ctypes.c_long
        # Do not create crash or hang restart loops. Patch/install and reboot restarts remain enabled.
        register("--no-browser", 0x1 | 0x2)
    except (AttributeError, OSError):
        # Restart registration is an upgrade convenience and must never block local inference.
        pass


_restore_output("stdout", 1)
_restore_output("stderr", 2)
_restore_input()
_register_for_update_restart()
