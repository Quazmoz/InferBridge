"""Composer context-budget indicator and omission preview for the bundled WebGUI."""

from __future__ import annotations

from app import ui_extension

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
    inputArea.appendChild(panel);

    function formatNumber(value) {
        return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '–';
    }

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

    const previousFetch = window.fetch.bind(window);
    window.fetch = async function contextBudgetAwareFetch(input, init = {}) {
        const target = endpoint(input);
        const method = String(init?.method || input?.method || 'GET').toUpperCase();
        const response = await previousFetch(input, init);
        if (target.sameOrigin && STATUS_PATHS.has(target.path) && method === 'GET' && response.ok) {
            scheduleForModelStatus(response);
        }
        return response;
    };

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


def install_context_budget_ui_extension() -> None:
    """Inject context-budget visibility after the per-chat context extension."""

    if getattr(ui_extension, "_CONTEXT_BUDGET_UI_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_context_budget(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{CONTEXT_BUDGET_JS}\n</script>\n'
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_context_budget
    ui_extension._CONTEXT_BUDGET_UI_INSTALLED = True


__all__ = ["CONTEXT_BUDGET_JS", "install_context_budget_ui_extension"]
