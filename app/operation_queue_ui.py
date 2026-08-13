"""Expandable multi-operation queue for the stable model progress dock."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-operation-queue-extension"

OPERATION_QUEUE_JS = r"""
(() => {
    'use strict';
    if (window.__inferbridgeOperationQueueInstalled) return;
    window.__inferbridgeOperationQueueInstalled = true;

    const STATUS_PATH = '/v1/system/status';
    const PANEL_ID = 'ovrp-operation-queue';
    let latestPayload = null;
    let queueExpanded = false;
    let renderScheduled = false;
    let lastSignature = '';
    let dockObserver = null;
    let rootObserver = null;

    const style = document.createElement('style');
    style.textContent = `
        .ovrp-queue-toggle{min-height:28px;padding:4px 9px;border:1px solid color-mix(in srgb,var(--primary) 35%,var(--border));border-radius:8px;background:color-mix(in srgb,var(--primary) 7%,var(--surface-2));color:var(--text-2);font:inherit;font-size:10.5px;font-weight:700;cursor:pointer}
        .ovrp-queue-toggle:hover{background:color-mix(in srgb,var(--primary) 13%,var(--surface-2));color:var(--text-1)}
        .ovrp-queue-toggle:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
        .ovrp-operation-queue{display:none;margin-top:10px;padding-top:10px;border-top:1px solid color-mix(in srgb,var(--border) 78%,transparent)}
        .ovrp-operation-queue.expanded{display:block}
        .ovrp-queue-heading{margin:0 0 7px;color:var(--text-2);font-size:10.5px;font-weight:750}
        .ovrp-queue-list{display:grid;gap:7px}
        .ovrp-queue-row{--ovrp-queue-progress:0%;width:100%;display:grid;grid-template-areas:'select' 'track';gap:7px;min-height:48px;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);color:inherit;transition:border-color .16s ease,background .16s ease,transform .16s ease}
        .ovrp-queue-row:hover{border-color:color-mix(in srgb,var(--primary) 35%,var(--border));background:color-mix(in srgb,var(--primary) 5%,var(--surface-2));transform:translateY(-1px)}
        .ovrp-queue-row:focus-within{outline:2px solid var(--primary);outline-offset:2px}
        .ovrp-queue-row.current{border-color:color-mix(in srgb,var(--primary) 45%,var(--border));background:color-mix(in srgb,var(--primary) 8%,var(--surface-2))}
        .ovrp-queue-select{grid-area:select;width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:start;padding:0;border:0;background:transparent;color:inherit;text-align:left;font:inherit;cursor:pointer}
        .ovrp-queue-select:focus-visible{outline:none}
        .ovrp-queue-copy{min-width:0;display:grid;gap:3px}
        .ovrp-queue-title{display:flex;align-items:center;gap:6px;min-width:0}
        .ovrp-queue-model{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-1);font-size:10.5px;font-weight:750}
        .ovrp-queue-phase{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-3);font-size:10.5px}
        .ovrp-queue-value{align-self:start;color:var(--primary);font-size:10.5px;font-weight:750;font-variant-numeric:tabular-nums;white-space:nowrap}
        .ovrp-queue-current{flex:0 0 auto;display:inline-flex;align-items:center;padding:1px 5px;border-radius:999px;background:color-mix(in srgb,var(--primary) 14%,transparent);color:var(--primary);font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.04em}
        .ovrp-queue-track{grid-area:track;position:relative;height:5px;overflow:hidden;border-radius:999px;background:var(--surface-3);box-shadow:inset 0 0 0 1px var(--border)}
        .ovrp-queue-fill{position:absolute;inset:0 auto 0 0;width:var(--ovrp-queue-progress);border-radius:inherit;background:var(--accent-grad);transition:width .3s ease}
        .ovrp-queue-row.indeterminate .ovrp-queue-fill{width:34%;animation:ovrp-queue-scan 1.35s ease-in-out infinite}
        .ovrp-queue-row.queued .ovrp-queue-fill{width:0;animation:none}
        @keyframes ovrp-queue-scan{from{transform:translateX(-120%)}to{transform:translateX(395%)}}
        @media(max-width:640px){.ovrp-queue-toggle{min-height:34px}.ovrp-queue-row{min-height:52px;padding:9px 10px}.ovrp-queue-phase{white-space:normal;line-height:1.35}}
        @media(prefers-reduced-motion:reduce){.ovrp-queue-row{transition:none}.ovrp-queue-row:hover{transform:none}.ovrp-queue-fill{transition:none}.ovrp-queue-row.indeterminate .ovrp-queue-fill{left:62%;width:28%;animation:none}}
    `;
    document.head.appendChild(style);

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

    function requestMethod(input, init) {
        return String(init?.method || input?.method || 'GET').toUpperCase();
    }

    function activeOperations(payload) {
        const models = payload?.models?.available;
        if (!Array.isArray(models)) return [];
        return models.filter(model => {
            const operationId = model?.progress?.operation_id;
            return model?.is_loading && typeof operationId === 'string' && operationId;
        });
    }

    function primaryOperation(operations) {
        const selectedId = document.getElementById('model-select')?.value || '';
        const waitingId = typeof waitingForModelId === 'undefined' ? '' : waitingForModelId || '';
        return operations.find(model => model.id === waitingId)
            || operations.find(model => model.id === selectedId)
            || operations[0]
            || null;
    }

    function phaseLabel(model) {
        const phase = String(model?.progress?.phase || model?.status || 'preparing').toLowerCase();
        const labels = {
            queued: 'Waiting to start',
            queued_convert: 'Waiting to convert',
            resolving: 'Resolving files',
            downloading: 'Downloading',
            converting: 'Converting',
            finalizing: 'Finalizing',
            loading: 'Loading runtime',
        };
        return labels[phase] || String(model?.status_label || 'Preparing');
    }

    function progressPercent(model) {
        const progress = model?.progress || {};
        const overall = Number(progress.overall_percent);
        if (
            progress.overall_percent !== null
            && progress.overall_percent !== undefined
            && progress.overall_percent !== ''
            && Number.isFinite(overall)
        ) {
            return Math.max(0, Math.min(100, overall));
        }
        const percent = Number(progress.percent);
        if (
            progress.percent !== null
            && progress.percent !== undefined
            && progress.percent !== ''
            && Number.isFinite(percent)
        ) {
            return Math.max(0, Math.min(100, percent));
        }
        const completed = Number(progress.completed);
        const total = Number(progress.total);
        if (Number.isInteger(completed) && Number.isInteger(total) && total > 0 && completed >= 0 && completed <= total) {
            return Math.max(0, Math.min(100, (completed / total) * 100));
        }
        return null;
    }

    function valueLabel(model) {
        const percent = progressPercent(model);
        if (percent !== null) return `${Math.round(percent)}%`;
        const phase = String(model?.progress?.phase || model?.status || '').toLowerCase();
        if (phase === 'queued' || phase === 'queued_convert') return 'Queued';
        return 'Working…';
    }

    function operationName(model) {
        return String(model?.name || model?.id || 'Model').split(' — ')[0];
    }

    function operationSignature(operations, primary) {
        return operations.map(model => [
            model.id,
            model.progress?.operation_id || '',
            model.progress?.revision || 0,
            model.progress?.phase || model.status || '',
            model.progress?.overall_percent ?? '',
            model.progress?.percent ?? '',
            model.progress?.completed ?? '',
            model.progress?.total ?? '',
            model.id === primary?.id ? 'primary' : '',
        ].join(':')).join('|');
    }

    function selectOperation(modelId) {
        const select = document.getElementById('model-select');
        if (!select || !modelId) return;
        if (select.value !== modelId) select.value = modelId;
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function ensurePanel(detail) {
        let panel = detail.querySelector(`#${PANEL_ID}`);
        if (panel) return panel;
        panel = document.createElement('section');
        panel.id = PANEL_ID;
        panel.className = 'ovrp-operation-queue';
        panel.setAttribute('aria-label', 'Active model operations');
        const heading = document.createElement('p');
        heading.className = 'ovrp-queue-heading';
        const list = document.createElement('div');
        list.className = 'ovrp-queue-list';
        list.setAttribute('role', 'group');
        list.setAttribute('aria-label', 'Choose the primary model operation');
        panel.append(heading, list);
        detail.appendChild(panel);
        return panel;
    }

    function removeQueue(dock) {
        dock.querySelector('.ovrp-queue-toggle')?.remove();
        dock.querySelector(`#${PANEL_ID}`)?.remove();
        queueExpanded = false;
        lastSignature = '';
    }

    function renderQueue() {
        renderScheduled = false;
        const dock = document.getElementById('ov-reliable-progress');
        if (!dock) return;
        const detail = dock.querySelector('.ovrp-detail');
        const metadata = dock.querySelector('.ovrp-meta');
        const operations = activeOperations(latestPayload);
        if (!detail || !metadata || operations.length <= 1) {
            removeQueue(dock);
            return;
        }

        const primary = primaryOperation(operations);
        const ordered = [...operations].sort((left, right) => {
            if (left.id === primary?.id) return -1;
            if (right.id === primary?.id) return 1;
            const leftStarted = Number(left.progress?.started_at || 0);
            const rightStarted = Number(right.progress?.started_at || 0);
            if (leftStarted !== rightStarted) return leftStarted - rightStarted;
            return operationName(left).localeCompare(operationName(right));
        });

        let toggle = metadata.querySelector('.ovrp-queue-toggle');
        if (!toggle) {
            toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'ovrp-queue-toggle';
            toggle.setAttribute('aria-controls', PANEL_ID);
            toggle.onclick = event => {
                event.stopPropagation();
                queueExpanded = !queueExpanded;
                scheduleRender();
            };
            metadata.appendChild(toggle);
        }
        const toggleText = `${operations.length} operations active`;
        if (toggle.textContent !== toggleText) toggle.textContent = toggleText;
        toggle.setAttribute('aria-expanded', String(queueExpanded));
        toggle.setAttribute(
            'aria-label',
            `${queueExpanded ? 'Hide' : 'Show'} ${operations.length} active model operations`,
        );

        const existingPanel = detail.querySelector(`#${PANEL_ID}`);
        const panelWasMissing = !existingPanel;
        const panel = existingPanel || ensurePanel(detail);
        panel.classList.toggle('expanded', queueExpanded);
        panel.hidden = !queueExpanded;
        const heading = panel.querySelector('.ovrp-queue-heading');
        if (heading.textContent !== toggleText) heading.textContent = toggleText;

        const signature = operationSignature(ordered, primary);
        if (signature === lastSignature && !panelWasMissing) return;
        lastSignature = signature;
        const list = panel.querySelector('.ovrp-queue-list');
        const fragment = document.createDocumentFragment();
        for (const model of ordered) {
            const percent = progressPercent(model);
            const phase = String(model?.progress?.phase || model?.status || '').toLowerCase();
            const queued = phase === 'queued' || phase === 'queued_convert';
            const row = document.createElement('div');
            row.className = `ovrp-queue-row${model.id === primary?.id ? ' current' : ''}${percent === null && !queued ? ' indeterminate' : ''}${queued ? ' queued' : ''}`;
            row.setAttribute('data-model-id', model.id);

            const selectButton = document.createElement('button');
            selectButton.type = 'button';
            selectButton.className = 'ovrp-queue-select';
            selectButton.setAttribute('aria-current', model.id === primary?.id ? 'true' : 'false');
            selectButton.setAttribute(
                'aria-label',
                `${operationName(model)}, ${phaseLabel(model)}, ${valueLabel(model)}`,
            );
            selectButton.onclick = event => {
                event.stopPropagation();
                selectOperation(model.id);
            };

            const copy = document.createElement('span');
            copy.className = 'ovrp-queue-copy';
            const title = document.createElement('span');
            title.className = 'ovrp-queue-title';
            const name = document.createElement('span');
            name.className = 'ovrp-queue-model';
            name.textContent = operationName(model);
            title.appendChild(name);
            if (model.id === primary?.id) {
                const badge = document.createElement('span');
                badge.className = 'ovrp-queue-current';
                badge.textContent = 'Current';
                title.appendChild(badge);
            }
            const phaseText = document.createElement('span');
            phaseText.className = 'ovrp-queue-phase';
            phaseText.textContent = phaseLabel(model);
            copy.append(title, phaseText);

            const value = document.createElement('span');
            value.className = 'ovrp-queue-value';
            value.textContent = valueLabel(model);
            selectButton.append(copy, value);

            const track = document.createElement('span');
            track.className = 'ovrp-queue-track';
            track.setAttribute('role', 'progressbar');
            track.setAttribute('aria-label', `${operationName(model)} progress`);
            track.setAttribute('aria-valuemin', '0');
            track.setAttribute('aria-valuemax', '100');
            if (percent !== null) {
                track.setAttribute('aria-valuenow', String(Math.round(percent)));
                track.setAttribute('aria-valuetext', `${Math.round(percent)} percent`);
                row.style.setProperty('--ovrp-queue-progress', `${percent}%`);
            } else {
                track.setAttribute('aria-valuetext', valueLabel(model));
            }
            const fill = document.createElement('span');
            fill.className = 'ovrp-queue-fill';
            fill.setAttribute('aria-hidden', 'true');
            track.appendChild(fill);

            row.append(selectButton, track);
            fragment.appendChild(row);
        }
        list.replaceChildren(fragment);
    }

    function scheduleRender(payload = latestPayload) {
        latestPayload = payload;
        if (renderScheduled) return;
        renderScheduled = true;
        queueMicrotask(renderQueue);
    }

    function attachDockObserver() {
        const dock = document.getElementById('ov-reliable-progress');
        if (!dock || dockObserver) return !!dock;
        rootObserver?.disconnect();
        rootObserver = null;
        dockObserver = new MutationObserver(() => scheduleRender());
        dockObserver.observe(dock, { childList: true, subtree: true });
        scheduleRender();
        return true;
    }

    const previousFetch = InferBridge.chain();
    InferBridge.use(async function operationQueueFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const response = await previousFetch(input, init);
        if (target.sameOrigin && target.path === STATUS_PATH && method === 'GET' && response.ok) {
            response.clone().json().then(payload => scheduleRender(payload)).catch(() => {});
        }
        return response;
    });

    document.getElementById('model-select')?.addEventListener('change', () => scheduleRender());

    if (!attachDockObserver()) {
        rootObserver = new MutationObserver(() => attachDockObserver());
        rootObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=OPERATION_QUEUE_JS,
    before=("ovllm-model-progress-extension",),
    description="Queued lifecycle operation visibility.",
)


def install_operation_queue_ui_extension() -> None:
    """Register queued-operation visibility."""

    ui_registry.register(EXTENSION)


__all__ = ["OPERATION_QUEUE_JS", "install_operation_queue_ui_extension"]
