"""Local Connection Hub for InferBridge's dependency-free browser UI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-connection-hub-extension"

CONNECTION_HUB_CSS = r"""
#connection-hub-entry{padding:12px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}
#connection-hub-entry h4{margin:0 0 5px;color:var(--text-1);font-size:11px}.ch-entry-copy{margin:0 0 9px;color:var(--text-3);font-size:10.5px;line-height:1.45}.ch-open-btn,.ch-btn{min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.ch-open-btn{width:100%}.ch-open-btn:hover,.ch-btn:hover:not(:disabled){border-color:var(--primary);background:var(--surface-3)}.ch-open-btn:focus-visible,.ch-btn:focus-visible,.ch-select:focus-visible,.ch-copy:focus-visible,.ch-details summary:focus-visible,.ch-lan summary:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.ch-btn.primary{border-color:transparent;background:var(--primary);color:white}.ch-btn:disabled{opacity:.5;cursor:not-allowed}
#connection-hub-modal .modal-card{width:min(900px,calc(100vw - 24px));max-height:min(880px,calc(100dvh - 24px));overflow:hidden}.ch-head p{margin-top:3px;color:var(--text-3);font-size:10px;text-transform:none;letter-spacing:0}.ch-body{min-height:0;overflow:auto;padding:16px 18px 20px;overscroll-behavior:contain}.ch-section{padding:13px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}.ch-section+.ch-section{margin-top:10px}.ch-section h4{margin:0 0 9px;color:var(--text-1);font-size:11px}.ch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ch-field{min-width:0;padding:9px 10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-field-label{display:block;color:var(--text-3);font-size:8px;text-transform:uppercase;letter-spacing:.45px}.ch-field-row{display:flex;align-items:center;gap:7px;margin-top:4px}.ch-field-value{min-width:0;flex:1;color:var(--text-1);font-size:10px;line-height:1.4;overflow-wrap:anywhere}.ch-mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.ch-copy{flex:0 0 auto;min-width:31px;min-height:31px;padding:5px 7px;border:1px solid var(--border);border-radius:7px;background:var(--surface-2);color:var(--text-2);font:inherit;font-size:9px;cursor:pointer}.ch-copy:hover{border-color:var(--primary);color:var(--text-1)}.ch-model-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;align-items:center;margin-top:8px}.ch-select{min-width:0;width:100%;min-height:36px;padding:7px 9px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1);color:var(--text-1);font:inherit;font-size:10px}.ch-hint{margin-top:7px;color:var(--text-3);font-size:9px;line-height:1.45}.ch-runtime.unavailable{color:var(--amber)}
.ch-details{border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-details+.ch-details{margin-top:7px}.ch-details summary{padding:9px 10px;color:var(--text-2);font-size:10px;font-weight:700;cursor:pointer;user-select:none}.ch-details[open] summary{border-bottom:1px solid var(--border);color:var(--text-1)}.ch-code-wrap{position:relative;padding:10px}.ch-code{margin:0;padding:10px 44px 10px 10px;overflow:auto;border-radius:8px;background:var(--code-bg);color:var(--code-text);font:10px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre}.ch-code-wrap>.ch-copy{position:absolute;top:17px;right:17px}.ch-config{display:grid;gap:6px}.ch-config-row{display:grid;grid-template-columns:120px minmax(0,1fr) auto;gap:8px;align-items:center;padding:8px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1)}.ch-config-row>span:first-child{color:var(--text-3);font-size:9px}.ch-config-value{min-width:0;overflow-wrap:anywhere;color:var(--text-1);font:9.5px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace}
.ch-test-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:9px}.ch-test-head h4{margin:0}.ch-tests{display:grid;gap:6px}.ch-test-row{display:grid;grid-template-columns:minmax(145px,.8fr) 92px 74px minmax(0,1.6fr);gap:8px;align-items:center;padding:9px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1)}.ch-test-name{color:var(--text-1);font-size:9.5px;font-weight:700}.ch-test-status{font-size:9px;font-weight:750}.ch-test-status.passed{color:var(--green)}.ch-test-status.failed{color:var(--red)}.ch-test-status.running{color:var(--primary)}.ch-test-status.skipped{color:var(--amber)}.ch-test-duration{color:var(--text-3);font-size:8.5px;font-variant-numeric:tabular-nums}.ch-test-detail{color:var(--text-2);font-size:9px;line-height:1.4;overflow-wrap:anywhere}.ch-message{min-height:18px;margin-top:8px;color:var(--text-2);font-size:9.5px;line-height:1.4}.ch-message.error{color:var(--red)}
.ch-lan{margin-top:10px}.ch-lan summary{color:var(--text-2);font-size:10px;font-weight:700;cursor:pointer}.ch-lan-body{margin-top:8px;padding:10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-lan-title{font-size:10px;font-weight:750;color:var(--text-1)}.ch-lan-detail{margin-top:5px;color:var(--text-2);font-size:9px;line-height:1.5}.ch-lan-body.warn{border-color:color-mix(in srgb,var(--amber) 42%,var(--border))}.ch-lan-body.warn .ch-lan-title{color:var(--amber)}
.ch-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 18px calc(11px + env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface-1)}.ch-footer-note{max-width:650px;color:var(--text-3);font-size:9px;line-height:1.4}.ch-copy-feedback{min-height:16px;color:var(--green);font-size:9px}
@media(max-width:720px){#connection-hub-modal .modal-card{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}.ch-body{padding:12px}.ch-grid{grid-template-columns:1fr}.ch-test-head{align-items:stretch;flex-direction:column}.ch-test-row{grid-template-columns:minmax(0,1fr) auto}.ch-test-duration{grid-column:2}.ch-test-detail{grid-column:1/-1}.ch-config-row{grid-template-columns:1fr auto}.ch-config-row>span:first-child{grid-column:1/-1}.ch-footer{align-items:stretch;flex-direction:column;padding:10px 12px calc(10px + env(safe-area-inset-bottom))}.ch-footer .ch-btn{min-height:42px}.ch-model-row{grid-template-columns:1fr}.ch-copy-model{width:100%}}
"""

CONNECTION_HUB_JS = r"""
(() => {
'use strict';
if (window.__ovllmConnectionHubInstalled) return;
window.__ovllmConnectionHubInstalled = true;
const META_PATH = '/internal/connection-hub';
const TEST_PATH = '/internal/connection-hub/self-test';
const TESTS = [
    ['models', 'Model listing'],
    ['non_streaming', 'Non-streaming generation'],
    ['streaming', 'Streaming generation'],
    ['cancellation', 'Cancellation'],
    ['authentication', 'Authentication'],
];
let metadata = null;
let selectedModel = '';
let returnFocus = null;
let activeController = null;
let running = false;

function browserApiKey() {
    const fieldValue = String(document.getElementById('settings-api-key')?.value || '').trim();
    if (fieldValue) return fieldValue;
    try { return String(localStorage.getItem('ovllm.apikey.v1') || '').trim(); } catch { return ''; }
}
function uiHeaders(json = false, includeAuth = false) {
    const key = includeAuth ? browserApiKey() : '';
    return {
        'X-OV-LLM-UI': '1',
        ...(key ? {Authorization: `Bearer ${key}`} : {}),
        ...(json ? {'Content-Type': 'application/json'} : {}),
    };
}
function safeText(value) { return String(value ?? ''); }
function apiError(body, status) {
    const detail = body?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail.message === 'string' && detail.message.trim()) return detail.message.trim();
    return `Connection Hub request failed (HTTP ${status}).`;
}
async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            ...uiHeaders(Boolean(options.body), path === TEST_PATH),
            ...(options.headers || {}),
        },
    });
    let body = null;
    try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(apiError(body, response.status));
    return body;
}

