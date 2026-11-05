"""Small native desktop shell helpers with cross-platform fallbacks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Set by the Linux bundle launcher so the private runtime is never inherited by desktop
# helpers (xdg-open, the browser, zenity, systemctl, secret-tool) or a system python3.
_BUNDLE_ONLY_VARIABLES = (
    "PYTHONPATH",
    "PYTHONSAFEPATH",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHOME",
)
# Credentials the engine reads must not leak into a browser or helper process.
_PRIVATE_VARIABLES = (
    "OV_LLM_API_KEY",
    "OV_LLM_DESKTOP_CONTROL_TOKEN",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
)


def host_environment(base: dict[str, str] | None = None) -> dict[str, str] | None:
    """Environment for host programs started on Linux; ``None`` (inherit) elsewhere."""
    if base is None and not sys.platform.startswith("linux"):
        return None
    environment = dict(os.environ if base is None else base)
    for name in _PRIVATE_VARIABLES:
        environment.pop(name, None)
    if "INFERBRIDGE_HOST_PATH" in environment:
        environment["PATH"] = environment.pop("INFERBRIDGE_HOST_PATH")
        library_path = environment.pop("INFERBRIDGE_HOST_LD_LIBRARY_PATH", "")
        if library_path:
            environment["LD_LIBRARY_PATH"] = library_path
        else:
            environment.pop("LD_LIBRARY_PATH", None)
        for name in _BUNDLE_ONLY_VARIABLES:
            environment.pop(name, None)
    return environment


def _graphical_session_available() -> bool:
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _linux_dialog_command(
    title: str,
    message: str,
    *,
    error: bool = False,
    confirm: bool = False,
) -> list[str] | None:
    if not _graphical_session_available():
        return None

    if shutil.which("zenity"):
        mode = "--question" if confirm else ("--error" if error else "--info")
        return ["zenity", mode, f"--title={title}", f"--text={message}"]
    if shutil.which("kdialog"):
        mode = "--yesno" if confirm else ("--error" if error else "--msgbox")
        return ["kdialog", mode, message, "--title", title]
    return None


def show_dialog(title: str, message: str, *, error: bool = False) -> None:
    if os.name == "nt":
        try:
            import ctypes

            flags = 0x10 if error else 0x40
            ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
            return
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        command = _linux_dialog_command(str(title), str(message), error=error)
        if command is not None:
            try:
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    env=host_environment(),
                )
                if result.returncode == 0:
                    return
            except OSError:
                pass
    print(f"{title}: {message}", file=sys.stderr if error else sys.stdout)


def confirm_dialog(title: str, message: str) -> bool:
    if os.name == "nt":
        try:
            import ctypes

            # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2
            result = ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x24 | 0x100)
            return result == 6
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        command = _linux_dialog_command(str(title), str(message), confirm=True)
        if command is not None:
            try:
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    env=host_environment(),
                )
                return result.returncode == 0
            except OSError:
                pass
    return False


def open_browser(url: str) -> bool:
    if sys.platform.startswith("linux"):
        # xdg-open honors the desktop's default browser and gets the host environment.
        for candidate in ("xdg-open", "gio"):
            if shutil.which(candidate):
                command = (
                    [candidate, str(url)] if candidate == "xdg-open" else ["gio", "open", str(url)]
                )
                if _spawn_detached(command):
                    return True
    return bool(webbrowser.open(str(url), new=1, autoraise=True))


def _spawn_detached(command: list[str]) -> bool:
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            start_new_session=os.name != "nt",
            env=host_environment(),
        )
        return True
    except OSError:
        return False


def open_path(path: Path) -> bool:
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _spawn_detached(["open", str(target)])

    for candidate in (("xdg-open", str(target)), ("gio", "open", str(target))):
        if shutil.which(candidate[0]) and _spawn_detached(list(candidate)):
            return True
    return False


def clipboard_available() -> bool:
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return shutil.which("pbcopy") is not None
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return True
    return any(shutil.which(command) for command in ("xclip", "xsel", "wl-copy"))


def _copy_with_command(command: list[str], text: str) -> bool:
    try:
        result = subprocess.run(
            command,
            input=str(text).encode("utf-8"),
            stdin=None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            env=host_environment(),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _copy_with_clip_exe(text: str) -> bool:
    """Fallback for the Win32 clipboard path: pipe UTF-16 text to clip.exe.

    clip.exe reads UTF-16LE correctly when the input starts with a BOM. Used only
    when the direct Win32 calls fail (busy clipboard, GlobalLock returning null).
    """
    clip = shutil.which("clip.exe") or shutil.which("clip")
    if not clip:
        return False
    try:
        result = subprocess.run(
            [clip],
            input=b"\xff\xfe" + str(text).encode("utf-16-le"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def copy_to_clipboard(text: str) -> None:
    if os.name == "nt":
        try:
            _copy_with_win32(text)
        except RuntimeError:
            # InferBridge 1.0.0 failed here with "could not lock clipboard memory"
            # (32-bit ctypes return values, fixed in cd1b15d). Keep a second path.
            if _copy_with_clip_exe(text):
                return
            raise
        return

    _copy_posix(text)


def _copy_with_win32(text: str) -> None:
    """Copy through the Win32 clipboard API with pointer-sized ctypes declarations."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    payload = (str(text) + "\0").encode("utf-16-le")

    # ctypes defaults to a 32-bit int result, which truncates HGLOBAL and
    # memory addresses in an x64 process. Declare the Win32 ABI explicitly.
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    # Allocate and populate before touching the current clipboard contents.
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise RuntimeError("Windows could not allocate clipboard memory.")
    opened = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("Windows could not lock clipboard memory.")
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)

        # Transient contention is normal; retry briefly and fail with an
        # explicit alternative rather than blocking the tray indefinitely.
        for attempt in range(3):
            if user32.OpenClipboard(None):
                opened = True
                break
            if attempt < 2:
                time.sleep(0.05)
        if not opened:
            raise RuntimeError("The Windows clipboard is busy. Try again shortly.")
        if not user32.EmptyClipboard():
            raise RuntimeError("Windows could not clear the clipboard.")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("Windows could not write to the clipboard.")
        handle = None
    finally:
        if opened:
            user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def _copy_posix(text: str) -> None:
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        if _copy_with_command(["pbcopy"], str(text)):
            return

    if sys.platform.startswith("linux"):
        commands: list[list[str]] = []
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            commands.append(["wl-copy", "--type", "text/plain;charset=utf-8"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-in"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--input"])
        if shutil.which("wl-copy") and not commands:
            commands.append(["wl-copy", "--type", "text/plain;charset=utf-8"])
        for command in commands:
            if _copy_with_command(command, str(text)):
                return
        raise RuntimeError(
            "Clipboard integration is unavailable. Install wl-clipboard on Wayland or "
            "xclip/xsel on X11, then try again."
        )

    raise RuntimeError("Clipboard integration is unavailable on this desktop platform.")
