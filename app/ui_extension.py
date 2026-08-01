"""Compose the vision, advisor, lifecycle, release, search, and responsive browser extensions."""

from __future__ import annotations

from app import ui_extension_vision as _vision
from app.advisor_ui import ADVISOR_EXTENSION_JS
from app.hf_search_ui import HF_SEARCH_EXTENSION_JS
from app.model_lifecycle_ui import MODEL_LIFECYCLE_EXTENSION_JS
from app.release_ui import RELEASE_EXTENSION_JS
from app.responsive_ui import RESPONSIVE_EXTENSION_JS
from app.ui_extension_vision import VISION_EXTENSION_JS, inject_multimodal_ui as _inject_vision_ui

__all__ = [
    "HF_SEARCH_EXTENSION_JS",
    "MODEL_LIFECYCLE_EXTENSION_JS",
    "RESPONSIVE_EXTENSION_JS",
    "VISION_EXTENSION_JS",
    "inject_multimodal_ui",
]

_ADVISOR_EXTENSION_ID = "ovllm-hardware-advisor-extension"
_HF_SEARCH_EXTENSION_ID = "ovllm-hf-search-extension"
_MODEL_LIFECYCLE_EXTENSION_ID = "ovllm-model-lifecycle-extension"
_ONBOARDING_EXIT_GUARD_ID = "inferbridge-onboarding-exit-guard"
_RELEASE_EXTENSION_ID = "ovllm-release-extension"
_RESPONSIVE_EXTENSION_ID = "ovllm-responsive-extension"

_ONBOARDING_EXIT_GUARD_JS = r"""
(() => {
'use strict';
if (window.__inferbridgeOnboardingExitGuardInstalled) return;
window.__inferbridgeOnboardingExitGuardInstalled = true;
const selector = '#ovw-shell [data-action="exit"]';
const removeExitButtons = root => {
  const buttons = [];
  if (root instanceof Element && root.matches(selector)) buttons.push(root);
  if (root.querySelectorAll) buttons.push(...root.querySelectorAll(selector));
  buttons.forEach(button => button.remove());
};
removeExitButtons(document);
const observer = new MutationObserver(records => {
  for (const record of records) {
    for (const node of record.addedNodes) {
      if (node.nodeType === Node.ELEMENT_NODE) removeExitButtons(node);
    }
  }
});
if (document.body) observer.observe(document.body, { childList: true, subtree: true });
})();
"""


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
    html = _inject_script(html, _RELEASE_EXTENSION_ID, RELEASE_EXTENSION_JS)
    html = _inject_script(html, _HF_SEARCH_EXTENSION_ID, HF_SEARCH_EXTENSION_JS)
    html = _inject_script(
        html,
        _RESPONSIVE_EXTENSION_ID,
        RESPONSIVE_EXTENSION_JS,
    )
    return _inject_script(
        html,
        _ONBOARDING_EXIT_GUARD_ID,
        _ONBOARDING_EXIT_GUARD_JS,
    )


def __getattr__(name: str):
    """Forward legacy attributes to the original vision extension module."""

    return getattr(_vision, name)
