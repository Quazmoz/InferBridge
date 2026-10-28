"""Per-model context validation, persistence, handoff and API regressions."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import context_configuration
from app.config import Settings
from app.model_registry import load_catalog
from app.server import create_app
from runtime.openvino_engine import MockEngine, build_plugin_config

MODEL = "context-test-model"


def _settings(tmp_path: Path, *, metadata: dict | None = None) -> Settings:
    model_dir = tmp_path / "models" / MODEL
    model_dir.mkdir(parents=True)
    if metadata is not None:
        (model_dir / "config.json").write_text(json.dumps(metadata), encoding="utf-8")
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL: {
                    "name": "Context test model",
                    "model_path": str(model_dir),
                    "source_model": "org/test-model",
                    "backend": "openvino-genai",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 2048,
                    "max_output_tokens": 512,
                }
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        device="CPU",
        force_mock=True,
    )


def _request(tokens: object, *, expected: int = 2048) -> dict:
    return {"model": MODEL, "context_tokens": tokens, "expected_context_tokens": expected}


def test_architecture_bounds_and_kv_cache_estimate(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        metadata={
            "max_position_embeddings": 8192,
            "num_hidden_layers": 4,
            "num_key_value_heads": 2,
            "num_attention_heads": 8,
            "hidden_size": 512,
        },
    )
    cfg = load_catalog(settings.models_file)[MODEL]
    metadata = context_configuration.model_metadata(cfg, tmp_path)
    assert context_configuration.model_limit(metadata) == 8192
    assert context_configuration.kv_cache_gib(metadata, 8192) == pytest.approx(0.016, abs=0.001)
    assert any(
        row["tokens"] == 8192 for row in context_configuration.context_options(cfg, metadata)
    )
    with pytest.raises(ValueError, match="architecture limit"):
        context_configuration.validate_context(cfg, 16384, metadata)
    with pytest.raises(ValueError, match="whole number"):
        context_configuration.validate_context(cfg, True, metadata)


def test_missing_or_corrupt_metadata_never_authorizes_higher_context(tmp_path) -> None:
    settings = _settings(tmp_path)
    cfg = load_catalog(settings.models_file)[MODEL]
    with pytest.raises(ValueError, match="without verified"):
        context_configuration.validate_context(cfg, 4096, {})
    assert context_configuration.kv_cache_gib({}, 2048) is None
    model_path = cfg.abs_path(tmp_path)
    (model_path / "config.json").write_text("{broken", encoding="utf-8")
    assert context_configuration.model_metadata(cfg, tmp_path) == {}


def test_legacy_context_above_architecture_is_visible_but_not_reapplicable(tmp_path) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 1024})
    cfg = load_catalog(settings.models_file)[MODEL]
    choices = context_configuration.context_options(
        cfg, context_configuration.model_metadata(cfg, tmp_path)
    )
    legacy = next(option for option in choices if option["tokens"] == 2048)
    assert legacy["classification"] == "existing-unverified"
    assert not any(option["classification"] == "recommended" for option in choices)
    with pytest.raises(context_configuration.ContextValidationError, match="architecture limit"):
        context_configuration.validate_context(cfg, 2048, {"max_position_embeddings": 1024})


def test_corrupt_saved_recommendation_does_not_hide_legacy_model(tmp_path) -> None:
    settings = _settings(tmp_path)
    data = json.loads(settings.models_file.read_text(encoding="utf-8"))
    data[MODEL]["recommended_context_len"] = "corrupted"
    settings.models_file.write_text(json.dumps(data), encoding="utf-8")
    cfg = load_catalog(settings.models_file)[MODEL]
    assert cfg.max_context_len == 2048
    assert cfg.recommended_context_len is None


def test_openvino_npu_context_is_prompt_plus_reserved_response() -> None:
    npu = build_plugin_config("NPU", 3584, max_response_len=512)
    assert npu["MAX_PROMPT_LEN"] == 3584
    assert npu["MIN_RESPONSE_LEN"] == 512
    assert "MAX_PROMPT_LEN" not in build_plugin_config("CPU", 3584, max_response_len=512)


def test_context_endpoint_persists_and_restores_without_chat_mutation(tmp_path) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        before = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = before
        manager.devices[MODEL] = "CPU"
        preview = client.get("/v1/models/context", params={"model": MODEL})
        assert preview.status_code == 200
        assert preview.json()["prompt_budget_tokens"] == 1536
        assert preview.json()["recommended_context_tokens"] == 2048
        assert preview.json()["architecture_evidence"] == "local config.json"

        applied = client.post("/v1/models/context", json=_request(4096))
        assert applied.status_code == 200, applied.text
        assert applied.json()["context"]["configured_context_tokens"] == 4096
        assert applied.json()["context"]["changing"] is False
        assert manager.engines[MODEL] is not before
        assert manager.config_for(MODEL).max_prompt_len == 3584
        assert (
            client.get("/v1/models/context", params={"model": MODEL}).json()[
                "recommended_context_tokens"
            ]
            == 2048
        )

        stale = client.post("/v1/models/context", json=_request(8192))
        assert stale.status_code == 409
        invalid = client.post("/v1/models/context", json=_request(16384, expected=4096))
        assert invalid.status_code == 400
        fractional = client.post("/v1/models/context", json=_request(4096.5, expected=4096))
        assert fractional.status_code == 422
        reset = client.post("/v1/models/context", json=_request(2048, expected=4096))
        assert reset.status_code == 200, reset.text
        again = client.post("/v1/models/context", json=_request(4096))
        assert again.status_code == 200, again.text

    restored = create_app(settings).state.manager
    assert restored.catalog[MODEL].max_context_len == 4096
    assert restored.catalog[MODEL].recommended_context_len == 2048


@pytest.mark.parametrize("exc_type", [RuntimeError, ValueError])
def test_failed_rebuild_does_not_replace_live_engine_or_persist(
    tmp_path, monkeypatch, exc_type
) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "CPU"

        def fail(*_args, **_kwargs):
            raise exc_type("secret hf_something_private from /private/model-path")

        monkeypatch.setattr("app.model_manager.create_engine", fail)
        result = client.post("/v1/models/context", json=_request(4096))
        assert result.status_code == 409
        assert "hf_something" not in result.text
        assert "/private/" not in result.text
        assert manager.engines[MODEL] is old
        assert load_catalog(settings.models_file)[MODEL].max_context_len == 2048


def test_out_of_memory_rebuild_is_sanitized_and_keeps_engine(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "CPU"

        def oom(*_args, **_kwargs):
            raise MemoryError("private diagnostic data and path")

        monkeypatch.setattr("app.model_manager.create_engine", oom)
        response = client.post("/v1/models/context", json=_request(4096))
        assert response.status_code == 409
        assert "private diagnostic" not in response.text
        assert manager.engines[MODEL] is old
        assert load_catalog(settings.models_file)[MODEL].max_context_len == 2048


def test_catalog_save_failure_rolls_back_compiled_replacement(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        old = MockEngine(MODEL, device="CPU")
        manager.engines[MODEL] = old
        manager.devices[MODEL] = "CPU"

        class Candidate(MockEngine):
            closed = False

            def close(self):
                self.closed = True

        candidate = Candidate(MODEL)
        monkeypatch.setattr("app.model_manager.create_engine", lambda **_kw: candidate)

        def fail_save(*_args):
            raise OSError("permission denied")

        monkeypatch.setattr("app.model_manager.registry.save_catalog", fail_save)
        response = client.post("/v1/models/context", json=_request(4096))
        assert response.status_code == 409
        assert candidate.closed
        assert manager.engines[MODEL] is old
        assert manager.catalog[MODEL].max_context_len == 2048


def test_cancelled_engine_build_cleans_candidate_and_preserves_config(tmp_path, monkeypatch):
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    manager = app.state.manager
    old = MockEngine(MODEL, device="CPU")
    manager.engines[MODEL] = old
    manager.devices[MODEL] = "CPU"
    started = threading.Event()
    release = threading.Event()

    class Candidate(MockEngine):
        closed = False

        def close(self):
            self.closed = True

    candidate = Candidate(MODEL)

    def build(**_kwargs):
        started.set()
        assert release.wait(timeout=10)
        return candidate

    monkeypatch.setattr("app.model_manager.create_engine", build)

    async def scenario():
        task = asyncio.create_task(
            manager.configure_context(MODEL, 4096, expected_context_tokens=2048)
        )
        assert await asyncio.to_thread(started.wait, 10)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert candidate.closed
    assert manager.engines[MODEL] is old
    assert manager.catalog[MODEL].max_context_len == 2048
    assert not manager._context_changes


def test_in_progress_reconfiguration_rejects_duplicate_and_unload(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    started = threading.Event()
    release = threading.Event()
    with TestClient(app) as client:
        manager = app.state.manager
        manager.engines[MODEL] = MockEngine(MODEL, device="CPU")
        manager.devices[MODEL] = "CPU"

        def slow_build(*_args, **_kwargs):
            started.set()
            assert release.wait(timeout=10)
            return MockEngine(MODEL, device="CPU")

        monkeypatch.setattr("app.model_manager.create_engine", slow_build)
        result: list = []
        thread = threading.Thread(
            target=lambda: result.append(client.post("/v1/models/context", json=_request(4096)))
        )
        thread.start()
        try:
            assert started.wait(timeout=10)
            assert client.post("/v1/models/context", json=_request(8192)).status_code == 409
            assert client.post("/v1/models/unload", json={"model": MODEL}).status_code == 409
            assert (
                client.get("/v1/models/context", params={"model": MODEL}).json()[
                    "configured_context_tokens"
                ]
                == 2048
            )
        finally:
            release.set()
            thread.join(timeout=10)
        assert not thread.is_alive()
        assert result[0].status_code == 200


def test_benchmark_matrix_and_context_changes_cannot_cross(tmp_path) -> None:
    settings = _settings(tmp_path, metadata={"max_position_embeddings": 8192})
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        manager._context_changes.add(MODEL)
        try:
            response = client.post(
                "/v1/benchmarks/run",
                json={"model": MODEL, "devices": ["CPU"], "max_tokens": 16},
            )
            assert response.status_code == 409
        finally:
            manager._context_changes.discard(MODEL)

        class BusyBenchmark:
            def locked(self):
                return True

        manager._benchmark_suite_lock = BusyBenchmark()
        response = client.post("/v1/models/context", json=_request(4096))
        assert response.status_code == 409
        assert manager.catalog[MODEL].max_context_len == 2048
