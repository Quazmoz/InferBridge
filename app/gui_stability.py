"""Final browser-GUI stability fixes layered over the composed local client."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-gui-stability-extension"

GUI_STABILITY_CSS = r"""
:root {
    /* Older search-error markup referenced this token. Keep it mapped to the
       established destructive color instead of rendering an invalid value. */
    --danger: var(--red);
}

.ovllm-gui-stable .modal-card {
    max-height: 90vh;
    max-height: calc(100dvh - max(16px, env(safe-area-inset-top)) - max(16px, env(safe-area-inset-bottom)));
    overscroll-behavior: contain;
}

.ovllm-gui-stable .modal-form,
.ovllm-gui-stable .modal-panel,
.ovllm-gui-stable .form-group,
.ovllm-gui-stable .search-result-copy {
    min-width: 0;
}

.ovllm-gui-stable .search-row input {
    min-width: 0;
}

.ovllm-gui-stable .search-result-item {
    gap: 12px;
}

.ovllm-gui-stable .search-result-copy {
    flex: 1 1 auto;
}

.ovllm-gui-stable .search-result-name {
    overflow-wrap: anywhere;
}

.ovllm-gui-stable #hf-search-results[aria-busy="true"] {
    cursor: progress;
}

.ovllm-gui-stable #ov-header-more-menu {
    max-height: min(70dvh, 420px);
    overflow-y: auto;
    overscroll-behavior: contain;
}

