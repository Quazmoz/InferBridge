import json
from types import SimpleNamespace

import pytest

from app.data_migrations import ensure_data_schema


def paths(tmp_path):
    config = tmp_path / "config"
    onboarding = tmp_path / "onboarding"
    config.mkdir()
    onboarding.mkdir()
    return SimpleNamespace(
        data_root=tmp_path,
        config_dir=config,
        models_file=config / "models.json",
        onboarding_file=onboarding / "state.json",
    )


def test_data_schema_creation_is_idempotent(tmp_path):
    value = paths(tmp_path)
    assert ensure_data_schema(value) == 1
    first = (tmp_path / "data-schema.json").read_text()
    assert ensure_data_schema(value) == 1
    second = (tmp_path / "data-schema.json").read_text()
    assert json.loads(first)["schema_version"] == json.loads(second)["schema_version"] == 1


def test_newer_data_schema_is_rejected(tmp_path):
    value = paths(tmp_path)
    (tmp_path / "data-schema.json").write_text('{"schema_version": 2}')
    with pytest.raises(RuntimeError, match="older than the persistent data schema"):
        ensure_data_schema(value)


def test_the_marker_records_the_supported_range_and_a_timestamp(tmp_path):
    ensure_data_schema(paths(tmp_path))

    payload = json.loads((tmp_path / "data-schema.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["minimum_supported_schema"] == 1
    assert payload["updated_at"].endswith("Z")


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("truncated", "{ not json"),
        ("empty", ""),
        ("array", "[1, 2, 3]"),
        ("null version", '{"schema_version": null}'),
        ("text version", '{"schema_version": "abc"}'),
        ("missing key", "{}"),
        ("zero", '{"schema_version": 0}'),
        ("negative", '{"schema_version": -1}'),
    ],
)
def test_every_malformed_marker_shape_reports_the_same_actionable_failure(tmp_path, label, content):
    """``ensure_data_schema`` runs during desktop startup, so a hand-edited or truncated
    marker must not escape as a raw decoding traceback."""

    value = paths(tmp_path)
    (tmp_path / "data-schema.json").write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="persistent data schema marker is invalid"):
        ensure_data_schema(value)


def test_a_utf8_bom_marker_is_still_readable(tmp_path):
    value = paths(tmp_path)
    (tmp_path / "data-schema.json").write_text('{"schema_version": 1}', encoding="utf-8-sig")

    assert ensure_data_schema(value) == 1


def test_the_marker_is_written_atomically_leaving_no_temporary_file(tmp_path):
    ensure_data_schema(paths(tmp_path))

    assert (tmp_path / "data-schema.json").is_file()
    assert list(tmp_path.glob("*.tmp")) == []
