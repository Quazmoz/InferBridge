from __future__ import annotations

import sys
import types
from enum import Enum

import pytest

from app import openvino_privacy


class _Result(Enum):
    DECLINED = 0
    ACCEPTED = 1
    NO_FILE = 2


@pytest.fixture
def fake_telemetry(monkeypatch):
    state = {"result": _Result.NO_FILE}

    class OptInChecker:
        def check(self, enable_opt_in_dialog, disable_in_ci=False):
            return state["result"]

    module = types.ModuleType("openvino_telemetry.utils.opt_in_checker")
    module.OptInChecker = OptInChecker
    module.ConsentCheckResult = _Result
    for name in ("openvino_telemetry", "openvino_telemetry.utils"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "openvino_telemetry.utils.opt_in_checker", module)
    monkeypatch.delenv(openvino_privacy.ALLOW_VARIABLE, raising=False)
    return OptInChecker, state


def test_missing_consent_file_is_declined_and_explicit_choices_are_kept(fake_telemetry):
    checker, state = fake_telemetry
    assert openvino_privacy.decline_openvino_telemetry() is True
    assert openvino_privacy.decline_openvino_telemetry() is True  # Idempotent.
    assert checker().check(False, False) == _Result.DECLINED
    state["result"] = _Result.ACCEPTED  # The user ran opt_in_out --opt_in.
    assert checker().check(False, False) == _Result.ACCEPTED


def test_opt_in_variable_restores_the_library_default(fake_telemetry, monkeypatch):
    checker, _state = fake_telemetry
    monkeypatch.setenv(openvino_privacy.ALLOW_VARIABLE, "1")
    assert openvino_privacy.decline_openvino_telemetry() is False
    assert checker().check(False, False) == _Result.NO_FILE
