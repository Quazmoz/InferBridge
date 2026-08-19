"""Desktop Network / API access settings for the packaged browser UI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-desktop-network-extension"

DESKTOP_NETWORK_CSS = r"""
#desktop-network-entry{margin-top:10px;padding:12px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}
#desktop-network-entry h4{margin:0 0 5px;color:var(--text-1);font-size:11px}.dn-entry-copy{margin:0 0 9px;color:var(--text-3);font-size:10px;line-height:1.45}.dn-btn{min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.dn-btn:hover:not(:disabled){border-color:var(--primary);background:var(--surface-3)}.dn-btn.primary{border-color:transparent;background:var(--primary);color:#fff}.dn-btn.danger{color:var(--red)}.dn-btn:disabled{opacity:.5;cursor:not-allowed}.dn-open{width:100%}
#desktop-network-modal .modal-card{width:min(760px,calc(100vw - 24px));max-height:min(860px,calc(100dvh - 24px));overflow:hidden}.dn-body{min-height:0;overflow:auto;padding:16px 18px 20px;overscroll-behavior:contain}.dn-section{padding:13px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}.dn-section+.dn-section{margin-top:10px}.dn-section h4{margin:0 0 8px;color:var(--text-1);font-size:11px}.dn-copy{margin:0 0 10px;color:var(--text-3);font-size:9.5px;line-height:1.5}.dn-toggle{display:flex;align-items:flex-start;gap:9px;padding:10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.dn-toggle input{margin-top:2px}.dn-toggle strong{display:block;color:var(--text-1);font-size:10px}.dn-toggle span{display:block;margin-top:3px;color:var(--text-3);font-size:9px;line-height:1.45}.dn-label{display:block;margin:10px 0 5px;color:var(--text-2);font-size:9.5px;font-weight:700}.dn-input{width:100%;min-height:36px;padding:7px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1);color:var(--text-1);font:10px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace}.dn-input:focus-visible,.dn-btn:focus-visible,.dn-toggle input:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.dn-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.dn-row .dn-input{flex:1 1 280px}.dn-status{display:grid;gap:7px}.dn-status-row{display:grid;grid-template-columns:145px minmax(0,1fr);gap:8px;padding:8px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1)}.dn-status-row>span:first-child{color:var(--text-3);font-size:9px}.dn-value{min-width:0;color:var(--text-1);font:9.5px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.dn-warning{margin-top:8px;padding:9px 10px;border:1px solid color-mix(in srgb,var(--amber) 45%,var(--border));border-radius:8px;color:var(--text-2);font-size:9px;line-height:1.45}.dn-secret{display:none;margin-top:9px;padding:10px;border:1px solid color-mix(in srgb,var(--green) 40%,var(--border));border-radius:8px;background:var(--surface-1)}.dn-secret.visible{display:block}.dn-secret code{display:block;margin:7px 0;overflow-wrap:anywhere;color:var(--text-1);font:10px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}.dn-env{margin-top:6px;color:var(--amber);font-size:8.8px;line-height:1.4}.dn-message{min-height:18px;margin-top:9px;color:var(--text-2);font-size:9.5px;line-height:1.4}.dn-message.error{color:var(--red)}.dn-footer{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:11px 18px calc(11px + env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface-1)}
@media(max-width:640px){#desktop-network-modal .modal-card{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}.dn-body{padding:12px}.dn-status-row{grid-template-columns:1fr}.dn-footer{padding:10px 12px calc(10px + env(safe-area-inset-bottom));flex-direction:column-reverse}.dn-footer .dn-btn{width:100%;min-height:42px}}
"""

DESKTOP_NETWORK_JS = r"""
(() => {
'use strict';
if (window.__ovllmDesktopNetworkInstalled) return;
window.__ovllmDesktopNetworkInstalled = true;
const API = '/internal/desktop-network';
const RESTART = '/v1/desktop/operations/restart-server';
const SESSION_API_KEY = 'inferbridge.desktop.apikey.session.v1';
const PENDING_API_KEY = 'inferbridge.desktop.apikey.pending.v1';
const LEGACY_API_KEY = 'ovllm.apikey.v1';
const REMOVE_PENDING = '__inferbridge_remove__';
let status = null;
let generatedKey = '';
let returnFocus = null;
let saving = false;

function headers(json = false) { return {'X-OV-LLM-UI':'1', ...(json ? {'Content-Type':'application/json'} : {})}; }
function detail(body, fallback) { return typeof body?.detail === 'string' && body.detail.trim() ? body.detail.trim() : fallback; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{...headers(Boolean(options.body)), ...(options.headers || {})}});
  let body = null; try { body = await response.json(); } catch {}
  if (!response.ok) throw new Error(detail(body, `Network settings request failed (HTTP ${response.status}).`));
  return body;
}

function sessionApiKey() { try { return String(sessionStorage.getItem(SESSION_API_KEY) || '').trim(); } catch { return ''; } }
function setSessionApiKey(value) {
  const clean=String(value || '').trim();
  try { if (clean) sessionStorage.setItem(SESSION_API_KEY, clean); else sessionStorage.removeItem(SESSION_API_KEY); } catch {}
  try { localStorage.removeItem(LEGACY_API_KEY); } catch {}
  const field=document.getElementById('settings-api-key'); if (field) field.value=clean;
}
function setPendingApiKey(value) {
  try {
    if (value === undefined) sessionStorage.removeItem(PENDING_API_KEY);
    else sessionStorage.setItem(PENDING_API_KEY, value === null ? REMOVE_PENDING : String(value));
  } catch {}
}
function pendingApiKey() { try { const value=sessionStorage.getItem(PENDING_API_KEY); return value === null ? undefined : value; } catch { return undefined; } }
function promotePendingApiKey() {
  const pending=pendingApiKey();
  if (pending === undefined) return;
  setSessionApiKey(pending === REMOVE_PENDING ? '' : pending);
  setPendingApiKey(undefined);
}
function migrateLegacyApiKey() {
  let legacy='';
  try { legacy=String(localStorage.getItem(LEGACY_API_KEY) || '').trim(); localStorage.removeItem(LEGACY_API_KEY); } catch {}
  if (!sessionApiKey() && legacy) setSessionApiKey(legacy);
  else setSessionApiKey(sessionApiKey());
}

migrateLegacyApiKey();
const settingsApiKey=document.getElementById('settings-api-key');
settingsApiKey?.addEventListener('change', event => {
  event.stopImmediatePropagation();
  setSessionApiKey(settingsApiKey.value);
  setPendingApiKey(undefined);
}, true);

if (window.InferBridge?.chain && window.InferBridge?.use) {
  const previousFetch=window.InferBridge.chain();
  window.InferBridge.use(async function desktopSessionAuth(input, init = {}) {
    const request=window.InferBridge.describe(input, init);
    const protectedPath=request.path.startsWith('/v1/') || request.path === '/internal/connection-hub/self-test';
    if (!request.sameOrigin || !protectedPath) return previousFetch(input, init);
    const sessionKey=sessionApiKey();
    const sourceHeaders=init.headers || (typeof input !== 'string' && input?.headers) || undefined;
    const nextHeaders=new Headers(sourceHeaders);
    if (sessionKey) nextHeaders.set('Authorization', `Bearer ${sessionKey}`);
    else nextHeaders.delete('Authorization');
    return previousFetch(input, {...init, headers:nextHeaders});
  });
}

const anchor = document.getElementById('connection-hub-entry') || document.getElementById('settings-api-key')?.closest('.setting-group');
if (anchor && !document.getElementById('desktop-network-entry')) {
  const entry = document.createElement('div');
  entry.id = 'desktop-network-entry';
  entry.innerHTML = '<h4>Network / API access</h4><p class="dn-entry-copy">Keep InferBridge local to this PC or explicitly allow authenticated access from trusted devices on your LAN or home lab.</p><button type="button" class="dn-btn dn-open" id="desktop-network-open">Open Network / API settings</button>';
  anchor.insertAdjacentElement('afterend', entry);
}

const modal = document.createElement('div');
modal.className = 'modal-overlay hidden';
modal.id = 'desktop-network-modal';
modal.setAttribute('aria-hidden','true');
modal.innerHTML = `
<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="dn-title" aria-describedby="dn-description">
  <div class="modal-header"><div><h3 id="dn-title">Network / API access</h3><p id="dn-description" class="dn-copy">Localhost remains the default. LAN mode is for trusted devices on your local network or home lab.</p></div><button type="button" class="close-btn" id="dn-close" aria-label="Close Network / API settings">&times;</button></div>
  <div class="dn-body">
    <section class="dn-section"><h4>Listener</h4>
      <label class="dn-toggle"><input type="checkbox" id="dn-lan"><span><strong>Allow access from other devices on my local network</strong><span>When enabled, InferBridge listens on available network interfaces. An API key is required. This is not a public internet mode.</span></span></label>
      <div class="dn-env" id="dn-host-env"></div>
    </section>
    <section class="dn-section"><h4>API authentication</h4><p class="dn-copy">LAN access requires a strong API key. InferBridge stores desktop-managed keys with Windows DPAPI and never returns a stored key after it has been saved.</p>
      <div class="dn-row"><input class="dn-input" id="dn-key" type="password" autocomplete="new-password" maxlength="512" placeholder="Enter a new API key (24+ characters)"><button type="button" class="dn-btn" id="dn-generate">Generate strong key</button><button type="button" class="dn-btn danger" id="dn-remove-key">Remove stored key</button></div>
      <div class="dn-env" id="dn-key-env"></div>
      <div class="dn-secret" id="dn-secret"><strong>Generated API key, shown once</strong><code id="dn-secret-value"></code><button type="button" class="dn-btn" id="dn-copy-secret">Copy API key</button><p class="dn-copy">Store it in your remote client now. InferBridge will not display the stored secret again.</p></div>
    </section>
    <section class="dn-section"><h4>Allowed browser origins</h4><p class="dn-copy">CORS is only needed for browser-based clients. Open WebUI, n8n, SDKs, and server-to-server clients normally do not need it. Leave blank unless a browser app runs on another origin.</p>
      <label class="dn-label" for="dn-cors">Comma-separated origins</label><input class="dn-input" id="dn-cors" maxlength="2048" placeholder="http://192.168.1.50:3000">
      <p class="dn-copy">Use <code>*</code> only when you genuinely need any browser origin. Wildcard CORS is an advanced setting and still requires API authentication in LAN mode.</p><div class="dn-env" id="dn-cors-env"></div>
    </section>
    <section class="dn-section"><h4>Active endpoints</h4><div class="dn-status">
      <div class="dn-status-row"><span>Local endpoint</span><span class="dn-value" id="dn-local">Checking...</span></div>
      <div class="dn-status-row"><span>LAN endpoint(s)</span><span class="dn-value" id="dn-lan-endpoints">Disabled</span></div>
      <div class="dn-status-row"><span>Active listener</span><span class="dn-value" id="dn-listener">Checking...</span></div>
      <div class="dn-status-row"><span>API key</span><span class="dn-value" id="dn-key-status">Checking...</span></div>
      <div class="dn-status-row"><span>CORS</span><span class="dn-value" id="dn-cors-status">Checking...</span></div>
    </div><div id="dn-warnings"></div>
      <div class="dn-warning">Windows Firewall may prompt after LAN mode is applied. Allow InferBridge only on trusted <strong>Private</strong> network profiles. Do not create an unrestricted Public-network rule.</div>
    </section>
    <div class="dn-message" id="dn-message" role="status" aria-live="polite"></div>
  </div>
  <div class="dn-footer"><button type="button" class="dn-btn" id="dn-cancel">Close</button><button type="button" class="dn-btn primary" id="dn-apply">Apply and restart</button></div>
</div>`;
document.body.appendChild(modal);
const $ = selector => modal.querySelector(selector);
const lan = $('#dn-lan'), cors = $('#dn-cors'), key = $('#dn-key'), apply = $('#dn-apply');

function message(text='', error=false) { const el=$('#dn-message'); el.textContent=String(text || ''); el.className=`dn-message${error ? ' error' : ''}`; }
function showGenerated(value='') { generatedKey=String(value || ''); $('#dn-secret-value').textContent=generatedKey; $('#dn-secret').classList.toggle('visible', Boolean(generatedKey)); }
function render() {
  if (!status) return;
  lan.checked = Boolean(status.lan_setting_enabled);
  cors.value = status.cors_origins || '';
  lan.disabled = Boolean(status.host_environment_override);
  cors.disabled = Boolean(status.cors_environment_override);
  key.disabled = Boolean(status.api_key_environment_override);
  $('#dn-generate').disabled = Boolean(status.api_key_environment_override);
  $('#dn-remove-key').disabled = Boolean(status.api_key_environment_override || !status.api_key_configured || status.api_key_source !== 'secure_store');
  $('#dn-host-env').textContent = status.host_environment_override ? 'OV_LLM_HOST is set in the Windows environment and overrides the desktop LAN toggle.' : '';
  $('#dn-key-env').textContent = status.api_key_environment_override ? 'OV_LLM_API_KEY is set in the Windows environment. The desktop UI will not replace or reveal it.' : '';
  $('#dn-cors-env').textContent = status.cors_environment_override ? 'OV_LLM_CORS_ORIGINS is set in the Windows environment and overrides this field.' : '';
  $('#dn-local').textContent = status.local_endpoint || 'Unavailable';
  $('#dn-lan-endpoints').textContent = status.lan_active ? ((status.lan_endpoints || []).join('\n') || 'LAN listener active, but no private IPv4 address was detected.') : 'Disabled';
  $('#dn-listener').textContent = `${status.active_bind_host} · ${status.host_source}${status.restart_required ? ' · restart pending' : ''}`;
  $('#dn-key-status').textContent = status.api_key_configured ? `Configured · ${status.api_key_source || 'configured'}${status.api_key_persistence ? ` · ${status.api_key_persistence}` : ''}` : 'Not configured';
  $('#dn-cors-status').textContent = status.cors_origins ? `${status.cors_origins} · ${status.cors_source}` : 'Same-origin only';
  const warnings = $('#dn-warnings'); warnings.replaceChildren();
  (status.warnings || []).forEach(text => { const el=document.createElement('div'); el.className='dn-warning'; el.textContent=text; warnings.appendChild(el); });
}
async function load() {
  message(); showGenerated();
  try { status = await api(API); if (!status.restart_required) promotePendingApiKey(); render(); }
  catch (error) { message(error instanceof Error ? error.message : 'Network settings are unavailable.', true); }
}
async function copyText(text) {
  if (!text) return;
  if (navigator.clipboard?.writeText) { try { await navigator.clipboard.writeText(text); return; } catch {} }
  const ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select(); const ok=document.execCommand?.('copy')===true; ta.remove(); if (!ok) throw new Error('Clipboard access was blocked.');
}
async function saveAndRestart() {
  if (saving) return;
  const wildcard = String(cors.value || '').trim() === '*';
  if (wildcard && !window.confirm('Wildcard CORS allows browser scripts from any origin to attempt requests. Keep API authentication enabled and use this only when required. Continue?')) return;
  saving=true; apply.disabled=true; message('Saving network settings...');
  try {
    const body={allow_lan:Boolean(lan.checked),cors_origins:String(cors.value || '').trim(),api_key:String(key.value || '').trim() || null,generate_api_key:false,remove_stored_api_key:false,acknowledge_wildcard_cors:wildcard};
    const result=await api(API,{method:'POST',body:JSON.stringify(body)});
    if (body.api_key) setPendingApiKey(body.api_key);
    key.value=''; status=result.status; render(); showGenerated(result.generated_api_key || generatedKey);
    if (!status.restart_required) { promotePendingApiKey(); message(result.message || 'Network settings saved.'); return; }
    message('Settings saved. Restarting InferBridge...');
    await api(RESTART,{method:'POST'});
    promotePendingApiKey();
    message('InferBridge is restarting. Reopen it from the tray if this page does not reconnect automatically.');
  } catch (error) { message(error instanceof Error ? error.message : 'Network settings could not be applied.', true); }
  finally { saving=false; apply.disabled=false; }
}
async function generate() {
  if (saving) return;
  const draftLan = Boolean(lan.checked);
  const draftCors = String(cors.value || '').trim();
  const persistedCors = String(status?.cors_origins || '').trim();
  saving=true; apply.disabled=true; message('Generating and securely storing an API key...');
  try {
    const body={allow_lan:Boolean(status?.lan_setting_enabled),cors_origins:persistedCors,api_key:null,generate_api_key:true,remove_stored_api_key:false,acknowledge_wildcard_cors:persistedCors === '*'};
    const result=await api(API,{method:'POST',body:JSON.stringify(body)});
    status=result.status; render();
    if (!status.host_environment_override) lan.checked=draftLan;
    if (!status.cors_environment_override) cors.value=draftCors;
    showGenerated(result.generated_api_key || '');
    if (result.generated_api_key) setPendingApiKey(result.generated_api_key);
    message('API key generated and stored. Copy it now. Listener and CORS edits remain drafts until you choose Apply and restart.');
  } catch (error) { message(error instanceof Error ? error.message : 'API key could not be generated.', true); }
  finally { saving=false; apply.disabled=false; }
}
async function removeKey() {
  if (!window.confirm('Remove the desktop-managed API key? LAN access cannot remain enabled without authentication.')) return;
  if (lan.checked) { message('Turn off LAN access before removing the stored API key.', true); return; }
  const persistedCors=String(status?.cors_origins || '').trim();
  const nextCors=persistedCors === '*' ? '' : persistedCors;
  saving=true; apply.disabled=true;
  try {
    const result=await api(API,{method:'POST',body:JSON.stringify({allow_lan:false,cors_origins:nextCors,api_key:null,generate_api_key:false,remove_stored_api_key:true,acknowledge_wildcard_cors:false})});
    status=result.status; setPendingApiKey(null); if (!status.restart_required) promotePendingApiKey(); render(); showGenerated();
    message(persistedCors === '*' ? 'Stored API key removed and wildcard CORS cleared. Apply and restart if a restart is pending.' : 'Stored API key removed. Apply and restart if a restart is pending.');
  }
  catch (error) { message(error instanceof Error ? error.message : 'Stored API key could not be removed.', true); }
  finally { saving=false; apply.disabled=false; }
}
function focusables() { return Array.from(modal.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[href],[tabindex]:not([tabindex="-1"])')).filter(item => item.offsetParent !== null); }
function open(source) { returnFocus=source || document.activeElement; modal.classList.remove('hidden'); modal.setAttribute('aria-hidden','false'); document.getElementById('app')?.setAttribute('inert',''); $('#dn-close').focus(); void load(); }
function close() { modal.classList.add('hidden'); modal.setAttribute('aria-hidden','true'); document.getElementById('app')?.removeAttribute('inert'); key.value=''; showGenerated(); returnFocus?.focus?.(); }

document.getElementById('desktop-network-open')?.addEventListener('click', event => open(event.currentTarget));
$('#dn-close').addEventListener('click', close); $('#dn-cancel').addEventListener('click', close); apply.addEventListener('click', () => void saveAndRestart()); $('#dn-generate').addEventListener('click', () => void generate()); $('#dn-remove-key').addEventListener('click', () => void removeKey()); $('#dn-copy-secret').addEventListener('click', () => void copyText(generatedKey).then(() => message('API key copied.')).catch(error => message(error.message, true)));
modal.addEventListener('click', event => { if (event.target === modal) close(); });
modal.addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); close(); return; }
  if (event.key !== 'Tab') return;
  const items=focusables(); if (!items.length) return;
  const first=items[0], last=items.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
})();
"""

EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=DESKTOP_NETWORK_JS,
    css=DESKTOP_NETWORK_CSS,
    capability="desktop",
    description="Secure packaged LAN and API access settings.",
)


def install_desktop_network_ui_extension() -> None:
    ui_registry.register(EXTENSION)


__all__ = ["DESKTOP_NETWORK_CSS", "DESKTOP_NETWORK_JS", "install_desktop_network_ui_extension"]
