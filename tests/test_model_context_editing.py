"""Per-model context editing for any registered model, loaded or not.

Covers the shared editor's backend: the all-models list, setting context on an
unloaded model, choosing context at load time, validation errors, persistence,
the loaded-model rebuild path, embedding refusal and Connect a tool limits.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import context_configuration
from app.config import Settings
from app.model_registry import load_catalog
from app.server import create_app
from runtime.openvino_engine import MockEngine

MODEL = "context-edit-model"
EMBED = "context-edit-embeddings"


def _settings(tmp_path: Path, *, metadata: dict | None = None) -> Settings:
    models = tmp_path / "models"
    model_dir = models / MODEL
    model_dir.mkdir(parents=True)
    if metadata is not None:
        (model_dir / "config.json").write_text(json.dumps(metadata), encoding="utf-8")
    embed_dir = models / EMBED
    embed_dir.mkdir(parents=True)
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL: {
                    "name": "Context edit model",
                    "model_path": str(model_dir),
                    "source_model": "org/test-model",
                    "backend": "openvino-genai",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 8192,
                    "max_output_tokens": 512,
                },
                EMBED: {
                    "name": "Embedding model",
                    "model_path": str(embed_dir),
                    "source_model": "org/embed",
                    "backend": "openvino-embeddings",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 512,
                    "max_output_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog_file,
        models_dir=models,
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        device="CPU",
        force_mock=True,
    )


ARCH = {
    "max_position_embeddings": 32768,
    "num_hidden_layers": 4,
    "num_key_value_heads": 2,
    "num_attention_heads": 8,
    "hidden_size": 512,
}


def _set(client: TestClient, tokens: object, expected: int = 8192, model: str = MODEL):
    return client.post(
        "/v1/models/context",
        json={"model": model, "context_tokens": tokens, "expected_context_tokens": expected},
    )


def test_presets_are_capped_at_model_max_and_flag_trials(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    cfg = load_catalog(settings.models_file)[MODEL]
    metadata = context_configuration.model_metadata(cfg, tmp_path)
    presets = context_configuration.context_presets(cfg, metadata)
    assert [row["tokens"] for row in presets] == [2048, 4096, 8192, 16384, 32768]
    assert [row["trial"] for row in presets] == [False, False, False, True, True]
    assert [row["recommended"] for row in presets] == [False, False, True, False, False]
    assert context_configuration.context_bounds(cfg, metadata) == (577, 32768)


def test_list_reports_every_model_without_loading(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/v1/models/contexts")
        assert response.status_code == 200
        rows = {row["model_id"]: row for row in response.json()["data"]}
        assert app.state.manager.engines == {}
    model = rows[MODEL]
    assert model["configured_context_tokens"] == 8192
    assert model["model_architecture_limit_tokens"] == 32768
    assert model["maximum_context_tokens"] == 32768
    assert model["configurable"] is True
    assert model["loaded"] is False
    assert model["applies"] == "next-load"
    assert model["trial"] is False
    embed = rows[EMBED]
    assert embed["configurable"] is False
    assert embed["configured_context_tokens"] is None


def test_set_context_for_unloaded_model_persists_and_reaches_connect_a_tool(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        details = client.get("/v1/models/context", params={"model": MODEL}).json()
        assert details["loaded"] is False
        assert details["applies"] == "next-load"
        assert details["maximum_context_tokens"] == 32768
        assert [row["tokens"] for row in details["presets"]][-1] == 32768
        # A custom (non-preset) value is accepted when it passes validation.
        applied = _set(client, 20000)
        assert applied.status_code == 200, applied.text
        assert applied.json()["context"]["configured_context_tokens"] == 20000
        assert MODEL not in manager.engines
        listed = {row["model_id"]: row for row in client.get("/v1/models/contexts").json()["data"]}
        assert listed[MODEL]["configured_context_tokens"] == 20000
        assert listed[MODEL]["trial"] is True
        hub = client.get("/internal/connection-hub", headers={"X-OV-LLM-UI": "1"})
        assert hub.status_code == 200, hub.text
        hub_model = next(row for row in hub.json()["models"] if row["id"] == MODEL)
        assert hub_model["context_tokens"] == 20000
        assert hub_model["max_output_tokens"] == 512

    restored = load_catalog(settings.models_file)[MODEL]
    assert restored.max_context_len == 20000
    assert restored.recommended_context_len == 8192


def test_validation_errors_are_friendly_and_nothing_is_persisted(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        too_big = _set(client, 65536)
        assert too_big.status_code == 400
        assert "architecture limit" in too_big.json()["detail"]
        too_small = _set(client, 512)
        assert too_small.status_code == 400
        assert "64 prompt tokens" in too_small.json()["detail"]
        assert _set(client, 100).status_code == 422
        assert _set(client, "4096").status_code == 422
        stale = _set(client, 4096, expected=2048)
        assert stale.status_code == 409
        unknown = _set(client, 4096, model="missing-model")
        assert unknown.status_code == 404
    assert load_catalog(settings.models_file)[MODEL].max_context_len == 8192


def test_missing_metadata_never_allows_above_recommended(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        details = client.get("/v1/models/context", params={"model": MODEL}).json()
        assert details["maximum_context_tokens"] == 8192
        assert all(not row["trial"] for row in details["presets"])
        assert _set(client, 16384).status_code == 400
        assert _set(client, 4096).status_code == 200


def test_embedding_models_are_refused(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        refused = _set(client, 1024, expected=512, model=EMBED)
        assert refused.status_code == 400
        assert "Embedding" in refused.json()["detail"]
        load = client.post("/v1/models/load", json={"model": EMBED, "context_tokens": 1024})
        assert load.status_code == 400


def test_apply_to_loaded_model_uses_rebuild_path(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    built: list[dict] = []

    def build(**kwargs):
        built.append(kwargs)
        return MockEngine(MODEL, device="CPU")

    monkeypatch.setattr("app.model_manager.create_engine", build)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "CPU"
        assert client.get("/v1/models/context", params={"model": MODEL}).json()["applies"] == "now"
        applied = _set(client, 16384)
        assert applied.status_code == 200, applied.text
        assert built and built[0]["max_prompt_len"] == 16384 - 512
        assert manager.engines[MODEL] is not old
        assert manager.catalog[MODEL].max_context_len == 16384


def test_load_with_context_persists_before_loading(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/v1/models/load", json={"model": MODEL, "context_tokens": 4096})
        assert response.status_code == 200, response.text
        assert app.state.manager.catalog[MODEL].max_context_len == 4096
    assert load_catalog(settings.models_file)[MODEL].max_context_len == 4096


def test_load_with_invalid_context_does_not_load_or_persist(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/v1/models/load", json={"model": MODEL, "context_tokens": 65536})
        assert response.status_code == 400
        assert "architecture limit" in response.json()["detail"]
        assert MODEL not in app.state.manager.load_tasks
        bad_type = client.post("/v1/models/load", json={"model": MODEL, "context_tokens": 4096.5})
        assert bad_type.status_code == 422
    assert load_catalog(settings.models_file)[MODEL].max_context_len == 8192


def test_load_with_context_on_loaded_model_points_to_apply_now(tmp_path) -> None:
    settings = _settings(tmp_path, metadata=ARCH)
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        manager.engines[MODEL] = MockEngine(MODEL, device="CPU")
        manager.devices[MODEL] = "CPU"
        same = client.post("/v1/models/load", json={"model": MODEL, "context_tokens": 8192})
        assert same.status_code == 200
        different = client.post("/v1/models/load", json={"model": MODEL, "context_tokens": 4096})
        assert different.status_code == 409
        assert "Apply now" in different.json()["detail"]
        assert manager.catalog[MODEL].max_context_len == 8192
