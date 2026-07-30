"""Write final InferBridge public-branding and runtime-hook regression tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

(ROOT / "tests/test_inferbridge_public_branding.py").write_text(
    '''from pathlib import Path\n\nfrom app.brand import DISPLAY_NAME\nfrom app.config import Settings\nfrom app.server import create_app\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_fastapi_and_static_browser_use_inferbridge():\n    app = create_app(Settings(force_mock=True))\n    assert app.title == DISPLAY_NAME\n    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")\n    assert "<title>InferBridge</title>" in html\n    assert ">OpenVINO LLM<" not in html\n\n\ndef test_current_public_surfaces_do_not_use_legacy_display_name():\n    paths = [\n        "app/desktop_launcher.py",\n        "app/tray_support.py",\n        "app/tray_menu.py",\n        "app/server.py",\n        "app/release_ui.py",\n        "app/onboarding_ui.py",\n        "app/ui_quality.py",\n        "app/ui_polish.py",\n        "packaging/runtime_hook.py",\n        "web/index.html",\n    ]\n    for relative in paths:\n        text = (ROOT / relative).read_text(encoding="utf-8")\n        assert "OpenVINO Windows LLM" not in text, relative\n\n\ndef test_onboarding_does_not_report_an_obsolete_version():\n    text = (ROOT / "app" / "onboarding_ui.py").read_text(encoding="utf-8")\n    assert "Version 0.3.0" not in text\n    assert "__version__" in text\n\n\ndef test_diagnostics_use_inferbridge_identity():\n    text = (ROOT / "app" / "diagnostics.py").read_text(encoding="utf-8")\n    assert "inferbridge-diagnostics-" in text\n    assert '"application_name": DISPLAY_NAME' in text\n''',
    encoding="utf-8",
)

(ROOT / "tests/test_inferbridge_runtime_hook_paths.py").write_text(
    '''import runpy\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nHOOK = ROOT / "packaging" / "runtime_hook.py"\n\n\ndef _log_path(monkeypatch, local_app_data: Path) -> Path:\n    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))\n    namespace = runpy.run_path(str(HOOK))\n    return Path(namespace["_runtime_failure_log_path"]())\n\n\ndef test_runtime_hook_uses_inferbridge_for_clean_install(monkeypatch, tmp_path):\n    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "InferBridge"\n\n\ndef test_runtime_hook_preserves_legacy_data_root(monkeypatch, tmp_path):\n    (tmp_path / "OpenVINOWindowsLLM").mkdir()\n    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "OpenVINOWindowsLLM"\n\n\ndef test_runtime_hook_prefers_existing_inferbridge_root(monkeypatch, tmp_path):\n    (tmp_path / "OpenVINOWindowsLLM").mkdir()\n    (tmp_path / "InferBridge").mkdir()\n    assert _log_path(monkeypatch, tmp_path).parent.parent == tmp_path / "InferBridge"\n''',
    encoding="utf-8",
)
