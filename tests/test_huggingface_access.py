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
    _converter_environment,
    register_huggingface_access_routes,
)
from app.model_manager import ModelManager


def _token(character: str = "a") -> str:
    return "hf_" + character * 32


def _settings(tmp_path: Path, catalog: dict | None = None):
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(catalog or {}), encoding="utf-8")
    return SimpleNamespace(models_file=models_file, api_key=None)


def _client_factory(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport, follow_redirects=True)


def _manager_settings(tmp_path: Path, catalog: dict) -> Settings:
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps(catalog), encoding="utf-8")
    return Settings(
        models_file=models_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
    )


def test_store_masks_token_and_never_returns_raw_value(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    secret = _token("a")
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(secret)

    status = store.status()

    assert store.get_token() == secret
    assert status["configured"] is True
    assert status["token_masked"] == "••••••••"
    assert secret not in json.dumps(status)
    if os.name != "nt":
        assert status["persistence"] == "memory_only"
        assert not store.token_path.exists()


def test_remove_clears_token_metadata_and_status(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(_token("b"))
    store.write_metadata(
        {"state": "connected", "username": "quinn", "last_checked": 123}
    )

    assert store.remove() is True

    status = store.status()
    assert status["configured"] is False
    assert status["status"] == "not_configured"
    assert status["username"] is None
    assert status["last_checked"] is None
    assert not store.metadata_path.exists()


def test_environment_token_is_read_only_fallback(tmp_path, monkeypatch):
    environment_token = _token("c")
    monkeypatch.setenv("HF_TOKEN", environment_token)
    store = HuggingFaceCredentialStore(_settings(tmp_path))

    status = store.status()

    assert store.get_token() == environment_token
    assert status["source"] == "environment"
    assert status["removable"] is False


def test_known_gated_preflight_stops_before_network_without_token(tmp_path, monkeypatch):
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
    assert result["access_type"] == "gated"
    assert calls == []


def test_public_model_is_probed_before_conversion(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(206, content=b"{")

    service = HuggingFaceAccessService(
        HuggingFaceCredentialStore(_settings(tmp_path)),
        client_factory=_client_factory(handler),
    )

    result = asyncio.run(
        service.preflight("Qwen/Qwen2.5-0.5B-Instruct", access_type="public")
    )

    assert result["code"] == "hf_access_granted"
    assert calls == ["/Qwen/Qwen2.5-0.5B-Instruct/resolve/main/config.json"]


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

    result = asyncio.run(
        service.preflight("publisher/custom-gated-model", access_type="unknown")
    )

    assert result["code"] == "hf_token_missing"
    assert result["access_type"] == "gated"
    assert calls == ["/publisher/custom-gated-model/resolve/main/config.json"]


def test_valid_token_and_model_access_are_verified_without_exposing_token(tmp_path):
    token = _token("d")

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
    token = _token("e")

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


def test_missing_model_is_not_misclassified_as_approval_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)

    def handler(_request: httpx.Request):
        return httpx.Response(404)

    service = HuggingFaceAccessService(
        HuggingFaceCredentialStore(_settings(tmp_path)),
        client_factory=_client_factory(handler),
    )

    result = asyncio.run(service.preflight("owner/missing-model", access_type="unknown"))

    assert result["code"] == "hf_model_not_found"
    assert result["action"] == "review_model_id"


def test_invalid_replacement_preserves_existing_token_and_status(tmp_path):
    old_token = _token("f")
    replacement = _token("g")

    def handler(_request: httpx.Request):
        return httpx.Response(401)

    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(old_token)
    store.write_metadata(
        {"state": "connected", "username": "existing-user", "last_checked": 123}
    )
    service = HuggingFaceAccessService(store, client_factory=_client_factory(handler))

    result = asyncio.run(service.test_token(replacement, persist=True))

    assert result["code"] == "hf_token_invalid"
    assert store.get_token() == old_token
    assert store.status()["status"] == "connected"
    assert store.status()["username"] == "existing-user"


def test_preflight_middleware_blocks_before_conversion_is_scheduled(tmp_path):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    app.state.manager = SimpleNamespace(
        catalog={
            "llama": SimpleNamespace(
                source_model="meta-llama/Llama-3.2-1B-Instruct"
            )
        },
        _hf_access_metadata={
            "llama": {
                "access_type": "gated",
                "model_url": "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct",
                "license_url": "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct",
            }
        },
    )

    class BlockedService:
        async def preflight(self, source_model, *, access_type):
            assert access_type == "gated"
            return {
                "code": "hf_token_missing",
                "message": "Configure a token.",
                "recoverable": True,
                "token_configured": False,
                "source_model": source_model,
                "access_type": "gated",
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
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert scheduled == []


def test_custom_request_cannot_spoof_public_access_type(tmp_path):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
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
            "access_type": "public",
        },
    )

    assert response.status_code == 409
    assert observed == [("publisher/custom-model", "unknown")]
    assert scheduled == []


def test_preflight_middleware_replays_approved_request_body(tmp_path):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    app.state.manager = SimpleNamespace(
        catalog={"public": SimpleNamespace(source_model="Qwen/Qwen2.5-0.5B-Instruct")},
        _hf_access_metadata={
            "public": {
                "access_type": "public",
                "model_url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct",
                "license_url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct",
            }
        },
    )

    class ApprovedService:
        async def preflight(self, _source_model, *, access_type):
            assert access_type == "public"
            return {"code": "hf_access_granted"}

    app.state.huggingface_access_service = ApprovedService()

    @app.post("/v1/models/convert")
    async def convert(request: Request):
        return await request.json()

    register_huggingface_access_routes(app)
    body = {
        "model": "public",
        "device": "CPU",
        "load_after": True,
        "weight_format": "int4",
        "group_size": 128,
    }
    response = TestClient(app).post("/v1/models/convert", json=body)

    assert response.status_code == 200
    assert response.json() == body


def test_status_route_is_no_store_and_never_returns_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    secret = _token("h")
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store.set_token(secret)
    store.write_metadata(
        {"state": "connected", "username": "quinn", "last_checked": 123}
    )

    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    app.state.manager = SimpleNamespace(
        catalog={},
        _hf_access_metadata={},
        _hf_credential_store=store,
    )
    register_huggingface_access_routes(app)

    response = TestClient(app).get("/v1/huggingface/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["token_masked"] == "••••••••"
    assert secret not in response.text


def test_converter_environment_injects_token_without_mutating_base_environment():
    from app import huggingface_access

    secret = _token("i")
    base = {"PYTHONIOENCODING": "utf-8:replace"}
    context_token = huggingface_access._TOKEN_CONTEXT.set(secret)
    try:
        result = _converter_environment(base)
    finally:
        huggingface_access._TOKEN_CONTEXT.reset(context_token)

    assert result is not base
    assert base == {"PYTHONIOENCODING": "utf-8:replace"}
    assert result["HF_TOKEN"] == secret
    assert result["HUGGING_FACE_HUB_TOKEN"] == secret


def test_manager_entries_expose_structured_gated_metadata(tmp_path):
    settings = _manager_settings(
        tmp_path,
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
        },
    )
    manager = ModelManager(settings)

    entry = manager.catalog_entry("llama")

    assert entry["is_gated"] is True
    assert entry["huggingface_access"]["access_type"] == "gated"
    assert entry["huggingface_access"]["license_url"].startswith(
        "https://huggingface.co/"
    )


def test_huggingface_tokens_are_removed_from_errors_progress_and_diagnostics(tmp_path):
    secret = _token("j")
    message = errors.format_model_convert_error(
        RuntimeError(f"GatedRepoError: token={secret} unauthorized")
    )
    assert secret not in message
    assert "Settings > Hugging Face access" in message

    manager = ModelManager(_manager_settings(tmp_path, {}))
    assert secret not in manager._sanitize_progress_line(f"token={secret}")
    assert secret not in sanitize_text(f"HF_TOKEN={secret}")
