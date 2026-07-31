from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import errors
from app.config import Settings
from app.diagnostics_privacy import sanitize_text
from app.huggingface_access import (
    HuggingFaceAccessService,
    HuggingFaceCredentialStore,
    register_huggingface_access_routes,
)
from app.model_manager import ModelManager


def _settings(tmp_path: Path, catalog: dict | None = None):
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(catalog or {}), encoding="utf-8")
    return SimpleNamespace(models_file=models_file, api_key=None)


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport, follow_redirects=True)


def test_non_windows_store_is_memory_only_and_never_returns_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token("hf_abcdefghijklmnopqrstuvwxyz")

    status = store.status()

    assert store.get_token() == "hf_abcdefghijklmnopqrstuvwxyz"
    assert status["configured"] is True
    assert status["token_masked"] == "••••••••"
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(status)
    if os.name != "nt":
        assert status["persistence"] == "memory_only"
        assert not store.token_path.exists()


def test_environment_token_is_read_only_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_environmenttoken123456")
    store = HuggingFaceCredentialStore(_settings(tmp_path))

    status = store.status()

    assert store.get_token() == "hf_environmenttoken123456"
    assert status["source"] == "environment"
    assert status["removable"] is False


def test_gated_preflight_stops_before_network_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(500)

    service = HuggingFaceAccessService(
        HuggingFaceCredentialStore(_settings(tmp_path)),
        client_factory=_client_factory(handler),
    )

    result = asyncio.run(
        service.preflight("meta-llama/Llama-3.2-1B-Instruct", access_type="gated")
    )

    assert result["code"] == "hf_token_missing"
    assert result["recoverable"] is True
    assert calls == []


