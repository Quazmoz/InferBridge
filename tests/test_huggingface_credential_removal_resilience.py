from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.huggingface_access import (
    HuggingFaceAccessService,
    HuggingFaceCredentialStore,
    register_huggingface_access_routes,
)


def _settings(tmp_path):
    models_file = tmp_path / "models.json"
    models_file.write_text("{}", encoding="utf-8")
    return SimpleNamespace(models_file=models_file, api_key=None)


def _token() -> str:
    return "hf_" + "r" * 32


def test_remove_preserves_in_memory_token_when_persisted_delete_fails(
    tmp_path, monkeypatch
):
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store._memory_token = _token()
    store.token_path.write_bytes(b"locked-token-placeholder")
    original_unlink = Path.unlink

    def blocked_unlink(path, *args, **kwargs):
        if path == store.token_path:
            raise PermissionError("simulated Windows sharing violation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", blocked_unlink)

    with pytest.raises(PermissionError, match="sharing violation"):
        store.remove()

    assert store._memory_token == _token()
    assert store.token_path.exists()


def test_remove_succeeds_when_only_non_secret_metadata_cleanup_fails(tmp_path, monkeypatch):
    store = HuggingFaceCredentialStore(_settings(tmp_path))
    store._memory_token = _token()
    store.token_path.write_bytes(b"stored-token-placeholder")
    store.metadata_path.write_text('{"state":"connected"}', encoding="utf-8")
    original_unlink = Path.unlink

    def metadata_locked(path, *args, **kwargs):
        if path == store.metadata_path:
            raise PermissionError("metadata is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", metadata_locked)

    assert store.remove() is True
    assert store._memory_token is None
    assert not store.token_path.exists()
    assert store.metadata_path.exists()


def test_remove_route_reports_secure_storage_failure_without_claiming_success(
    tmp_path, monkeypatch
):
    app = FastAPI()
    app.state.settings = _settings(tmp_path)
    store = HuggingFaceCredentialStore(app.state.settings)
    service = HuggingFaceAccessService(store)
    app.state.huggingface_access_service = service

    def blocked_remove():
        raise PermissionError("C:/private/path/huggingface-token.dpapi is locked")

    monkeypatch.setattr(store, "remove", blocked_remove)
    register_huggingface_access_routes(app)

    response = TestClient(app).delete("/v1/huggingface/token")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Stored Hugging Face token could not be removed from secure storage."
    }
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "private/path" not in response.text