const apiKeyGroup = document.getElementById('settings-api-key')?.closest('.setting-group');
if (apiKeyGroup && !document.getElementById('connection-hub-entry')) {
    const entry = document.createElement('div');
    entry.className = 'setting-group';
    entry.id = 'connection-hub-entry';
    entry.innerHTML = '<h4>Local API</h4><p class="ch-entry-copy">Copy the active endpoint and model ID, then verify OpenAI-compatible connectivity.</p><button type="button" class="ch-open-btn" id="connection-hub-open">Open Connection Hub</button>';
    apiKeyGroup.insertAdjacentElement('afterend', entry);
}

const modal = document.createElement('div');
modal.className = 'modal-overlay hidden';
modal.id = 'connection-hub-modal';
modal.setAttribute('aria-hidden', 'true');
modal.innerHTML = `
<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="ch-title" aria-describedby="ch-description">
  <div class="modal-header"><div class="ch-head"><h3 id="ch-title">Local Connection Hub</h3><p id="ch-description">Endpoint, model, authentication, and live API checks</p></div><button type="button" class="close-btn" id="ch-close" aria-label="Close Connection Hub">&times;</button></div>
  <div class="ch-body">
    <section class="ch-section" aria-labelledby="ch-connection-heading">
      <h4 id="ch-connection-heading">Connection</h4>
      <div class="ch-grid">
        <div class="ch-field"><span class="ch-field-label">Base URL</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-base-url">Checking...</span><button type="button" class="ch-copy" data-copy-target="base" aria-label="Copy Base URL">Copy</button></div></div>
        <div class="ch-field"><span class="ch-field-label">Authentication</span><div class="ch-field-row"><span class="ch-field-value" id="ch-auth">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Listener</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-listener">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Runtime</span><div class="ch-field-row"><span class="ch-field-value ch-runtime" id="ch-runtime">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Loaded models</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-loaded-models">Checking...</span></div></div>
      </div>
      <div class="ch-model-row"><select class="ch-select" id="ch-model-select" aria-label="Model ID to copy and self-test"><option value="">Checking loaded models...</option></select><button type="button" class="ch-btn ch-copy-model" id="ch-copy-model">Copy model ID</button></div>
      <p class="ch-hint" id="ch-model-hint">Only loaded generation-capable models can run generation self-tests.</p>
    </section>

    <section class="ch-section" aria-labelledby="ch-quick-heading">
      <h4 id="ch-quick-heading">Quick configuration</h4>
      <details class="ch-details"><summary>OpenAI Python SDK</summary><div class="ch-code-wrap"><pre class="ch-code" id="ch-python"></pre><button type="button" class="ch-copy" data-copy-target="python" aria-label="Copy OpenAI Python SDK example">Copy</button></div></details>
      <details class="ch-details"><summary>curl</summary><div class="ch-code-wrap"><pre class="ch-code" id="ch-curl"></pre><button type="button" class="ch-copy" data-copy-target="curl" aria-label="Copy curl example">Copy</button></div></details>
      <details class="ch-details" open><summary>Connection values</summary><div class="ch-code-wrap"><div class="ch-config">
        <div class="ch-config-row"><span>Base URL</span><span class="ch-config-value" id="ch-value-base">-</span><button type="button" class="ch-copy" data-copy-target="base" aria-label="Copy Base URL configuration value">Copy</button></div>
        <div class="ch-config-row"><span>API key</span><span class="ch-config-value" id="ch-value-key">-</span><button type="button" class="ch-copy" data-copy-target="key" aria-label="Copy API key placeholder">Copy</button></div>
        <div class="ch-config-row"><span>Model ID</span><span class="ch-config-value" id="ch-value-model">Select a loaded model</span><button type="button" class="ch-copy" data-copy-target="model" aria-label="Copy Model ID configuration value">Copy</button></div>
      </div></div></details>
    </section>

    <section class="ch-section" aria-labelledby="ch-test-heading">
      <div class="ch-test-head"><h4 id="ch-test-heading">Connection self-test</h4><button type="button" class="ch-btn primary" id="ch-run-test">Run connection self-test</button></div>
      <div class="ch-tests" id="ch-tests"></div>
      <div class="ch-message" id="ch-message" role="status" aria-live="polite"></div>
    </section>

    <details class="ch-lan"><summary>Advanced LAN access</summary><div class="ch-lan-body" id="ch-lan-body"><div class="ch-lan-title" id="ch-lan-title">Checking listener...</div><div class="ch-lan-detail" id="ch-lan-detail"></div></div></details>
  </div>
  <div class="ch-footer"><div><div class="ch-footer-note">The Hub never returns the configured server secret. When authentication is enabled, the self-test proves access with the API key already entered in this browser.</div><div class="ch-copy-feedback" id="ch-copy-feedback" role="status" aria-live="polite"></div></div><button type="button" class="ch-btn primary" id="ch-done">Done</button></div>
</div>`;
document.body.appendChild(modal);

