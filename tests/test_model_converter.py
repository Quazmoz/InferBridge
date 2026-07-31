import io
import json

import pytest

from runtime import model_converter as mc
from runtime.progress_protocol import ProgressEventEmitter, decode_progress_event


def test_build_export_command_basic():
    cmd = mc.build_export_command("org/model", "out/dir", "int4")
    assert cmd[0] == "optimum-cli"
    assert cmd[1:3] == ["export", "openvino"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "org/model"
    assert "--weight-format" in cmd and cmd[cmd.index("--weight-format") + 1] == "int4"
    assert "--trust-remote-code" not in cmd
    assert cmd[-1] == "out/dir"


def test_build_export_command_with_task_and_explicit_trust():
    cmd = mc.build_export_command(
        "org/model", "out", "int8", trust_remote_code=True, task="text-generation"
    )
    assert "--task" in cmd and cmd[cmd.index("--task") + 1] == "text-generation"
    assert "--trust-remote-code" in cmd
    assert "int8" in cmd


def test_export_model_raises_when_cli_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mc.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="optimum-cli not found"):
        mc.export_model("org/model", tmp_path / "out")

    stdout = capsys.readouterr().out.splitlines()
    events = [decode_progress_event(line) for line in stdout]
    assert [event.phase for event in events if event is not None] == ["resolving", "error"]


def test_conversion_progress_survives_cp1252_stdout(monkeypatch):
    """Progress glyphs from tqdm/Transformers must not crash conversion on cp1252."""
    import sys

    glyph_line = "Loading weights:  50%|█▏    | 1/272"

    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    with pytest.raises(UnicodeEncodeError):
        legacy.write(glyph_line)
        legacy.flush()

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", newline=""))
    mc._ensure_utf8_stdio()
    mc._ProgressLineEmitter().emit(glyph_line)
    sys.stdout.flush()
    assert "▏".encode() in raw.getvalue()


def test_export_model_runs_streaming_command_and_makes_parent(monkeypatch, tmp_path, capsys):
    captured = {}

    def fake_streaming_command(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["progress_emitter"] = kwargs.get("progress_emitter")

    monkeypatch.setattr(mc.shutil, "which", lambda name: "/usr/bin/optimum-cli")
    monkeypatch.setattr(mc, "_run_streaming_command", fake_streaming_command)

    out = tmp_path / "ir" / "model"
    result = mc.export_model("org/model", out, "int8")

    assert result == out
    assert out.parent.is_dir()
    assert "org/model" in captured["cmd"]
    assert "int8" in captured["cmd"]
    assert captured["progress_emitter"] is not None
    console = capsys.readouterr().out
    assert "Downloading model metadata and weights" in console
    assert "Saving OpenVINO IR" in console


def test_export_model_stdout_is_jsonl_and_human_output_is_stderr(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(mc.shutil, "which", lambda name: "/usr/bin/optimum-cli")

    def fake_streaming_command(cmd, *, progress_emitter=None):
        assert progress_emitter is not None
        progress_emitter.emit("converting", "Converting model to OpenVINO IR…", percent=50)
        print("Optimum human diagnostic", file=mc.sys.stderr)

    monkeypatch.setattr(mc, "_run_streaming_command", fake_streaming_command)
    mc.export_model("org/model", tmp_path / "out", model_id="model-1")

    captured = capsys.readouterr()
    events = [decode_progress_event(line) for line in captured.out.splitlines()]
    assert events
    assert all(event is not None for event in events)
    assert [event.phase for event in events] == [
        "resolving",
        "downloading",
        "converting",
        "finalizing",
        "ready",
    ]
    assert all(event.model_id == "model-1" for event in events)
    assert "Optimum human diagnostic" in captured.err
    assert "Downloading model metadata and weights" in captured.err
    assert "Saving OpenVINO IR" in captured.err


def test_console_progress_splits_carriage_returns_and_strips_ansi():
    chunks = [
        b"\x1b[2Kmodel.safetensors: 10%|#         | 1.0MiB/10MiB\r",
        b"model.safetensors: 20%|##        | 2.0MiB/10MiB\r\n",
        b"Exporting OpenVINO model\nDone",
    ]

    assert list(mc._iter_console_lines(chunks)) == [
        "model.safetensors: 10%|#         | 1.0MiB/10MiB",
        "model.safetensors: 20%|##        | 2.0MiB/10MiB",
        "Exporting OpenVINO model",
        "Done",
    ]


def test_progress_emitter_labels_download_bars(capsys):
    emitter = mc._ProgressLineEmitter()
    emitter.emit("model.safetensors: 25%|##5       | 1.0MiB/4.0MiB [00:01<00:03, 1.0MiB/s]")

    output = capsys.readouterr().out
    assert output.startswith("Downloading model.safetensors: 25%")
    assert "1.0MiB/s" in output


def test_progress_emitter_produces_monotonic_structured_phases(capsys):
    protocol_stream = io.StringIO()
    human_stream = io.StringIO()
    protocol = ProgressEventEmitter(
        operation_id="producer-1",
        model_id="model-1",
        stream=protocol_stream,
    )
    emitter = mc._ProgressLineEmitter(protocol, human_stream=human_stream)

    emitter.emit("model.safetensors: 25%|##5| 1/4")
    emitter.emit("Quantizing weights: 10%|#| 1/10")
    emitter.emit("model.safetensors: 100%|##########| 4/4")

    events = [decode_progress_event(line) for line in protocol_stream.getvalue().splitlines()]
    assert [event.phase for event in events] == ["downloading", "converting", "converting"]
    assert [event.revision for event in events] == [1, 2, 3]
    assert "model.safetensors" in human_stream.getvalue()
    assert capsys.readouterr().out == ""


def test_resolve_from_catalog_reads_models_json(monkeypatch, tmp_path):
    catalog = {
        "m1": {
            "name": "M1",
            "model_path": "models/openvino/m1",
            "source_model": "org/m1",
            "weight_format": "int8",
            "trust_remote_code": True,
        }
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))

    source, output_dir, weight_format = mc._resolve_from_catalog("m1")
    assert source == "org/m1"
    assert weight_format == "int8"
    assert output_dir.name == "m1"


def test_resolve_from_catalog_includes_safe_execution_policy(monkeypatch, tmp_path):
    catalog = {
        "vision": {
            "model_path": "models/openvino/vision",
            "source_model": "org/vision",
            "backend": "openvino-vlm",
            "trust_remote_code": True,
        }
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))

    source, output_dir, weight_format, task, trusted = mc._resolve_from_catalog(
        "vision", include_task=True
    )
    assert source == "org/vision"
    assert output_dir.name == "vision"
    assert weight_format == "int4"
    assert task == "image-text-to-text"
    assert trusted is True


