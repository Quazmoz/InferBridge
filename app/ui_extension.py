"""Base browser extensions and the stable entry point for composing the client page.

The vision, advisor, lifecycle, release, search, responsive, and generation-state payloads
declared here are the first extensions in the document, so they register with
:mod:`app.ui_registry` when this module is imported.

``inject_multimodal_ui`` is the long-standing public way to compose the page and stays
exactly that: ``html -> html``, idempotent. It is now a thin, stable dispatcher over the
registry. Previously every feature module rebound this attribute to a closure wrapping the
previous value, which made ``app.server``'s ``from app.ui_extension import
inject_multimodal_ui`` capture a half-built chain and forced late installers to reach into
``sys.modules`` to repair it. Because the name no longer changes, that whole class of
ordering hazard is gone.
"""

from __future__ import annotations

from app import ui_extension_vision as _vision, ui_registry, ui_runtime
from app.advisor_ui import ADVISOR_EXTENSION_JS
from app.generation_state_ui import GENERATION_STATE_JS
from app.hf_search_ui import HF_SEARCH_EXTENSION_JS
from app.model_lifecycle_ui import MODEL_LIFECYCLE_EXTENSION_JS
from app.release_ui import RELEASE_EXTENSION_JS
from app.responsive_ui import RESPONSIVE_EXTENSION_JS
from app.ui_extension_vision import VISION_EXTENSION_JS
from app.ui_registry import UiExtension

__all__ = [
    "ADVISOR_EXTENSION_JS",
    "BASE_EXTENSIONS",
    "GENERATION_STATE_JS",
    "HF_SEARCH_EXTENSION_JS",
    "MODEL_LIFECYCLE_EXTENSION_JS",
    "RELEASE_EXTENSION_JS",
    "RESPONSIVE_EXTENSION_JS",
    "VISION_EXTENSION_JS",
    "inject_multimodal_ui",
]

#: The extensions that open the composed document, in document order.
BASE_EXTENSIONS: tuple[UiExtension, ...] = (
    UiExtension(
        extension_id="ovllm-vision-extension",
        javascript=VISION_EXTENSION_JS,
        description="Local vision-language attachment controls for the chat composer.",
    ),
    UiExtension(
        extension_id="ovllm-hardware-advisor-extension",
        javascript=ADVISOR_EXTENSION_JS,
        description="Conservative hardware and model recommendations.",
    ),
    UiExtension(
        extension_id="ovllm-model-lifecycle-extension",
        javascript=MODEL_LIFECYCLE_EXTENSION_JS,
        description="Model load, unload, convert, and delete controls.",
    ),
    UiExtension(
        extension_id="ovllm-release-extension",
        javascript=RELEASE_EXTENSION_JS,
        description="Build and update information surfaced in settings.",
    ),
    UiExtension(
        extension_id="ovllm-hf-search-extension",
        javascript=HF_SEARCH_EXTENSION_JS,
        description="Hugging Face model search and custom registration.",
    ),
    UiExtension(
        extension_id="ovllm-responsive-extension",
        javascript=RESPONSIVE_EXTENSION_JS,
        description="Narrow-viewport layout behavior.",
    ),
    UiExtension(
        extension_id="ovllm-generation-state-extension",
        javascript=GENERATION_STATE_JS,
        description="Generation lifecycle state shared by composer controls.",
    ),
)

# The runtime is registered before anything else, and this is the line that guarantees it.
# Registry position is decided by first registration, and importing this module registers
# the base payloads; vision and the hardware advisor call InferBridge.chain() as they load,
# so the runtime has to be earlier in the document than they are, not merely listed first in
# app.ui_composition.
ui_registry.register(ui_runtime.EXTENSION)
for _extension in BASE_EXTENSIONS:
    ui_registry.register(_extension)
del _extension


def inject_multimodal_ui(html: str) -> str:
    """Return *html* with every active browser extension embedded, exactly once.

    Idempotent: composing an already-composed document returns it unchanged.
    """

    return ui_registry.render_inline(html)


def __getattr__(name: str):
    """Forward legacy attributes to the original vision extension module."""

    return getattr(_vision, name)
