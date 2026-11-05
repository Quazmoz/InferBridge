"""Linux credential persistence through the desktop keyring (freedesktop Secret Service).

Uses libsecret's ``secret-tool`` (GNOME Keyring, KWallet 5.97+ and KeePassXC all serve the
Secret Service API). Secrets travel on stdin only, never on a command line, in the
environment or on disk. When the tool, a session bus or an unlocked keyring is missing,
callers keep the credential in memory for the session: the safe fallback.

Ubuntu/Debian: ``sudo apt install libsecret-tools``. Fedora: ``sudo dnf install libsecret``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

_APPLICATION = "InferBridge"
_TIMEOUT_SECONDS = 10.0
DISABLE_VARIABLE = "OV_LLM_DISABLE_SECRET_SERVICE"


def _session_bus_available(environment: dict[str, str]) -> bool:
    if environment.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime_dir = environment.get("XDG_RUNTIME_DIR")
    return bool(runtime_dir) and Path(runtime_dir, "bus").exists()


class SecretServiceStore:
    """One named secret, scoped to an InferBridge data directory."""

    def __init__(self, item: str, scope: str | Path, *, runner=subprocess.run) -> None:
        self.item = item
        # Several data directories (portable, --data-dir) must not share one credential.
        self.scope = hashlib.sha256(str(Path(scope).expanduser()).encode("utf-8")).hexdigest()[:16]
        self._runner = runner
        # One keyring round trip per process: status polling must not spawn secret-tool
        # (or a keyring unlock prompt) again and again. store/clear keep this current.
        self._looked_up = False
        self._cached: str | None = None

    def _attributes(self) -> list[str]:
        return ["application", _APPLICATION, "item", self.item, "scope", self.scope]

    def _environment(self) -> dict[str, str]:
        from app.desktop_shell import host_environment

        return host_environment(dict(os.environ)) or dict(os.environ)

    def available(self) -> bool:
        if not sys.platform.startswith("linux") or os.environ.get(DISABLE_VARIABLE):
            return False
        return bool(shutil.which("secret-tool")) and _session_bus_available(dict(os.environ))

    def _run(self, *arguments: str, secret: str | None = None) -> subprocess.CompletedProcess:
        return self._runner(
            ["secret-tool", *arguments],
            input=secret,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
            env=self._environment(),
        )

    def store(self, label: str, secret: str) -> bool:
        """Persist ``secret``; False (never an exception) when the keyring refuses."""
        if not self.available():
            return False
        try:
            result = self._run("store", f"--label={label}", *self._attributes(), secret=secret)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0:
            self._looked_up, self._cached = True, secret
        return result.returncode == 0

    def lookup(self) -> str | None:
        if self._looked_up:
            return self._cached
        if not self.available():
            return None
        try:
            result = self._run("lookup", *self._attributes())
        except (OSError, subprocess.TimeoutExpired):
            return None  # A timed-out unlock prompt may be retried later.
        value = result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
        self._looked_up, self._cached = True, value or None
        return self._cached

    def clear(self) -> bool:
        """Delete the secret; True only when one was stored (``secret-tool clear`` exits 0
        whether or not anything matched, so a removal is reported from the lookup)."""
        if not self.available():
            return False
        existed = self.lookup() is not None
        try:
            cleared = self._run("clear", *self._attributes()).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
        self._looked_up, self._cached = True, None
        return cleared and existed
