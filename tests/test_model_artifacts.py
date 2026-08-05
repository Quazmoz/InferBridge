import json

from app import model_registry
from runtime.model_artifacts import validate_openvino_model_dir

_VALID_IR_XML = "<net name='model' version='11'></net>"


def _write_ready_model(tmp_path, marker: str = "openvino_model.xml"):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / marker).write_text(_VALID_IR_XML, encoding="utf-8")
    (model_dir / marker).with_suffix(".bin").write_bytes(b"weights")
    (model_dir / "config.json").write_text(
        json.dumps({"model_type": "test"}),
        encoding="utf-8",
    )
    return model_dir


def test_missing_model_directory_is_not_ready(tmp_path):
    result = validate_openvino_model_dir(tmp_path / "missing")

    assert result.ready is False
    assert "does not exist" in result.reason


def test_ir_xml_without_weights_or_config_is_not_ready(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "openvino_model.xml").write_text(_VALID_IR_XML, encoding="utf-8")

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is False
    assert "openvino_model.bin" in result.reason
    assert model_registry.is_openvino_model_dir(model_dir) is False


def test_truncated_ir_xml_is_not_ready(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "openvino_model.xml").write_text("<net>", encoding="utf-8")
    (model_dir / "openvino_model.bin").write_bytes(b"weights")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is False
    assert "truncated" in result.reason


def test_empty_weights_are_not_ready(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "openvino_model.xml").write_text(_VALID_IR_XML, encoding="utf-8")
    (model_dir / "openvino_model.bin").write_bytes(b"")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is False
    assert "missing or empty" in result.reason


def test_invalid_config_is_not_ready(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "openvino_model.xml").write_text(_VALID_IR_XML, encoding="utf-8")
    (model_dir / "openvino_model.bin").write_bytes(b"weights")
    (model_dir / "config.json").write_text("not-json", encoding="utf-8")

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is False
    assert "config.json" in result.reason


def test_standard_openvino_model_artifact_is_ready(tmp_path):
    model_dir = _write_ready_model(tmp_path)

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is True
    assert result.ir_xml == model_dir / "openvino_model.xml"
    assert result.ir_bin == model_dir / "openvino_model.bin"
    assert model_registry.is_openvino_model_dir(model_dir) is True


def test_language_model_artifact_is_ready(tmp_path):
    model_dir = _write_ready_model(tmp_path, "openvino_language_model.xml")

    result = validate_openvino_model_dir(model_dir)

    assert result.ready is True
    assert result.ir_xml == model_dir / "openvino_language_model.xml"
    assert result.ir_bin == model_dir / "openvino_language_model.bin"
