import json

from fastapi.testclient import TestClient

from app import chat_format
from app.config import Settings
from app.context_budget import analyze_prompt_budget
from app.server import create_app
from runtime.openvino_engine import MockEngine

MODEL_ID = "model-1"


def _settings(tmp_path, *, api_key: str | None = None) -> Settings:
    models_dir = tmp_path / "models" / "openvino"
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                MODEL_ID: {
                    "name": "Context Test Model",
                    "model_path": str(models_dir / MODEL_ID),
                    "source_model": "org/model-1",
                    "weight_format": "int4",
                    "recommended_device": "CPU",
                    "max_context_len": 256,
                    "max_output_tokens": 64,
                }
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog,
        models_dir=models_dir,
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
        api_key=api_key,
    )


def _install_mock_engine(client: TestClient) -> MockEngine:
    manager = client.app.state.manager
    engine = MockEngine(MODEL_ID)
    manager.engines[MODEL_ID] = engine
    manager.devices[MODEL_ID] = "MOCK"
    return engine


def test_analyzer_matches_generation_prompt_and_preserves_whole_turns() -> None:
    messages = [
        {"role": "system", "content": "Keep these instructions."},
        {"role": "user", "content": "old question " * 20},
        {"role": "assistant", "content": "old answer " * 20},
        {"role": "user", "content": "new question " * 8},
        {"role": "assistant", "content": "new answer"},
    ]

    def apply_template(items):
        return chat_format.render_chatml(items)

    def count_tokens(prompt):
        return max(1, len(prompt) // 4)

    expected_prompt, expected_tokens = chat_format.build_prompt_within_budget(
        messages,
        apply_template,
        count_tokens,
        90,
    )
    analysis = analyze_prompt_budget(messages, apply_template, count_tokens, 90)

    assert analysis.prompt == expected_prompt
    assert analysis.prompt_tokens == expected_tokens
    assert analysis.retained_indexes == (0, 3, 4)
    assert analysis.dropped_indexes == (1, 2)
    assert analysis.dropped_turns == 1
    assert "Keep these instructions." in analysis.prompt
    assert "new question" in analysis.prompt
    assert "old question" not in analysis.prompt


def test_context_budget_route_matches_loaded_model_tokenizer_and_reports_omissions(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        engine = _install_mock_engine(client)
        messages = [
            {"role": "system", "content": "Pinned system instructions."},
            {"role": "user", "content": "old question " * 45},
            {"role": "assistant", "content": "old answer " * 45},
            {"role": "user", "content": "latest question " * 12},
        ]
        response = client.post(
            "/v1/chat/context-budget",
            json={
                "model": MODEL_ID,
                "messages": messages,
                "max_tokens": 80,
                "image_count": 2,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    normalized = chat_format.normalize_messages(
        [chat_format.ChatMessage(**message) for message in messages]
        if hasattr(chat_format, "ChatMessage")
        else [],
    )
    # The route uses the same mock tokenizer contract as generation.
    assert payload["prompt_tokens"] > 0
    assert payload["max_prompt_tokens"] == 192
    assert payload["max_context_tokens"] == 256
    assert payload["requested_output_tokens"] == 80
    assert payload["effective_output_tokens"] <= payload["available_output_tokens"]
    assert payload["will_truncate"] is True
    assert payload["dropped_turn_count"] == 1
    assert payload["dropped_message_count"] == 2
    assert payload["retained_message_count"] == 2
    assert payload["system_instructions_retained"] is True
    assert payload["dropped_messages"][0]["role"] == "user"
    assert "old question" in payload["dropped_messages"][0]["preview"]
    assert payload["attachment_count"] == 2
    assert payload["attachment_token_estimate"] == 1024
    assert payload["prompt_tokens"] >= 1024
    assert engine.model_id == MODEL_ID


def test_context_budget_route_reports_output_limit_without_mutating_chat(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _install_mock_engine(client)
        response = client.post(
            "/v1/chat/context-budget",
            json={
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "x" * 650}],
                "max_tokens": 200,
            },
        )
        manager = client.app.state.manager

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_output_tokens"] == 200
    assert payload["output_limited"] is True
    assert payload["effective_output_tokens"] == payload["available_output_tokens"]
    assert manager.metrics_summary()["totals"]["requests"] == 0


def test_context_budget_route_uses_api_key_and_browser_origin_policy(tmp_path) -> None:
    app = create_app(_settings(tmp_path, api_key="secret-key"))
    with TestClient(app) as client:
        _install_mock_engine(client)
        body = {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "hello"}],
        }
        missing = client.post("/v1/chat/context-budget", json=body)
        wrong = client.post(
            "/v1/chat/context-budget",
            json=body,
            headers={"Authorization": "Bearer wrong"},
        )
        cross_site = client.post(
            "/v1/chat/context-budget",
            json=body,
            headers={
                "Authorization": "Bearer secret-key",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        allowed = client.post(
            "/v1/chat/context-budget",
            json=body,
            headers={"Authorization": "Bearer secret-key"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert cross_site.status_code == 403
    assert allowed.status_code == 200
