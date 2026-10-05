from pathlib import Path

from app.config import Settings
from app.model_library import ModelDefinitionImportRequest, ModelLibraryService
from app.model_manager import ModelManager
from app.model_registry import ModelConfig, save_catalog

ROOT = Path(__file__).resolve().parents[1]


def _settings(tmp_path):
    models_file = tmp_path / "config" / "models.json"
    models_file.parent.mkdir(parents=True)
    models_file.write_text("{}\n", encoding="utf-8")
    return Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=models_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks" / "benchmarks.json",
        default_model=None,
        api_key=None,
        force_mock=True,
    )


def _definition(model_id: str, *, description: str) -> dict:
    return {
        "model_id": model_id,
        "name": "Existing Model",
        "description": description,
        "source_model": "example/existing-model",
        "backend": "openvino-genai",
        "weight_format": "fp16",
        "recommended_device": "CPU",
        "max_context_len": 2048,
        "max_output_tokens": 512,
        "trust_remote_code": False,
    }


def _existing_model(model_id: str, model_path: Path) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        name="Existing Model",
        description="Before update",
        backend="openvino-genai",
        model_path=str(model_path),
        source_model="example/existing-model",
        weight_format="fp16",
        recommended_device="CPU",
        max_context_len=2048,
        max_output_tokens=512,
        trust_remote_code=False,
    )


def test_official_definition_update_preserves_existing_model_path(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    established_path = settings.models_dir / "existing-model-instruct-fp16"
    manager.catalog["existing-model"] = _existing_model("existing-model", established_path)
    save_catalog(settings.models_file, manager.catalog)
    manager.reload_catalog()
    service = ModelLibraryService(settings, manager)

    result = service.apply_official_definitions(
        {
            "catalog": {
                "existing-model": {
                    "definition": _definition(
                        "existing-model", description="Updated official description"
                    ),
                    "metadata": {},
                }
            }
        }
    )

    assert result["updated"] == ["existing-model"]
    assert manager.catalog["existing-model"].model_path == str(established_path)


def test_definition_overwrite_preserves_existing_model_path(tmp_path):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    established_path = settings.models_dir / "custom-storage-leaf"
    manager.catalog["existing-model"] = _existing_model("existing-model", established_path)
    save_catalog(settings.models_file, manager.catalog)
    manager.reload_catalog()
    service = ModelLibraryService(settings, manager)

    result = service.import_definitions(
        ModelDefinitionImportRequest(
            payload={
                "models": {
                    "existing-model": _definition(
                        "existing-model", description="Updated imported description"
                    )
                }
            },
            overwrite=True,
        )
    )

    assert result["updated"] == ["existing-model"]
    assert manager.catalog["existing-model"].model_path == str(established_path)


def test_model_library_browser_guards_actions_and_token_budget():
    source = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")

    assert "if (!state.can_convert)" in source
    assert "No conversion source" in source
    assert "Reconvert" in source
    assert "metrics.measurement_source" in source
    assert "Bundled certification and your local benchmark results" in source
    assert "Local benchmarks describe only this PC" in source
    assert "Math.min(512, maxContext - 1)" in source
    assert "Model ID, display name, and source directory are required." in source
    assert "Import local / pre-converted OpenVINO model" in source
    assert "/v1/model-library/unregistered-managed" in source
    assert "/v1/model-library/adopt-managed" in source
    assert "imported and selected — ready to load." in source

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    chat_group = html.index('class="btn-group chat-action-group"')
    new_chat = html.index('id="new-chat-btn"', chat_group)
    export_chat = html.index('id="export-chat-btn"', chat_group)
    add_model = html.index('id="add-model-btn"', chat_group)
    assert chat_group < new_chat < export_chat < add_model
