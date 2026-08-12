"""Browser UI for secure Hugging Face credentials and gated-model recovery."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "inferbridge-huggingface-access-extension"

HUGGINGFACE_ACCESS_CSS = r"""
.hf-access-card{display:grid;gap:9px;padding:12px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2)}
.hf-access-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.hf-access-head h4{margin:0;font-size:11px;color:var(--text-1)}
.hf-access-state{display:inline-flex;align-items:center;gap:5px;padding:3px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-3);font-size:9px;font-weight:750}
.hf-access-state.connected{border-color:color-mix(in srgb,var(--green) 42%,var(--border));color:var(--green)}.hf-access-state.warning{border-color:color-mix(in srgb,var(--amber) 42%,var(--border));color:var(--amber)}
.hf-access-grid{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 9px;font-size:10px}.hf-access-grid dt{color:var(--text-3)}.hf-access-grid dd{min-width:0;margin:0;overflow:hidden;text-overflow:ellipsis;color:var(--text-2);text-align:right;white-space:nowrap}
.hf-access-actions{display:flex;flex-wrap:wrap;gap:6px}.hf-access-btn{min-height:34px;padding:6px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-3);color:var(--text-1);font:inherit;font-size:9.5px;font-weight:750;cursor:pointer}.hf-access-btn:hover:not(:disabled){border-color:var(--primary)}.hf-access-btn.primary{border-color:transparent;background:var(--primary);color:#fff}.hf-access-btn:disabled{opacity:.5;cursor:not-allowed}
.hf-access-note{color:var(--text-3);font-size:9px;line-height:1.45}.hf-gated-badge{display:inline-flex;align-items:center;margin-left:6px;padding:1px 6px;border:1px solid color-mix(in srgb,var(--amber) 42%,var(--border));border-radius:999px;color:var(--amber);font-size:9px;font-weight:800;vertical-align:middle}
.hf-dialog-card{width:min(520px,calc(100vw - 24px))}.hf-dialog-body{display:grid;gap:12px;padding:16px 18px 18px}.hf-dialog-copy{color:var(--text-2);font-size:11px;line-height:1.55}.hf-dialog-model{padding:9px 10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:10px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.hf-dialog-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px}.hf-token-field{width:100%;min-height:42px;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:12px}.hf-token-field:focus{border-color:var(--primary);outline:2px solid var(--primary-glow)}.hf-dialog-error{min-height:16px;color:var(--red);font-size:10px;line-height:1.4}
@media(max-width:540px){.hf-dialog-actions{display:grid;grid-template-columns:1fr}.hf-access-actions{display:grid;grid-template-columns:1fr 1fr}.hf-access-btn{min-height:42px}}
"""

HUGGINGFACE_ACCESS_JS = r"""
(() => {
'use strict';
if (window.__inferbridgeHuggingFaceAccessInstalled) return;
window.__inferbridgeHuggingFaceAccessInstalled = true;

const apiKey = () => {
  try { return localStorage.getItem('ovllm.apikey.v1') || ''; } catch { return ''; }
};
const headers = extra => {
  const key = apiKey();
  return {...(key ? {Authorization: `Bearer ${key}`} : {}), ...(extra || {})};
};
const endpoint = input => {
  const raw = typeof input === 'string' ? input : input?.url || '';
  try {
    const url = new URL(raw, location.href);
    return {path: url.pathname, sameOrigin: url.origin === location.origin};
  } catch { return {path: '', sameOrigin: false}; }
};
const formatChecked = value => {
  const seconds = Number(value || 0);
  if (!seconds) return 'Not checked';
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - seconds);
  if (elapsed < 10) return 'Just now';
  if (elapsed < 60) return `${elapsed}s ago`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ago`;
  return new Date(seconds * 1000).toLocaleString();
};
const showToast = message => {
  if (typeof window.showToast === 'function') window.showToast(message);
};
const readJson = async response => {
  try { return await response.json(); } catch { return {}; }
};

let latestStatus = null;
let pendingAccess = null;
let pendingRetry = null;
let busy = false;

function openSettings() {
  if (typeof window.setSettingsSidebarOpen === 'function') {
    window.setSettingsSidebarOpen(true);
    return;
  }
  const sidebar = document.getElementById('settings-sidebar');
  if (sidebar?.classList.contains('closed')) {
    document.getElementById('settings-toggle-btn')?.click();
  }
}

function createSettingsCard() {
  if (document.getElementById('hf-access-card')) return;
  const sidebar = document.getElementById('settings-sidebar');
  if (!sidebar) return;
  const divider = document.createElement('div');
  divider.className = 'sidebar-divider';
  divider.dataset.hfAccess = 'divider';
  const wrap = document.createElement('section');
  wrap.className = 'setting-group';
  wrap.dataset.hfAccess = 'settings';
  wrap.innerHTML = `
    <div class="hf-access-card" id="hf-access-card">
      <div class="hf-access-head"><h4>Hugging Face access</h4><span class="hf-access-state" id="hf-access-state">Checking…</span></div>
      <dl class="hf-access-grid">
        <dt>Status</dt><dd id="hf-access-account">Checking…</dd>
        <dt>Token</dt><dd id="hf-access-token">Not configured</dd>
        <dt>Last checked</dt><dd id="hf-access-checked">Not checked</dd>
      </dl>
      <div class="hf-access-actions">
        <button type="button" class="hf-access-btn" id="hf-access-test">Test access</button>
        <button type="button" class="hf-access-btn primary" id="hf-access-replace">Add token</button>
        <button type="button" class="hf-access-btn" id="hf-access-remove">Remove</button>
      </div>
      <p class="hf-access-note" id="hf-access-note">Tokens are sent only to this local InferBridge server and are never stored in the browser.</p>
    </div>`;
  const systemInfo = [...sidebar.querySelectorAll('.setting-group')]
    .find(item => item.querySelector('h4')?.textContent?.trim() === 'System Info');
  if (systemInfo) {
    sidebar.insertBefore(divider, systemInfo);
    sidebar.insertBefore(wrap, systemInfo);
  } else {
    sidebar.append(divider, wrap);
  }
  document.getElementById('hf-access-test')?.addEventListener('click', testAccess);
  document.getElementById('hf-access-replace')?.addEventListener('click', openTokenDialog);
  document.getElementById('hf-access-remove')?.addEventListener('click', removeToken);
}

function createDialogs() {
  if (document.getElementById('hf-token-modal')) return;
  const tokenModal = document.createElement('div');
  tokenModal.id = 'hf-token-modal';
  tokenModal.className = 'modal-overlay hidden';
  tokenModal.setAttribute('aria-hidden', 'true');
  tokenModal.innerHTML = `<div class="modal-card hf-dialog-card" role="dialog" aria-modal="true" aria-labelledby="hf-token-title">
    <div class="modal-header"><h3 id="hf-token-title">Hugging Face token</h3><button type="button" class="close-btn" data-hf-close="token" aria-label="Close">&times;</button></div>
    <form class="hf-dialog-body" id="hf-token-form">
      <p class="hf-dialog-copy">Use a Hugging Face user access token with read permission. It is sent only to this local InferBridge server and is never stored in the browser.</p>
      <input class="hf-token-field" id="hf-token-input" type="password" autocomplete="off" spellcheck="false" data-1p-ignore data-lpignore="true" placeholder="hf_…" required minlength="11" maxlength="503">
      <div class="hf-dialog-error" id="hf-token-error" role="alert"></div>
      <div class="hf-dialog-actions"><button type="button" class="hf-access-btn" data-hf-close="token">Cancel</button><button type="submit" class="hf-access-btn primary" id="hf-token-save">Verify and save</button></div>
    </form></div>`;
  const accessModal = document.createElement('div');
  accessModal.id = 'hf-access-modal';
  accessModal.className = 'modal-overlay hidden';
  accessModal.setAttribute('aria-hidden', 'true');
  accessModal.innerHTML = `<div class="modal-card hf-dialog-card" role="dialog" aria-modal="true" aria-labelledby="hf-required-title">
    <div class="modal-header"><h3 id="hf-required-title">Hugging Face access check</h3><button type="button" class="close-btn" data-hf-close="access" aria-label="Close">&times;</button></div>
    <div class="hf-dialog-body">
      <p class="hf-dialog-copy" id="hf-required-message"></p>
      <div class="hf-dialog-model" id="hf-required-model"></div>
      <p class="hf-dialog-copy" id="hf-required-guidance"></p>
      <div class="hf-dialog-error" id="hf-required-error" role="alert"></div>
      <div class="hf-dialog-actions">
        <button type="button" class="hf-access-btn" id="hf-required-settings">Configure token</button>
        <button type="button" class="hf-access-btn" id="hf-required-open">Open model page</button>
        <button type="button" class="hf-access-btn primary" id="hf-required-check">Check access again</button>
      </div>
    </div></div>`;
  document.body.append(tokenModal, accessModal);
  document.querySelectorAll('[data-hf-close="token"]').forEach(button => {
    button.addEventListener('click', closeTokenDialog);
  });
  document.querySelectorAll('[data-hf-close="access"]').forEach(button => {
    button.addEventListener('click', cancelAccessDialog);
  });
  tokenModal.addEventListener('click', event => {
    if (event.target === tokenModal) closeTokenDialog();
  });
  accessModal.addEventListener('click', event => {
    if (event.target === accessModal) cancelAccessDialog();
  });
  document.getElementById('hf-token-form')?.addEventListener('submit', saveToken);
  document.getElementById('hf-required-settings')?.addEventListener('click', () => {
    closeAccessDialog();
    openSettings();
    openTokenDialog();
  });
  document.getElementById('hf-required-open')?.addEventListener('click', () => {
    const url = pendingAccess?.license_url || pendingAccess?.model_url;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  });
  document.getElementById('hf-required-check')?.addEventListener('click', checkRequiredAccess);
}

function setModal(modal, open) {
  if (!modal) return;
  modal.classList.toggle('hidden', !open);
  modal.setAttribute('aria-hidden', String(!open));
  if (open) document.body.style.overflow = 'hidden';
  else if (document.querySelectorAll('.modal-overlay:not(.hidden)').length === 0) {
    document.body.style.overflow = '';
  }
}
function openTokenDialog() {
  createDialogs();
  document.getElementById('hf-token-error').textContent = '';
  document.getElementById('hf-token-input').value = '';
  setModal(document.getElementById('hf-token-modal'), true);
  document.getElementById('hf-token-input')?.focus();
}
function closeTokenDialog() {
  const input = document.getElementById('hf-token-input');
  if (input) input.value = '';
  setModal(document.getElementById('hf-token-modal'), false);
}
function closeAccessDialog() {
  setModal(document.getElementById('hf-access-modal'), false);
}
function cancelAccessDialog() {
  closeAccessDialog();
  pendingAccess = null;
  pendingRetry = null;
}

function renderStatus(status) {
  latestStatus = status || {};
  createSettingsCard();
  const state = document.getElementById('hf-access-state');
  const account = document.getElementById('hf-access-account');
  const token = document.getElementById('hf-access-token');
  const checked = document.getElementById('hf-access-checked');
  const replace = document.getElementById('hf-access-replace');
  const remove = document.getElementById('hf-access-remove');
  const note = document.getElementById('hf-access-note');
  if (!state || !account || !token || !checked || !replace || !remove || !note) return;
  const connected = status?.configured && status?.status === 'connected';
  state.textContent = connected
    ? 'Connected'
    : status?.configured
      ? 'Needs verification'
      : 'Not configured';
  state.className = `hf-access-state ${connected ? 'connected' : 'warning'}`;
  account.textContent = connected
    ? `Connected as ${status.username || 'Hugging Face user'}`
    : status?.configured
      ? 'Token configured'
      : 'Not connected';
  token.textContent = status?.token_masked || 'Not configured';
  checked.textContent = formatChecked(status?.last_checked);
  replace.textContent = status?.configured ? 'Replace token' : 'Add token';
  remove.disabled = !status?.removable;
  remove.title = status?.source === 'environment'
    ? 'HF_TOKEN is managed through the environment'
    : 'Remove the locally stored token';
  note.textContent = status?.persistence === 'windows_dpapi'
    ? 'The token is encrypted for this Windows user with DPAPI. The browser never stores or receives it.'
    : status?.source === 'environment'
      ? 'HF_TOKEN is active as an advanced environment fallback. The browser never receives it.'
      : 'Tokens are sent only to this local InferBridge server and are never stored in the browser.';
}

async function loadStatus() {
  try {
    const response = await fetch('/v1/huggingface/status', {headers: headers()});
    if (response.ok) renderStatus(await readJson(response));
  } catch {}
}
function setBusy(value) {
  busy = value;
  ['hf-access-test', 'hf-access-replace', 'hf-access-remove', 'hf-token-save', 'hf-required-check']
    .forEach(id => {
      const button = document.getElementById(id);
      if (!button) return;
      button.disabled = value || (id === 'hf-access-remove' && !latestStatus?.removable);
    });
}
async function testAccess() {
  if (busy) return;
  setBusy(true);
  try {
    const response = await fetch('/v1/huggingface/test', {
      method: 'POST',
      headers: headers(),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.detail?.message || payload.detail || 'Access test failed.');
    }
    renderStatus(payload.status);
    showToast('Hugging Face access verified');
  } catch (error) {
    showToast(error instanceof Error ? error.message : 'Hugging Face access test failed');
    await loadStatus();
  } finally {
    setBusy(false);
  }
}
async function saveToken(event) {
  event.preventDefault();
  if (busy) return;
  const input = document.getElementById('hf-token-input');
  const error = document.getElementById('hf-token-error');
  const token = input?.value?.trim() || '';
  const shouldRecheck = !!pendingAccess;
  let saved = false;
  error.textContent = '';
  setBusy(true);
  try {
    const response = await fetch('/v1/huggingface/token', {
      method: 'POST',
      headers: headers({'Content-Type': 'application/json'}),
      body: JSON.stringify({token}),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.detail?.message || payload.detail || 'Token validation failed.');
    }
    if (input) input.value = '';
    renderStatus(payload.status);
    closeTokenDialog();
    showToast(payload.message || 'Hugging Face token saved');
    saved = true;
  } catch (problem) {
    error.textContent = problem instanceof Error ? problem.message : 'Token validation failed.';
  } finally {
    setBusy(false);
  }
  if (saved && shouldRecheck) await checkRequiredAccess();
}
async function removeToken() {
  if (busy || !latestStatus?.removable) return;
  setBusy(true);
  try {
    const response = await fetch('/v1/huggingface/token', {
      method: 'DELETE',
      headers: headers(),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.detail || 'Token removal failed.');
    renderStatus(payload.status);
    showToast(payload.message || 'Hugging Face token removed');
  } catch (error) {
    showToast(error instanceof Error ? error.message : 'Token removal failed');
  } finally {
    setBusy(false);
  }
}

function issuePresentation(detail = {}) {
  const code = String(detail.code || '');
  if (code === 'hf_approval_required') {
    return {
      title: 'Publisher approval required',
      guidance: 'InferBridge cannot accept the publisher agreement for you. Open the model page, complete approval, then check access again.',
      configure: true,
      open: true,
    };
  }
  if (code === 'hf_token_missing' || code === 'hf_token_invalid') {
    return {
      title: 'Hugging Face access required',
      guidance: 'Configure a valid read token. For gated models, also complete the publisher approval on the model page.',
      configure: true,
      open: !!(detail.license_url || detail.model_url),
    };
  }
  if (code === 'hf_model_not_found') {
    return {
      title: 'Model could not be found',
      guidance: 'Review the repository ID and confirm the model contains a config.json file that Optimum can convert.',
      configure: false,
      open: !!detail.model_url,
    };
  }
  if (code === 'hf_rate_limited' || code === 'hf_network_error') {
    return {
      title: 'Hugging Face check unavailable',
      guidance: 'The conversion was not queued. Check the connection and try the access check again.',
      configure: false,
      open: false,
    };
  }
  return {
    title: 'Hugging Face access check failed',
    guidance: 'Review the model page or token, then check access again.',
    configure: true,
    open: !!(detail.license_url || detail.model_url),
  };
}
function showAccessIssue(detail, retry = {}) {
  createDialogs();
  pendingAccess = detail || {};
  pendingRetry = retry;
  const presentation = issuePresentation(pendingAccess);
  document.getElementById('hf-required-title').textContent = presentation.title;
  document.getElementById('hf-required-message').textContent = pendingAccess.message
    || 'Hugging Face access must be checked before this model can be downloaded.';
  document.getElementById('hf-required-model').textContent = pendingAccess.source_model
    || retry.modelId
    || 'Hugging Face model';
  document.getElementById('hf-required-guidance').textContent = presentation.guidance;
  document.getElementById('hf-required-error').textContent = '';
  const configure = document.getElementById('hf-required-settings');
  const open = document.getElementById('hf-required-open');
  configure.hidden = !presentation.configure;
  open.hidden = !presentation.open;
  open.disabled = !presentation.open;
  setModal(document.getElementById('hf-access-modal'), true);
  document.getElementById('hf-required-check')?.focus();
  void loadStatus();
}
async function replayPreparation() {
  const retry = pendingRetry;
  if (!retry?.path) return;
  if (retry.path === '/v1/models/download-custom') {
    document.getElementById('custom-model-form')?.requestSubmit();
    return;
  }
  const response = await fetch(retry.path, {
    method: 'POST',
    headers: headers({'Content-Type': 'application/json'}),
    body: JSON.stringify(retry.body || {}),
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new Error(payload.detail?.message || payload.detail || 'Model preparation failed.');
  }
  showToast(payload.message || 'Model preparation started');
  if (typeof window.setStatusPolling === 'function') window.setStatusPolling(1000);
  if (typeof window.updateStatus === 'function') await window.updateStatus();
}
async function checkRequiredAccess() {
  if (busy || !pendingAccess) return;
  setBusy(true);
  const error = document.getElementById('hf-required-error');
  error.textContent = '';
  try {
    const body = pendingRetry?.path === '/v1/models/download-custom'
      ? {
          source_model: pendingAccess.source_model,
          access_type: pendingAccess.access_type || 'unknown',
        }
      : pendingRetry?.modelId
        ? {model_id: pendingRetry.modelId}
        : {
            source_model: pendingAccess.source_model,
            access_type: pendingAccess.access_type || 'unknown',
          };
    const response = await fetch('/v1/huggingface/preflight', {
      method: 'POST',
      headers: headers({'Content-Type': 'application/json'}),
      body: JSON.stringify(body),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      const detail = payload.detail || {};
      if (typeof detail === 'object') {
        pendingAccess = detail;
        showAccessIssue(detail, pendingRetry || {});
      }
      throw new Error(detail.message || detail || 'Access is not ready yet.');
    }
    closeAccessDialog();
    showToast('Access granted. Starting model preparation…');
    await replayPreparation();
    pendingAccess = null;
    pendingRetry = null;
  } catch (problem) {
    error.textContent = problem instanceof Error ? problem.message : 'Access is not ready yet.';
  } finally {
    setBusy(false);
  }
}

function decorateGatedModels(payload) {
  const models = payload?.models?.available;
  if (!Array.isArray(models)) return;
  window.setTimeout(() => {
    const select = document.getElementById('model-select');
    if (!select) return;
    for (const model of models) {
      if (!model?.is_gated) continue;
      const option = [...select.options].find(item => item.value === model.id);
      if (option && !option.textContent.includes('Gated access')) {
        option.textContent += ' · Gated access';
      }
      if (option) {
        option.title = `${option.title || model.description || ''}\nRequires publisher approval on Hugging Face.`.trim();
      }
    }
    const selected = models.find(model => model.id === select.value);
    const status = document.getElementById('model-status');
    status?.querySelector('.hf-gated-badge')?.remove();
    if (selected?.is_gated && status) {
      const badge = document.createElement('span');
      badge.className = 'hf-gated-badge';
      badge.textContent = 'Gated';
      badge.title = 'Requires Hugging Face publisher approval and an authorized token';
      status.appendChild(badge);
    }
  }, 0);
}

const upstreamFetch = InferBridge.chain();
InferBridge.use(async function huggingFaceAwareFetch(input, init = {}) {
  const target = endpoint(input);
  const method = String(init?.method || input?.method || 'GET').toUpperCase();
  const response = await upstreamFetch(input, init);
  if (
    target.sameOrigin
    && ['/v1/models/status', '/v1/system/status'].includes(target.path)
    && method === 'GET'
    && response.ok
  ) {
    response.clone().json().then(decorateGatedModels).catch(() => {});
  }
  if (
    target.sameOrigin
    && method === 'POST'
    && ['/v1/models/convert', '/v1/models/download-custom'].includes(target.path)
    && !response.ok
  ) {
    try {
      const payload = await response.clone().json();
      const detail = payload?.detail;
      if (detail && typeof detail === 'object' && String(detail.code || '').startsWith('hf_')) {
        let requestBody = {};
        try { requestBody = JSON.parse(String(init.body || '{}')); } catch {}
        showAccessIssue(detail, {
          path: target.path,
          modelId: requestBody.model || requestBody.model_id || '',
          body: requestBody,
        });
        const safePayload = {
          ...payload,
          detail: detail.message || 'Hugging Face access must be checked.',
          hf_access: detail,
        };
        const responseHeaders = new Headers(response.headers);
        responseHeaders.delete('content-length');
        responseHeaders.set('cache-control', 'no-store');
        return new Response(JSON.stringify(safePayload), {
          status: response.status,
          statusText: response.statusText,
          headers: responseHeaders,
        });
      }
    } catch {}
  }
  return response;
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (!document.getElementById('hf-token-modal')?.classList.contains('hidden')) {
    closeTokenDialog();
  } else if (!document.getElementById('hf-access-modal')?.classList.contains('hidden')) {
    cancelAccessDialog();
  }
});

createSettingsCard();
createDialogs();
void loadStatus();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=HUGGINGFACE_ACCESS_JS,
    css=HUGGINGFACE_ACCESS_CSS,
    style_id=f"{_EXTENSION_ID}-style",
    description="Secure Hugging Face token settings and gated-model preflight.",
)


def install_huggingface_access_ui_extension() -> None:
    """Register secure Hugging Face access settings."""

    ui_registry.register(EXTENSION)


__all__ = [
    "HUGGINGFACE_ACCESS_CSS",
    "HUGGINGFACE_ACCESS_JS",
    "install_huggingface_access_ui_extension",
]
