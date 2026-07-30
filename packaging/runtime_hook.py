"""Restore windowed streams, validate native dependencies, and register update restarts."""

from __future__ import annotations

import contextlib
import os
import re
import sys
from datetime import UTC, datetime

_APP_TITLE = "OpenVINO Windows LLM"
_RUNTIME_FAILURE_EXIT_CODE = 12
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)+[^\s]*")
_POSIX_HOME_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s]+(?:/[^\s]+)*")


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


def _portable_install() -> bool:
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.exists(os.path.join(executable_dir, "portable.flag"))


def _runtime_failure_log_path() -> str | None:
    if _portable_install():
        root = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
    else:
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if not local_app_data:
            return None
        root = os.path.join(local_app_data, "OpenVINOWindowsLLM")
    return os.path.join(root, "logs", "startup-runtime-error.log")


def _safe_error_detail(error: BaseException) -> str:
    detail = str(error or error.__class__.__name__).replace("\r", " ").replace("\n", " ")
    detail = " ".join(detail.split())
    detail = _WINDOWS_PATH_RE.sub(lambda _match: "...\\", detail)
    detail = _POSIX_HOME_RE.sub(".../", detail)
    return detail[:180]


def _record_runtime_failure(detail: str) -> None:
    path = _runtime_failure_log_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            timestamp = datetime.now(UTC).isoformat(timespec="seconds")
            stream.write(f"{timestamp} packaged runtime validation failed: {detail}\n")
    except OSError:
        pass


def _show_runtime_failure(detail: str) -> None:
    message = (
        "The installed application contains incompatible runtime files, usually because "
        "files from two versions were mixed during an older upgrade.\n\n"
        "Close OpenVINO Windows LLM and run the latest installer over the existing installation. "
        "The installer will replace application files while preserving downloaded models, settings, "
        "and logs.\n\n"
        f"Technical detail: {detail}"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, _APP_TITLE, 0x10)
    except (AttributeError, OSError):
        with contextlib.suppress(OSError):
            if sys.stderr is not None:
                sys.stderr.write(message + "\n")
                sys.stderr.flush()


def _validate_windows_native_runtime() -> None:
    """Fail cleanly when packaged Python and psutil native files do not match."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import psutil

        # Importing psutil loads its version-coupled Windows extension. Accessing the current
        # process proves the extension initialized instead of merely being present on disk.
        psutil.Process(os.getpid()).create_time()
    except Exception as error:  # noqa: BLE001 - this is the frozen native dependency boundary
        detail = _safe_error_detail(error)
        _record_runtime_failure(detail)
        _show_runtime_failure(detail)
        os._exit(_RUNTIME_FAILURE_EXIT_CODE)


def _register_for_update_restart() -> None:
    """Allow Restart Manager to relaunch only the installed tray after an update."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    arguments = set(sys.argv[1:])
    helper_modes = {"--server-child", "--convert-model", "--diagnostic", "--headless"}
    if arguments & helper_modes or _portable_install():
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
_validate_windows_native_runtime()
_register_for_update_restart()
