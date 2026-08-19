from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.huggingface_access import register_huggingface_access_routes


def test_string_false_cannot_bypass_hugging_face_preflight(tmp_path):
    models_file = tmp_path / "models.json"
    models_file.write_text("{}", encoding="utf-8")
    app = FastAPI()
    app.state.settings = SimpleNamespace(models_file=models_file, api_key=None)
    app.state.manager = SimpleNamespace(catalog={}, _hf_access_metadata={})
    observed = []

    class BlockedService:
        async def preflight(self, source_model, *, access_type):
            observed.append((source_model, access_type))
            return {
                "code": "hf_token_missing",
                "message": "Configure a token.",
                "recoverable": True,
                "token_configured": False,
                "source_model": source_model,
            }

    app.state.huggingface_access_service = BlockedService()
    scheduled = []

    @app.post("/v1/models/download-custom")
    async def download_custom(_request: Request):
        scheduled.append(True)
        return {"scheduled": True}

    register_huggingface_access_routes(app)
    response = TestClient(app).post(
        "/v1/models/download-custom",
        json={
            "model_id": "custom-model",
            "source_model": "publisher/custom-model",
            "trust_remote_code": "false",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hf_token_missing"
    assert observed == [("publisher/custom-model", "unknown")]
    assert scheduled == []
