from app import ui_extension
from app.branding_ui import BRAND_ICON_DATA_URI, install_branding_extension


def test_branding_extension_injects_favicon_and_header_asset_once(monkeypatch):
    monkeypatch.setattr(ui_extension, "inject_multimodal_ui", lambda html: html)
    monkeypatch.delattr(ui_extension, "_BRANDING_EXTENSION_INSTALLED", raising=False)

    install_branding_extension()
    html = '<html><head></head><body><div class="logo-icon"><svg></svg></div></body></html>'
    branded = ui_extension.inject_multimodal_ui(html)

    assert BRAND_ICON_DATA_URI.startswith("data:image/svg+xml;base64,")
    assert branded.count('id="ovllm-brand-favicon"') == 1
    assert branded.count('id="ovllm-branding-extension"') == 1
    assert branded.count(f'href="{BRAND_ICON_DATA_URI}"') == 1
    assert f"icon.src = {BRAND_ICON_DATA_URI!r}" in branded
    assert "/app-icon.svg" not in branded
    assert "logoContainer.replaceChildren(icon)" in branded
    assert ui_extension.inject_multimodal_ui(branded) == branded
