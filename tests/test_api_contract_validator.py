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


class _TestClientAdapter:
    """Run the dependency-free validator against an in-process mock server."""

    def __init__(self, client):
        self._client = client

    def request(self, method, path, payload=None, *, auth=True):
        response = self._client.request(method, path, json=payload)
        return response.status_code, dict(response.headers), response.content

    def json(self, method, path, payload=None, *, auth=True, expected=(200,)):
        status, _, body = self.request(method, path, payload)
        if status not in expected:
            raise ValidationError(f"{method} {path} returned HTTP {status}: {body[:400]!r}")
        import json as _json

        return _json.loads(body.decode())


def test_mock_contract_exercises_resident_slots(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from app.config import BASE_DIR, Settings
    from app.server import create_app
    from scripts.validate_api_contract import parser

    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight",
        AsyncMock(return_value={"code": "hf_access_granted"}),
    )
    settings = Settings(
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
        device="CPU",
    )
    args = parser().parse_args(
        [
            "--profile",
            "core",
            "--device",
            "CPU",
            "--expect-mock",
            "--include-embeddings",
            "--exercise-slots",
            "--exercise-lifecycle",
            "--load-timeout",
            "20",
        ]
    )
    with TestClient(create_app(settings)) as client:
        validator = Validator(args)
        validator.client = _TestClientAdapter(client)
        report = validator.run()
    checks = {check["name"]: check for check in report["checks"]}
    assert report["summary"]["fail"] == 0, report["checks"]
    assert checks["Resident model slots"]["status"] == "pass"
    assert "embedding=bge-small-en-v1.5" in checks["Resident model slots"]["detail"]