@media (max-width: 560px) {
    .ovllm-gui-stable .modal-overlay {
        align-items: flex-start;
        padding: max(8px, env(safe-area-inset-top)) 8px max(8px, env(safe-area-inset-bottom));
    }

    .ovllm-gui-stable .modal-card {
        width: 100%;
        max-height: calc(100dvh - max(16px, env(safe-area-inset-top)) - max(16px, env(safe-area-inset-bottom)));
    }

    .ovllm-gui-stable .search-row {
        flex-direction: column;
    }

    .ovllm-gui-stable .search-row button {
        width: 100%;
        min-height: 44px;
    }

    .ovllm-gui-stable .form-group input,
    .ovllm-gui-stable .form-group select {
        font-size: 16px;
    }
}
"""

GUI_STABILITY_JS = r"""(() => {
'use strict';
if (window.__ovllmGuiStabilityInstalled) return;
window.__ovllmGuiStabilityInstalled = true;
document.documentElement.classList.add('ovllm-gui-stable');

const defer = callback => {
    if (typeof queueMicrotask === 'function') queueMicrotask(callback);
    else Promise.resolve().then(callback);
};

/* Recompute legitimate model/composer disables when the local server comes
   back. The earlier connection layer intentionally disables controls while
   offline, but a successful poll did not restore them. */
const deviceChipElement = document.getElementById('device-chip');
const deviceLabelElement = document.getElementById('device-label');
function connectionUnavailable() {
    const label = String(deviceLabelElement?.textContent || '').trim().toLowerCase();
    return Boolean(
        deviceChipElement?.classList.contains('offline') ||
        label.includes('offline') ||
        label.includes('auth required') ||
        label.includes('connecting')
    );
}
let wasUnavailable = connectionUnavailable();
function repairControlsAfterReconnect() {
    const unavailable = connectionUnavailable();
    if (!unavailable && wasUnavailable) {
        defer(() => {
            if (typeof updateModelUi === 'function') updateModelUi();
            if (typeof updateSendButtonState === 'function') updateSendButtonState();
        });
    }
    wasUnavailable = unavailable;
}
if (deviceChipElement) {
    new MutationObserver(repairControlsAfterReconnect).observe(deviceChipElement, {
        attributes: true,
        attributeFilter: ['class'],
        childList: true,
        subtree: true,
    });
}
repairControlsAfterReconnect();

/* Render remote model-search metadata with text nodes, bound result counts,
   and cancellation. This prevents stale requests from replacing newer results
   and avoids treating Hub-provided metadata as HTML. */
const searchInput = document.getElementById('hf-search-input');
const searchButton = document.getElementById('hf-search-btn');
const searchTask = document.getElementById('hf-search-task');
const searchResults = document.getElementById('hf-search-results');
let searchController = null;

function setSearchMessage(message, tone = '') {
    if (!searchResults) return;
    const node = document.createElement('div');
    node.className = 'search-empty';
    if (tone === 'error') node.style.color = 'var(--red)';
    node.textContent = message;
    searchResults.replaceChildren(node);
}

function finiteCount(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.round(number) : 0;
}

function appendSearchMeta(parent, label, value) {
    const item = document.createElement('span');
    item.textContent = `${label} ${finiteCount(value).toLocaleString()}`;
    parent.appendChild(item);
}

function selectSearchResult(item) {
    const modelId = String(item?.id || '').trim();
    if (!modelId) return;
    const source = document.getElementById('custom-source-model');
    const backend = document.getElementById('custom-backend');
    const context = document.getElementById('custom-max-context-len');
    const name = document.getElementById('custom-model-name');
    if (source) source.value = modelId;
    const backendValue = String(item?.backend || '').trim();
    if (backend && backendValue && Array.from(backend.options).some(option => option.value === backendValue)) {
        backend.value = backendValue;
    }
    if (typeof updateAutofilledFields === 'function') updateAutofilledFields();
    if (context) {
        const normalized = modelId.toLowerCase();
        context.value = normalized.includes('llama-3.2') || normalized.includes('qwen2.5')
            ? '4096'
            : normalized.includes('bge') || normalized.includes('embedding')
            ? '512'
            : '2048';
    }
    if (typeof selectCustomModelTab === 'function') selectCustomModelTab('manual', true);
    else document.getElementById('tab-btn-manual')?.click();
    name?.focus();
}

function renderSafeSearchResults(items) {
    if (!searchResults) return;
    searchResults.replaceChildren();
    const models = Array.isArray(items) ? items.slice(0, 100) : [];
    if (!models.length) {
        setSearchMessage('No models found matching your query.');
        return;
    }
    models.forEach(item => {
        const modelId = String(item?.id || '').trim();
        if (!modelId) return;
        const row = document.createElement('div');
        row.className = 'search-result-item';

        const copy = document.createElement('div');
        copy.className = 'search-result-copy';
        const name = document.createElement('div');
        name.className = 'search-result-name';
        name.textContent = modelId;
        const meta = document.createElement('div');
        meta.className = 'search-result-meta';
        appendSearchMeta(meta, 'Downloads', item?.downloads);
        appendSearchMeta(meta, 'Likes', item?.likes);
        const badge = document.createElement('span');
        badge.className = 'search-result-badge';
        badge.textContent = String(item?.pipeline_tag || 'model');
        meta.appendChild(badge);
        copy.append(name, meta);

        const select = document.createElement('button');
        select.type = 'button';
        select.className = 'search-result-select-btn';
        select.textContent = 'Select';
        select.setAttribute('aria-label', `Select ${modelId}`);
        select.addEventListener('click', () => selectSearchResult(item));
        row.append(copy, select);
        searchResults.appendChild(row);
    });
    if (!searchResults.children.length) setSearchMessage('No usable models were returned.');
}

async function runSafeModelSearch() {
    const query = String(searchInput?.value || '').trim();
    if (!query || !searchButton || !searchResults) {
        searchInput?.focus();
        return;
    }
    searchController?.abort();
    const controller = new AbortController();
    searchController = controller;
    const previousLabel = searchButton.textContent;
    searchButton.disabled = true;
    searchButton.textContent = 'Searching…';
    searchResults.setAttribute('aria-busy', 'true');
    setSearchMessage('Searching Hugging Face Hub…');
    try {
        const response = await fetch(
            `/v1/models/search-hf?query=${encodeURIComponent(query)}&task=${encodeURIComponent(String(searchTask?.value || ''))}`,
            {
                headers: typeof authHeaders === 'function' ? authHeaders() : {},
                signal: controller.signal,
            },
        );
        if (response.status === 401 && typeof handleAuthRequired === 'function') {
            handleAuthRequired();
            return;
        }
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error(body?.detail || `Search failed (HTTP ${response.status})`);
        if (!Array.isArray(body)) throw new Error('Search returned an invalid response.');
        renderSafeSearchResults(body);
    } catch (error) {
        if (error?.name === 'AbortError') return;
        setSearchMessage(`Search failed: ${String(error?.message || error)}`, 'error');
        if (typeof showToast === 'function') showToast('Model search failed', 'error');
    } finally {
        if (searchController === controller) {
            searchController = null;
            searchButton.disabled = false;
            searchButton.textContent = previousLabel || 'Search';
            searchResults.setAttribute('aria-busy', 'false');
        }
    }
}

searchResults?.setAttribute('role', 'status');
searchResults?.setAttribute('aria-live', 'polite');
searchResults?.setAttribute('aria-busy', 'false');
searchButton?.addEventListener('click', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    void runSafeModelSearch();
}, true);
searchInput?.addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void runSafeModelSearch();
}, true);

