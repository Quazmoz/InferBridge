"""Low-friction feedback entry point for the browser System Doctor."""

from __future__ import annotations

import json

from app.support import SUPPORT_URL
from app.ui_registry import UiExtension

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

SUPPORT_JS = SUPPORT_JS_TEMPLATE.replace("__SUPPORT_URL__", json.dumps(SUPPORT_URL))

EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=SUPPORT_JS,
    css=SUPPORT_CSS,
    description="Adds a low-friction feedback link to System Doctor without uploading diagnostics.",
)

__all__ = ["EXTENSION", "SUPPORT_CSS", "SUPPORT_JS", "SUPPORT_JS_TEMPLATE"]
