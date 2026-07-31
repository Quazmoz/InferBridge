import io
import json

import pytest

from runtime.progress_protocol import (
    EVENT_TYPE,
    SCHEMA_VERSION,
    ProgressEventEmitter,
    decode_progress_event,
)


def test_emitter_writes_ordered_compact_json_lines() -> None:
    stream = io.StringIO()
    emitter = ProgressEventEmitter(
        operation_id="producer-convert-123",
        model_id="model-1",
        stream=stream,
    )

    emitter.emit("downloading", "Downloading model files…", percent=25, completed=1, total=4)
    emitter.emit("converting", "Converting model…", percent=10)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert all(line.startswith("{") and "\n" not in line for line in lines)
    first = decode_progress_event(lines[0])
    second = decode_progress_event(lines[1])
    assert first is not None and second is not None
    assert first.revision == 1
    assert second.revision == 2
    assert first.completed == 1
    assert first.total == 4
    assert json.loads(lines[0])["type"] == EVENT_TYPE


def test_decoder_distinguishes_human_output_from_protocol_records() -> None:
    assert decode_progress_event("Downloading model.safetensors: 50%") is None
    assert decode_progress_event('{"message":"ordinary json"}') is None


def test_decoder_rejects_claimed_events_with_invalid_schema() -> None:
    base = {
        "schema_version": SCHEMA_VERSION,
        "type": EVENT_TYPE,
        "operation_id": "producer-1",
        "revision": 1,
        "phase": "downloading",
        "message": "Downloading",
        "percent": 50,
        "model_id": "model-1",
        "completed": 1,
        "total": 2,
        "timestamp": 1.0,
    }

    invalid_cases = [
        {**base, "schema_version": 99},
        {**base, "operation_id": "bad operation id"},
        {**base, "revision": 0},
        {**base, "phase": "invented"},
        {**base, "percent": 101},
        {**base, "completed": 3, "total": 2},
        {**base, "timestamp": 0},
    ]
    for payload in invalid_cases:
        with pytest.raises(ValueError):
            decode_progress_event(json.dumps(payload))


def test_emitter_validates_before_writing() -> None:
    stream = io.StringIO()
    emitter = ProgressEventEmitter(operation_id="producer-1", stream=stream)

    with pytest.raises(ValueError):
        emitter.emit("invalid", "bad phase")
    assert stream.getvalue() == ""
    assert emitter.revision == 0
