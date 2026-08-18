"""Durability guard for desktop-managed API-key removal."""

from __future__ import annotations

from typing import Any

_INSTALL_FLAG = "_OVLLM_DESKTOP_CREDENTIAL_SAFETY_INSTALLED"


def install_desktop_credential_safety() -> None:
    """Make credential removal fail closed when the encrypted file cannot be deleted.

    The original implementation suppressed ``OSError`` from ``Path.unlink`` and cleared
    the in-memory key anyway. On Windows that can report a successful removal even though
    the DPAPI file remains on disk and is restored on the next read or restart.
    """

    from app.desktop_network import DesktopApiKeyStore

    if getattr(DesktopApiKeyStore, _INSTALL_FLAG, False):
        return

    def remove_with_verified_delete(self: Any) -> bool:
        with self._lock:
            existed = bool(self._memory_key) or self.key_path.exists()
            try:
                self.key_path.unlink(missing_ok=True)
            except OSError:
                # Preserve the currently usable in-memory key when durable removal did
                # not happen. The caller surfaces the write failure instead of claiming
                # the credential was removed and silently restoring it later.
                raise
            self._memory_key = None
            return existed

    DesktopApiKeyStore.remove = remove_with_verified_delete
    setattr(DesktopApiKeyStore, _INSTALL_FLAG, True)


__all__ = ["install_desktop_credential_safety"]
