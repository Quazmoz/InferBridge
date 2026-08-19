from __future__ import annotations

from types import SimpleNamespace

from app.model_recovery_status import _normalize_recovery_record


class _Manager:
    def _sanitize_progress_line(self, value, *, limit=240):
        text = str(value or "").replace("hf_secret-token", "[redacted]")
        return text[:limit]


def test_nonfinite_and_wrong_shape_recovery_fields_are_normalized():
    manager = _Manager()
    record = {
        "schema_version": 999,
        "recovery_id": 123,
        "model_id": "wrong-model",
        "operation_id": ["bad"],
        "operation_type": "other",
        "terminal_state": "ready",
        "interrupted_at": float("inf"),
        "failed_stage": "unknown",
        "last_completed_stage": {"bad": True},
        "message": "failure hf_secret-token",
        "log_tail": 42,
    }

    normalized = _normalize_recovery_record(manager, "expected-model", record)

    assert normalized is not None
    assert normalized["schema_version"] == 1
    assert normalized["model_id"] == "expected-model"
    assert normalized["recovery_id"] == "recovery-expected-model"
    assert normalized["operation_id"] is None
    assert normalized["operation_type"] is None
    assert normalized["terminal_state"] == "error"
    assert normalized["interrupted_at"] == 0
    assert normalized["failed_stage"] == "conversion"
    assert normalized["last_completed_stage"] == "none"
    assert normalized["log_tail"] == []
    assert "hf_secret-token" not in normalized["message"]


def test_unhashable_values_in_every_enumerated_field_are_rejected():
    """Each enumerated field must reject a container, not raise on hashing it.

    These four fields are validated by set membership, which hashes the candidate. A
    recovery file holding a JSON object or array in any of them used to raise
    TypeError out of the guard whose whole purpose is to survive a corrupt file, which
    surfaced as a 500 from the model-status poll.
    """

    manager = _Manager()
    record = {
        "recovery_id": "recover-1",
        "operation_type": {"nested": "object"},
        "terminal_state": ["array"],
        "interrupted_at": 5,
        "failed_stage": {},
        "last_completed_stage": [],
        "message": "interrupted",
        "log_tail": [],
    }

    normalized = _normalize_recovery_record(manager, "model", record)

    assert normalized is not None
    assert normalized["operation_type"] is None
    assert normalized["terminal_state"] == "error"
    assert normalized["failed_stage"] == "conversion"
    assert normalized["last_completed_stage"] == "none"


def test_recovery_log_tail_is_bounded_and_sanitized():
    manager = _Manager()
    record = {
        "recovery_id": "recover-1",
        "terminal_state": "cancelled",
        "interrupted_at": 123,
        "failed_stage": "download",
        "last_completed_stage": "none",
        "message": "cancelled",
        "log_tail": [*(f"line-{index}" for index in range(12)), "hf_secret-token", 99],
    }

    normalized = _normalize_recovery_record(manager, "model", record)

    assert normalized is not None
    assert len(normalized["log_tail"]) == 9
    assert normalized["log_tail"][0] == "line-4"
    assert normalized["log_tail"][-1] == "[redacted]"


def test_non_object_recovery_record_is_rejected():
    assert _normalize_recovery_record(SimpleNamespace(), "model", ["bad"]) is None
