from pathlib import Path

from app.brand import (
    APPLICATION_DESCRIPTION,
    APPLICATION_TAGLINE,
    DISPLAY_NAME,
    LEGACY_DISPLAY_NAME,
)
from app.branding_ui import _apply_static_branding
from app.config import Settings
from app.onboarding_ui import ONBOARDING_UI
from app.release_ui import RELEASE_EXTENSION_JS
from app.server import create_app
from app.version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_fastapi_and_static_browser_render_inferbridge():
    app = create_app(Settings(force_mock=True))
    assert app.title == DISPLAY_NAME

    source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    rendered = _apply_static_branding(source)
    assert f"<title>{DISPLAY_NAME}</title>" in rendered
    assert f">{DISPLAY_NAME}<" in rendered
    assert f">{APPLICATION_TAGLINE}<" in rendered
    assert APPLICATION_DESCRIPTION in rendered


def test_embedded_public_surfaces_render_current_identity():
    assert DISPLAY_NAME in RELEASE_EXTENSION_JS
    assert LEGACY_DISPLAY_NAME not in RELEASE_EXTENSION_JS
    assert DISPLAY_NAME in ONBOARDING_UI
    assert LEGACY_DISPLAY_NAME not in ONBOARDING_UI
    assert f"Version {__version__}" in ONBOARDING_UI
    assert "Version 0.3.0" not in ONBOARDING_UI


def test_diagnostics_use_inferbridge_identity():
    text = (ROOT / "app" / "diagnostics.py").read_text(encoding="utf-8")
    assert "inferbridge-diagnostics-" in text
    assert '"application_name": DISPLAY_NAME' in text
