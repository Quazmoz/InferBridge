"""Regression checks for hardware qualification targets and switch failures."""

from types import SimpleNamespace

import pytest

from scripts.validate_api_contract import ValidationError, Validator


def _validator(monkeypatch, statuses):
    validator = object.__new__(Validator)
    validator.args = SimpleNamespace(load_timeout=5)
    iterator = iter(statuses)
    observed = []

    def json(method, _path, _body=None):
        if method == "POST":
            return {}
        entry = next(iterator)
        observed.append(entry)
        return {"models": {"available": [dict(id="demo", **entry)]}}

    validator.client = SimpleNamespace(json=json)
    monkeypatch.setattr("scripts.validate_api_contract.time.sleep", lambda _seconds: None)
    return validator, observed


def test_validator_waits_for_requested_device_and_completed_switch(monkeypatch):
    validator, observed = _validator(
        monkeypatch,
        [
            {"is_loaded": True, "device": "CPU", "is_loading": False},
            {"is_loaded": True, "device": "NPU", "is_loading": True},
            {"is_loaded": True, "device": "NPU", "is_loading": False},
        ],
    )
    assert validator.load("demo", "NPU") == "loaded on NPU"
    assert len(observed) == 3


def test_validator_does_not_certify_old_engine_after_failed_switch(monkeypatch):
    validator, _ = _validator(
        monkeypatch,
        [{"is_loaded": True, "device": "CPU", "error": "NPU compile failed"}],
    )
    with pytest.raises(ValidationError, match="NPU compile failed"):
        validator.load("demo", "NPU")


def test_validator_accepts_ready_loaded_model_without_explicit_target(monkeypatch):
    validator, _ = _validator(
        monkeypatch,
        [{"is_loaded": True, "device": "CPU", "is_loading": False}],
    )
    assert validator.load("demo", None) == "loaded on CPU"
