from pathlib import Path

from app.brand import DISPLAY_NAME
from app.config import Settings
from app.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_fastapi_and_static_browser_use_inferbridge():
    app = create_app(Settings(force_mock=True))
    assert app.title == DISPLAY_NAME
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "<title>InferBridge</title>" in html
    assert ">OpenVINO LLM<" not in html


def test_current_public_surfaces_do_not_use_legacy_display_name():
    paths = [
        "app/desktop_launcher.py",
        "app/tray_support.py",
        "app/tray_menu.py",
        "app/server.py",
        "app/release_ui.py",
        "app/onboarding_ui.py",
        "app/ui_quality.py",
        "app/ui_polish.py",
        "packaging/runtime_hook.py",
        "web/index.html",
    ]
    for relative in paths:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "OpenVINO Windows LLM" not in text, relative


def test_onboarding_does_not_report_an_obsolete_version():
    text = (ROOT / "app" / "onboarding_ui.py").read_text(encoding="utf-8")
    assert "Version 0.3.0" not in text
    assert "__version__" in text


def test_diagnostics_use_inferbridge_identity():
    text = (ROOT / "app" / "diagnostics.py").read_text(encoding="utf-8")
    assert "inferbridge-diagnostics-" in text
    assert '"application_name": DISPLAY_NAME' in text
