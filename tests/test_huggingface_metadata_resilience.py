from __future__ import annotations

import json
from types import SimpleNamespace

from app.huggingface_access import HuggingFaceCredentialStore
from app.huggingface_metadata_safety import install_huggingface_metadata_safety


def _store(tmp_path) -> HuggingFaceCredentialStore:
    models_file = tmp_path / "models.json"
    models_file.write_text("{}", encoding="utf-8")
    install_huggingface_metadata_safety()
    return HuggingFaceCredentialStore(SimpleNamespace(models_file=models_file))


def _token() -> str:
    return "hf_" + "m" * 32


def test_read_metadata_normalizes_nonfinite_and_structured_values(tmp_path):
    store = _store(tmp_path)
    store.metadata_path.write_text(
        '{"state":{"bad":true},"username":["bad"],"last_checked":Infinity}',
        encoding="utf-8",
    )

    assert store.read_metadata() == {
        "state": "unverified",
        "username": None,
        "last_checked": None,
    }


def test_metadata_text_is_bounded_and_control_characters_removed(tmp_path):
    store = _store(tmp_path)
    store.metadata_path.write_text(
        json.dumps(
            {
                "state": "connected\r\nforged",
                "username": "user\nname" + ("x" * 500),
                "last_checked": 123,
            }
        ),
        encoding="utf-8",
    )

    metadata = store.read_metadata()

    assert "\r" not in metadata["state"]
    assert "\n" not in metadata["state"]
    assert len(metadata["state"]) <= 40
    assert "\n" not in metadata["username"]
    assert len(metadata["username"]) <= 200
    assert metadata["last_checked"] == 123


def test_boolean_timestamp_is_not_treated_as_epoch_second(tmp_path):
    store = _store(tmp_path)
    store.metadata_path.write_text(
        '{"state":"connected","username":"user","last_checked":true}',
        encoding="utf-8",
    )

    assert store.read_metadata()["last_checked"] is None


def test_write_metadata_persists_only_normalized_values(tmp_path):
    store = _store(tmp_path)

    store.write_metadata(
        {
            "state": {"unexpected": "object"},
            "username": ["unexpected"],
            "last_checked": float("inf"),
        }
    )

    persisted = json.loads(store.metadata_path.read_text(encoding="utf-8"))
    assert persisted == {
        "state": "unverified",
        "username": None,
        "last_checked": None,
    }


def test_status_stays_json_serializable_with_corrupt_metadata(tmp_path):
    store = _store(tmp_path)
    store._memory_token = _token()
    store.metadata_path.write_text(
        '{"state":"connected","username":{"bad":true},"last_checked":NaN}',
        encoding="utf-8",
    )

    payload = store.status()
    encoded = json.dumps(payload, allow_nan=False)

    assert payload["configured"] is True
    assert payload["status"] == "connected"
    assert payload["username"] is None
    assert payload["last_checked"] is None
    assert _token() not in encoded
