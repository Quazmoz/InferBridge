"""Compose the vision, hardware-advisor, model-lifecycle, and release browser extensions."""

from __future__ import annotations

from app import ui_extension_vision as _vision
from app.advisor_ui import ADVISOR_EXTENSION_JS
from app.model_lifecycle_ui import MODEL_LIFECYCLE_EXTENSION_JS
from app.release_ui import RELEASE_EXTENSION_JS
from app.ui_extension_vision import VISION_EXTENSION_JS, inject_multimodal_ui as _inject_vision_ui

__all__ = ["MODEL_LIFECYCLE_EXTENSION_JS", "VISION_EXTENSION_JS", "inject_multimodal_ui"]

_ADVISOR_EXTENSION_ID = "ovllm-hardware-advisor-extension"
_MODEL_LIFECYCLE_EXTENSION_ID = "ovllm-model-lifecycle-extension"
_RELEASE_EXTENSION_ID = "ovllm-release-extension"


def _inject_script(html: str, extension_id: str, javascript: str) -> str:
    if f'id="{extension_id}"' in html:
        return html
    script = f'\n<script id="{extension_id}">\n{javascript}\n</script>\n'
    if "</body>" in html:
        return html.replace("</body>", f"{script}</body>", 1)
    return html + script


def inject_multimodal_ui(html: str) -> str:
    """Inject browser extensions exactly once without changing the base frontend stack."""

    html = _inject_vision_ui(html)
    html = _inject_script(html, _ADVISOR_EXTENSION_ID, ADVISOR_EXTENSION_JS)
    html = _inject_script(
        html,
        _MODEL_LIFECYCLE_EXTENSION_ID,
        MODEL_LIFECYCLE_EXTENSION_JS,
    )
    return _inject_script(html, _RELEASE_EXTENSION_ID, RELEASE_EXTENSION_JS)


def __getattr__(name: str):
    """Forward legacy attributes to the original vision extension module."""

    return getattr(_vision, name)
