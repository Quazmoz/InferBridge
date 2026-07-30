from pathlib import Path

import pytest

from app.paths import resolve_runtime_paths


def _env(root: Path) -> dict[str, str]:
    return {"LOCALAPPDATA": str(root)}


def test_clean_install_uses_inferbridge_data_root(tmp_path):
    paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
    assert paths.data_root == (tmp_path / "InferBridge").resolve()


def test_legacy_upgrade_keeps_existing_data_root(tmp_path):
    legacy = tmp_path / "OpenVINOWindowsLLM"
    legacy.mkdir()
    (legacy / "models").mkdir()
    paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
    assert paths.data_root == legacy.resolve()


def test_existing_new_root_is_preferred(tmp_path):
    current = tmp_path / "InferBridge"
    current.mkdir()
    legacy = tmp_path / "OpenVINOWindowsLLM"
    legacy.mkdir()
    paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
    assert paths.data_root == current.resolve()


def test_two_populated_roots_choose_new_without_merging(tmp_path):
    current = tmp_path / "InferBridge"
    legacy = tmp_path / "OpenVINOWindowsLLM"
    current.mkdir()
    legacy.mkdir()
    (current / "current.txt").write_text("current", encoding="utf-8")
    (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="without moving or merging"):
        paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
    assert paths.data_root == current.resolve()
    assert (legacy / "legacy.txt").is_file()


def test_explicit_override_remains_highest_priority(tmp_path):
    override = tmp_path / "custom"
    paths = resolve_runtime_paths(
        desktop=True,
        portable=False,
        env={"LOCALAPPDATA": str(tmp_path), "OV_LLM_DATA_DIR": str(override)},
    )
    assert paths.data_root == override.resolve()


def test_portable_mode_remains_sibling_data(monkeypatch, tmp_path):
    monkeypatch.setattr("app.paths.executable_dir", lambda: tmp_path)
    paths = resolve_runtime_paths(desktop=True, portable=True, env={})
    assert paths.data_root == (tmp_path / "data").resolve()
