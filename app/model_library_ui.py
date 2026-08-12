"""Curated model-library browser for the built-in local UI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-model-library-extension"

# fmt: off
MODEL_LIBRARY_CSS = r"""
#model-library-btn{position:relative}.ml-modal .modal-card{width:min(1080px,calc(100vw - 24px));max-height:min(920px,calc(100dvh - 24px));overflow:hidden}.ml-head p{margin-top:3px;color:var(--text-3);font-size:10px}.ml-body{min-height:0;overflow:auto;padding:16px 18px 22px;overscroll-behavior:contain}.ml-toolbar{display:grid;grid-template-columns:minmax(180px,1fr) auto auto;gap:8px;align-items:center}.ml-search,.ml-path,.ml-field{width:100%;min-height:38px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);padding:8px 10px;font:inherit;font-size:10px}.ml-btn,.ml-chip{min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.ml-btn:hover:not(:disabled),.ml-chip:hover{border-color:var(--primary);background:var(--surface-3)}.ml-btn.primary,.ml-chip.active{border-color:transparent;background:var(--primary);color:#fff}.ml-btn:disabled{opacity:.5;cursor:not-allowed}.ml-profiles{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}.ml-summary{display:flex;justify-content:space-between;gap:12px;margin:14px 0 10px;color:var(--text-3);font-size:9.5px}.ml-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.ml-card{display:flex;min-width:0;flex-direction:column;gap:11px;padding:14px;border:1px solid var(--border);border-radius:13px;background:color-mix(in srgb,var(--surface-2) 80%,transparent)}.ml-card-head{display:flex;justify-content:space-between;gap:10px}.ml-card h4{font-size:13px;color:var(--text-1)}.ml-id{margin-top:2px;color:var(--text-3);font-size:8.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ml-desc{color:var(--text-2);font-size:9.5px;line-height:1.45}.ml-badges{display:flex;flex-wrap:wrap;gap:5px}.ml-badge{display:inline-flex;padding:3px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-3);font-size:8.5px;font-weight:700}.ml-badge.verified{color:var(--green);border-color:color-mix(in srgb,var(--green) 38%,var(--border))}.ml-badge.local{color:var(--primary);border-color:color-mix(in srgb,var(--primary) 40%,var(--border))}.ml-badge.warn{color:var(--amber);border-color:color-mix(in srgb,var(--amber) 38%,var(--border))}.ml-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.ml-stat{min-width:0;padding:8px;border-radius:9px;background:var(--surface-3)}.ml-stat span,.ml-stat strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ml-stat span{color:var(--text-3);font-size:8px;text-transform:uppercase;letter-spacing:.55px}.ml-stat strong{margin-top:4px;color:var(--text-1);font-size:10px}.ml-health{padding:8px 9px;border-left:3px solid var(--border);border-radius:6px;background:var(--surface-3);color:var(--text-2);font-size:9px;line-height:1.4}.ml-health.compatible{border-color:var(--green)}.ml-health.stale_runtime,.ml-health.incomplete,.ml-health.incompatible_definition,.ml-health.invalid_metadata{border-color:var(--amber)}.ml-quant{color:var(--text-2);font-size:9px;line-height:1.42}.ml-quant strong{color:var(--text-1)}.ml-actions{display:flex;gap:7px;margin-top:auto}.ml-actions .ml-btn{flex:1}.ml-empty,.ml-loading{display:grid;min-height:280px;place-items:center;color:var(--text-3);text-align:center;font-size:10px}.ml-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 18px calc(11px + env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface-1)}.ml-footer-note{max-width:650px;color:var(--text-3);font-size:9px;line-height:1.4}.ml-footer-actions{display:flex;gap:7px}.ml-import-panel{display:none;margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2)}.ml-import-panel.open{display:block}.ml-import-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.ml-import-grid .wide{grid-column:1/-1}.ml-import-panel h4{margin-bottom:8px;font-size:11px}.ml-import-actions{display:flex;justify-content:flex-end;gap:7px;margin-top:9px}.ml-error{margin-top:10px;padding:9px;border:1px solid color-mix(in srgb,var(--red) 40%,var(--border));border-radius:8px;color:var(--red);font-size:9.5px}.ml-check{display:flex;align-items:center;gap:6px;color:var(--text-2);font-size:9.5px}.ml-check input{accent-color:var(--primary)}
@media(max-width:820px){.ml-grid{grid-template-columns:1fr}.ml-toolbar{grid-template-columns:1fr auto}.ml-toolbar .ml-check{grid-column:1/-1}.ml-import-grid{grid-template-columns:1fr}.ml-import-grid .wide{grid-column:auto}}@media(max-width:540px){.ml-modal .modal-card{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}.ml-body{padding:12px}.ml-toolbar{grid-template-columns:1fr}.ml-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.ml-footer{align-items:stretch;flex-direction:column;padding:10px 12px calc(10px + env(safe-area-inset-bottom))}.ml-footer-actions{display:grid;grid-template-columns:1fr 1fr}.ml-btn{min-height:42px}}
"""

MODEL_LIBRARY_JS = r"""
(() => {
'use strict';
if (window.__ovllmModelLibraryInstalled) return;
window.__ovllmModelLibraryInstalled = true;

const header = document.querySelector('.header-right');
if (!header) return;

const icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M4 5.5v16"/><path d="M8 7h8M8 11h6"/></svg>';
const trigger = document.createElement('button');
trigger.type = 'button';
trigger.id = 'model-library-btn';
trigger.className = 'icon-btn';
trigger.title = 'Open Model Library Evidence';
trigger.setAttribute('aria-label', 'Open Model Library Evidence');
trigger.innerHTML = icon;
header.insertBefore(
  trigger,
  document.getElementById('doctor-btn') || document.getElementById('settings-toggle-btn')
);

const modal = document.createElement('div');
modal.className = 'modal-overlay hidden ml-modal';
modal.id = 'model-library-modal';
modal.setAttribute('aria-hidden', 'true');
modal.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="ml-title"><div class="modal-header"><div class="ml-head"><h3 id="ml-title">Verified Model Library</h3><p>Maintained recommendations, local evidence, conversion health, and safe imports</p></div><button type="button" class="close-btn" id="ml-close" aria-label="Close model library">&times;</button></div><div class="ml-body"><div class="ml-toolbar"><input class="ml-search" id="ml-search" type="search" maxlength="160" placeholder="Search maintained models"><button type="button" class="ml-btn" id="ml-refresh">Refresh official catalog</button><label class="ml-check"><input type="checkbox" id="ml-all"> Show all registered</label></div><div class="ml-profiles" id="ml-profiles"></div><div class="ml-import-panel" id="ml-import-panel"><h4>Import converted OpenVINO model</h4><div class="ml-import-grid"><input class="ml-field" id="ml-import-id" placeholder="Model ID, for example my-model-int4"><input class="ml-field" id="ml-import-name" placeholder="Display name"><input class="ml-path wide" id="ml-import-path" placeholder="Absolute Windows directory containing OpenVINO IR"><select class="ml-field" id="ml-import-backend"><option value="openvino-genai">Text generation</option><option value="openvino-vlm">Vision language</option><option value="openvino-embeddings">Embeddings</option></select><select class="ml-field" id="ml-import-format"><option value="fp16">FP16</option><option value="int8">INT8</option><option value="int4">INT4</option></select><select class="ml-field" id="ml-import-device"><option>CPU</option><option>GPU</option><option>NPU</option><option>AUTO</option></select><input class="ml-field" id="ml-import-context" type="number" min="128" max="262144" value="2048" aria-label="Maximum context length"></div><div class="ml-import-actions"><button type="button" class="ml-btn" id="ml-import-cancel">Cancel</button><button type="button" class="ml-btn primary" id="ml-import-submit">Import and manage copy</button></div></div><div class="ml-summary"><span id="ml-summary">Loading model library…</span><span id="ml-source"></span></div><div id="ml-error"></div><div class="ml-grid" id="ml-grid"><div class="ml-loading">Loading model evidence…</div></div></div><div class="ml-footer"><span class="ml-footer-note">Verified means a retained certification record exists. Local benchmarks are labeled separately. Expected compatibility is never presented as verified.</span><div class="ml-footer-actions"><button type="button" class="ml-btn" id="ml-import-defs">Import definitions</button><button type="button" class="ml-btn" id="ml-import-converted">Import converted</button><button type="button" class="ml-btn" id="ml-export">Export definitions</button><input type="file" id="ml-file" accept="application/json,.json" hidden></div></div></div>`;
document.body.appendChild(modal);
modal.querySelector('#ml-title').textContent = 'Model Library Evidence';
modal.querySelector('.ml-head p').textContent =
  'Bundled certification and your local benchmark results are separate evidence sources';
modal.querySelector('.ml-footer-note').textContent =
  'Bundled certification requires a retained report and checksum. Local benchmarks describe only this PC and never become bundled certification automatically. Expected compatibility is unverified.';

const $ = selector => modal.querySelector(selector);
const grid = $('#ml-grid');
const summary = $('#ml-summary');
const source = $('#ml-source');
const error = $('#ml-error');
const search = $('#ml-search');
const showAll = $('#ml-all');
const profiles = $('#ml-profiles');
const panel = $('#ml-import-panel');
let profile = 'balanced';
let returnFocus = null;
let controller = null;
let data = null;
let searchTimer = null;

const escapeHtml = value => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');
const number = (value, digits = 1) => Number.isFinite(Number(value))
  ? Number(value).toFixed(digits)
  : 'Unknown';
const headers = () => {
  let key = '';
  try { key = localStorage.getItem('ovllm.apikey.v1') || ''; } catch {}
  return key ? {Authorization: `Bearer ${key}`} : {};
};
const jsonHeaders = () => ({...headers(), 'Content-Type': 'application/json'});

function showError(message = '') {
  error.innerHTML = message ? `<div class="ml-error">${escapeHtml(message)}</div>` : '';
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {...headers(), ...(options.headers || {})},
  });
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function renderProfiles() {
  profiles.innerHTML = ['fastest', 'balanced', 'best_quality', 'lowest_memory']
    .map(value => `<button type="button" class="ml-chip ${value === profile ? 'active' : ''}" data-profile="${value}">${value.replaceAll('_', ' ')}</button>`)
    .join('');
  profiles.querySelectorAll('[data-profile]').forEach(button => {
    button.onclick = () => {
      profile = button.dataset.profile;
      renderProfiles();
      load();
    };
  });
}

function badge(info = {}) {
  const kind = info.status === 'verified'
    ? 'verified'
    : info.status === 'locally_verified'
      ? 'local'
      : 'warn';
  const title = info.driver_version || 'No retained certification record';
  return `<span class="ml-badge ${kind}" title="${escapeHtml(title)}">${escapeHtml(info.label || 'Unverified')}</span>`;
}

function action(item) {
  const state = item.runtime || {};
  const recommendation = item.recommended_quantization || {};
  const device = recommendation.device || item.recommended_device || 'CPU';
  const format = recommendation.format || item.weight_format || 'fp16';
  const health = item.conversion_health || {};
  if (state.is_loaded) {
    return `<button class="ml-btn" disabled>Loaded on ${escapeHtml(state.device || 'device')}</button>`;
  }
  if (state.is_loading) {
    return `<button class="ml-btn" disabled>${escapeHtml(state.status_label || 'Preparing')}</button>`;
  }
  if (state.is_downloaded) {
    if (['stale_runtime', 'incompatible_definition', 'invalid_metadata'].includes(health.status)) {
      return `<button class="ml-btn primary" data-convert="${escapeHtml(item.id)}" data-device="${escapeHtml(device)}" data-format="${escapeHtml(format)}">Reconvert ${escapeHtml(format.toUpperCase())}</button>`;
    }
    return `<button class="ml-btn primary" data-load="${escapeHtml(item.id)}" data-device="${escapeHtml(device)}">Load</button>`;
  }
  if (!state.can_convert) {
    return '<button class="ml-btn" disabled>No conversion source</button>';
  }
  return `<button class="ml-btn primary" data-convert="${escapeHtml(item.id)}" data-device="${escapeHtml(device)}" data-format="${escapeHtml(format)}">Convert ${escapeHtml(format.toUpperCase())}</button>`;
}

function render() {
  if (!data || !Array.isArray(data.items)) {
    grid.innerHTML = '<div class="ml-empty">No model-library data is available.</div>';
    return;
  }
  summary.textContent = `${data.count} ${data.profile.replaceAll('_', ' ')} recommendation${data.count === 1 ? '' : 's'}`;
  source.textContent = data.manifest?.source === 'official-cache'
    ? 'Official release manifest'
    : data.manifest?.source === 'bundled'
      ? 'Offline bundled catalog'
      : 'Fallback catalog';
  grid.innerHTML = data.items.map(item => {
    const requirements = item.requirements || {};
    const metrics = item.metrics || {};
    const health = item.conversion_health || {};
    const recommendation = item.recommended_quantization || {};
    const license = item.gated ? `${item.license} · gated` : item.license;
    const measurementSource = metrics.measurement_source === 'local'
      ? 'Local benchmark'
      : metrics.measurement_source === 'official'
        ? 'Bundled certification'
        : 'Unmeasured';
    const measurement = metrics.measurement_device
      ? `${measurementSource} · ${metrics.measurement_device}`
      : measurementSource;
    return `<article class="ml-card"><div class="ml-card-head"><div><h4>${escapeHtml(item.name)}</h4><div class="ml-id">${escapeHtml(item.id)} · ${escapeHtml(item.backend)}</div></div><span class="ml-badge">${escapeHtml(String(item.weight_format || '').toUpperCase())}</span></div><div class="ml-desc">${escapeHtml(item.description || item.maintainer_note)}</div><div class="ml-badges">${['CPU', 'GPU', 'NPU'].map(device => badge(item.verification?.[device])).join('')}</div><div class="ml-stats"><div class="ml-stat"><span>Minimum RAM</span><strong>${number(requirements.minimum_ram_gb)} GB</strong></div><div class="ml-stat"><span>Minimum disk</span><strong>${number(requirements.minimum_disk_gb)} GB</strong></div><div class="ml-stat"><span>First load</span><strong>${metrics.time_to_first_load_ms != null ? number(metrics.time_to_first_load_ms / 1000, 1) + ' s' : 'Unmeasured'}</strong></div><div class="ml-stat"><span>Throughput</span><strong>${metrics.tokens_sec != null ? number(metrics.tokens_sec, 1) + ' t/s' : 'Unmeasured'}</strong></div><div class="ml-stat"><span>First token</span><strong>${metrics.time_to_first_token_ms != null ? number(metrics.time_to_first_token_ms, 0) + ' ms' : 'Unmeasured'}</strong></div><div class="ml-stat"><span>Evidence</span><strong>${escapeHtml(measurement)}</strong></div><div class="ml-stat"><span>Tested context</span><strong>${metrics.maximum_tested_context || 'Unverified'}</strong></div><div class="ml-stat"><span>License</span><strong title="${escapeHtml(license)}">${escapeHtml(license)}</strong></div><div class="ml-stat"><span>Tested OpenVINO</span><strong>${escapeHtml(metrics.tested_openvino_version || 'Unverified')}</strong></div><div class="ml-stat"><span>Tested driver</span><strong title="${escapeHtml(metrics.tested_driver_version || 'Unverified')}">${escapeHtml(metrics.tested_driver_version || 'Unverified')}</strong></div><div class="ml-stat"><span>Last certified</span><strong>${escapeHtml(metrics.last_certification_date || 'Never')}</strong></div><div class="ml-stat"><span>Local status</span><strong>${escapeHtml(item.runtime?.status_label || 'Unknown')}</strong></div></div><div class="ml-health ${escapeHtml(health.status)}"><strong>${escapeHtml(health.label || 'Unknown conversion state')}</strong>${health.details ? `<br>${escapeHtml(health.details)}` : ''}</div><div class="ml-quant"><strong>Recommended:</strong> ${escapeHtml(String(recommendation.format || item.weight_format || '').toUpperCase())} on ${escapeHtml(recommendation.device || item.recommended_device)}. ${escapeHtml(recommendation.reason || '')}</div><div class="ml-actions">${action(item)}</div></article>`;
  }).join('') || '<div class="ml-empty">No models match this profile and search.</div>';
  grid.querySelectorAll('[data-load]').forEach(button => {
    button.onclick = () => prepare(button, 'load');
  });
  grid.querySelectorAll('[data-convert]').forEach(button => {
    button.onclick = () => prepare(button, 'convert');
  });
}

async function prepare(button, kind) {
  button.disabled = true;
  showError();
  try {
    const id = button.dataset[kind];
    const device = button.dataset.device;
    if (kind === 'load') {
      await api('/v1/models/load', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({model: id, device}),
      });
    } else {
      await api('/v1/models/convert', {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({
          model: id,
          device,
          weight_format: button.dataset.format,
          load_after: true,
        }),
      });
    }
    await load();
  } catch (err) {
    showError(err.message);
    button.disabled = false;
  }
}

async function load() {
  controller?.abort();
  controller = new AbortController();
  grid.innerHTML = '<div class="ml-loading">Loading model evidence…</div>';
  showError();
  const params = new URLSearchParams({
    profile,
    query: search.value.trim(),
    include_all: String(showAll.checked),
  });
  try {
    data = await api(`/v1/model-library?${params}`, {signal: controller.signal});
    render();
  } catch (err) {
    if (err.name !== 'AbortError') {
      grid.innerHTML = '<div class="ml-empty">Model library could not be loaded.</div>';
      showError(err.message);
    }
  }
}

function open() {
  returnFocus = document.activeElement;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  renderProfiles();
  load();
  search.focus();
}

function close() {
  controller?.abort();
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  panel.classList.remove('open');
  returnFocus?.focus?.();
}

trigger.onclick = open;
$('#ml-close').onclick = close;
modal.addEventListener('click', event => {
  if (event.target === modal) close();
});
modal.addEventListener('keydown', event => {
  if (event.key === 'Escape') close();
});
search.oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(load, 180);
};
showAll.onchange = load;

$('#ml-refresh').onclick = async event => {
  const button = event.currentTarget;
  button.disabled = true;
  showError();
  try {
    await api('/v1/model-library/refresh', {method: 'POST', headers: jsonHeaders()});
    await load();
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
  }
};

$('#ml-export').onclick = async () => {
  showError();
  try {
    const response = await fetch(
      `/v1/model-library/export?include_all=${showAll.checked}`,
      {headers: headers()}
    );
    if (!response.ok) throw new Error('Definition export failed.');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'openvino-model-definitions.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    showError(err.message);
  }
};

const file = $('#ml-file');
$('#ml-import-defs').onclick = () => file.click();
file.onchange = async () => {
  const selected = file.files?.[0];
  if (!selected) return;
  showError();
  try {
    if (selected.size > 1_000_000) throw new Error('Definition file exceeds 1 MB.');
    const payload = JSON.parse(await selected.text());
    await api('/v1/model-library/import-definitions', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({payload, overwrite: false}),
    });
    await load();
  } catch (err) {
    showError(err.message);
  } finally {
    file.value = '';
  }
};

$('#ml-import-converted').onclick = () => {
  panel.classList.add('open');
  $('#ml-import-id').focus();
};
$('#ml-import-cancel').onclick = () => panel.classList.remove('open');
$('#ml-import-submit').onclick = async event => {
  const button = event.currentTarget;
  const backend = $('#ml-import-backend').value;
  const modelId = $('#ml-import-id').value.trim();
  const name = $('#ml-import-name').value.trim();
  const sourcePath = $('#ml-import-path').value.trim();
  const rawContext = Number($('#ml-import-context').value);
  const maxContext = Number.isFinite(rawContext)
    ? Math.min(Math.max(Math.trunc(rawContext), 128), 262144)
    : 2048;
  button.disabled = true;
  showError();
  try {
    if (!modelId || !name || !sourcePath) {
      throw new Error('Model ID, display name, and source directory are required.');
    }
    const body = {
      model_id: modelId,
      name,
      source_path: sourcePath,
      backend,
      weight_format: $('#ml-import-format').value,
      recommended_device: $('#ml-import-device').value,
      max_context_len: maxContext,
      max_output_tokens: backend === 'openvino-embeddings'
        ? 0
        : Math.min(512, maxContext - 1),
      overwrite: false,
    };
    await api('/v1/model-library/import-converted', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(body),
    });
    panel.classList.remove('open');
    await load();
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
  }
};
})();
"""
# fmt: on


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=MODEL_LIBRARY_JS,
    css=MODEL_LIBRARY_CSS,
    description="Verified Model Library browser and evidence view.",
)


def install_model_library_ui_extension() -> None:
    """Register the Verified Model Library surface."""

    ui_registry.register(EXTENSION)