def test_unknown_custom_gated_model_is_probed_before_conversion(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(403)

    service = HuggingFaceAccessService(
        HuggingFaceCredentialStore(_settings(tmp_path)),
        client_factory=_client_factory(handler),
    )

    result = asyncio.run(service.preflight("publisher/custom-gated-model", access_type="unknown"))

    assert result["code"] == "hf_token_missing"
    assert calls == ["/publisher/custom-gated-model/resolve/main/config.json"]


def test_valid_token_and_model_access_are_verified_without_exposing_token(tmp_path):
    token = "hf_validtokenabcdefghijklmnopqrstuvwxyz"

    def handler(request: httpx.Request):
        assert token not in str(request.url)
        if request.url.path == "/api/whoami-v2":
            assert request.headers["authorization"] == f"Bearer {token}"
            return httpx.Response(200, json={"name": "quinn"})
        assert request.url.path.endswith("/resolve/main/config.json")
        return httpx.Response(206, content=b"{")

    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(token)
    service = HuggingFaceAccessService(store, client_factory=_client_factory(handler))

    result = asyncio.run(
        service.preflight("meta-llama/Llama-3.2-1B-Instruct", access_type="gated")
    )

    assert result["code"] == "hf_access_granted"
    assert result["username"] == "quinn"
    assert token not in json.dumps(result)
    assert token not in json.dumps(service.status())


def test_valid_token_without_model_approval_is_actionable(tmp_path):
    token = "hf_validtokenabcdefghijklmnopqrstuvwxyz"

    def handler(request: httpx.Request):
        if request.url.path == "/api/whoami-v2":
            return httpx.Response(200, json={"name": "quinn"})
        return httpx.Response(403)

    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(token)
    service = HuggingFaceAccessService(store, client_factory=_client_factory(handler))

    result = asyncio.run(service.preflight("google/gemma-2-2b-it", access_type="gated"))

    assert result["code"] == "hf_approval_required"
    assert result["action"] == "open_model_agreement"
    assert result["model_url"] == "https://huggingface.co/google/gemma-2-2b-it"
    assert token not in json.dumps(result)


def test_invalid_token_is_not_persisted(tmp_path):
    token = "hf_invalidtokenabcdefghijklmnop"

    def handler(_request: httpx.Request):
        return httpx.Response(401)

    store = HuggingFaceCredentialStore(_settings(tmp_path))
    service = HuggingFaceAccessService(store, client_factory=_client_factory(handler))

    result = asyncio.run(service.test_token(token, persist=True))

    assert result["code"] == "hf_token_invalid"
    assert store.get_token() is None


def test_preflight_middleware_blocks_before_conversion_is_scheduled(tmp_path):
    catalog = {
        "llama": {
            "source_model": "meta-llama/Llama-3.2-1B-Instruct",
            "access_type": "gated",
        }
    }
    app = FastAPI()
    app.state.settings = _settings(tmp_path, catalog)
    app.state.manager = SimpleNamespace(
        catalog={
            "llama": SimpleNamespace(
                source_model="meta-llama/Llama-3.2-1B-Instruct"
            )
        }
    )

    class BlockedService:
        async def preflight(self, source_model, *, access_type):
            return {
                "code": "hf_token_missing",
                "message": "Configure a token.",
                "recoverable": True,
                "token_configured": False,
                "source_model": source_model,
                "model_url": f"https://huggingface.co/{source_model}",
                "license_url": f"https://huggingface.co/{source_model}",
            }

    app.state.huggingface_access_service = BlockedService()
    scheduled = []

    @app.post("/v1/models/convert")
    async def convert(_request: Request):
        scheduled.append(True)
        return {"scheduled": True}

    register_huggingface_access_routes(app)
    response = TestClient(app).post("/v1/models/convert", json={"model": "llama"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hf_token_missing"
    assert scheduled == []


def test_custom_model_middleware_uses_unknown_access_preflight(tmp_path):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    app.state.manager = SimpleNamespace(catalog={})
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
        },
    )

    assert response.status_code == 409
    assert observed == [("publisher/custom-model", "unknown")]
    assert scheduled == []


def test_preflight_middleware_replays_approved_request_body(tmp_path):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    app.state.manager = SimpleNamespace(
        catalog={"public": SimpleNamespace(source_model="Qwen/Qwen2.5-0.5B-Instruct")}
    )

    class ApprovedService:
        async def preflight(self, _source_model, *, access_type):
            assert access_type in {"public", "unknown"}
            return {"code": "hf_access_granted"}

    app.state.huggingface_access_service = ApprovedService()

    @app.post("/v1/models/convert")
    async def convert(request: Request):
        return await request.json()

    register_huggingface_access_routes(app)
    body = {"model": "public", "device": "CPU", "load_after": True}
    response = TestClient(app).post("/v1/models/convert", json=body)

    assert response.status_code == 200
    assert response.json() == body


def test_manager_entries_expose_structured_gated_metadata(tmp_path):
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "llama": {
                    "name": "Llama",
                    "description": "Gated test model",
                    "backend": "openvino-genai",
                    "model_path": str(tmp_path / "llama"),
                    "source_model": "meta-llama/Llama-3.2-1B-Instruct",
                    "access_type": "gated",
                    "model_url": "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct",
                    "license_url": "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 2048,
                    "max_output_tokens": 512,
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        models_file=models_file,
        models_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
    )
    manager = ModelManager(settings)

    entry = manager.catalog_entry("llama")

    assert entry["is_gated"] is True
    assert entry["huggingface_access"]["access_type"] == "gated"
    assert entry["huggingface_access"]["license_url"].startswith(
        "https://huggingface.co/"
    )


def test_huggingface_tokens_are_removed_from_errors_progress_and_diagnostics(tmp_path):
    secret = "hf_abcdefghijklmnopqrstuvwxyz"
    message = errors.format_model_convert_error(
        RuntimeError(f"GatedRepoError: token={secret} unauthorized")
    )
    assert secret not in message
    assert "Settings > Hugging Face access" in message

    models_file = tmp_path / "models.json"
    models_file.write_text("{}", encoding="utf-8")
    manager = ModelManager(
        Settings(
            models_file=models_file,
            models_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            benchmark_results_file=tmp_path / "benchmarks.json",
            force_mock=True,
        )
    )
    assert secret not in manager._sanitize_progress_line(f"token={secret}")
    assert secret not in sanitize_text(f"HF_TOKEN={secret}")
