from types import SimpleNamespace

import pytest

from scripts.validate_api_contract import ValidationError, Validator


def validator_with_statuses(monkeypatch, entries):
    validator = object.__new__(Validator)
    validator.args = SimpleNamespace(load_timeout=5)
    statuses = iter(entries)
    seen = []

    def json(method, _path, _body=None):
        if method == "POST":
            return {}
        entry = next(statuses)
        seen.append(entry)
        return {"models": {"available": [dict(id="demo", **entry)]}}

    validator.client = SimpleNamespace(json=json)
    monkeypatch.setattr("scripts.validate_api_contract.time.sleep", lambda _seconds: None)
    return validator, seen


def test_load_waits_for_requested_target_and_completed_switch(monkeypatch):
    validator, seen = validator_with_statuses(
        monkeypatch,
        [
            dict(is_loaded=True, device="CPU", is_loading=False),
            dict(is_loaded=True, device="NPU", is_loading=True),
            dict(is_loaded=True, device="NPU", is_loading=False),
        ],
    )
    assert validator.load("demo", "NPU") == "loaded on NPU"
    assert len(seen) == 3


def test_failed_switch_does_not_certify_preserved_old_engine(monkeypatch):
    validator, _seen = validator_with_statuses(
        monkeypatch,
        [dict(is_loaded=True, device="CPU", error="NPU compile failed")],
    )
    with pytest.raises(ValidationError, match="NPU compile failed"):
        validator.load("demo", "NPU")
