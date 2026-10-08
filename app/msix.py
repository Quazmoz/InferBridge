"""Detect whether the running process has MSIX package identity (Microsoft Store build)."""

from __future__ import annotations

import sys
from functools import cache

_ERROR_INSUFFICIENT_BUFFER = 122


@cache
def is_packaged() -> bool:
    """True only when Windows reports package identity for this process.

    The Store build updates through the Store and registers Start with Windows through
    its manifest StartupTask, so callers use this to keep the installer-only paths off.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    except (AttributeError, OSError):
        return False
    # Unpackaged processes get APPMODEL_ERROR_NO_PACKAGE; packaged ones report the
    # buffer size they need.
    return result == _ERROR_INSUFFICIENT_BUFFER
