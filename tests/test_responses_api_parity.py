"""Regression coverage for InferBridge's OpenAI-compatible Responses API."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.openai_api import ResponseRequest
from app.server import create_app
from runtime.openvino_engine import GenResult

MODEL_ID = "tinyllama-1.1b-chat-fp16"


@pytest.fixture()
def client():
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _load_and_wait(client: TestClient, model_id: str = MODEL_ID, timeout: float = 10.0) -> None:
    response = client.post("/v1/models/load", json={"model": model_id})
    assert response.status_code == 200, response.text
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/v1/system/status").json()
        if model_id in status["models"]["loaded"]:
            return
        time.sleep(0.05)
    raise AssertionError("model did not load in time")


def _sse_payloads(body: str) -> list[dict]:
    payloads = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payloads.append(json.loads(line[6:]))
    return payloads


def test_responses_forwards_sampling_stop_seed_and_reports_usage(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager
    original_generate = manager.generate
    captured = {}

    async def capture_generate(engine, prompt, params):
        captured["params"] = params
        return await original_generate(engine, prompt, params)

    monkeypatch.setattr(manager, "generate", capture_generate)
    response = client.post(
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "hello",
            "temperature": 0.25,
            "top_p": 0.8,
            "seed": 42,
            "stop": ["You said"],
            "max_output_tokens": 80,
        },
    )

    assert response.status_code == 200, response.text
    params = captured["params"]
    assert params.temperature == 0.25
    assert params.top_p == 0.8
    assert params.seed == 42
    assert params.stop == ["You said"]

    data = response.json()
    text = data["output"][0]["content"][0]["text"]
    assert "Mock engine" in text
    assert "You said" not in text
    usage = data["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
    assert usage["input_tokens_details"] == {"cached_tokens": 0}
    assert usage["output_tokens_details"] == {"reasoning_tokens": 0}


def test_responses_text_format_maps_to_shared_structured_output(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager
    original_generate = manager.generate
    captured = {}
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    async def capture_generate(engine, prompt, params):
        captured["response_format"] = params.response_format
        return await original_generate(engine, prompt, params)

    monkeypatch.setattr(manager, "generate", capture_generate)
    response = client.post(
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "Return JSON.",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "answer_schema",
                    "schema": schema,
                    "strict": True,
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "answer_schema",
            "schema": schema,
            "strict": True,
        },
    }
    assert response.json()["text"]["format"] == {
        "type": "json_schema",
        "name": "answer_schema",
        "schema": schema,
        "strict": True,
    }


def test_responses_function_tools_accept_flat_shape_and_forced_choice(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager
    captured = {}

    async def tool_generate(engine, prompt, params):
        captured["prompt"] = prompt
        text = '{"name":"get_weather","arguments":{"city":"London"}}'
        return GenResult(text=text, completion_tokens=engine.count_tokens(text))

    monkeypatch.setattr(manager, "generate", tool_generate)
    response = client.post(
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "What is the weather?",
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get current weather.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                }
            ],
            "tool_choice": {"type": "function", "name": "get_weather"},
        },
    )

    assert response.status_code == 200, response.text
    assert "get_weather" in captured["prompt"]
    assert "MUST call" in captured["prompt"]
    data = response.json()
    assert data["tool_choice"] == {"type": "function", "name": "get_weather"}
    assert data["tools"][0]["name"] == "get_weather"
    assert len(data["output"]) == 1
    item = data["output"][0]
    assert item["type"] == "function_call"
    assert item["name"] == "get_weather"
    assert json.loads(item["arguments"]) == {"city": "London"}
    assert item["call_id"].startswith("call-")
    assert data["usage"]["total_tokens"] > 0


def test_responses_rejects_unsupported_hosted_tools_and_bad_choice(client):
    hosted = client.post(
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "search",
            "tools": [{"type": "web_search_preview"}],
        },
    )
    assert hosted.status_code == 422
    assert "function" in hosted.text

    bad_choice = client.post(
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "search",
            "tool_choice": {"type": "web_search_preview"},
        },
    )
    assert bad_choice.status_code == 422
    assert "function" in bad_choice.text


def test_responses_streaming_emits_rich_text_lifecycle_and_usage(client):
    _load_and_wait(client)
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": MODEL_ID, "input": "hello", "stream": True},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    required_events = {
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    }
    observed_events = {line[7:] for line in body.splitlines() if line.startswith("event: ")}
    assert required_events <= observed_events
    assert "data: [DONE]" in body

    payloads = _sse_payloads(body)
    sequences = [payload["sequence_number"] for payload in payloads]
    assert sequences == list(range(1, len(sequences) + 1))
    completed = next(payload for payload in payloads if payload["type"] == "response.completed")
    usage = completed["response"]["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_responses_streaming_emits_function_call_argument_events(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager

    async def fake_stream(_engine, _prompt, _params):
        yield '{"name":"lookup","arguments":{"id":7}}'

    monkeypatch.setattr(manager, "stream", fake_stream)
    with client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": MODEL_ID,
            "input": "look it up",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                }
            ],
            "tool_choice": "required",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    events = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
    assert "response.output_item.added" in events
    assert "response.function_call_arguments.delta" in events
    assert "response.function_call_arguments.done" in events
    assert "response.output_item.done" in events
    payloads = _sse_payloads(body)
    completed = next(payload for payload in payloads if payload["type"] == "response.completed")
    output = completed["response"]["output"]
    assert output[0]["type"] == "function_call"
    assert output[0]["name"] == "lookup"
    assert json.loads(output[0]["arguments"]) == {"id": 7}


def test_responses_nonstream_generation_failures_are_sanitized(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager

    async def fail_generate(_engine, _prompt, _params):
        raise RuntimeError("secret C:/private/model.xml")

    monkeypatch.setattr(manager, "generate", fail_generate)
    response = client.post(
        "/v1/responses",
        json={"model": MODEL_ID, "input": "hello"},
    )
    assert response.status_code == 500
    assert "secret" not in response.text
    assert "private" not in response.text
    assert "see server logs" in response.json()["detail"]


def test_responses_stream_generation_failures_are_sanitized(client, monkeypatch):
    _load_and_wait(client)
    manager = client.app.state.manager

    async def fail_stream(_engine, _prompt, _params):
        if False:
            yield "unused"
        raise RuntimeError("secret C:/private/model.xml")

    monkeypatch.setattr(manager, "stream", fail_stream)
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": MODEL_ID, "input": "hello", "stream": True},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: error" in body
    assert "event: response.failed" in body
    assert "secret" not in body
    assert "private" not in body
    assert "see server logs" in body
    assert "data: [DONE]" in body


def test_responses_stream_close_releases_generation_lock():
    settings = Settings(
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        force_mock=True,
    )
    app = create_app(settings)
    manager = app.state.manager
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/v1/responses" and "POST" in route.methods
    )

    async def scenario():
        await manager.startup()
        task = manager.schedule_load(MODEL_ID)
        if task:
            await task
        lock = manager.get_lock(MODEL_ID)
        response = await route.endpoint(
            ResponseRequest(model=MODEL_ID, input="generate a long answer", stream=True)
        )
        stream = response.body_iterator
        while True:
            chunk = await stream.__anext__()
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if "response.output_text.delta" in text:
                break
        assert lock.locked()
        await stream.aclose()
        assert not lock.locked()

        follow_up = await route.endpoint(ResponseRequest(model=MODEL_ID, input="hello"))
        assert follow_up.status == "completed"
        await manager.shutdown()

    asyncio.run(scenario())
