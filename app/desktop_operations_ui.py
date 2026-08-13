"""Small browser desktop-operations panel layered into the existing setup dialog."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-desktop-operations-extension"

_DESKTOP_OPERATIONS_CSS = r"""
#ovw-operations{margin-left:auto}.ovops-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.ovops-card{padding:12px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2)}.ovops-card strong{display:block;margin-top:4px}.ovops-note{padding:10px;border-left:4px solid var(--primary);background:var(--surface-2)}@media(max-width:680px){.ovops-grid{grid-template-columns:1fr}}
"""

_DESKTOP_OPERATIONS_JS = r"""
(() => {
'use strict';
if(window.__ovllmDesktopOperationsInstalled)return;
window.__ovllmDesktopOperationsInstalled=true;
const shell=document.getElementById('ovw-shell');
const content=document.getElementById('ovw-content');
const header=shell?.querySelector('.ovw-head');
if(!shell||!content||!header)return;
const button=document.createElement('button');
button.id='ovw-operations';button.className='ovw-btn';button.textContent='Desktop operations';
header.insertBefore(button,header.querySelector('#ovw-close'));
let priorContent='',priorSteps='';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options={}){let key='';try{key=localStorage.getItem('ovllm.apikey.v1')||''}catch{}const headers={'Content-Type':'application/json',...(options.headers||{})};if(key)headers.Authorization=`Bearer ${key}`;const response=await fetch(path,{...options,headers});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||`Request failed (${response.status})`);return body}
function buttons(items){return `<div class="ovw-actions">${items.map(x=>`<button class="ovw-btn ${x.primary?'ovw-primary':''}" data-ovops="${x.id}" ${x.disabled?'disabled':''}>${esc(x.label)}</button>`).join('')}</div>`}
async function render(){priorContent=content.innerHTML;const steps=shell.querySelector('.ovw-steps');priorSteps=steps?.innerHTML||'';if(steps)steps.innerHTML='<li class="active">Desktop operations</li>';content.innerHTML='<section class="ovw-panel"><h2>Desktop operations</h2><p>Loading local controller status…</p></section>';try{const status=await api('/v1/desktop/operations/status');const controller=status.controller_available?'Running':'Unavailable';content.innerHTML=`<section class="ovw-panel"><h2>Desktop operations</h2><div class="ovops-grid"><div class="ovops-card"><span>Tray controller</span><strong>${esc(controller)}</strong></div><div class="ovops-card"><span>Application version</span><strong>${esc(status.application_version)}</strong></div><div class="ovops-card"><span>Installation mode</span><strong>${esc(status.installation_mode)}</strong></div><div class="ovops-card"><span>Active server port</span><strong>${esc(status.server_port)}</strong></div><div class="ovops-card"><span>Data directory</span><strong>${esc(status.data_directory)}</strong></div><div class="ovops-card"><span>Start with Windows</span><strong>${status.start_with_windows?'Enabled':'Disabled'}</strong></div><div class="ovops-card"><span>Server state</span><strong>${esc(status.server_status)}</strong></div><div class="ovops-card"><span>Last diagnostics export</span><strong>${esc(status.last_diagnostics_export||'None')}</strong></div></div><p class="ovops-note">Diagnostics are created locally. Prompts, chat history, API keys, Hugging Face tokens, source images, model files, caches, certificates, and browser localStorage are excluded.</p>${buttons([{id:'export',label:'Export Diagnostics',primary:true},{id:'onboarding',label:'Restart Onboarding'},{id:'restart',label:'Restart Server',disabled:!status.controller_available},{id:'back',label:'Back'}])}<p id="ovops-result" aria-live="polite"></p></section>`}catch(error){content.innerHTML=`<section class="ovw-panel"><h2>Desktop operations</h2><p class="ovw-error">${esc(error.message||error)}</p>${buttons([{id:'back',label:'Back'}])}</section>`}}
button.addEventListener('click',render);
content.addEventListener('click',async event=>{const action=event.target.closest('[data-ovops]')?.dataset.ovops;if(!action)return;const result=content.querySelector('#ovops-result');try{if(action==='back'){content.innerHTML=priorContent;const steps=shell.querySelector('.ovw-steps');if(steps)steps.innerHTML=priorSteps;return}if(action==='export'){const confirmed=window.confirm('Create a local sanitized diagnostics ZIP?\n\nIncluded: application, Windows, hardware, OpenVINO, model state, benchmark summaries, configuration, and sanitized operational logs.\n\nExcluded: prompts, chat history, API keys, tokens, images, model files, caches, certificates, and browser localStorage.');if(!confirmed)return;const response=await api('/v1/desktop/operations/diagnostics/export',{method:'POST'});if(result)result.textContent=`Created ${response.filename}. Review the ZIP before attaching it to a GitHub issue.`}else if(action==='onboarding'){await api('/v1/onboarding/restart',{method:'POST'});location.reload()}else if(action==='restart'){await api('/v1/desktop/operations/restart-server',{method:'POST'});if(result)result.textContent='Restart requested. The tray controller will restore the server.'}}catch(error){if(result){result.className='ovw-error';result.textContent=error.message||String(error)}}});
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=_DESKTOP_OPERATIONS_JS,
    css=_DESKTOP_OPERATIONS_CSS,
    style_id="ovllm-desktop-operations-style",
    description="Desktop operation progress surface.",
)


def install_desktop_operations_ui_extension() -> None:
    """Register the desktop operation surface."""

    ui_registry.register(EXTENSION)