/* Keep conditional quantization controls and their displayed value aligned
   after HTMLFormElement.reset() and whenever the modal is reopened. */
const customForm = document.getElementById('custom-model-form');
function syncCustomFormUi() {
    if (typeof updateInt4Visibility === 'function') updateInt4Visibility();
    const ratio = document.getElementById('custom-ratio');
    const ratioValue = document.getElementById('custom-ratio-val');
    if (ratio && ratioValue) ratioValue.textContent = ratio.value;
}
customForm?.addEventListener('reset', () => defer(syncCustomFormUi));

/* If Add Model was activated inside the compact overflow menu, the base modal
   remembers a button that becomes hidden. Return focus to the visible More
   Actions trigger instead. */
const moreTrigger = document.getElementById('ov-header-more-btn');
let returnFocusToMore = false;
if (typeof setCustomModelModalOpen === 'function') {
    const previousSetCustomModelModalOpen = setCustomModelModalOpen;
    setCustomModelModalOpen = function stableSetCustomModelModalOpen(open, ...args) {
        if (open) {
            returnFocusToMore = Boolean(document.activeElement?.closest?.('#ov-header-more-menu'));
        }
        const result = previousSetCustomModelModalOpen(open, ...args);
        defer(syncCustomFormUi);
        if (!open && returnFocusToMore) {
            returnFocusToMore = false;
            defer(() => moreTrigger?.focus());
        }
        return result;
    };
}

/* Complete keyboard behavior for the ARIA menu and close it when keyboard
   focus leaves the compact header control. */
const moreWrap = document.getElementById('ov-header-more-wrap');
const moreMenu = document.getElementById('ov-header-more-menu');
function enabledMenuButtons() {
    if (!moreMenu) return [];
    return Array.from(moreMenu.querySelectorAll('button:not([disabled])'))
        .filter(button => button.offsetParent !== null);
}
function hideMoreMenu() {
    if (!moreMenu || !moreTrigger) return;
    moreMenu.hidden = true;
    moreTrigger.setAttribute('aria-expanded', 'false');
}
moreTrigger?.addEventListener('keydown', event => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    if (moreMenu?.hidden) moreTrigger.click();
    defer(() => {
        const buttons = enabledMenuButtons();
        const target = event.key === 'ArrowUp' ? buttons.at(-1) : buttons[0];
        target?.focus();
    });
});
moreMenu?.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const buttons = enabledMenuButtons();
    if (!buttons.length) return;
    event.preventDefault();
    const current = Math.max(0, buttons.indexOf(document.activeElement));
    const next = event.key === 'Home'
        ? 0
        : event.key === 'End'
        ? buttons.length - 1
        : event.key === 'ArrowDown'
        ? (current + 1) % buttons.length
        : (current - 1 + buttons.length) % buttons.length;
    buttons[next]?.focus();
});
moreWrap?.addEventListener('focusout', () => {
    window.setTimeout(() => {
        if (!moreWrap.contains(document.activeElement)) hideMoreMenu();
    }, 0);
});

/* Do not force the activity feed back to the newest event while the user is
   reading older entries. */
if (typeof renderActivityFeed === 'function') {
    const previousRenderActivityFeed = renderActivityFeed;
    renderActivityFeed = function stableRenderActivityFeed(...args) {
        const list = document.getElementById('activity-list');
        const priorTop = list?.scrollTop || 0;
        const distanceFromBottom = list
            ? list.scrollHeight - list.scrollTop - list.clientHeight
            : 0;
        const followLatest = distanceFromBottom < 28;
        const result = previousRenderActivityFeed(...args);
        if (list && !followLatest) {
            requestAnimationFrame(() => {
                list.scrollTop = Math.min(priorTop, Math.max(0, list.scrollHeight - list.clientHeight));
            });
        }
        return result;
    };
}

const toast = document.getElementById('toast');
toast?.setAttribute('role', 'status');
toast?.setAttribute('aria-live', 'polite');
toast?.setAttribute('aria-atomic', 'true');
syncCustomFormUi();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=GUI_STABILITY_JS,
    css=GUI_STABILITY_CSS,
    description="Final GUI recovery, safety, and narrow-screen fixes.",
)


def install_gui_stability_extension() -> None:
    """Register GUI recovery and narrow-screen fixes."""

    ui_registry.register(EXTENSION)