const $ = selector => modal.querySelector(selector);
const openButton = document.getElementById('connection-hub-open');
const modelSelect = $('#ch-model-select');
const runButton = $('#ch-run-test');
const message = $('#ch-message');
const copyFeedback = $('#ch-copy-feedback');

function statusLabel(status) {
    return ({not_run:'Not run', running:'Running', passed:'Passed', failed:'Failed', skipped:'Skipped'})[status] || safeText(status);
}
function renderTestRows(results = null) {
    const byId = new Map((results || []).map(item => [item.id, item]));
    $('#ch-tests').innerHTML = '';
    for (const [id, label] of TESTS) {
        const item = byId.get(id) || {id, label, status:'not_run', duration_ms:null, detail:'Not run yet.'};
        const row = document.createElement('div');
        row.className = 'ch-test-row';
        row.dataset.testId = id;
        const name = document.createElement('span'); name.className = 'ch-test-name'; name.textContent = label;
        const status = document.createElement('span'); status.className = `ch-test-status ${item.status}`; status.textContent = statusLabel(item.status); status.setAttribute('aria-label', `${label}: ${statusLabel(item.status)}`);
        const duration = document.createElement('span'); duration.className = 'ch-test-duration'; duration.textContent = Number.isFinite(item.duration_ms) ? `${item.duration_ms} ms` : '—';
        const detail = document.createElement('span'); detail.className = 'ch-test-detail'; detail.textContent = safeText(item.detail || '');
        row.append(name, status, duration, detail);
        $('#ch-tests').appendChild(row);
    }
}
function setAllRunning() {
    renderTestRows(TESTS.map(([id, label]) => ({id, label, status:'running', duration_ms:null, detail:'Running through the local API...'})));
}
function setMessage(text = '', isError = false) {
    message.textContent = safeText(text);
    message.className = `ch-message${isError ? ' error' : ''}`;
}
function setRunning(value) {
    running = Boolean(value);
    runButton.disabled = running;
    runButton.setAttribute('aria-busy', running ? 'true' : 'false');
    modelSelect.disabled = running || !usableModels().length;
}
function usableModels() {
    return Array.isArray(metadata?.models) ? metadata.models.filter(item => item.loaded && item.generation_capable) : [];
}
function chooseModel() {
    const usable = usableModels();
    const current = document.getElementById('model-select')?.value || '';
    if (usable.some(item => item.id === selectedModel)) return selectedModel;
    if (usable.some(item => item.id === current)) return current;
    return usable.length === 1 ? usable[0].id : '';
}
function renderModelOptions() {
    selectedModel = chooseModel();
    modelSelect.replaceChildren();
    const usable = usableModels();
    if (!usable.length) {
        const option = new Option('No loaded generation model', '', true, true); option.disabled = true; modelSelect.add(option);
        modelSelect.disabled = true;
        $('#ch-model-hint').textContent = 'Load a chat model before running generation checks.';
    } else {
        modelSelect.disabled = running;
        if (usable.length > 1) modelSelect.add(new Option('Select a loaded model...', ''));
        usable.forEach(item => modelSelect.add(new Option(`${item.name} (${item.id})${item.busy ? ' · busy' : ''}`, item.id)));
        modelSelect.value = selectedModel;
        $('#ch-model-hint').textContent = usable.length > 1 && !selectedModel
            ? 'Multiple generation-capable models are loaded. Select the model ID to copy and test.'
            : 'The self-test never unloads, reloads, converts, or changes the selected model.';
    }
    $('#ch-copy-model').disabled = !selectedModel;
    updateSnippets();
}
function pythonSnippet() {
    const base = metadata?.base_url || 'http://127.0.0.1:8000/v1';
    const key = metadata?.authentication?.required ? 'YOUR_INFERBRIDGE_API_KEY' : 'not-required';
    const model = selectedModel || 'MODEL_ID';
    return `from openai import OpenAI\n\nclient = OpenAI(\n    base_url=${JSON.stringify(base)},\n    api_key=${JSON.stringify(key)},\n)\n\nresponse = client.chat.completions.create(\n    model=${JSON.stringify(model)},\n    messages=[{"role": "user", "content": "Hello"}],\n)\n\nprint(response.choices[0].message.content)`;
}
function curlSnippet() {
    const base = metadata?.base_url || 'http://127.0.0.1:8000/v1';
    const auth = metadata?.authentication?.required ? ' -H "Authorization: Bearer YOUR_INFERBRIDGE_API_KEY"' : '';
    return `curl.exe "${base}/models"${auth}`;
}
function copyValue(kind) {
    if (kind === 'base') return metadata?.base_url || '';
    if (kind === 'key') return metadata?.authentication?.api_key_placeholder || '';
    if (kind === 'model') return selectedModel;
    if (kind === 'python') return pythonSnippet();
    if (kind === 'curl') return curlSnippet();
    return '';
}
function updateSnippets() {
    $('#ch-python').textContent = pythonSnippet();
    $('#ch-curl').textContent = curlSnippet();
    $('#ch-value-base').textContent = metadata?.base_url || '-';
    $('#ch-value-key').textContent = metadata?.authentication?.api_key_placeholder || '-';
    $('#ch-value-model').textContent = selectedModel || 'Select a loaded model';
}
async function writeClipboard(text) {
    if (!text) throw new Error('Nothing is available to copy yet.');
    if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(text); return; } catch {}
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand?.('copy') === true;
    textarea.remove();
    if (!copied) throw new Error('Clipboard access was blocked. Select the visible value and copy it manually.');
}
async function copy(kind, label) {
    try {
        await writeClipboard(copyValue(kind));
        copyFeedback.textContent = `${label} copied.`;
    } catch (error) {
        copyFeedback.textContent = error instanceof Error ? error.message : 'Clipboard access failed.';
    }
}
function renderMetadata() {
    $('#ch-base-url').textContent = metadata?.base_url || 'Unavailable';
    $('#ch-auth').textContent = metadata?.authentication?.label || 'Unavailable';
    $('#ch-listener').textContent = metadata ? `${metadata.listen_host}:${metadata.port} · ${metadata.api_root}` : 'Unavailable';
    const runtime = $('#ch-runtime');
    const state = metadata?.runtime_state || 'unavailable';
    runtime.textContent = state === 'available' ? 'Available' : state === 'shutting_down' ? 'Shutting down' : 'Unavailable';
    runtime.className = `ch-field-value ch-runtime ${state === 'available' ? '' : 'unavailable'}`;
    const loaded = Array.isArray(metadata?.loaded_model_ids) ? metadata.loaded_model_ids : [];
    $('#ch-loaded-models').textContent = loaded.length ? loaded.join(', ') : 'None';
    const lan = metadata?.lan;
    $('#ch-lan-title').textContent = lan?.label || 'LAN state unavailable';
    $('#ch-lan-detail').textContent = lan ? `${lan.detail}${lan.url ? ` LAN Base URL: ${lan.url}` : ''}` : 'Refresh the Hub after the server is available.';
    $('#ch-lan-body').classList.toggle('warn', Boolean(lan?.enabled && lan?.security_attention));
    renderModelOptions();
}
async function loadMetadata(preserveMessage = false) {
    activeController?.abort();
    activeController = new AbortController();
    if (!preserveMessage) setMessage();
    copyFeedback.textContent = '';
    try {
        metadata = await api(META_PATH, {signal: activeController.signal});
        renderMetadata();
    } catch (error) {
        if (error?.name === 'AbortError') return;
        metadata = null; selectedModel = ''; renderMetadata();
        setMessage(error instanceof Error ? error.message : 'Connection metadata is unavailable.', true);
    }
}
async function runSelfTest() {
    if (running) return;
    if (metadata?.authentication?.required && !browserApiKey()) {
        setMessage('Authentication is enabled. Close the Hub, enter the InferBridge API key in Generation Settings, then run the self-test again.', true);
        return;
    }
    setRunning(true); setAllRunning(); setMessage('Testing the local API with synthetic input...');
    try {
        const payload = await api(TEST_PATH, {method:'POST', body:JSON.stringify({model_id:selectedModel || null})});
        renderTestRows(payload?.tests || []);
        setMessage('Self-test finished. Review each check independently.');
        await loadMetadata(true);
    } catch (error) {
        renderTestRows(TESTS.map(([id,label]) => ({id,label,status:'failed',duration_ms:null,detail:'The self-test coordinator did not return a result.'})));
        setMessage(error instanceof Error ? error.message : 'The self-test could not run.', true);
    } finally {
        setRunning(false);
    }
}
function focusables() {
    return Array.from(modal.querySelectorAll('button:not(:disabled),select:not(:disabled),summary,[href],input:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')).filter(item => item.offsetParent !== null);
}
async function openHub(source) {
    returnFocus = source || document.activeElement;
    modal.classList.remove('hidden'); modal.setAttribute('aria-hidden','false');
    document.getElementById('app')?.setAttribute('inert','');
    $('#ch-close').focus();
    await loadMetadata();
}
function closeHub() {
    modal.classList.add('hidden'); modal.setAttribute('aria-hidden','true');
    activeController?.abort();
    document.getElementById('app')?.removeAttribute('inert');
    returnFocus?.focus?.();
}

