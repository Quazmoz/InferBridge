"""Composer context-budget indicator and omission preview for the bundled WebGUI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-context-budget-extension"

CONTEXT_BUDGET_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmContextBudgetInstalled) return;
    window.__ovllmContextBudgetInstalled = true;

    if (
        typeof activeChat !== 'function' || typeof apiMessages !== 'function' ||
        typeof newChat !== 'function' || typeof saveConversation !== 'function'
    ) return;

    const ENDPOINT = '/v1/chat/context-budget';
    const STATUS_PATHS = new Set(['/v1/system/status', '/v1/models/status']);
    const inputArea = document.getElementById('input-area');
    const footerRight = inputArea?.querySelector('.footer-right');
    const tokenCounter = document.getElementById('token-counter');
    if (!inputArea || !footerRight || !tokenCounter) return;

    let latestBudget = null;
    let inspectTimer = null;
    let inspectController = null;
    let inspectSequence = 0;
    let panelOpen = false;
    let trayObserver = null;
    let traySearchObserver = null;
    let lastModelSignature = '';

    const style = document.createElement('style');
    style.textContent = `
        #ov-context-budget-chip{display:inline-flex;align-items:center;gap:6px;min-height:26px;max-width:420px;padding:4px 8px;border:1px solid var(--border);border-radius:999px;background:var(--surface-2);color:var(--text-2);font:inherit;font-size:10.5px;font-weight:700;white-space:nowrap;cursor:pointer;font-variant-numeric:tabular-nums;transition:border-color .18s,background .18s,color .18s}
        #ov-context-budget-chip:hover{border-color:var(--border-hover);background:var(--surface-3);color:var(--text-1)}
        #ov-context-budget-chip:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
        #ov-context-budget-chip[data-state="warning"]{border-color:color-mix(in srgb,var(--amber) 58%,var(--border));color:var(--amber)}
        #ov-context-budget-chip[data-state="danger"]{border-color:color-mix(in srgb,var(--red) 62%,var(--border));color:var(--red)}
        #ov-context-budget-chip[data-state="loading"]{opacity:.72}
        .ovcb-dot{width:6px;height:6px;border-radius:50%;background:currentColor;flex:0 0 auto}
        .ovcb-chip-text{min-width:0;overflow:hidden;text-overflow:ellipsis}
        #ov-context-budget-panel{position:absolute;right:max(20px,calc((100% - 820px)/2));bottom:76px;z-index:1200;width:min(580px,calc(100% - 24px));max-height:min(620px,calc(100vh - 150px));overflow:auto;border:1px solid var(--border);border-radius:16px;background:var(--surface-1);box-shadow:0 22px 64px rgba(0,0,0,.42);color:var(--text-1)}
        #ov-context-budget-panel[hidden]{display:none}
        .ovcb-head{display:flex;align-items:flex-start;gap:14px;padding:16px 17px 13px;border-bottom:1px solid var(--border)}
        .ovcb-head-copy{min-width:0;flex:1}.ovcb-title{margin:0;font-size:15px;line-height:1.3}.ovcb-subtitle{margin:5px 0 0;color:var(--text-2);font-size:11px;line-height:1.5}
        .ovcb-close{width:30px;height:30px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text-2);font:inherit;font-size:18px;cursor:pointer}
        .ovcb-body{padding:14px 17px 17px}.ovcb-meter{height:8px;overflow:hidden;border-radius:999px;background:var(--surface-3)}.ovcb-meter-fill{height:100%;width:0;border-radius:inherit;background:var(--primary);transition:width .18s}.ovcb-meter-fill.warning{background:var(--amber)}.ovcb-meter-fill.danger{background:var(--red)}
        .ovcb-meter-copy{display:flex;justify-content:space-between;gap:12px;margin-top:7px;color:var(--text-2);font-size:10.5px;font-variant-numeric:tabular-nums}
        .ovcb-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:13px}.ovcb-fact{min-width:0;padding:10px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2)}.ovcb-fact-label{color:var(--text-3);font-size:9px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}.ovcb-fact-value{margin-top:4px;font-size:11.5px;font-weight:760;line-height:1.4;overflow-wrap:anywhere}
        .ovcb-notice{margin-top:11px;padding:10px 11px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);color:var(--text-2);font-size:11px;line-height:1.5}.ovcb-notice.warning{border-color:color-mix(in srgb,var(--amber) 48%,var(--border));color:var(--amber)}.ovcb-notice.danger{border-color:color-mix(in srgb,var(--red) 52%,var(--border));color:var(--red)}
        .ovcb-section{margin-top:14px}.ovcb-section-title{font-size:11px;font-weight:820}.ovcb-omissions{display:grid;gap:7px;margin-top:8px}.ovcb-omission{padding:9px 10px;border-left:3px solid var(--amber);border-radius:7px;background:var(--surface-2)}.ovcb-role{color:var(--text-3);font-size:9px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.ovcb-preview{margin-top:3px;color:var(--text-2);font-size:10.5px;line-height:1.45;overflow-wrap:anywhere}
        .ovcb-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.ovcb-button{min-height:32px;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:10.5px;font-weight:750;cursor:pointer}.ovcb-button:hover:not(:disabled){background:var(--surface-3)}.ovcb-button:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.ovcb-button:disabled{opacity:.5;cursor:not-allowed}.ovcb-button.primary{border-color:color-mix(in srgb,var(--primary) 58%,var(--border));background:color-mix(in srgb,var(--primary) 13%,var(--surface-2))}
        .ovcb-config{border-top:1px solid var(--border);padding:12px 17px 16px;display:grid;gap:9px}.ovcb-config label{font-size:11px;font-weight:700}.ovcb-config select{min-height:36px;width:100%;padding:6px 9px;border-radius:8px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-1);font:inherit;font-size:11px}.ovcb-config-note{color:var(--text-2);font-size:10.5px;line-height:1.5;overflow-wrap:anywhere}#ov-context-budget-panel{max-height:min(78dvh,800px);overflow-y:auto}
        #ov-context-rebuild{display:flex;align-items:center;gap:9px;width:min(820px,100%);margin:0 auto 8px;padding:7px 11px;border:1px solid color-mix(in srgb,var(--primary) 50%,var(--border));border-radius:10px;background:var(--surface-2);color:var(--text-1);font-size:11px;font-weight:700;line-height:1.45;box-sizing:border-box}
        #ov-context-rebuild[hidden]{display:none}
        #ov-context-rebuild[data-state="done"]{border-color:color-mix(in srgb,var(--green) 55%,var(--border));color:var(--green)}
        #ov-context-rebuild[data-state="error"]{border-color:color-mix(in srgb,var(--red) 55%,var(--border));color:var(--red)}
        #ov-context-rebuild .spinner,.ovcb-config-progress .spinner{width:14px;height:14px}
        #ov-context-rebuild .spinner[hidden]{display:none}
        .ovcb-config-progress{display:flex;align-items:center;gap:8px;color:var(--text-1);font-size:10.5px;font-weight:700;line-height:1.45}
        .ovcb-config-progress[hidden]{display:none}
        .ovcb-config[aria-busy="true"] select{opacity:.6}
        @media(max-width:700px){#ov-context-budget-chip{max-width:52vw}#ov-context-budget-panel{right:12px;bottom:104px}.ovcb-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ovcb-actions{display:grid;grid-template-columns:1fr}.ovcb-button{width:100%}}
        @media(max-width:430px){.ovcb-grid{grid-template-columns:1fr}}
        @media(prefers-reduced-motion:reduce){.ovcb-meter-fill{transition:none}}
    `;
    document.head.appendChild(style);

    function createElement(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    const chip = createElement('button');
    chip.type = 'button';
    chip.id = 'ov-context-budget-chip';
    chip.dataset.state = 'idle';
    chip.setAttribute('aria-expanded', 'false');
    chip.setAttribute('aria-controls', 'ov-context-budget-panel');
    chip.title = 'Inspect context-window usage';
    const chipText = createElement('span', 'ovcb-chip-text', 'Context idle');
    chip.append(createElement('span', 'ovcb-dot'), chipText);
    footerRight.insertBefore(chip, tokenCounter);

    const panel = createElement('section');
    panel.id = 'ov-context-budget-panel';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-labelledby', 'ovcb-title');

    const head = createElement('div', 'ovcb-head');
    const headCopy = createElement('div', 'ovcb-head-copy');
    const title = createElement('h2', 'ovcb-title', 'Context budget');
    title.id = 'ovcb-title';
    headCopy.append(
        title,
        createElement('p', 'ovcb-subtitle', 'Exact tokenizer preflight for the selected loaded model.'),
    );
    const closeButton = createElement('button', 'ovcb-close', '×');
    closeButton.type = 'button';
    closeButton.setAttribute('aria-label', 'Close context budget');
    head.append(headCopy, closeButton);
    const body = createElement('div', 'ovcb-body');
    panel.append(head, body);
    const configPanel = createElement('section', 'ovcb-config');
    const configLabel = createElement('label', '', 'Per-model context configuration');
    configLabel.htmlFor = 'ovcb-context-size';
    const configSelect = createElement('select');
    configSelect.id = 'ovcb-context-size';
    configSelect.setAttribute('aria-label', 'Model context tokens');
    const configNote = createElement('div', 'ovcb-config-note', 'Select a model to inspect context options.');
    configNote.setAttribute('aria-live', 'polite');
    const configActions = createElement('div', 'ovcb-actions');
    const configApply = createElement('button', 'ovcb-button primary', 'Apply context');
    const configReset = createElement('button', 'ovcb-button', 'Recommended default');
    const configRefresh = createElement('button', 'ovcb-button', 'Refresh settings');
    configApply.type = configReset.type = configRefresh.type = 'button';
    configActions.append(configApply, configReset, configRefresh);
    // Shown from Apply until the engine confirms; the select and buttons stay locked meanwhile.
    const configProgress = createElement('div', 'ovcb-config-progress');
    configProgress.setAttribute('role', 'status');
    configProgress.setAttribute('aria-live', 'polite');
    configProgress.hidden = true;
    const configSpinner = createElement('span', 'spinner');
    configSpinner.setAttribute('aria-hidden', 'true');
    const configProgressText = createElement('span');
    configProgress.append(configSpinner, configProgressText);
    configPanel.append(configLabel, configSelect, configProgress, configNote, configActions);
    panel.appendChild(configPanel);
    inputArea.appendChild(panel);

    let contextSettings = null;
    let contextBusy = false;
    let contextSequence = 0;
    function updateContextControls() {
        const chosen = Number(configSelect.value);
        const current = contextSettings?.configured_context_tokens;
        const option = contextSettings?.options?.find(item => item.tokens === chosen);
        configSelect.disabled = contextBusy || !contextSettings;
        configApply.disabled = contextBusy || !option || chosen === current ||
            option.classification === 'existing-unverified';
        configReset.disabled = contextBusy || !contextSettings ||
            current === contextSettings.recommended_context_tokens ||
            !contextSettings.options.some(item =>
                item.tokens === contextSettings.recommended_context_tokens &&
                item.classification !== 'existing-unverified');
        if (option && !contextBusy) {
            const kvGib = option.kv_cache_gib_estimate ?? option.fp16_kv_cache_gib_estimate;
            const kvPrecision = contextSettings.kv_cache_precision_applies === false
                ? 'auto' : (contextSettings.kv_cache_precision || 'auto');
            const memory = kvGib == null
                ? 'KV-cache estimate unavailable (architecture metadata incomplete).'
                : `Estimated KV cache ~${kvGib} GiB at ${kvPrecision === 'auto' ? 'the FP16 upper bound (KV precision Auto)' : kvPrecision.toUpperCase() + ' KV precision'}; not measured device memory.`;
            const footprint = option.estimated_host_ram_gib == null
                ? 'Total memory estimate unavailable.'
                : `Estimated host-RAM planning floor ~${option.estimated_host_ram_gib} GiB ` +
                  `(${option.fit_guidance.replace(/-/g, ' ')}); not a device measurement.`;
            configNote.textContent = `${option.classification.replace(/-/g, ' ')} · ` +
                `${formatNumber(chosen - contextSettings.reserved_output_tokens)} prompt tokens · ` +
                `${formatNumber(contextSettings.reserved_output_tokens)} reserved output. ${memory} ${footprint} ` +
                'Larger options remain hardware-unverified until tested.';
        }
    }

    async function refreshContextSettings() {
        const model = modelSelect.value;
        const sequence = ++contextSequence;
        contextSettings = null;
        configSelect.replaceChildren();
        contextBusy = true;
        updateContextControls();
        configNote.textContent = model ? 'Inspecting model architecture and context options…' : 'Select a model first.';
        if (!model) { contextBusy = false; updateContextControls(); return; }
        try {
            const response = await fetch(`/v1/models/context?model=${encodeURIComponent(model)}`, {
                headers: requestHeaders(), cache: 'no-store',
            });
            const payload = await response.json();
            if (sequence !== contextSequence || model !== modelSelect.value) return;
            if (!response.ok) throw new Error('Context settings are unavailable. Check model state.');
            if (!Array.isArray(payload.options) || payload.model_id !== model) throw new Error('Invalid context options.');
            contextSettings = payload;
            payload.options.forEach(option => {
                const row = createElement('option', '', `${formatNumber(option.tokens)} · ${option.classification.replace(/-/g, ' ')}`);
                row.value = String(option.tokens);
                configSelect.appendChild(row);
            });
            configSelect.value = String(payload.configured_context_tokens);
        } catch (error) {
            if (sequence !== contextSequence) return;
            configNote.textContent = error instanceof Error ? error.message : 'Unable to inspect model context.';
        } finally {
            if (sequence === contextSequence) { contextBusy = false; updateContextControls(); }
        }
    }

    function setConfigProgress(text) {
        configPanel.setAttribute('aria-busy', text ? 'true' : 'false');
        configProgress.hidden = !text;
        configProgressText.textContent = text;
    }

    async function applyContext(tokens) {
        const original = contextSettings;
        if (contextBusy || !original || !original.options.some(item => item.tokens === tokens)) return;
        contextBusy = true;
        updateContextControls();
        const loaded = availableModels.get(original.model_id)?.is_loaded === true;
        configNote.textContent = '';
        setConfigProgress(loaded
            ? `Applying ${shortTokens(tokens)} context… rebuilding engine. Chat history is kept.`
            : `Saving ${shortTokens(tokens)} context…`);
        let applied = false;
        try {
            const response = await fetch('/v1/models/context', {
                method: 'POST', headers: requestHeaders(), cache: 'no-store',
                body: JSON.stringify({model: original.model_id, context_tokens: tokens,
                    expected_context_tokens: original.configured_context_tokens}),
            });
            const payload = await response.json();
            if (!response.ok || payload?.status !== 'applied') {
                throw new Error(typeof payload?.detail === 'string' ? payload.detail :
                    'Context was not confirmed. Refresh before retrying.');
            }
            applied = true;
            scheduleInspect(0);
        } catch (error) {
            setConfigProgress('');
            configNote.textContent = error instanceof Error ? error.message : 'Context state uncertain. Refresh before retrying.';
        } finally {
            contextBusy = false;
            setConfigProgress('');
            await refreshContextSettings();
        }
        // After the refresh, which rewrites the note: a brief success state.
        if (applied) {
            configNote.textContent = `Context set to ${shortTokens(tokens)}. ` +
                (loaded ? 'The engine was rebuilt; conversation history was preserved.' : 'Used next time this model loads.');
        }
    }
    configSelect.addEventListener('change', updateContextControls);
    configApply.addEventListener('click', () => void applyContext(Number(configSelect.value)));
    configReset.addEventListener('click', () => void applyContext(contextSettings?.recommended_context_tokens));
    configRefresh.addEventListener('click', () => void refreshContextSettings());
    modelSelect.addEventListener('change', () => {
        contextSettings = null;
        if (panelOpen) void refreshContextSettings();
    });

    function formatNumber(value) {
        return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '–';
    }

    function shortTokens(value) {
        return value >= 1024 && value % 1024 === 0 ? `${value / 1024}K` : formatNumber(value);
    }

    // --- Engine rebuild status in the composer ----------------------------------------
    // Applying a new context to a loaded model rebuilds its engine (often many seconds).
    // Every apply goes through POST /v1/models/context, whichever surface sent it (this
    // panel or the Model Library), so the request layer below drives one composer strip
    // and guards sends to that model until the engine confirms.
    const rebuild = createElement('div');
    rebuild.id = 'ov-context-rebuild';
    rebuild.hidden = true;
    rebuild.setAttribute('role', 'status');
    rebuild.setAttribute('aria-live', 'polite');
    const rebuildSpinner = createElement('span', 'spinner');
    rebuildSpinner.setAttribute('aria-hidden', 'true');
    const rebuildText = createElement('span');
    rebuild.append(rebuildSpinner, rebuildText);
    inputArea.insertBefore(rebuild, inputArea.firstChild);
    let rebuilding = null;
    let rebuildTimer = null;

    function showRebuild(state, message) {
        clearTimeout(rebuildTimer);
        rebuild.dataset.state = state;
        rebuildSpinner.hidden = state !== 'busy';
        rebuildText.textContent = message;
        rebuild.hidden = !message;
        const sendButton = document.getElementById('send-btn');
        if (state === 'busy') sendButton?.setAttribute('aria-describedby', 'ov-context-rebuild');
        else sendButton?.removeAttribute('aria-describedby');
        if (state !== 'busy' && message) rebuildTimer = setTimeout(() => { rebuild.hidden = true; }, 5000);
    }

    function rebuildingSelected() {
        return !!rebuilding && rebuilding.loaded && rebuilding.model === modelSelect.value;
    }

    async function trackContextApply(downstream, input, init) {
        let request = {};
        try { request = JSON.parse(typeof init?.body === 'string' ? init.body : '{}') || {}; } catch { request = {}; }
        const model = String(request.model || '');
        const meta = availableModels.get(model);
        const tokens = Number(request.context_tokens);
        const loaded = meta?.is_loaded === true;
        const size = Number.isFinite(tokens) ? `${shortTokens(tokens)} context` : 'the new context';
        rebuilding = { model, tokens, loaded };
        if (loaded) {
            showRebuild('busy', `Applying ${size} to ${meta.name || model}… rebuilding engine. ` +
                'Sending waits until it is ready; your message is kept.');
        }
        let response;
        try {
            response = await downstream(input, init);
        } catch (error) {
            rebuilding = null;
            if (loaded) showRebuild('error', 'Context change was not confirmed. The current engine is still running.');
            throw error;
        }
        rebuilding = null;
        if (loaded) {
            let ok = response.ok;
            if (ok) {
                try { ok = (await response.clone().json())?.status === 'applied'; } catch { ok = false; }
            }
            showRebuild(ok ? 'done' : 'error', ok
                ? `Context set to ${shortTokens(tokens)}. The engine was rebuilt; chat history is kept.`
                : 'Context change was not applied. The current engine is still running.');
        }
        return response;
    }

    function blockSendWhileRebuilding() {
        if (typeof isGenerating !== 'undefined' && isGenerating) return false;
        if (!rebuildingSelected()) return false;
        if (typeof showToast === 'function') {
            showToast('The engine is rebuilding for the new context. Your message is kept; send it when it is ready.');
        }
        userInput.focus();
        return true;
    }

    if (typeof sendMessage === 'function') {
        const originalSendMessage = sendMessage;
        sendMessage = function contextRebuildAwareSendMessage(...args) {
            if (blockSendWhileRebuilding()) return Promise.resolve();
            return originalSendMessage(...args);
        };
    }
    // Regenerate is bound by reference, so guard its clicks before they reach it.
    document.addEventListener('click', event => {
        if (event.target instanceof Element && event.target.closest('.meta-btn.regen') && blockSendWhileRebuilding()) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    function requestHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        if (key) headers.Authorization = `Bearer ${key}`;
        return headers;
    }

    function selectedModel() {
        return availableModels.get(modelSelect.value) || null;
    }

    function pendingImageCount() {
        return document.querySelectorAll('#vision-preview-tray .vision-preview').length;
    }

    function requestPayload() {
        const chat = activeChat();
        const history = Array.isArray(chat?.messages) ? chat.messages : [];
        const messages = [];
        const systemPrompt = String(settingsSystemPrompt.value || '').trim();
        if (systemPrompt) messages.push({ role: 'system', content: systemPrompt });
        messages.push(...apiMessages(history));
        const draft = String(userInput.value || '').trim();
        if (draft) messages.push({ role: 'user', content: draft });
        if (!messages.length) return null;
        return {
            model: modelSelect.value,
            messages,
            max_tokens: Math.max(1, Number.parseInt(settingsMaxTokens.value, 10) || 512),
            image_count: pendingImageCount(),
        };
    }

    function setChip(label, state = 'idle', titleText = '') {
        chipText.textContent = label;
        chip.dataset.state = state;
        chip.title = titleText || label;
    }

    function clearBody(message, state = '') {
        body.replaceChildren(createElement('div', `ovcb-notice${state ? ` ${state}` : ''}`, message));
    }

    function addFact(grid, label, value) {
        const fact = createElement('div', 'ovcb-fact');
        fact.append(
            createElement('div', 'ovcb-fact-label', label),
            createElement('div', 'ovcb-fact-value', value),
        );
        grid.appendChild(fact);
    }

    function restoreDraftInFreshChat() {
        const draft = String(userInput.value || '');
        newChat();
        if (draft) {
            userInput.value = draft;
            if (typeof autoResize === 'function') autoResize();
            userInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        closePanel(false);
        userInput.focus();
    }

    function reduceOutputToFit(data) {
        const available = Math.max(1, Number(data?.effective_output_tokens || 1));
        const minimum = Number(settingsMaxTokens.min || 1);
        const maximum = Number(settingsMaxTokens.max || available);
        settingsMaxTokens.value = String(Math.min(maximum, Math.max(minimum, available)));
        settingsMaxTokens.dispatchEvent(new Event('input', { bubbles: true }));
        settingsMaxTokens.dispatchEvent(new Event('change', { bubbles: true }));
        scheduleInspect(0);
    }

    function renderBudget(data) {
        latestBudget = data;
        const used = Number(data.prompt_tokens || 0);
        const maximum = Number(data.max_prompt_tokens || 0);
        const omitted = Number(data.dropped_turn_count || 0);
        const percent = Number(data.prompt_budget_percent || 0);
        const danger = data.blocked || data.prompt_over_budget;
        const warning = danger || data.will_truncate || data.output_limited || percent >= 80;
        const state = danger ? 'danger' : warning ? 'warning' : 'ready';
        const omittedLabel = omitted
            ? ` · ${omitted} turn${omitted === 1 ? '' : 's'} omitted`
            : '';
        setChip(
            `Context ${formatNumber(used)} / ${formatNumber(maximum)}${omittedLabel}`,
            state,
            `${formatNumber(data.context_usage_tokens)} of ${formatNumber(data.max_context_tokens)} total context tokens committed`,
        );

        body.replaceChildren();
        const meter = createElement('div', 'ovcb-meter');
        const fill = createElement(
            'div',
            `ovcb-meter-fill${danger ? ' danger' : warning ? ' warning' : ''}`,
        );
        fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
        meter.appendChild(fill);
        const meterCopy = createElement('div', 'ovcb-meter-copy');
        meterCopy.append(
            createElement('span', '', `${formatNumber(used)} prompt tokens`),
            createElement(
                'span',
                '',
                `${Math.min(100, Math.max(0, percent)).toFixed(1)}% of prompt budget`,
            ),
        );
        body.append(meter, meterCopy);

        const grid = createElement('div', 'ovcb-grid');
        addFact(grid, 'Context window', formatNumber(data.max_context_tokens));
        addFact(grid, 'Prompt budget', formatNumber(data.max_prompt_tokens));
        addFact(grid, 'Requested output', formatNumber(data.requested_output_tokens));
        addFact(grid, 'Available output', formatNumber(data.available_output_tokens));
        addFact(
            grid,
            'Retained messages',
            `${formatNumber(data.retained_message_count)} / ${formatNumber(data.message_count)}`,
        );
        addFact(
            grid,
            'Attachments',
            data.attachment_count
                ? `${data.attachment_count} · ~${formatNumber(data.attachment_token_estimate)} tokens`
                : 'None',
        );
        body.appendChild(grid);

        let notice = 'All current messages fit. Leading system instructions remain pinned.';
        let noticeState = '';
        if (data.blocked) {
            notice = 'The retained prompt leaves no generation room. Start a fresh chat or shorten the current input.';
            noticeState = 'danger';
        } else if (data.will_truncate) {
            notice = `${formatNumber(data.dropped_turn_count)} older turn${data.dropped_turn_count === 1 ? '' : 's'} will be omitted before generation. Leading system instructions remain retained.`;
            noticeState = 'warning';
        } else if (data.output_limited) {
            notice = `The requested output will be capped at ${formatNumber(data.effective_output_tokens)} tokens for this prompt.`;
            noticeState = 'warning';
        } else if (percent >= 80) {
            notice = 'The chat is nearing its prompt budget. Older turns may be omitted after additional messages.';
            noticeState = 'warning';
        }
        body.appendChild(createElement('div', `ovcb-notice${noticeState ? ` ${noticeState}` : ''}`, notice));

        const dropped = Array.isArray(data.dropped_messages) ? data.dropped_messages : [];
        if (dropped.length) {
            const section = createElement('section', 'ovcb-section');
            section.appendChild(
                createElement('div', 'ovcb-section-title', 'Omitted message preview'),
            );
            const list = createElement('div', 'ovcb-omissions');
            dropped.forEach(message => {
                const item = createElement('div', 'ovcb-omission');
                item.append(
                    createElement('div', 'ovcb-role', message.role || 'message'),
                    createElement('div', 'ovcb-preview', message.preview || '(empty message)'),
                );
                list.appendChild(item);
            });
            if (data.dropped_preview_truncated) {
                list.appendChild(
                    createElement(
                        'div',
                        'ovcb-preview',
                        'Additional omitted messages are not shown in this preview.',
                    ),
                );
            }
            section.appendChild(list);
            body.appendChild(section);
        }

        const actions = createElement('div', 'ovcb-actions');
        const fresh = createElement('button', 'ovcb-button primary', 'Start new chat from here');
        fresh.type = 'button';
        fresh.addEventListener('click', restoreDraftInFreshChat);
        const fit = createElement('button', 'ovcb-button', 'Reduce output to fit');
        fit.type = 'button';
        fit.disabled = !data.output_limited || Number(data.effective_output_tokens || 0) < 1;
        fit.addEventListener('click', () => reduceOutputToFit(data));
        const refresh = createElement('button', 'ovcb-button', 'Refresh');
        refresh.type = 'button';
        refresh.addEventListener('click', () => scheduleInspect(0));
        actions.append(fresh, fit, refresh);
        body.appendChild(actions);
    }

    async function inspectContext() {
        clearTimeout(inspectTimer);
        const model = selectedModel();
        if (!modelSelect.value || !model?.is_loaded) {
            latestBudget = null;
            setChip(
                'Context after model load',
                'idle',
                'Load the selected model for exact tokenizer usage.',
            );
            if (panelOpen) {
                clearBody('Load the selected model to inspect its exact context budget.');
            }
            return;
        }

        const payload = requestPayload();
        if (!payload) {
            latestBudget = null;
            setChip(
                'Context empty',
                'ready',
                'No chat content is currently using the context window.',
            );
            if (panelOpen) {
                clearBody('Type a message or add system instructions to inspect context usage.');
            }
            return;
        }

        inspectController?.abort();
        inspectController = new AbortController();
        const sequence = ++inspectSequence;
        if (!latestBudget) setChip('Checking context…', 'loading');
        try {
            const response = await window.fetch(ENDPOINT, {
                method: 'POST',
                headers: requestHeaders(),
                body: JSON.stringify(payload),
                cache: 'no-store',
                signal: inspectController.signal,
            });
            let result = null;
            try { result = await response.json(); } catch { result = null; }
            if (sequence !== inspectSequence) return;
            if (response.status === 401 && typeof handleAuthRequired === 'function') {
                handleAuthRequired();
            }
            if (!response.ok) {
                const detail = typeof result?.detail === 'string'
                    ? result.detail
                    : result?.detail?.message || `Context preflight failed with HTTP ${response.status}.`;
                throw new Error(detail);
            }
            renderBudget(result);
        } catch (error) {
            if (error?.name === 'AbortError' || sequence !== inspectSequence) return;
            latestBudget = null;
            const message = error instanceof Error ? error.message : 'Context preflight failed.';
            setChip('Context unavailable', 'danger', message);
            if (panelOpen) clearBody(message, 'danger');
        }
    }

    function scheduleInspect(delay = 300) {
        clearTimeout(inspectTimer);
        inspectTimer = window.setTimeout(() => void inspectContext(), delay);
    }

    function openPanel() {
        panelOpen = true;
        panel.hidden = false;
        chip.setAttribute('aria-expanded', 'true');
        if (latestBudget) renderBudget(latestBudget);
        else clearBody('Calculating context usage…');
        scheduleInspect(0);
        void refreshContextSettings();
        closeButton.focus();
    }

    function closePanel(restoreFocus = true) {
        panelOpen = false;
        panel.hidden = true;
        chip.setAttribute('aria-expanded', 'false');
        if (restoreFocus) chip.focus();
    }

    chip.addEventListener('click', () => panelOpen ? closePanel() : openPanel());
    closeButton.addEventListener('click', () => closePanel());
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && panelOpen) closePanel();
    });
    document.addEventListener('mousedown', event => {
        if (!panelOpen || panel.contains(event.target) || chip.contains(event.target)) return;
        closePanel(false);
    });

    userInput.addEventListener('input', () => scheduleInspect());
    settingsSystemPrompt.addEventListener('input', () => scheduleInspect());
    settingsMaxTokens.addEventListener('input', () => scheduleInspect());
    modelSelect.addEventListener('change', () => scheduleInspect(0));

    const originalSwitchChat = switchChat;
    switchChat = function contextBudgetSwitchChat(...args) {
        const result = originalSwitchChat(...args);
        scheduleInspect(0);
        return result;
    };

    const originalNewChat = newChat;
    newChat = function contextBudgetNewChat(...args) {
        const result = originalNewChat(...args);
        scheduleInspect(0);
        return result;
    };

    const originalDeleteChat = deleteChat;
    deleteChat = function contextBudgetDeleteChat(...args) {
        const result = originalDeleteChat(...args);
        scheduleInspect(0);
        return result;
    };

    const originalSaveConversation = saveConversation;
    saveConversation = function contextBudgetSaveConversation(...args) {
        const result = originalSaveConversation(...args);
        scheduleInspect(0);
        return result;
    };

    const originalExecuteGeneration = executeGeneration;
    executeGeneration = function contextBudgetExecuteGeneration(...args) {
        const result = originalExecuteGeneration(...args);
        Promise.resolve(result).finally(() => scheduleInspect(0));
        scheduleInspect(0);
        return result;
    };

    function endpoint(input) {
        const value = typeof input === 'string'
            ? input
            : input instanceof URL
                ? input.href
                : input?.url || '';
        try {
            const url = new URL(value, window.location.href);
            return { path: url.pathname, sameOrigin: url.origin === window.location.origin };
        } catch {
            return { path: '', sameOrigin: false };
        }
    }

    function scheduleForModelStatus(response) {
        response.clone().json().then(payload => {
            const models = Array.isArray(payload?.models?.available)
                ? payload.models.available
                : [];
            const selected = models.find(model => model?.id === modelSelect.value) || null;
            const signature = [
                modelSelect.value,
                selected?.is_loaded === true ? 'loaded' : 'not-loaded',
                selected?.max_context_len || '',
                selected?.max_output_tokens || '',
                selected?.backend || '',
            ].join('|');
            if (signature === lastModelSignature) return;
            lastModelSignature = signature;
            scheduleInspect(0);
        }).catch(() => {});
    }

    const previousFetch = InferBridge.chain();
    InferBridge.use(async function contextBudgetAwareFetch(input, init = {}) {
        const target = endpoint(input);
        const method = String(init?.method || input?.method || 'GET').toUpperCase();
        if (target.sameOrigin && target.path === '/v1/models/context' && method === 'POST') {
            return trackContextApply(previousFetch, input, init);
        }
        const response = await previousFetch(input, init);
        if (target.sameOrigin && STATUS_PATHS.has(target.path) && method === 'GET' && response.ok) {
            scheduleForModelStatus(response);
        }
        return response;
    });

    function attachTrayObserver() {
        const tray = document.getElementById('vision-preview-tray');
        if (!tray || trayObserver) return Boolean(tray);
        trayObserver = new MutationObserver(() => scheduleInspect());
        trayObserver.observe(tray, { childList: true });
        traySearchObserver?.disconnect();
        traySearchObserver = null;
        return true;
    }

    if (!attachTrayObserver()) {
        traySearchObserver = new MutationObserver(() => attachTrayObserver());
        traySearchObserver.observe(document.documentElement, { childList: true, subtree: true });
    }

    scheduleInspect(0);
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=CONTEXT_BUDGET_JS,
    description="Tokenizer-backed context-window preflight and omission visibility.",
)


def install_context_budget_ui_extension() -> None:
    """Register context-budget visibility."""

    ui_registry.register(EXTENSION)


__all__ = ["CONTEXT_BUDGET_JS", "install_context_budget_ui_extension"]
