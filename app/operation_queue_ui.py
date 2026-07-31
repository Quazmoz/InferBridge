"""Expandable multi-operation queue for the stable model progress dock."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-operation-queue-extension"
_PROGRESS_MARKER = '<script id="ovllm-model-progress-extension">'

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
        .ovrp-queue-list{display:grid;gap:6px}
        .ovrp-queue-row{width:100%;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(90px,.8fr) auto;gap:8px;align-items:center;min-height:36px;padding:7px 9px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:inherit;text-align:left;font:inherit;cursor:pointer}
        .ovrp-queue-row:hover{border-color:color-mix(in srgb,var(--primary) 35%,var(--border));background:color-mix(in srgb,var(--primary) 5%,var(--surface-2))}
        .ovrp-queue-row:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
        .ovrp-queue-row.current{border-color:color-mix(in srgb,var(--primary) 45%,var(--border));background:color-mix(in srgb,var(--primary) 8%,var(--surface-2))}
        .ovrp-queue-model{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-1);font-size:10.5px;font-weight:750}
        .ovrp-queue-phase{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-3);font-size:10.5px}
        .ovrp-queue-value{color:var(--primary);font-size:10.5px;font-weight:750;font-variant-numeric:tabular-nums;white-space:nowrap}
        .ovrp-queue-current{display:inline-block;margin-left:6px;padding:1px 5px;border-radius:999px;background:color-mix(in srgb,var(--primary) 14%,transparent);color:var(--primary);font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.04em}
        @media(max-width:640px){.ovrp-queue-toggle{min-height:34px}.ovrp-queue-row{grid-template-columns:minmax(0,1fr) auto}.ovrp-queue-phase{grid-column:1/-1;grid-row:2}}
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

    function valueLabel(model) {
        const percent = Number(model?.progress?.percent);
        if (Number.isFinite(percent)) return `${Math.round(Math.max(0, Math.min(100, percent)))}%`;
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
            model.progress?.percent ?? '',
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
        list.setAttribute('role', 'list');
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

        const panel = ensurePanel(detail);
        panel.classList.toggle('expanded', queueExpanded);
        panel.hidden = !queueExpanded;
        const heading = panel.querySelector('.ovrp-queue-heading');
        if (heading.textContent !== toggleText) heading.textContent = toggleText;

        const signature = operationSignature(ordered, primary);
        if (signature === lastSignature) return;
        lastSignature = signature;
        const list = panel.querySelector('.ovrp-queue-list');
        const fragment = document.createDocumentFragment();
        for (const model of ordered) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = `ovrp-queue-row${model.id === primary?.id ? ' current' : ''}`;
            row.setAttribute('role', 'listitem');
            row.setAttribute('data-model-id', model.id);
            row.setAttribute(
                'aria-label',
                `${operationName(model)}, ${phaseLabel(model)}, ${valueLabel(model)}`,
            );
            row.onclick = event => {
                event.stopPropagation();
                selectOperation(model.id);
            };

            const name = document.createElement('span');
            name.className = 'ovrp-queue-model';
            name.textContent = operationName(model);
            if (model.id === primary?.id) {
                const badge = document.createElement('span');
                badge.className = 'ovrp-queue-current';
                badge.textContent = 'Current';
                name.appendChild(badge);
            }
            const phase = document.createElement('span');
            phase.className = 'ovrp-queue-phase';
            phase.textContent = phaseLabel(model);
            const value = document.createElement('span');
            value.className = 'ovrp-queue-value';
            value.textContent = valueLabel(model);
            row.append(name, phase, value);
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

    const previousFetch = window.fetch.bind(window);
    window.fetch = async function operationQueueFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const response = await previousFetch(input, init);
        if (target.sameOrigin && target.path === STATUS_PATH && method === 'GET' && response.ok) {
            response.clone().json().then(payload => scheduleRender(payload)).catch(() => {});
        }
        return response;
    };

    document.getElementById('model-select')?.addEventListener('change', () => scheduleRender());

    if (!attachDockObserver()) {
        rootObserver = new MutationObserver(() => attachDockObserver());
        rootObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
})();
"""


def install_operation_queue_ui_extension() -> None:
    """Inject the queue before the primary progress controller executes."""

    if getattr(ui_extension, "_OPERATION_QUEUE_UI_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_operation_queue(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{OPERATION_QUEUE_JS}\n</script>\n'
        if _PROGRESS_MARKER in html:
            return html.replace(_PROGRESS_MARKER, f"{script}{_PROGRESS_MARKER}", 1)
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_operation_queue
    ui_extension._OPERATION_QUEUE_UI_INSTALLED = True


__all__ = ["OPERATION_QUEUE_JS", "install_operation_queue_ui_extension"]
