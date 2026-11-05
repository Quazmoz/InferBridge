"""Decline OpenVINO and NNCF usage telemetry unless the user opted in explicitly.

``openvino-telemetry`` (used by OVC and NNCF, both pulled in by model conversion and
optimum-intel) treats a missing consent file as consent: on first import it writes a
Google Analytics client ID and counters to ``~/intel`` (Linux) or
``%LOCALAPPDATA%\\Intel Corporation`` (Windows) and sends usage events. InferBridge is a
local-first app that promises no telemetry, so a missing consent file is read as
*declined* in every InferBridge process. An explicit choice made with OpenVINO's own
``opt_in_out --opt_in`` (consent file ``1``) is respected, and
``OV_LLM_ALLOW_OPENVINO_TELEMETRY=1`` restores the library default.

Nothing is written to disk: the consent file is never created or changed.
"""

from __future__ import annotations

import os

ALLOW_VARIABLE = "OV_LLM_ALLOW_OPENVINO_TELEMETRY"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_MARKER = "_inferbridge_declines_missing_consent"


def decline_openvino_telemetry() -> bool:
    """Patch the consent check in this process; True when the patch is active."""
    if str(os.environ.get(ALLOW_VARIABLE) or "").strip().lower() in _TRUTHY:
        return False
    try:
        from openvino_telemetry.utils.opt_in_checker import ConsentCheckResult, OptInChecker
    except Exception:  # noqa: BLE001 - absent (mock installs) or an incompatible version
        return False
    if getattr(OptInChecker.check, _MARKER, False):
        return True
    original = OptInChecker.check

    def check(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        return ConsentCheckResult.DECLINED if result == ConsentCheckResult.NO_FILE else result

    setattr(check, _MARKER, True)
    OptInChecker.check = check
    return True
