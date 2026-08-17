"""Browser-side masking for packaged API keys managed by InferBridge."""

from __future__ import annotations

from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-desktop-browser-auth-extension"

DESKTOP_BROWSER_AUTH_JS = r"""
(() => {
'use strict';
if (window.__ovllmDesktopBrowserAuthInstalled) return;
window.__ovllmDesktopBrowserAuthInstalled = true;

const NETWORK_PATH = '/internal/desktop-network';
const LEGACY_KEY = 'ovllm.apikey.v1';
const SESSION_KEY = 'inferbridge.desktop.apikey.session.v1';
const PENDING_KEY = 'inferbridge.desktop.apikey.pending.v1';
const MASK = '••••••••';

function effectiveStatus(body) {
  if (body?.status?.active_bind_host) return body.status;
  return body?.active_bind_host ? body : null;
}
function sync(body, request = null) {
  const current = effectiveStatus(body);
  if (!current) return;
  const field = document.getElementById('settings-api-key');
  if (!field) return;
  const managed = Boolean(
    current.api_key_configured &&
    (current.api_key_source === 'secure_store' || current.api_key_source === 'environment')
  );
  if (managed) {
    try { localStorage.removeItem(LEGACY_KEY); } catch {}
    // A fresh document already has the per-process HttpOnly desktop UI cookie. At that
    // point the real key no longer needs to remain in browser storage.
    if (request?.method === 'GET') {
      try {
        sessionStorage.removeItem(SESSION_KEY);
        sessionStorage.removeItem(PENDING_KEY);
      } catch {}
    }
    field.value = MASK;
    field.disabled = true;
    field.title = 'API authentication is managed securely by InferBridge desktop settings.';
    return;
  }
  if (field.disabled && field.title.includes('managed securely by InferBridge')) {
    field.disabled = false;
    if (field.value === MASK) field.value = '';
    field.title = '';
  }
}

if (window.InferBridge?.observe) {
  window.InferBridge.observe(
    request => request.path === NETWORK_PATH,
    (body, request) => sync(body, request),
  );
}

fetch(NETWORK_PATH, {headers:{'X-OV-LLM-UI':'1'}})
  .then(response => response.ok ? response.json() : null)
  .then(body => sync(body, {method:'GET'}))
  .catch(() => {});
})();
"""

EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=DESKTOP_BROWSER_AUTH_JS,
    capability="desktop",
    description="Masks server-managed desktop API keys and relies on the HttpOnly UI bridge.",
)

__all__ = ["DESKTOP_BROWSER_AUTH_JS", "EXTENSION"]
