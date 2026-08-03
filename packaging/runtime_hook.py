"""Restore windowed streams, validate packaged dependencies, and register restarts."""

from __future__ import annotations

import contextlib
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

_APP_TITLE = "InferBridge"
_CURRENT_DATA_DIR_NAME = "InferBridge"
_LEGACY_DATA_DIR_NAME = "OpenVINOWindowsLLM"
_RUNTIME_FAILURE_EXIT_CODE = 12
_PATH_REDACTION = "[redacted path]"
_QUOTED_WINDOWS_PATH_RE = re.compile(r"(?i)(?P<quote>[\"'])(?:[A-Z]:\\|\\\\)[^\"'\r\n]+(?P=quote)")
_WINDOWS_PATH_RE = re.compile(
    r'(?i)(?<![A-Za-z0-9_])(?:[A-Z]:\\|\\\\[^\\/:*?"<>|\r\n]+\\)'
    r'(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*'
)
_POSIX_HOME_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^/\s]+(?:/[^\s]+)*")
_DLL_DIRECTORY_HANDLES: list[object] = []
_OPENVINO_NATIVE_NAMES = {
    "openvino.dll",
    "openvino_c.dll",
    "openvino_genai.dll",
    "openvino_tokenizers.dll",
}


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
        current = os.path.join(local_app_data, _CURRENT_DATA_DIR_NAME)
        legacy = os.path.join(local_app_data, _LEGACY_DATA_DIR_NAME)
        root = current if os.path.isdir(current) or not os.path.isdir(legacy) else legacy
    return os.path.join(root, "logs", "startup-runtime-error.log")


def _safe_error_detail(error: BaseException) -> str:
    detail = str(error or error.__class__.__name__).replace("\r", " ").replace("\n", " ")
    detail = " ".join(detail.split())
    detail = _QUOTED_WINDOWS_PATH_RE.sub(
        lambda match: f"{match.group('quote')}{_PATH_REDACTION}{match.group('quote')}",
        detail,
    )
    detail = _WINDOWS_PATH_RE.sub(_PATH_REDACTION, detail)
    detail = _POSIX_HOME_RE.sub(_PATH_REDACTION, detail)
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


def _write_runtime_failure(message: str) -> None:
    with contextlib.suppress(OSError):
        if sys.stderr is not None:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()


def _show_runtime_failure(detail: str) -> None:
    message = (
        "The installed application contains incompatible runtime files, usually because "
        "files from two versions were mixed during an older upgrade.\n\n"
        "Close InferBridge and run the latest installer over the existing installation. "
        "The installer will replace application files while preserving downloaded models, settings, "
        "and logs.\n\n"
        f"Technical detail: {detail}"
    )
    # Release validation launches the hidden native-smoke helper non-interactively.
    # Never display a modal dialog in that mode because it would hang the build.
    if "--native-smoke" in sys.argv[1:]:
        _write_runtime_failure(message)
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, _APP_TITLE, 0x10)
    except (AttributeError, OSError):
        _write_runtime_failure(message)


def _fail_runtime_validation(error: BaseException) -> None:
    """Record one sanitized packaged-runtime failure and terminate immediately."""

    detail = _safe_error_detail(error)
    _record_runtime_failure(detail)
    _show_runtime_failure(detail)
    os._exit(_RUNTIME_FAILURE_EXIT_CODE)


def _native_directory_candidates(bundle_root: Path) -> list[Path]:
    """Return deterministic and discovered directories containing OpenVINO DLLs."""

    candidates = [
        bundle_root,
        bundle_root / "openvino" / "libs",
        bundle_root / "openvino_genai",
        bundle_root / "openvino_genai" / "libs",
        bundle_root / "openvino_tokenizers",
        bundle_root / "openvino_tokenizers" / "libs",
    ]
    # PyInstaller hook behavior can change the destination directory across package
    # versions. Discover the actual packaged DLL parents so a future layout change
    # cannot silently bypass the Windows DLL search path registration.
    with contextlib.suppress(OSError):
        for dll in bundle_root.rglob("*.dll"):
            if dll.name.lower() in _OPENVINO_NATIVE_NAMES:
                candidates.append(dll.parent)
    return candidates


def _configure_windows_native_search_path() -> None:
    """Expose packaged OpenVINO DLL directories before native imports occur.

    OpenVINO GenAI asks the runtime to load ``openvino_tokenizers.dll`` by its DLL
    name. In a PyInstaller one-directory build, the extension and its companion
    libraries can live in package subdirectories under ``sys._MEIPASS`` rather than
    beside the executable. Register those directories explicitly so Windows can
    resolve both the extension and its transitive dependencies.
    """

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    directories: list[Path] = []
    seen: set[str] = set()
    for candidate in _native_directory_candidates(bundle_root):
        if not candidate.is_dir():
            continue
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        directories.append(candidate)

    if not directories:
        return

    existing_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join(
        [*(str(directory) for directory in directories), existing_path]
    ).rstrip(os.pathsep)

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    for directory in directories:
        try:
            handle = add_dll_directory(str(directory))
        except OSError:
            continue
        # The directory is removed when the handle is closed, so retain each handle
        # for the lifetime of the frozen process.
        _DLL_DIRECTORY_HANDLES.append(handle)


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
        _fail_runtime_validation(error)


def _optimum_command_name(command: object) -> str:
    command_metadata = getattr(command, "COMMAND", None)
    return (
        str(getattr(command_metadata, "name", "") or getattr(command, "name", "") or "")
        .strip()
        .lower()
    )


def _validate_packaged_optimum_cli() -> None:
    """Prove Optimum can discover the packaged OpenVINO export registration.

    Optimum 2.x walks the physical ``optimum.commands.register`` namespace at runtime.
    Merely bundling ``register_openvino.py`` is not sufficient if PyInstaller leaves it
    inaccessible to that filesystem scan. The release native-smoke process exercises
    the same discovery function used by ``optimum-cli export openvino`` without starting
    a model download.
    """

    if not getattr(sys, "frozen", False) or "--native-smoke" not in sys.argv[1:]:
        return
    try:
        from optimum.commands.optimum_cli import load_optimum_namespace_cli_commands

        registrations = load_optimum_namespace_cli_commands()
        names = {_optimum_command_name(command) for command, _parent in registrations}
        if "openvino" not in names:
            raise RuntimeError(
                "The packaged Optimum CLI cannot discover the OpenVINO export command."
            )
    except Exception as error:  # noqa: BLE001 - frozen plugin discovery boundary
        _fail_runtime_validation(error)


def _register_for_update_restart() -> None:
    """Allow Restart Manager to relaunch only the installed tray after an update."""

    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    arguments = set(sys.argv[1:])
    helper_modes = {
        "--server-child",
        "--convert-model",
        "--diagnostic",
        "--headless",
        "--native-smoke",
    }
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
_configure_windows_native_search_path()
_validate_windows_native_runtime()
_validate_packaged_optimum_cli()
_register_for_update_restart()