renderTestRows();
openButton?.addEventListener('click', event => void openHub(event.currentTarget));
$('#ch-close').addEventListener('click', closeHub); $('#ch-done').addEventListener('click', closeHub);
runButton.addEventListener('click', () => void runSelfTest());
modelSelect.addEventListener('change', () => { selectedModel = modelSelect.value; $('#ch-copy-model').disabled = !selectedModel; updateSnippets(); });
$('#ch-copy-model').addEventListener('click', () => void copy('model', 'Model ID'));
modal.addEventListener('click', event => { const button = event.target.closest('[data-copy-target]'); if (button) { const kind = button.dataset.copyTarget; const labels = {base:'Base URL',key:'API key placeholder',model:'Model ID',python:'Python example',curl:'curl example'}; void copy(kind, labels[kind] || 'Value'); return; } if (event.target === modal) closeHub(); });
modal.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); closeHub(); return; }
    if (event.key !== 'Tab') return;
    const items = focusables(); if (!items.length) return;
    const first = items[0]; const last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=CONNECTION_HUB_JS,
    css=CONNECTION_HUB_CSS,
    description="Local Connection Hub client connection examples.",
)


def install_connection_hub_ui_extension() -> None:
    """Register the Local Connection Hub."""

    ui_registry.register(EXTENSION)


__all__ = [
    "CONNECTION_HUB_CSS",
    "CONNECTION_HUB_JS",
    "install_connection_hub_ui_extension",
]
