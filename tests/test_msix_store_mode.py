"""Microsoft Store (MSIX) builds must leave updates and autostart to Windows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.msix import is_packaged
from app.release_routes import STORE_UPDATE_MESSAGE, register_release_routes
from app.startup_registration import RUN_KEY, MemoryRegistryBackend, StartupRegistration

LOCAL_UI = {"X-OV-LLM-UI": "1"}


def test_source_and_test_processes_have_no_package_identity():
    assert is_packaged() is False


def test_packaged_build_never_writes_the_run_key(tmp_path):
    backend = MemoryRegistryBackend()
    registration = StartupRegistration(
        executable=tmp_path / "InferBridge.exe", packaged=True, backend=backend
    )

    with pytest.raises(RuntimeError, match="Settings > Apps > Startup"):
        registration.set_enabled(True)
    assert registration.state().enabled is False
    assert (RUN_KEY, "InferBridge") not in backend.values


@pytest.fixture
def store_client(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    app = FastAPI()
    paths = SimpleNamespace(config_dir=config_dir, portable=False, resource_root=tmp_path)
    register_release_routes(app, paths=paths, packaged=True)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def test_store_build_reports_store_mode_and_disables_github_update_checks(store_client):
    status = store_client.get("/desktop/release/status").json()
    assert status["installation_mode"] == "store"
    assert status["update_checks"]["enabled"] is False

    result = store_client.post("/desktop/release/check", headers=LOCAL_UI, json={}).json()
    assert result["status"] == "disabled"
    assert result["message"] == STORE_UPDATE_MESSAGE

    response = store_client.put(
        "/desktop/release/settings", headers=LOCAL_UI, json={"enabled": True, "channel": "stable"}
    )
    assert response.status_code == 409
