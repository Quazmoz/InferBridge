"""Desktop storage and cache manager for the built-in InferBridge UI."""

from __future__ import annotations

import contextlib
import sys

from app import ui_extension

_EXTENSION_ID = "ovllm-storage-manager-extension"

_STORAGE_UI = r"""
<style id="ovllm-storage-manager-style">
#storage-manager-modal .modal-card{width:min(1040px,calc(100vw - 24px));max-height:min(900px,calc(100dvh - 24px));overflow:hidden}.sm-head p{margin-top:3px;color:var(--text-3);font-size:10px}.sm-body{min-height:0;overflow:auto;padding:16px 18px 22px;overscroll-behavior:contain}.sm-summary{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.sm-stat{min-width:0;padding:11px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2)}.sm-stat span,.sm-stat strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sm-stat span{color:var(--text-3);font-size:8px;text-transform:uppercase;letter-spacing:.55px}.sm-stat strong{margin-top:5px;color:var(--text-1);font-size:13px}.sm-stat.reclaim strong{color:var(--green)}.sm-section{margin-top:18px}.sm-section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:8px}.sm-section h4{font-size:12px}.sm-section-note{color:var(--text-3);font-size:9px}.sm-list{display:grid;gap:8px}.sm-row{display:grid;grid-template-columns:minmax(180px,1.4fr) repeat(3,minmax(100px,.7fr)) minmax(120px,auto);gap:10px;align-items:center;padding:11px 12px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 82%,transparent)}.sm-primary{min-width:0}.sm-primary strong,.sm-primary span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sm-primary strong{color:var(--text-1);font-size:10.5px}.sm-primary span{margin-top:3px;color:var(--text-3);font-size:8.5px}.sm-cell span,.sm-cell strong{display:block}.sm-cell span{color:var(--text-3);font-size:8px;text-transform:uppercase;letter-spacing:.45px}.sm-cell strong{margin-top:3px;color:var(--text-2);font-size:9.5px}.sm-health{color:var(--green)!important}.sm-health.warn{color:var(--amber)!important}.sm-btn{min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:9.5px;font-weight:700;cursor:pointer}.sm-btn:hover:not(:disabled){border-color:var(--primary);background:var(--surface-3)}.sm-btn.danger{color:var(--red);border-color:color-mix(in srgb,var(--red) 34%,var(--border))}.sm-btn:disabled{opacity:.5;cursor:not-allowed}.sm-empty,.sm-loading{display:grid;min-height:120px;place-items:center;padding:18px;border:1px dashed var(--border);border-radius:11px;color:var(--text-3);text-align:center;font-size:9.5px}.sm-message{margin-top:12px;padding:9px 10px;border-radius:9px;background:var(--surface-2);color:var(--text-2);font-size:9.5px}.sm-message.error{border:1px solid color-mix(in srgb,var(--red) 40%,var(--border));color:var(--red)}.sm-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 18px calc(11px + env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface-1)}.sm-footer-note{max-width:720px;color:var(--text-3);font-size:9px;line-height:1.4}.sm-actions{display:flex;gap:7px}@media(max-width:900px){.sm-summary{grid-template-columns:repeat(3,minmax(0,1fr))}.sm-row{grid-template-columns:minmax(160px,1.4fr) repeat(2,minmax(90px,.7fr)) minmax(110px,auto)}.sm-row .sm-optional{display:none}}@media(max-width:620px){#storage-manager-modal .modal-card{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}.sm-body{padding:12px}.sm-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.sm-row{grid-template-columns:1fr auto}.sm-cell{display:none}.sm-footer{align-items:stretch;flex-direction:column;padding:10px 12px calc(10px + env(safe-area-inset-bottom))}.sm-actions{display:grid;grid-template-columns:1fr 1fr}.sm-btn{min-height:42px}}
</style>
<script id="ovllm-storage-manager-extension">
(() => {
'use strict';
if(window.__ovllmStorageManagerInstalled)return;
window.__ovllmStorageManagerInstalled=true;
const header=document.querySelector('.header-right');
if(!header)return;
const icon='<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>';
const trigger=document.createElement('button');
trigger.type='button';trigger.id='storage-manager-btn';trigger.className='icon-btn';trigger.title='Storage and cache manager';trigger.setAttribute('aria-label','Open storage and cache manager');trigger.innerHTML=icon;
header.insertBefore(trigger,document.getElementById('model-library-btn')||document.getElementById('doctor-btn')||document.getElementById('settings-toggle-btn'));
const moreMenu=document.getElementById('ov-header-more-menu');
if(moreMenu){const marker=document.createComment('ov-header-placeholder-storage-manager-btn');trigger.parentNode?.insertBefore(marker,trigger);const item=document.createElement('div');item.className='ov-header-overflow-item';item.setAttribute('role','none');const label=document.createElement('span');label.className='ov-header-overflow-label';label.textContent='Storage and cache manager';const compact=window.matchMedia('(max-width: 760px)');const sync=()=>{if(compact.matches){item.prepend(trigger);if(!item.isConnected){item.append(label);moreMenu.append(item)}trigger.setAttribute('role','menuitem');trigger.tabIndex=-1}else{marker.parentNode?.insertBefore(trigger,marker.nextSibling);trigger.removeAttribute('role');trigger.removeAttribute('tabindex');item.remove()}};compact.addEventListener?.('change',sync);sync()}
const modal=document.createElement('div');modal.className='modal-overlay hidden';modal.id='storage-manager-modal';modal.setAttribute('aria-hidden','true');
modal.innerHTML=`<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="sm-title"><div class="modal-header"><div class="sm-head"><h3 id="sm-title">Storage and cache manager</h3><p>Understand managed disk use and remove only explicitly selected local files</p></div><button type="button" class="close-btn" id="sm-close" aria-label="Close storage manager">&times;</button></div><div class="sm-body"><div class="sm-summary" id="sm-summary"></div><div id="sm-content"><div class="sm-loading">Scanning managed storage…</div></div><div id="sm-message" aria-live="polite"></div></div><div class="sm-footer"><span class="sm-footer-note">Local paths are never returned to the browser. Transaction backups remain protected for automatic recovery. Removing a source cache requires downloading it again; clearing compiled cache can make the next load slower.</span><div class="sm-actions"><button type="button" class="sm-btn" id="sm-refresh">Refresh</button><button type="button" class="sm-btn" id="sm-done">Done</button></div></div></div>`;
document.body.appendChild(modal);
const $=selector=>modal.querySelector(selector);const content=$('#sm-content');const summary=$('#sm-summary');const message=$('#sm-message');let returnFocus=null;let controller=null;let data=null;
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const bytes=value=>{const amount=Math.max(0,Number(value)||0);if(amount<1024)return `${amount} B`;const units=['KB','MB','GB','TB'];let current=amount/1024,index=0;while(current>=1024&&index<units.length-1){current/=1024;index++}return `${current>=100?current.toFixed(0):current>=10?current.toFixed(1):current.toFixed(2)} ${units[index]}`};
const date=value=>value?new Date(Number(value)*1000).toLocaleString():'No generation recorded';
const headers=json=>{let key='';try{key=localStorage.getItem('ovllm.apikey.v1')||''}catch{}return {...(key?{Authorization:`Bearer ${key}`}:{ }),'X-OV-LLM-UI':'1',...(json?{'Content-Type':'application/json'}:{})}};
async function api(path,options={}){const response=await fetch(path,{...options,headers:{...headers(Boolean(options.body)),...(options.headers||{})}});let body={};try{body=await response.json()}catch{}if(!response.ok){const detail=body.detail;throw new Error(typeof detail==='object'?(detail.message||'Storage action failed.'):detail||`Request failed (${response.status})`)}return body}
function setMessage(text='',error=false){message.innerHTML=text?`<div class="sm-message ${error?'error':''}">${esc(text)}</div>`:''}
function capabilityButton(cleanup,label,modelId=''){const disabled=!cleanup?.available;const title=cleanup?.reason||`${bytes(cleanup?.reclaimable_bytes)} reclaimable`;return `<button type="button" class="sm-btn danger" data-action="${esc(cleanup?.action||'')}" data-model="${esc(modelId)}" data-bytes="${Number(cleanup?.reclaimable_bytes)||0}" title="${esc(title)}" ${disabled?'disabled':''}>${esc(label)}</button>`}
function section(title,note,rows,empty){return `<section class="sm-section"><div class="sm-section-head"><h4>${esc(title)}</h4><span class="sm-section-note">${esc(note)}</span></div><div class="sm-list">${rows||`<div class="sm-empty">${esc(empty)}</div>`}</div></section>`}
function modelRows(){return (data.models||[]).filter(item=>item.converted_size_bytes>0).map(item=>{const health=item.conversion_health||{};const used=item.last_used?.status==='loaded_now'?'Loaded now':date(item.last_used?.timestamp);const warning=health.status!=='compatible';return `<div class="sm-row"><div class="sm-primary"><strong>${esc(item.name)}</strong><span>${esc(item.model_id)}</span></div><div class="sm-cell"><span>Converted size</span><strong>${bytes(item.converted_size_bytes)}</strong></div><div class="sm-cell sm-optional"><span>Conversion health</span><strong class="sm-health ${warning?'warn':''}" title="${esc(health.details||'')}">${esc(health.label||health.status)}</strong></div><div class="sm-cell"><span>Last used</span><strong>${esc(used)}</strong></div>${capabilityButton(item.cleanup,'Delete model',item.model_id)}</div>`}).join('')}
function cacheRows(){return (data.source_caches||[]).filter(item=>item.size_bytes>0||item.cleanup?.reason).map(item=>`<div class="sm-row"><div class="sm-primary"><strong>${esc(item.source_model)}</strong><span>${esc(item.shared?`Shared by ${item.model_names.join(', ')}`:item.model_names.join(', '))}</span></div><div class="sm-cell"><span>Reusable cache</span><strong>${bytes(item.size_bytes)}</strong></div><div class="sm-cell sm-optional"><span>State</span><strong>${esc(item.state)}</strong></div><div class="sm-cell"><span>Future effect</span><strong>Download again</strong></div>${capabilityButton(item.cleanup,'Remove cache',item.cleanup?.model_id||'')}</div>`).join('')}
function recoveryRows(){return (data.recovery_items||[]).map(item=>`<div class="sm-row"><div class="sm-primary"><strong>${esc(item.name)}</strong><span>${esc(item.state.replaceAll('_',' '))}${item.protected_backup_bytes?` · ${bytes(item.protected_backup_bytes)} protected backup`:''}</span></div><div class="sm-cell"><span>Reclaimable</span><strong>${bytes(item.size_bytes)}</strong></div><div class="sm-cell sm-optional"><span>Incomplete output</span><strong>${bytes(item.parts?.incomplete_output_bytes)}</strong></div><div class="sm-cell"><span>Staging</span><strong>${bytes(item.parts?.staging_bytes)}</strong></div>${capabilityButton(item.cleanup,'Clean up',item.model_id)}</div>`).join('')}
function render(){const totals=data?.totals||{};summary.innerHTML=[['Converted models',totals.converted_models_bytes],['Hugging Face cache',totals.huggingface_cache_bytes],['Recovery data',totals.incomplete_recovery_bytes],['Compiled cache',totals.compiled_cache_bytes],['Reclaimable now',totals.currently_reclaimable_bytes,'reclaim']].map(([label,value,kind])=>`<div class="sm-stat ${kind||''}"><span>${esc(label)}</span><strong>${bytes(value)}</strong></div>`).join('');const compiled=data.compiled_cache||{};const compiledRows=`<div class="sm-row"><div class="sm-primary"><strong>OpenVINO compiled cache</strong><span>Reusable device-specific compilation artifacts</span></div><div class="sm-cell"><span>Cache size</span><strong>${bytes(compiled.size_bytes)}</strong></div><div class="sm-cell sm-optional"><span>State</span><strong>${esc(compiled.state)}</strong></div><div class="sm-cell"><span>Future effect</span><strong>Next load may slow</strong></div>${capabilityButton(compiled.cleanup,'Clear cache')}</div>`;content.innerHTML=section('Converted models','Health and last successful use',modelRows(),'No converted models are using managed storage.')+section('Reusable Hugging Face source cache','Shared downloads are counted once',cacheRows(),'No reusable source cache was found.')+section('Interrupted preparation and recovery','Transaction backups are protected',recoveryRows(),'No incomplete preparation data was found.')+section('OpenVINO compiled cache','Clear only while all models are unloaded',compiledRows,'No compiled cache was found.')}
async function load(){controller?.abort();controller=new AbortController();content.innerHTML='<div class="sm-loading">Scanning managed storage…</div>';summary.innerHTML='';setMessage();try{data=await api('/v1/storage',{signal:controller.signal});render()}catch(error){if(error.name==='AbortError')return;content.innerHTML='<div class="sm-empty">Storage inventory is unavailable.</div>';setMessage(error.message||String(error),true)}}
function close(){modal.classList.add('hidden');modal.setAttribute('aria-hidden','true');controller?.abort();returnFocus?.focus?.()}
trigger.addEventListener('click',()=>{returnFocus=document.activeElement;modal.classList.remove('hidden');modal.setAttribute('aria-hidden','false');$('#sm-close').focus();load()});
$('#sm-close').addEventListener('click',close);$('#sm-done').addEventListener('click',close);$('#sm-refresh').addEventListener('click',load);modal.addEventListener('click',event=>{if(event.target===modal)close()});
modal.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close()}});
content.addEventListener('click',async event=>{const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const action=button.dataset.action;const modelId=button.dataset.model||'';const reclaim=bytes(button.dataset.bytes);const consequence=action==='delete_converted_model'?'The model must be converted again before it can be loaded.':action==='remove_huggingface_cache'?'A future conversion will download the source files again.':action==='clear_compiled_cache'?'The next model load may take longer while OpenVINO recompiles.':'Only incomplete output, staging files, and recovery metadata will be removed. Protected transaction backups are not touched.';if(!window.confirm(`Reclaim approximately ${reclaim}?\n\n${consequence}`))return;button.disabled=true;setMessage('Cleaning managed storage…');try{const body={action,...(modelId?{model_id:modelId}:{})};const result=await api('/v1/storage/cleanup',{method:'POST',body:JSON.stringify(body)});await load();setMessage(`${result.message} Freed ${bytes(result.freed_bytes)}.`)}catch(error){setMessage(error.message||String(error),true);button.disabled=false}});
})();
</script>
"""


def install_storage_manager_ui_extension() -> None:
    """Append the storage manager after the existing composed UI extensions."""

    if getattr(ui_extension, "_STORAGE_MANAGER_UI_INSTALLED", False):
        return
    previous = ui_extension.inject_multimodal_ui

    def inject(html: str) -> str:
        html = previous(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        if "</body>" in html:
            return html.replace("</body>", f"\n{_STORAGE_UI}\n</body>", 1)
        return html + _STORAGE_UI

    ui_extension.inject_multimodal_ui = inject
    ui_extension._STORAGE_MANAGER_UI_INSTALLED = True

    # Desktop tests can import app.server before app.desktop_server. Keep the server's
    # bound injection function and cached page aligned when this extension is installed late.
    server = sys.modules.get("app.server")
    if server is not None:
        server.inject_multimodal_ui = inject
        cache = getattr(server, "_index_html", None)
        if cache is not None:
            with contextlib.suppress(AttributeError):
                cache.cache_clear()


__all__ = ["install_storage_manager_ui_extension"]
