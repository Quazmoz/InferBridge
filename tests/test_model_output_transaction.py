import errno
import os
from pathlib import Path

import pytest

from runtime import model_output_transaction as transaction
from runtime.model_artifacts import validate_openvino_model_dir
from runtime.model_output_transaction import staged_model_output


def _write_model(path: Path, payload: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    (path / "openvino_model.bin").write_bytes(payload)
    (path / "config.json").write_text("{}", encoding="utf-8")


def _staging(final: Path) -> Path:
    return final.parent / f".{final.name}.inferbridge-staging"


def _backup(final: Path) -> Path:
    return final.parent / f".{final.name}.inferbridge-backup"


def test_successful_staged_output_replaces_live_model(tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")

    with staged_model_output(final) as staging:
        assert final.is_dir()
        assert staging == _staging(final)
        assert not staging.exists()
        _write_model(staging, b"new")

    assert validate_openvino_model_dir(final).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"new"
    assert not _staging(final).exists()
    assert not _backup(final).exists()


def test_failed_conversion_preserves_previous_model(tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")

    with pytest.raises(RuntimeError, match="converter failed"):
        with staged_model_output(final) as staging:
            staging.mkdir()
            (staging / "partial.bin").write_bytes(b"partial")
            raise RuntimeError("converter failed")

    assert validate_openvino_model_dir(final).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"old"
    assert not _staging(final).exists()
    assert not _backup(final).exists()


def test_incomplete_success_output_is_rejected_without_replacing_model(tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")

    with pytest.raises(RuntimeError, match="staged OpenVINO model is incomplete"):
        with staged_model_output(final) as staging:
            staging.mkdir()
            (staging / "openvino_model.xml").write_text(
                "<net name='model' version='11'></net>",
                encoding="utf-8",
            )

    assert validate_openvino_model_dir(final).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"old"
    assert not _staging(final).exists()


def test_malformed_staged_xml_is_rejected_without_replacing_model(tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")

    with pytest.raises(RuntimeError, match="malformed"):
        with staged_model_output(final) as staging:
            _write_model(staging, b"new")
            malformed = "<net>" + ("<layers></layers>" * 600) + "<broken></net>"
            (staging / "openvino_model.xml").write_text(malformed, encoding="utf-8")

    assert (final / "openvino_model.bin").read_bytes() == b"old"
    assert validate_openvino_model_dir(final, thorough=True).ready is True


def test_transient_directory_replace_failure_is_retried(monkeypatch, tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")
    real_replace = os.replace
    publication_failures = 0

    def flaky_replace(source, destination):
        nonlocal publication_failures
        if (
            Path(source) == _staging(final)
            and Path(destination) == final
            and publication_failures < 2
        ):
            publication_failures += 1
            raise PermissionError(errno.EACCES, "temporarily locked")
        return real_replace(source, destination)

    monkeypatch.setattr(transaction.os, "replace", flaky_replace)
    monkeypatch.setattr(transaction.time, "sleep", lambda _delay: None)

    with staged_model_output(final) as staging:
        _write_model(staging, b"new")

    assert publication_failures == 2
    assert (final / "openvino_model.bin").read_bytes() == b"new"
    assert not _backup(final).exists()


def test_second_model_preparation_process_is_rejected(tmp_path):
    final = tmp_path / "model"
    _write_model(final, b"old")

    with pytest.raises(RuntimeError, match="already preparing this model"):
        with staged_model_output(final):
            with staged_model_output(final):
                pass

    assert validate_openvino_model_dir(final).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"old"
    assert not _staging(final).exists()


def test_stale_backup_restores_previous_model_before_new_attempt(tmp_path):
    final = tmp_path / "model"
    backup = _backup(final)
    _write_model(backup, b"recovered")

    with pytest.raises(RuntimeError, match="stop after recovery"):
        with staged_model_output(final):
            assert validate_openvino_model_dir(final).ready is True
            assert (final / "openvino_model.bin").read_bytes() == b"recovered"
            raise RuntimeError("stop after recovery")

    assert validate_openvino_model_dir(final).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"recovered"
    assert not backup.exists()


def test_symbolic_link_destination_is_rejected(tmp_path):
    target = tmp_path / "target"
    _write_model(target, b"old")
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment")

    with pytest.raises(RuntimeError, match="symbolic link or junction"):
        with staged_model_output(linked):
            pass


def test_symbolic_link_staging_is_rejected_without_touching_target(tmp_path):
    final = tmp_path / "model"
    target = tmp_path / "unrelated"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    staging = _staging(final)
    try:
        staging.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment")

    with pytest.raises(RuntimeError, match="symbolic link or junction"):
        with staged_model_output(final):
            pass

    assert marker.read_text(encoding="utf-8") == "keep"
    assert staging.is_symlink()
