"""Low-friction feedback entry point for the browser System Doctor."""

from __future__ import annotations

import json

from app import ui_extension
from app.support import SUPPORT_URL

_EXTENSION_ID = "inferbridge-support-extension"

SUPPORT_CSS = r"""
#doctor-feedback {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    text-decoration: none;
}
"""

SUPPORT_JS_TEMPLATE = r"""(() => {
'use strict';
if (window.__inferbridgeSupportInstalled) return;
window.__inferbridgeSupportInstalled = true;
const supportUrl = __SUPPORT_URL__;
const actions = document.querySelector('#doctor-modal .doctor-actions');
const refresh = document.getElementById('doctor-refresh');
if (!actions || !refresh) return;
const feedback = document.createElement('a');
feedback.id = 'doctor-feedback';
feedback.className = 'doctor-action';
feedback.href = supportUrl;
feedback.target = '_blank';
feedback.rel = 'noopener noreferrer';
feedback.textContent = 'Send Feedback';
feedback.setAttribute('aria-label', 'Send InferBridge feedback in your browser');
actions.insertBefore(feedback, refresh);
const note = document.querySelector('#doctor-modal .doctor-footer-note');
if (note) {
    note.textContent = 'Copy the privacy-safe support report and paste it into the feedback form or a GitHub issue. Nothing is uploaded automatically.';
}
})();
"""


def install_support_ui_extension() -> None:
    """Add the website feedback link to the existing System Doctor support surface."""

    if getattr(ui_extension, "_SUPPORT_UI_EXTENSION_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_support(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = SUPPORT_JS_TEMPLATE.replace("__SUPPORT_URL__", json.dumps(SUPPORT_URL))
        payload = (
            f'\n<style id="{_EXTENSION_ID}-styles">\n{SUPPORT_CSS}\n</style>\n'
            f'<script id="{_EXTENSION_ID}">\n{script}\n</script>\n'
        )
        if "</body>" in html:
            return html.replace("</body>", f"{payload}</body>", 1)
        return html + payload

    ui_extension.inject_multimodal_ui = inject_with_support
    ui_extension._SUPPORT_UI_EXTENSION_INSTALLED = True
