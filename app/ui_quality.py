"""Interaction reliability and accessibility hardening for the bundled browser client."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-ui-quality-extension"

UI_QUALITY_CSS = r""".ovllm-quality :is(button, select, input, textarea):focus-visible {
outline: 2px solid var(--primary);
outline-offset: 2px;
}
.ovllm-quality :is(button, select, input, textarea):disabled {
cursor: not-allowed;
}
#ov-connection-banner {
display: none;
flex: 0 0 auto;
align-items: center;
justify-content: space-between;
gap: 14px;
min-height: 46px;
margin: 10px 14px 0;
padding: 9px 11px 9px 13px;
border: 1px solid color-mix(in srgb, var(--red) 42%, var(--border));
border-radius: 11px;
background: color-mix(in srgb, var(--red) 8%, var(--surface-1));
box-shadow: var(--shadow-md);
color: var(--text-1);
}
#ov-connection-banner.visible {
display: flex;
}
#ov-connection-banner.auth {
border-color: color-mix(in srgb, var(--amber) 46%, var(--border));
background: color-mix(in srgb, var(--amber) 8%, var(--surface-1));
}
.ov-connection-copy {
min-width: 0;
}
.ov-connection-title {
display: block;
font-size: 11.5px;
font-weight: 750;
}
.ov-connection-detail {
display: block;
margin-top: 2px;
color: var(--text-3);
font-size: 10.5px;
line-height: 1.4;
}
#ov-connection-action {
flex: 0 0 auto;
min-height: 34px;
padding: 6px 11px;
border: 1px solid var(--border-hover);
border-radius: 9px;
background: var(--surface-2);
color: var(--text-1);
font: inherit;
font-size: 11px;
font-weight: 700;
cursor: pointer;
}
#ov-connection-action:hover:not(:disabled) {
border-color: var(--primary);
background: var(--surface-3);
}
#ov-panel-backdrop {
position: absolute;
inset: 0;
z-index: 7;
display: none;
border: 0;
background: color-mix(in srgb, var(--bg) 56%, transparent);
backdrop-filter: blur(2px);
cursor: default;
}
#ov-panel-backdrop.visible {
display: block;
}
.ovllm-quality .modal-tab-btn {
min-height: 44px;
}
.ovllm-quality .modal-panel[aria-hidden="true"] {
display: none !important;
}
.ovllm-quality #toast.success {
border-color: color-mix(in srgb, var(--green) 42%, var(--border));
}
.ovllm-quality #toast.error {
border-color: color-mix(in srgb, var(--red) 48%, var(--border));
}
.ovllm-quality #toast.warning {
border-color: color-mix(in srgb, var(--amber) 48%, var(--border));
}
.ovllm-quality .lifecycle-switching {
pointer-events: none;
opacity: .62;
}
@media (max-width: 950px) {
#ov-panel-backdrop.visible {
display: block;
}
#chats-sidebar,
#settings-sidebar {
z-index: 8;
}
}
@media (max-width: 640px) {
#ov-connection-banner {
align-items: stretch;
flex-direction: column;
margin: 8px 10px 0;
}
#ov-connection-action {
width: 100%;
min-height: 44px;
}
}
@media (prefers-reduced-motion: reduce) {
#ov-panel-backdrop {
backdrop-filter: none;
}
}
"""

UI_QUALITY_JS = r"""(() => {
'use strict';
if (window.__ovllmUiQualityInstalled) return;
window.__ovllmUiQualityInstalled = true;
document.documentElement.classList.add('ovllm-quality');
const appRoot = document.getElementById('app');
const chatColumn = document.querySelector('.chat-column');
const chatAreaElement = document.getElementById('chat-area');
const deviceChipElement = document.getElementById('device-chip');
const deviceLabelElement = document.getElementById('device-label');
const modelSelectElement = document.getElementById('model-select');
const deviceSelectElement = document.getElementById('device-select');
const loadButton = document.getElementById('load-model-btn');
const unloadButton = document.getElementById('unload-model-btn');
const deleteButton = document.getElementById('delete-model-btn');
const sendButton = document.getElementById('send-btn');
const settingsButton = document.getElementById('settings-toggle-btn');
const customModal = document.getElementById('custom-model-modal');
const searchTab = document.getElementById('tab-btn-search');
const manualTab = document.getElementById('tab-btn-manual');
const searchPanel = document.getElementById('modal-panel-search');
const manualPanel = document.getElementById('modal-panel-manual');
const connectionBanner = document.createElement('section');
connectionBanner.id = 'ov-connection-banner';
connectionBanner.setAttribute('role', 'alert');
connectionBanner.setAttribute('aria-live', 'assertive');
connectionBanner.innerHTML = `
<span class="ov-connection-copy">
<span class="ov-connection-title"></span>
<span class="ov-connection-detail"></span>
</span>
<button type="button" id="ov-connection-action">Retry</button>`;
if (chatColumn && chatAreaElement) chatColumn.insertBefore(connectionBanner, chatAreaElement);
const connectionTitle = connectionBanner.querySelector('.ov-connection-title');
const connectionDetail = connectionBanner.querySelector('.ov-connection-detail');
const connectionAction = connectionBanner.querySelector('#ov-connection-action');
const panelBackdrop = document.createElement('button');
panelBackdrop.type = 'button';
panelBackdrop.id = 'ov-panel-backdrop';
panelBackdrop.tabIndex = -1;
panelBackdrop.setAttribute('aria-label', 'Close open panel');
document.querySelector('.main-body')?.appendChild(panelBackdrop);
function chipState() {
const label = String(deviceLabelElement?.textContent || '').trim().toLowerCase();
if (label.includes('auth required')) return 'auth';
if (label.includes('connecting')) return 'connecting';
if (deviceChipElement?.classList.contains('offline')) return 'offline';
return 'online';
}
function syncConnectionUi() {
const state = chipState();
const unavailable = state !== 'online';
connectionBanner.classList.toggle('visible', state === 'offline' || state === 'auth');
connectionBanner.classList.toggle('auth', state === 'auth');
if (state === 'auth') {
connectionTitle.textContent = 'API key required';
connectionDetail.textContent = 'Open Settings and enter the key configured by the local server.';
connectionAction.textContent = 'Open Settings';
} else if (state === 'offline') {
connectionTitle.textContent = 'Local server unavailable';
connectionDetail.textContent = 'Check that the InferBridge process is still running, then retry the connection.';
connectionAction.textContent = 'Retry connection';
}
[loadButton, unloadButton, deleteButton].forEach(button => {
if (!button) return;
button.dataset.connectionDisabled = unavailable ? 'true' : 'false';
if (unavailable) button.disabled = true;
});
if (unavailable && sendButton) sendButton.disabled = true;
}
connectionAction?.addEventListener('click', () => {
if (chipState() === 'auth') {
if (typeof setSettingsSidebarOpen === 'function') setSettingsSidebarOpen(true);
document.getElementById('settings-api-key')?.focus();
return;
}
connectionAction.disabled = true;
connectionAction.textContent = 'Retrying…';
Promise.resolve(typeof updateStatus === 'function' ? updateStatus() : null)
.finally(() => {
connectionAction.disabled = false;
syncConnectionUi();
});
});
if (typeof updateSendButtonState === 'function') {
const previousUpdateSendButtonState = updateSendButtonState;
updateSendButtonState = function qualityUpdateSendButtonState(...args) {
const result = previousUpdateSendButtonState(...args);
if (chipState() !== 'online' && sendButton) sendButton.disabled = true;
return result;
};
}
if (typeof updateModelUi === 'function') {
const previousUpdateModelUi = updateModelUi;
updateModelUi = function qualityUpdateModelUi(...args) {
const result = previousUpdateModelUi(...args);
if (chipState() !== 'online') {
[loadButton, unloadButton, deleteButton].forEach(button => {
if (button) button.disabled = true;
});
}
syncConnectionUi();
return result;
};
}
if (typeof updateStatus === 'function') {
const previousUpdateStatus = updateStatus;
let statusRequest = null;
updateStatus = function qualityUpdateStatus(...args) {
if (statusRequest) return statusRequest;
statusRequest = Promise.resolve(previousUpdateStatus(...args))
.finally(() => {
statusRequest = null;
syncConnectionUi();
});
return statusRequest;
};
try {
clearInterval(statusInterval);
statusInterval = setInterval(updateStatus, currentStatusPollMs);
} catch { /* the base UI may not expose its timer in isolated tests */ }
}
new MutationObserver(syncConnectionUi).observe(deviceChipElement || document.body, {
attributes: true,
attributeFilter: ['class'],
childList: true,
subtree: true,
});
let toastTimer = null;
if (typeof showToast === 'function') {
showToast = function qualityToast(message, tone = '') {
const toast = document.getElementById('toast');
if (!toast) return;
const text = String(message || '');
const inferred = tone || (
/fail|error|offline|rejected|unavailable/i.test(text) ? 'error' :
/warning|stopping|queued|preparing/i.test(text) ? 'warning' :
/ready|complete|saved|copied|exported|started|unloaded|freed/i.test(text) ? 'success' : ''
);
toast.textContent = text;
toast.classList.remove('success', 'warning', 'error');
if (inferred) toast.classList.add(inferred);
toast.classList.add('show');
window.clearTimeout(toastTimer);
toastTimer = window.setTimeout(() => {
toast.classList.remove('show', 'success', 'warning', 'error');
}, Math.max(2400, Math.min(5200, 1600 + text.length * 24)));
};
}
const nativeClipboardWrite = navigator.clipboard?.writeText?.bind(navigator.clipboard);
async function resilientCopy(text) {
const value = String(text || '');
if (!value) throw new Error('Nothing to copy');
if (nativeClipboardWrite) {
try {
await nativeClipboardWrite(value);
return;
} catch { /* use the local fallback below */ }
}
const field = document.createElement('textarea');
field.value = value;
field.setAttribute('readonly', '');
field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;';
document.body.appendChild(field);
field.select();
field.setSelectionRange(0, field.value.length);
const copied = document.execCommand('copy');
field.remove();
if (!copied) throw new Error('Clipboard access is unavailable');
}
document.addEventListener('click', event => {
const button = event.target.closest('.code-copy, .meta-btn');
if (!button || button.classList.contains('regen')) return;
event.preventDefault();
event.stopImmediatePropagation();
const text = button.classList.contains('code-copy')
? button.closest('pre')?.querySelector('code')?.innerText || button.closest('pre')?.innerText || ''
: button.closest('.msg-row')?.querySelector('.bubble > div')?.innerText || '';
resilientCopy(text).then(() => {
if (button.classList.contains('code-copy')) {
const original = button.textContent;
button.textContent = 'Copied';
window.setTimeout(() => { button.textContent = original || 'Copy'; }, 1500);
}
if (typeof showToast === 'function') showToast('Copied', 'success');
}).catch(error => {
if (typeof showToast === 'function') showToast(`Copy failed: ${error.message}`, 'error');
});
}, true);
let activityFingerprint = '';
if (typeof renderActivityFeed === 'function') {
const previousRenderActivityFeed = renderActivityFeed;
renderActivityFeed = function qualityRenderActivityFeed(events) {
const normalizedEvents = Array.isArray(events) ? events : [];
const nextFingerprint = normalizedEvents.map(event =>
`${event.timestamp || ''}|${event.level || ''}|${event.message || ''}`
).join('\n');
if (nextFingerprint !== activityFingerprint) {
activityFingerprint = nextFingerprint;
try { lastEventCount = -1; } catch { /* isolated extension test */ }
}
if (!normalizedEvents.length) {
const list = document.getElementById('activity-list');
if (list) list.innerHTML = '<span class="activity-empty">No events yet</span>';
try { lastEventCount = 0; } catch { /* isolated extension test */ }
return;
}
return previousRenderActivityFeed(normalizedEvents);
};
}
function requestPath(input) {
const value = typeof input === 'string'
? input
: input instanceof URL
? input.href
: input?.url || '';
try {
const url = new URL(value, window.location.href);
return url.origin === window.location.origin ? url.pathname : '';
} catch {
return '';
}
}
const qualityPreviousFetch = window.fetch.bind(window);
window.fetch = async function qualityFetch(input, init = {}) {
const response = await qualityPreviousFetch(input, init);
if (requestPath(input) === '/v1/system/status' && response.ok) {
response.clone().json().then(data => {
if (typeof renderActivityFeed === 'function') renderActivityFeed(data?.events || []);
}).catch(() => {});
}
return response;
};
function syncModalAccessibility(open) {
document.documentElement.classList.toggle('ovllm-modal-open', open);
if (appRoot) appRoot.inert = open;
[
[searchTab, searchPanel],
[manualTab, manualPanel],
].forEach(([tab, panel]) => {
const selected = tab?.getAttribute('aria-selected') === 'true';
if (tab) tab.tabIndex = selected ? 0 : -1;
if (panel) panel.setAttribute('aria-hidden', String(!selected));
});
}
if (typeof selectCustomModelTab === 'function') {
const previousSelectCustomModelTab = selectCustomModelTab;
selectCustomModelTab = function qualitySelectCustomModelTab(tab, focus = false) {
const result = previousSelectCustomModelTab(tab);
syncModalAccessibility(true);
if (focus) (tab === 'search' ? searchTab : manualTab)?.focus();
return result;
};
}
if (typeof setCustomModelModalOpen === 'function') {
const previousSetCustomModelModalOpen = setCustomModelModalOpen;
setCustomModelModalOpen = function qualitySetCustomModelModalOpen(open) {
if (!open && appRoot) appRoot.inert = false;
const result = previousSetCustomModelModalOpen(open);
syncModalAccessibility(open);
return result;
};
}
customModal?.addEventListener('keydown', event => {
if (event.key === 'ArrowRight' || event.key === 'ArrowLeft' || event.key === 'Home' || event.key === 'End') {
const activeTab = event.target.closest('[role="tab"]');
if (activeTab) {
event.preventDefault();
const chooseSearch = event.key === 'Home'
|| (event.key === 'ArrowLeft' && activeTab === manualTab)
|| (event.key === 'ArrowRight' && activeTab === manualTab);
selectCustomModelTab(chooseSearch ? 'search' : 'manual', true);
return;
}
}
if (event.key !== 'Tab') return;
const focusable = Array.from(customModal.querySelectorAll(
'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
)).filter(element => !element.closest('[aria-hidden="true"]'));
if (!focusable.length) return;
const first = focusable[0];
const last = focusable[focusable.length - 1];
if (event.shiftKey && document.activeElement === first) {
event.preventDefault();
last.focus();
} else if (!event.shiftKey && document.activeElement === last) {
event.preventDefault();
first.focus();
}
});
syncModalAccessibility(false);
function mobilePanelOpen() {
if (window.innerWidth > 950) return false;
const chatsOpen = !document.getElementById('chats-sidebar')?.classList.contains('collapsed');
const settingsOpen = !document.getElementById('settings-sidebar')?.classList.contains('closed');
return chatsOpen || settingsOpen;
}
function syncPanelBackdrop() {
panelBackdrop.classList.toggle('visible', mobilePanelOpen());
}
if (typeof setChatsSidebarCollapsed === 'function') {
const previousSetChatsSidebarCollapsed = setChatsSidebarCollapsed;
setChatsSidebarCollapsed = function qualitySetChatsSidebarCollapsed(...args) {
const result = previousSetChatsSidebarCollapsed(...args);
syncPanelBackdrop();
return result;
};
}
if (typeof setSettingsSidebarOpen === 'function') {
const previousSetSettingsSidebarOpen = setSettingsSidebarOpen;
setSettingsSidebarOpen = function qualitySetSettingsSidebarOpen(...args) {
const result = previousSetSettingsSidebarOpen(...args);
syncPanelBackdrop();
return result;
};
}
panelBackdrop.addEventListener('click', () => {
if (typeof setChatsSidebarCollapsed === 'function') setChatsSidebarCollapsed(true);
if (typeof setSettingsSidebarOpen === 'function') setSettingsSidebarOpen(false, true);
syncPanelBackdrop();
});
window.addEventListener('resize', syncPanelBackdrop, { passive: true });
let deviceSwitchInFlight = false;
deviceSelectElement?.addEventListener('change', async event => {
if (deviceSwitchInFlight || typeof getSelectedModelMeta !== 'function') return;
const model = getSelectedModelMeta();
const nextDevice = typeof normalizeUiDevice === 'function'
? normalizeUiDevice(deviceSelectElement.value)
: String(deviceSelectElement.value || '').trim().toUpperCase();
if (!model?.is_loaded || !model.device || model.device === nextDevice) return;
event.preventDefault();
event.stopImmediatePropagation();
const previousDevice = model.device;
deviceSwitchInFlight = true;
document.querySelector('.header-right')?.classList.add('lifecycle-switching');
selectedDevice = nextDevice;
try { localStorage.setItem('ovllm.device.v1', selectedDevice); } catch { }
if (typeof updateDeviceWarning === 'function') updateDeviceWarning();
if (typeof showToast === 'function') {
showToast(`Switching ${model.name} to ${nextDevice}. The current model stays available while OpenVINO compiles the replacement…`, 'warning');
}
try {
const loadResponse = await fetch('/v1/models/load', {
method: 'POST',
headers: authHeaders({ 'Content-Type': 'application/json' }),
body: JSON.stringify({ model: model.id, device: nextDevice }),
});
const loadData = await loadResponse.json().catch(() => ({}));
if (loadResponse.status === 401) {
handleAuthRequired();
throw new Error('API key required');
}
if (!loadResponse.ok) throw new Error(loadData.detail || `Load failed (HTTP ${loadResponse.status})`);
if (typeof setStatusPolling === 'function') setStatusPolling(1000);
if (typeof showToast === 'function') showToast(loadData.message || `Preparing ${model.name} on ${nextDevice}…`, 'warning');
await updateStatus();
} catch (error) {
selectedDevice = previousDevice;
deviceSelectElement.value = previousDevice;
try { localStorage.setItem('ovllm.device.v1', previousDevice); } catch { }
if (typeof updateDeviceWarning === 'function') updateDeviceWarning();
if (typeof showToast === 'function') showToast(`Device switch failed: ${error.message}`, 'error');
await Promise.resolve(typeof updateStatus === 'function' ? updateStatus() : null);
} finally {
deviceSwitchInFlight = false;
document.querySelector('.header-right')?.classList.remove('lifecycle-switching');
if (typeof updateModelUi === 'function') updateModelUi();
}
}, true);
settingsButton?.addEventListener('click', () => window.setTimeout(syncPanelBackdrop, 0));
modelSelectElement?.addEventListener('change', syncConnectionUi);
syncConnectionUi();
syncPanelBackdrop();
})();
"""


def install_ui_quality_extension() -> None:
    """Compose reliability and accessibility hardening into the browser UI."""

    if getattr(ui_extension, "_UI_QUALITY_EXTENSION_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_ui_quality(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        payload = (
            f'\n<style id="{_EXTENSION_ID}-styles">\n{UI_QUALITY_CSS}\n</style>\n'
            f'<script id="{_EXTENSION_ID}">\n{UI_QUALITY_JS}\n</script>\n'
        )
        if "</body>" in html:
            return html.replace("</body>", f"{payload}</body>", 1)
        return html + payload

    ui_extension.inject_multimodal_ui = inject_with_ui_quality
    ui_extension._UI_QUALITY_EXTENSION_INSTALLED = True
