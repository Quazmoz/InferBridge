from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app import model_library_service as service_module
from app.config import Settings
from app.model_library import ConvertedModelImportRequest, ModelLibraryService
from app.model_manager import ModelManager


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


def test_duplicate_concurrent_import_does_not_delete_winning_model(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = ModelManager(settings)
    service = ModelLibraryService(settings, manager)
    source = tmp_path / "converted-source"
    source.mkdir()
    (source / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    (source / "openvino_model.bin").write_bytes(b"openvino")
    (source / "config.json").write_text("{}", encoding="utf-8")

    barrier = Barrier(2)
    original_copytree = service_module.shutil.copytree

    def synchronized_copytree(*args, **kwargs):
        result = original_copytree(*args, **kwargs)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service_module.shutil, "copytree", synchronized_copytree)
    request = ConvertedModelImportRequest(
        model_id="concurrent-model",
        name="Concurrent Model",
        source_path=str(source.resolve()),
        backend="openvino-genai",
        weight_format="fp16",
        recommended_device="CPU",
        max_context_len=2048,
        max_output_tokens=512,
    )

    def import_once():
        try:
            return "success", service.import_converted(request)
        except ValueError as exc:
            return "conflict", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: import_once(), range(2)))

    assert sorted(status for status, _result in results) == ["conflict", "success"]
    conflict = next(result for status, result in results if status == "conflict")
    assert "already registered" in conflict or "already exists" in conflict
    target = settings.models_dir / "concurrent-model"
    assert (target / "openvino_model.xml").is_file()
    assert (target / "openvino_model.bin").is_file()
    assert (target / "config.json").is_file()
    assert "concurrent-model" in manager.catalog
