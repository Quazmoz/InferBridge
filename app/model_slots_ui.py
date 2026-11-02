"""Resident model slots in the browser: one chip per role with device, estimate and Unload.

Reads the ``models.slots`` field that ``/v1/models/status`` and ``/v1/system/status``
already return, through the runtime's passive response observer, so it adds no polling
of its own. Unload goes through ``POST /v1/models/unload {"slot": ...}``.
"""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-model-slots-extension"

MODEL_SLOTS_CSS = r"""
#ovllm-slot-strip{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 auto 8px;
  max-width:var(--chat-max-width,920px);padding:0 12px;font-size:11px;color:var(--text-2)}
#ovllm-slot-strip[hidden]{display:none!important}
#ovllm-slot-strip .ovllm-slot-title{font-weight:700;color:var(--text-3);margin-right:2px}
.ovllm-slot{display:inline-flex;align-items:center;gap:6px;min-height:30px;padding:3px 4px 3px 9px;
  border:1px solid var(--border);border-radius:999px;background:var(--surface-2);max-width:100%}
.ovllm-slot.empty{opacity:.72;border-style:dashed;padding-right:9px}
.ovllm-slot .ovllm-slot-role{font-weight:800;color:var(--text-1)}
.ovllm-slot .ovllm-slot-model{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px}
.ovllm-slot .ovllm-slot-meta{color:var(--text-3)}
.ovllm-slot button{min-height:24px;padding:2px 9px;border:1px solid var(--border);border-radius:999px;
  background:var(--surface-1);color:var(--text-1);font:inherit;font-size:10.5px;font-weight:700;cursor:pointer}
.ovllm-slot button:hover:not(:disabled){border-color:var(--primary)}
.ovllm-slot button:disabled{opacity:.5;cursor:not-allowed}
.ovllm-slot button:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
@media (forced-colors:active){.ovllm-slot{border:1px solid CanvasText}}
"""

MODEL_SLOTS_JS = r"""
(() => {
'use strict';
if (window.__inferbridgeModelSlotsInstalled) return;
window.__inferbridgeModelSlotsInstalled = true;
const IB = window.InferBridge;
if (!IB) return;

const ROLE_LABELS = { chat: 'Chat', embedding: 'Embeddings', rerank: 'Reranker' };
const strip = document.createElement('section');
strip.id = 'ovllm-slot-strip';
strip.hidden = true;
strip.setAttribute('aria-label', 'Resident models');
const title = document.createElement('span');
title.className = 'ovllm-slot-title';
title.textContent = 'Resident models';
strip.appendChild(title);
const anchor = IB.el('inputArea');
if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(strip, anchor);
else document.body.appendChild(strip);

const chips = new Map();
let lastSlots = [];

function chipFor(role) {
  let chip = chips.get(role);
  if (chip) return chip;
  chip = document.createElement('div');
  chip.className = 'ovllm-slot';
  chip.id = `ovllm-slot-${role}`;
  chip.setAttribute('role', 'group');
  const label = document.createElement('span');
  label.className = 'ovllm-slot-role';
  label.textContent = ROLE_LABELS[role] || role;
  const model = document.createElement('span');
  model.className = 'ovllm-slot-model';
  const meta = document.createElement('span');
  meta.className = 'ovllm-slot-meta';
  const extra = document.createElement('span');
  extra.className = 'ovllm-slot-extra';
  const unload = document.createElement('button');
  unload.type = 'button';
  unload.textContent = 'Unload';
  unload.addEventListener('click', () => unloadSlot(role, unload));
  chip.append(label, model, meta, extra, unload);
  chip._parts = { model, meta, unload };
  chips.set(role, chip);
  strip.appendChild(chip);
  return chip;
}

function render(slots) {
  if (!Array.isArray(slots)) return;
  lastSlots = slots;
  const anyLoaded = slots.some((slot) => slot && (slot.model || slot.loading_model));
  strip.hidden = !anyLoaded;
  for (const slot of slots) {
    if (!slot || !ROLE_LABELS[slot.role]) continue;
    const chip = chipFor(slot.role);
    const { model, meta, unload } = chip._parts;
    const roleLabel = ROLE_LABELS[slot.role];
    const resident = Boolean(slot.model);
    chip.classList.toggle('empty', !resident && !slot.loading_model);
    if (resident) {
      model.textContent = slot.name || slot.model;
      const estimate = typeof slot.memory_estimate_gib === 'number'
        ? ` · ≈${slot.memory_estimate_gib.toFixed(1)} GB` : '';
      const state = slot.status === 'switching' ? ' · switching' : slot.busy ? ' · busy' : '';
      meta.textContent = `${slot.device || ''}${estimate}${state}`;
    } else if (slot.loading_model) {
      model.textContent = slot.loading_model;
      meta.textContent = 'loading…';
    } else {
      model.textContent = 'none';
      meta.textContent = '';
    }
    unload.hidden = !resident;
    // Background status refreshes never disable controls permanently: only the
    // server-reported capability (busy or switching) does.
    unload.disabled = !slot.can_unload;
    unload.setAttribute('aria-label', resident
      ? `Unload ${slot.name || slot.model} from the ${roleLabel.toLowerCase()} slot`
      : `${roleLabel} slot is empty`);
    chip.setAttribute('aria-label', resident
      ? `${roleLabel} slot: ${slot.name || slot.model} on ${slot.device || 'unknown device'}`
      : `${roleLabel} slot: empty`);
  }
  IB.emit('slots:updated', { slots });
}

async function unloadSlot(role, button) {
  button.disabled = true;
  try {
    const headers = typeof authHeaders === 'function'
      ? authHeaders({ 'Content-Type': 'application/json' })
      : { 'Content-Type': 'application/json' };
    const response = await fetch('/v1/models/unload', {
      method: 'POST', headers, body: JSON.stringify({ slot: role }),
    });
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (typeof showToast === 'function') showToast(data.message || 'Model unloaded');
  } catch (error) {
    if (typeof showToast === 'function') showToast(`Unload failed: ${error.message}`);
  } finally {
    if (typeof updateStatus === 'function') {
      try { await updateStatus(); } catch { /* the next poll refreshes */ }
    }
    render(lastSlots);
  }
}

const isStatus = (request) => request.method === 'GET'
  && (request.path === '/v1/models/status' || request.path === '/v1/system/status');
IB.observe(isStatus, (body) => render(body && body.models && body.models.slots));
window.InferBridgeSlots = { render, current: () => lastSlots.slice() };
})();
"""

EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=MODEL_SLOTS_JS,
    css=MODEL_SLOTS_CSS,
    description="Resident chat, embedding and reranker models with per-slot Unload.",
)


def install_model_slots_ui() -> None:
    """Register the resident-slot strip."""

    ui_registry.register(EXTENSION)
