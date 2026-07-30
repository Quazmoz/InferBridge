import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "packaging" / "runtime_hook.py"


def _log_path(monkeypatch, local_app_data: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    namespace = runpy.run_path(str(HOOK))
    return Path(namespace["_runtime_failure_log_path"]())


def test_runtime_hook_uses_inferbridge_for_clean_install(monkeypatch, tmp_path):
    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "InferBridge"


def test_runtime_hook_preserves_legacy_data_root(monkeypatch, tmp_path):
    (tmp_path / "OpenVINOWindowsLLM").mkdir()
    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "OpenVINOWindowsLLM"


def test_runtime_hook_prefers_existing_inferbridge_root(monkeypatch, tmp_path):
    (tmp_path / "OpenVINOWindowsLLM").mkdir()
    (tmp_path / "InferBridge").mkdir()
    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "InferBridge"
