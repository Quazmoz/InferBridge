from __future__ import annotations

from types import SimpleNamespace

from app.storage_state import StorageRuntimeState


class _Manager:
    def __init__(self) -> None:
        self.catalog = {"model": SimpleNamespace()}

    def record_request(self, *_args, **_kwargs) -> None:
        return None

    def schedule_load(self, *_args, **_kwargs):
        return None

    def schedule_convert(self, *_args, **_kwargs):
        return None

    def delete(self, *_args, **_kwargs):
        return {"deleted": True}


def test_nonfinite_usage_timestamp_does_not_break_storage_initialization(tmp_path):
    usage_file = tmp_path / "storage-usage.json"
    usage_file.write_text(
        '{"schema_version":1,"models":{"model":Infinity}}',
        encoding="utf-8",
    )

    state = StorageRuntimeState(manager=_Manager(), usage_file=usage_file)

    assert state.last_used("model", loaded=False) == {
        "timestamp": None,
        "status": "never_recorded",
    }


def test_boolean_usage_timestamp_is_not_treated_as_epoch_second(tmp_path):
    usage_file = tmp_path / "storage-usage.json"
    usage_file.write_text(
        '{"schema_version":1,"models":{"model":true}}',
        encoding="utf-8",
    )

    state = StorageRuntimeState(manager=_Manager(), usage_file=usage_file)

    assert state.last_used("model", loaded=False)["timestamp"] is None
