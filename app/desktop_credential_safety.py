"""Durability and transaction guards for desktop-managed network credentials."""

from __future__ import annotations

import threading
from typing import Any

_STORE_INSTALL_FLAG = "_OVLLM_DESKTOP_CREDENTIAL_SAFETY_INSTALLED"
_SERVICE_INSTALL_FLAG = "_OVLLM_DESKTOP_NETWORK_UPDATE_SAFETY_INSTALLED"
_UPDATE_LOCK_ATTR = "_ovllm_desktop_network_update_lock"


def install_desktop_credential_safety() -> None:
    """Fail closed on key deletion errors and serialize network-setting transactions.

    Credential persistence and onboarding/network state use separate atomic stores. A
    desktop network update spans both, so concurrent update requests must not interleave
    their state and credential writes. Key removal must also surface an ``OSError`` when
    Windows refuses to delete the DPAPI file instead of claiming success and restoring the
    supposedly removed key on the next read or restart.
    """

    from app.desktop_network import DesktopApiKeyStore, DesktopNetworkService

    if not getattr(DesktopApiKeyStore, _STORE_INSTALL_FLAG, False):

        def remove_with_verified_delete(self: Any) -> bool:
            with self._lock:
                existed = bool(self._memory_key) or self.key_path.exists()
                # Do not clear the live key until durable deletion succeeds. In
                # particular, antivirus/indexer locks on Windows must be reported to the
                # caller rather than silently leaving a credential behind on disk.
                self.key_path.unlink(missing_ok=True)
                self._memory_key = None
                return existed

        DesktopApiKeyStore.remove = remove_with_verified_delete
        setattr(DesktopApiKeyStore, _STORE_INSTALL_FLAG, True)

    if getattr(DesktopNetworkService, _SERVICE_INSTALL_FLAG, False):
        return

    original_init = DesktopNetworkService.__init__
    original_update = DesktopNetworkService.update

    def init_with_update_lock(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        setattr(self, _UPDATE_LOCK_ATTR, threading.RLock())

    def update_serialized(self, request):
        lock = getattr(self, _UPDATE_LOCK_ATTR, None)
        if lock is None:
            # Support service instances created before the installer ran in tests or
            # embedded consumers without weakening the serialization guarantee.
            lock = threading.RLock()
            setattr(self, _UPDATE_LOCK_ATTR, lock)
        with lock:
            return original_update(self, request)

    DesktopNetworkService.__init__ = init_with_update_lock
    DesktopNetworkService.update = update_serialized
    setattr(DesktopNetworkService, _SERVICE_INSTALL_FLAG, True)


__all__ = ["install_desktop_credential_safety"]
