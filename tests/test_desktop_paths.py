import json
from pathlib import Path

from app import paths as desktop_paths


def test_installed_and_portable_paths_are_writable_locations(monkeypatch, tmp_path):
    resource = tmp_path / "resources"
    executable = tmp_path / "portable"
    resource.mkdir()
    executable.mkdir()
    monkeypatch.setattr(desktop_paths, "packaged_resource_root", lambda: resource)
    monkeypatch.setattr(desktop_paths, "executable_dir", lambda: executable)

    installed = desktop_paths.resolve_runtime_paths(
        desktop=True,
        portable=False,
        env={"LOCALAPPDATA": str(tmp_path / "local")},
    )
    portable = desktop_paths.resolve_runtime_paths(desktop=True, portable=True, env={})

    assert installed.data_root == (tmp_path / "local" / "InferBridge").resolve()
    assert installed.models_file == installed.config_dir / "models.json"
    assert portable.data_root == (executable / "data").resolve()
    assert portable.models_dir == portable.data_root / "models"


def test_catalog_upgrade_preserves_existing_model_paths(monkeypatch, tmp_path):
    resource = tmp_path / "resources"
    resource.mkdir()
    (resource / "models.json").write_text(
        json.dumps(
            {
                "existing": {"name": "Existing", "model_path": "models/openvino/existing"},
                "new": {"name": "New", "model_path": "models/openvino/new"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_paths, "packaged_resource_root", lambda: resource)
    resolved = desktop_paths.resolve_runtime_paths(
        desktop=True,
        portable=False,
        env={"LOCALAPPDATA": str(tmp_path / "local")},
    )
    resolved.config_dir.mkdir(parents=True)
    existing_path = tmp_path / "legacy" / "existing"
    resolved.models_file.write_text(
        json.dumps({"existing": {"name": "Existing", "model_path": str(existing_path)}}),
        encoding="utf-8",
    )

    desktop_paths.materialize_user_catalog(resolved)
    catalog = json.loads(resolved.models_file.read_text(encoding="utf-8"))

    assert catalog["existing"]["model_path"] == str(existing_path)
    assert Path(catalog["new"]["model_path"]).parent == resolved.models_dir
