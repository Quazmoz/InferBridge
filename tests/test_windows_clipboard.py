"""Exercise pointer-sized Windows clipboard calls without requiring a Windows runner."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from app import desktop_shell


class _NativeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


def _fake_clipboard(monkeypatch, *, opens=(1,), lock=True, empty=True, set_data=True):
    storage = ctypes.create_string_buffer(512)
    handle = 0x100000001  # An x64 HGLOBAL must not be truncated to a 32-bit int.
    state = {"opens": 0, "closes": 0, "frees": 0, "empties": 0, "sets": [], "sleeps": []}

    def open_clipboard(_window):
        index = state["opens"]
        state["opens"] += 1
        return opens[min(index, len(opens) - 1)]

    def allocate(_flags, size):
        assert size <= len(storage)
        return handle

    def lock_memory(value):
        assert value == handle
        return ctypes.addressof(storage) if lock else 0

    def empty_clipboard():
        state["empties"] += 1
        return int(empty)

    def set_clipboard(_format, value):
        state["sets"].append(value)
        return value if set_data else 0

    user32 = SimpleNamespace(
        OpenClipboard=_NativeFunction(open_clipboard),
        EmptyClipboard=_NativeFunction(empty_clipboard),
        SetClipboardData=_NativeFunction(set_clipboard),
        CloseClipboard=_NativeFunction(
            lambda: state.__setitem__("closes", state["closes"] + 1) or 1
        ),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=_NativeFunction(allocate),
        GlobalLock=_NativeFunction(lock_memory),
        GlobalUnlock=_NativeFunction(lambda _handle: 1),
        GlobalFree=_NativeFunction(
            lambda value: state.__setitem__("frees", state["frees"] + 1) or 0
        ),
    )
    monkeypatch.setattr(desktop_shell, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        desktop_shell, "time", SimpleNamespace(sleep=lambda delay: state["sleeps"].append(delay))
    )
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32), raising=False
    )
    return state, storage, handle, user32, kernel32


def test_windows_clipboard_uses_pointer_sized_handles_and_preserves_unicode(monkeypatch):
    state, storage, handle, user32, kernel32 = _fake_clipboard(monkeypatch)

    desktop_shell.copy_to_clipboard("Intel NPU ✓")

    payload = "Intel NPU ✓\0".encode("utf-16-le")
    assert storage.raw[: len(payload)] == payload
    assert state["sets"] == [handle]
    assert state["empties"] == 1
    assert state["closes"] == 1
    assert state["frees"] == 0  # Ownership transfers to Windows on success.
    assert kernel32.GlobalAlloc.restype == ctypes.c_void_p
    assert kernel32.GlobalLock.restype == ctypes.c_void_p
    assert user32.SetClipboardData.restype == ctypes.c_void_p


def test_windows_clipboard_retries_transient_contention(monkeypatch):
    state, *_rest = _fake_clipboard(monkeypatch, opens=(0, 1))

    desktop_shell.copy_to_clipboard("safe diagnostics")

    assert state["opens"] == 2
    assert state["sleeps"] == [0.05]
    assert state["sets"]


def test_windows_clipboard_contention_fails_boundedly_and_frees_memory(monkeypatch):
    state, *_rest = _fake_clipboard(monkeypatch, opens=(0,))

    with pytest.raises(RuntimeError, match="clipboard is busy"):
        desktop_shell.copy_to_clipboard("safe diagnostics")

    assert state["opens"] == 3
    assert state["sleeps"] == [0.05, 0.05]
    assert state["empties"] == 0
    assert state["closes"] == 0
    assert state["frees"] == 1


@pytest.mark.parametrize("failure", ["lock", "empty", "set"])
def test_windows_clipboard_errors_release_allocated_memory(monkeypatch, failure):
    state, *_rest = _fake_clipboard(
        monkeypatch,
        lock=failure != "lock",
        empty=failure != "empty",
        set_data=failure != "set",
    )

    with pytest.raises(RuntimeError, match="Windows could not"):
        desktop_shell.copy_to_clipboard("safe diagnostics")

    assert state["frees"] == 1
    assert state["closes"] == (0 if failure == "lock" else 1)
    assert state["empties"] == (0 if failure == "lock" else 1)
