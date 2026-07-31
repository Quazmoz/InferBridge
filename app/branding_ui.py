"""Apply the shared InferBridge product identity to the browser client."""

from __future__ import annotations

from base64 import b64encode
from pathlib import Path

from app import ui_extension
from app.brand import APPLICATION_DESCRIPTION, APPLICATION_TAGLINE, DISPLAY_NAME

_EXTENSION_ID = "ovllm-branding-extension"
_FAVICON_ID = "ovllm-brand-favicon"
_ROOT = Path(__file__).resolve().parents[1]


def _load_brand_icon_data_uri() -> str:
    """Return the bundled SVG as a self-contained browser-safe data URI."""

    icon_bytes = (_ROOT / "web" / "app-icon.svg").read_bytes()
    encoded = b64encode(icon_bytes).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


BRAND_ICON_DATA_URI = _load_brand_icon_data_uri()

BRANDING_CSS = r"""
.ovllm-brand-icon {
    display: block;
    width: 100%;
    height: 100%;
    border-radius: inherit;
}

.logo-icon.has-brand-icon {
    padding: 0;
    overflow: hidden;
    background: transparent;
}
"""

BRANDING_JS = rf"""(() => {{
'use strict';
if (window.__ovllmBrandingInstalled) return;
window.__ovllmBrandingInstalled = true;

document.title = {DISPLAY_NAME!r};
const description = document.querySelector('meta[name="description"]');
if (description) description.setAttribute('content', {APPLICATION_DESCRIPTION!r});
const productName = document.querySelector('.logo-text');
if (productName) productName.textContent = {DISPLAY_NAME!r};
const tagline = document.querySelector('.logo-sub');
if (tagline) tagline.textContent = {APPLICATION_TAGLINE!r};

const logoContainer = document.querySelector('.logo-icon');
if (!logoContainer || logoContainer.querySelector('.ovllm-brand-icon')) return;

const icon = document.createElement('img');
icon.className = 'ovllm-brand-icon';
icon.src = {BRAND_ICON_DATA_URI!r};
icon.alt = '';
icon.width = 64;
icon.height = 64;
icon.decoding = 'async';
logoContainer.classList.add('has-brand-icon');
logoContainer.replaceChildren(icon);
}})();
"""


def _apply_static_branding(html: str) -> str:
    """Replace legacy static shell labels before the first browser paint."""

    html = html.replace("<title>OpenVINO LLM</title>", f"<title>{DISPLAY_NAME}</title>", 1)
    html = html.replace(
        'content="Local AI chat powered by OpenVINO GenAI with an OpenAI-compatible API."',
        f'content="{APPLICATION_DESCRIPTION}"',
        1,
    )
    html = html.replace(">OpenVINO LLM<", f">{DISPLAY_NAME}<")
    html = html.replace(">OpenVINO GenAI<", f">{APPLICATION_TAGLINE}<")
    html = html.replace(">Local &bull; Private &bull; Intel<", f">{APPLICATION_TAGLINE}<")
    return html


def install_branding_extension() -> None:
    """Inject InferBridge metadata, favicon, and header branding exactly once."""

    if getattr(ui_extension, "_BRANDING_EXTENSION_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_branding(html: str) -> str:
        html = _apply_static_branding(previous_inject(html))
        if f'id="{_FAVICON_ID}"' not in html:
            favicon = (
                f'\n<link id="{_FAVICON_ID}" rel="icon" type="image/svg+xml" '
                f'href="{BRAND_ICON_DATA_URI}">\n'
            )
            if "</head>" in html:
                html = html.replace("</head>", f"{favicon}</head>", 1)
            else:
                html = favicon + html
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        payload = (
            f'\n<style id="{_EXTENSION_ID}-styles">\n{BRANDING_CSS}\n</style>\n'
            f'<script id="{_EXTENSION_ID}">\n{BRANDING_JS}\n</script>\n'
        )
        if "</body>" in html:
            return html.replace("</body>", f"{payload}</body>", 1)
        return html + payload

    ui_extension.inject_multimodal_ui = inject_with_branding
    ui_extension._BRANDING_EXTENSION_INSTALLED = True
