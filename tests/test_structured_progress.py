import asyncio
import io
import json

from app.config import Settings
from app.model_manager import ModelManager
from runtime.progress_protocol import ProgressEventEmitter


def _manager(tmp_path) -> ModelManager:
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                "model-1": {
                    "name": "Model One",
                    "model_path": "models/openvino/model-1",
                    "source_model": "org/model-1",
                    "weight_format": "int4",
                    "recommended_device": "CPU",
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models" / "openvino",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
    )
    return ModelManager(settings)


def _reader(text: str) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(text.encode("utf-8"))
    reader.feed_eof()
    return reader


def test_server_operation_id_is_stable_and_revision_is_monotonic(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "loading")
    manager._set_progress("model-1", "queued", "Queued model")
    first = dict(manager.progress["model-1"])

    manager._set_progress("model-1", "loading", "Loading model", percent=50)
    second = dict(manager.progress["model-1"])

    assert first["schema_version"] == 1
    assert first["operation_id"].startswith("load-")
    assert first["operation_type"] == "load"
    assert second["operation_id"] == first["operation_id"]
    assert second["revision"] > first["revision"]


def test_retry_gets_new_operation_id_without_resetting_revision_counter(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "loading")
    manager._set_progress("model-1", "loading", "First attempt")
    first = dict(manager.progress["model-1"])

    manager._clear_progress("model-1")
    manager._set_status("model-1", "queued_convert")
    manager._set_progress("model-1", "queued", "Second attempt")
    second = dict(manager.progress["model-1"])

    assert second["operation_id"] != first["operation_id"]
    assert second["operation_id"].startswith("convert-")
    assert second["operation_type"] == "convert"
    assert second["revision"] > first["revision"]


def test_structured_stream_is_validated_and_mapped_to_server_operation(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "converting")

    stream = io.StringIO()
    producer = ProgressEventEmitter(
        operation_id="producer-1",
        model_id="model-1",
        stream=stream,
    )
    producer.emit("downloading", "Downloading model files…", percent=40, completed=2, total=5)
    producer.emit("converting", "Converting model to OpenVINO IR…", percent=10)

    lines = asyncio.run(
        manager._read_conversion_stream(
            "model-1",
            manager.catalog["model-1"],
            _reader(stream.getvalue()),
        )
    )
    progress = manager.progress["model-1"]

    assert lines == ["Downloading model files…", "Converting model to OpenVINO IR…"]
    assert progress["operation_id"].startswith("convert-")
    assert progress["operation_id"] != "producer-1"
    assert progress["operation_type"] == "convert"
    assert progress["phase"] == "converting"
    assert progress["percent"] == 10
    assert progress["revision"] >= 2


def test_duplicate_or_out_of_order_producer_revisions_are_ignored(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "converting")

    stream = io.StringIO()
    producer = ProgressEventEmitter(
        operation_id="producer-1",
        model_id="model-1",
        stream=stream,
    )
    producer.emit("downloading", "Downloading model files…", percent=25)
    first_line = stream.getvalue().splitlines()[0]
    duplicate_stream = f"{first_line}\n{first_line}\n"

    asyncio.run(
        manager._read_conversion_stream(
            "model-1",
            manager.catalog["model-1"],
            _reader(duplicate_stream),
        )
    )
    progress = manager.progress["model-1"]

    assert progress["revision"] == 1
    assert progress["log_tail"] == ["Downloading model files…"]


def test_malformed_claimed_event_and_wrong_model_are_rejected(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "converting")

    malformed = {
        "schema_version": 1,
        "type": "inferbridge.progress",
        "operation_id": "bad operation id",
        "revision": 1,
        "phase": "downloading",
        "message": "bad",
        "percent": 10,
        "model_id": "model-1",
        "completed": None,
        "total": None,
        "timestamp": 1.0,
    }
    wrong_model_stream = io.StringIO()
    producer = ProgressEventEmitter(
        operation_id="producer-2",
        model_id="different-model",
        stream=wrong_model_stream,
    )
    producer.emit("downloading", "Wrong model", percent=10)
    text = json.dumps(malformed) + "\n" + wrong_model_stream.getvalue()

    lines = asyncio.run(
        manager._read_conversion_stream(
            "model-1",
            manager.catalog["model-1"],
            _reader(text),
        )
    )

    assert lines == ["Ignored malformed structured progress event."]
    assert "model-1" not in manager.progress


def test_late_output_cannot_overwrite_terminal_state(tmp_path) -> None:
    manager = _manager(tmp_path)
    manager._set_status("model-1", "cancelled")
    manager._set_progress("model-1", "cancelled", "Conversion cancelled")
    terminal = dict(manager.progress["model-1"])

    stream = io.StringIO()
    producer = ProgressEventEmitter(
        operation_id="producer-3",
        model_id="model-1",
        stream=stream,
    )
    producer.emit("converting", "Late buffered output", percent=90)
    asyncio.run(
        manager._read_conversion_stream(
            "model-1",
            manager.catalog["model-1"],
            _reader(stream.getvalue()),
        )
    )

    assert manager.progress["model-1"] == terminal