def test_main_by_id_uses_catalog_weight_format(monkeypatch, tmp_path, capsys):
    catalog = {
        "m1-fp16": {
            "name": "M1 FP16",
            "model_path": "models/openvino/m1-fp16",
            "source_model": "org/m1",
            "weight_format": "fp16",
        }
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))

    captured = {}

    def fake_export(source_model, output_dir, weight_format, **kwargs):
        captured["source_model"] = source_model
        captured["output_dir"] = output_dir
        captured["weight_format"] = weight_format
        captured["trust_remote_code"] = kwargs["trust_remote_code"]
        captured["model_id"] = kwargs["model_id"]
        return output_dir

    monkeypatch.setattr(mc, "export_model", fake_export)

    assert mc.main(["--id", "m1-fp16"]) == 0
    assert captured["source_model"] == "org/m1"
    assert captured["weight_format"] == "fp16"
    assert captured["output_dir"].name == "m1-fp16"
    assert captured["trust_remote_code"] is False
    assert captured["model_id"] == "m1-fp16"
    assert capsys.readouterr().out == ""


def test_main_by_id_allows_weight_format_override(monkeypatch, tmp_path):
    catalog = {
        "m1-fp16": {
            "model_path": "models/openvino/m1-fp16",
            "source_model": "org/m1",
            "weight_format": "fp16",
        }
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))

    captured = {}
    monkeypatch.setattr(
        mc,
        "export_model",
        lambda source_model, output_dir, weight_format, **kwargs: (
            captured.setdefault("weight_format", weight_format) or output_dir
        ),
    )

    assert mc.main(["--id", "m1-fp16", "--weight-format", "int8"]) == 0
    assert captured["weight_format"] == "int8"


def test_main_catalog_trust_policy_can_be_explicitly_overridden(monkeypatch, tmp_path):
    catalog = {
        "trusted": {
            "model_path": "models/openvino/trusted",
            "source_model": "org/trusted",
            "trust_remote_code": True,
        }
    }
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))
    captured = {}

    def fake_export(source_model, output_dir, weight_format, **kwargs):
        captured["trust_remote_code"] = kwargs["trust_remote_code"]
        return output_dir

    monkeypatch.setattr(mc, "export_model", fake_export)
    assert mc.main(["--id", "trusted", "--no-trust-remote-code"]) == 0
    assert captured["trust_remote_code"] is False

    assert mc.main(["--id", "trusted", "--trust-remote-code"]) == 0
    assert captured["trust_remote_code"] is True


def test_resolve_from_catalog_unknown_id_exits(monkeypatch, tmp_path):
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))

    with pytest.raises(SystemExit):
        mc._resolve_from_catalog("does-not-exist")
