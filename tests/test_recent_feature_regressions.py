"""Regression coverage for the July 2026 OpenVINO feature expansion."""

import hashlib
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.server import create_app
from runtime.openvino_engine import GenResult

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def _client(tmp_path: Path, *, api_key: str | None = None) -> TestClient:
    models_file = tmp_path / "models.json"
    models_file.write_text((BASE_DIR / "models.json").read_text(encoding="utf-8"), encoding="utf-8")
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=models_file,
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=api_key,
        force_mock=True,
    )
    return TestClient(create_app(settings))


def _load(
    client: TestClient, model_id: str = MODEL_ID, headers: dict[str, str] | None = None
) -> None:
    response = client.post("/v1/models/load", json={"model": model_id}, headers=headers or {})
    assert response.status_code == 200, response.text
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get("/v1/system/status", headers=headers or {}).json()
        if model_id in status["models"]["loaded"]:
            return
        time.sleep(0.02)
    raise AssertionError(f"{model_id} did not load")


def test_structured_output_is_forwarded_to_generation(tmp_path: Path) -> None:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "person",
            "schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }
    with _client(tmp_path) as client:
        _load(client)
        manager = client.app.state.manager
        captured = {}

        async def fake_generate(engine, prompt, params):
            captured["response_format"] = params.response_format
            return GenResult(text='{"name":"Ada"}', completion_tokens=4)

        manager.generate = fake_generate
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "Return a person"}],
                "response_format": response_format,
            },
        )
        assert response.status_code == 200, response.text
        assert captured["response_format"] == response_format


def test_embedding_model_cannot_be_used_as_speculative_draft(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "draft_model": "bge-small-en-v1.5"},
        )
        assert response.status_code == 400
        assert "embedding model" in response.json()["detail"]
        assert MODEL_ID not in client.app.state.manager.load_tasks


def test_custom_embedding_registration_preserves_backend(tmp_path: Path, monkeypatch) -> None:
    async def granted_access(self, source_model, **kwargs):
        return {"code": "hf_access_granted"}

    monkeypatch.setattr("app.huggingface_access.HuggingFaceAccessService.preflight", granted_access)
    with _client(tmp_path) as client:
        manager = client.app.state.manager

        def fake_schedule_convert(model_id, device=None, **kwargs):
            manager._set_status(model_id, "queued_convert")
            return object()

        manager.schedule_convert = fake_schedule_convert
        response = client.post(
            "/v1/models/download-custom",
            json={
                "model_id": "custom-embedding-regression",
                "name": "Custom Embedding Regression",
                "source_model": "BAAI/bge-small-en-v1.5",
                "backend": "openvino-embeddings",
                "weight_format": "fp16",
                "recommended_device": "CPU",
                "max_context_len": 512,
                "max_output_tokens": 0,
                "load_after": False,
            },
        )
        assert response.status_code == 200, response.text
        config = manager.catalog["custom-embedding-regression"]
        assert config.backend == "openvino-embeddings"
        assert response.json()["model"]["backend"] == "openvino-embeddings"


def test_nonstream_responses_are_attributed_to_the_calling_key(tmp_path: Path) -> None:
    beta_headers = {"Authorization": "Bearer beta-key"}
    alpha_headers = {"Authorization": "Bearer alpha-key"}
    with _client(tmp_path, api_key="alpha-key,beta-key") as client:
        _load(client, headers=beta_headers)
        response = client.post(
            "/v1/responses",
            headers=beta_headers,
            json={"model": MODEL_ID, "input": "hello", "stream": False},
        )
        assert response.status_code == 200, response.text

        stats_response = client.get("/v1/keys/stats", headers=alpha_headers)
        assert stats_response.status_code == 200
        stats = stats_response.json()
        fingerprint = hashlib.sha256(b"beta-key").hexdigest()[:8]
        beta_stats = next(item for item in stats if item["key_name"].endswith(fingerprint))
        assert beta_stats["requests"] == 1
        assert beta_stats["prompt_tokens"] > 0
        assert beta_stats["completion_tokens"] > 0
