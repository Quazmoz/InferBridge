"""Guarded WebGUI controls for operation-scoped model cancellation."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-model-cancellation-extension"
_PROGRESS_MARKER = '<script id="ovllm-model-progress-extension">'

CANCELLATION_UI_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmCancellationUiInstalled) return;
    window.__ovllmCancellationUiInstalled = true;

    const STATUS_PATH = '/v1/system/status';
    const CANCEL_PATH = '/v1/models/cancel';
    let latestPayload = null;
    let renderScheduled = false;
    let dockObserver = null;
    let rootObserver = null;
    let cancellationInFlight = '';
    let feedback = null;

    const style = document.createElement('style');
    style.textContent = `
        .ovrp-cancel-control{display:inline-flex;align-items:center;gap:7px;min-width:0}
        .ovrp-cancel-button{min-height:30px;padding:5px 10px;border:1px solid color-mix(in srgb,var(--red) 50%,var(--border));border-radius:8px;background:color-mix(in srgb,var(--red) 9%,var(--surface-2));color:var(--red);font:inherit;font-size:10.5px;font-weight:750;cursor:pointer;white-space:nowrap}
        .ovrp-cancel-button:hover:not(:disabled){background:color-mix(in srgb,var(--red) 16%,var(--surface-2))}
        .ovrp-cancel-button:focus-visible{outline:2px solid var(--red);outline-offset:2px}
        .ovrp-cancel-button:disabled{opacity:.62;cursor:wait}
        .ovrp-cancel-feedback{max-width:100%;color:var(--text-2);font-size:10.5px;overflow-wrap:anywhere}
        .ovrp-cancel-feedback.error{color:var(--red)}
        .ovrp-cancel-note{max-width:100%;color:var(--text-3);font-size:10.5px;overflow-wrap:anywhere}
        @media(max-width:640px){.ovrp-cancel-control{flex:1 0 100%;justify-content:flex-start}.ovrp-cancel-button{min-height:34px}}
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

    function requestHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        if (key) headers.Authorization = `Bearer ${key}`;
        return headers;
    }

    function selectedOperation(payload) {
        const models = payload?.models?.available;
        if (!Array.isArray(models)) return null;
        const selectedId = document.getElementById('model-select')?.value || '';
        const waitingId = typeof waitingForModelId === 'undefined' ? '' : waitingForModelId || '';
        const loading = models.filter(model => model?.is_loading);
        const model = loading.find(item => item.id === waitingId)
            || loading.find(item => item.id === selectedId)
            || loading[0]
            || null;
        const progress = model?.progress || {};
        const operationId = typeof progress.operation_id === 'string' ? progress.operation_id : '';
        if (!model || !operationId) return null;
        return {
            modelId: model.id,
            modelName: String(model.name || model.id).split(' — ')[0],
            operationId,
            operationType: String(progress.operation_type || 'operation'),
            revision: Number.isInteger(progress.revision) ? progress.revision : 0,
            canCancel: model.can_cancel === true,
            cancelMode: typeof model.cancel_mode === 'string' ? model.cancel_mode : '',
            cancelReason: typeof model.cancel_reason === 'string' ? model.cancel_reason : '',
        };
    }

    function errorMessage(payload, fallback) {
        const detail = payload?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail.message === 'string' && detail.message.trim()) {
            return detail.message.trim();
        }
        return fallback;
    }

    function mergeReturnedModel(payload) {
        if (!payload?.model || !latestPayload?.models) return;
        const available = Array.isArray(latestPayload.models.available)
            ? [...latestPayload.models.available]
            : [];
        const index = available.findIndex(model => model.id === payload.model.id);
        if (index >= 0) available[index] = payload.model;
        else available.push(payload.model);
        latestPayload = {
            ...latestPayload,
            models: { ...latestPayload.models, available },
        };
    }

    function setFeedback(operationId, message, isError = false) {
        feedback = message ? { operationId, message, isError } : null;
        scheduleRender();
    }

    function confirmationMessage(operation) {
        if (operation.cancelMode === 'conversion') {
            return `Cancel conversion for ${operation.modelName}? The converter process will be stopped. Partial files may remain and can be replaced by retrying.`;
        }
        return `Cancel queued preparation for ${operation.modelName}?`;
    }

    async function refreshStatus() {
        try {
            const response = await window.fetch(STATUS_PATH, {
                headers: requestHeaders(),
                cache: 'no-store',
            });
            if (response.ok) await response.json();
        } catch {
            // The base UI owns persistent connectivity reporting.
        }
    }

    async function cancelOperation(operation) {
        if (!operation.canCancel || cancellationInFlight) return;
        if (!window.confirm(confirmationMessage(operation))) return;

        cancellationInFlight = operation.operationId;
        setFeedback(operation.operationId, 'Cancelling model preparation…');
        try {
            const response = await window.fetch(CANCEL_PATH, {
                method: 'POST',
                headers: requestHeaders(),
                body: JSON.stringify({
                    model: operation.modelId,
                    operation_id: operation.operationId,
                }),
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch {
                payload = null;
            }
            if (!response.ok) {
                throw new Error(errorMessage(payload, `Cancellation failed with HTTP ${response.status}.`));
            }
            mergeReturnedModel(payload);
            setFeedback(operation.operationId, String(payload?.message || 'Model preparation cancelled.'));
            window.dispatchEvent(new CustomEvent('inferbridge:model-operation-cancelled', {
                detail: {
                    model: operation.modelId,
                    operationId: operation.operationId,
                },
            }));
            await refreshStatus();
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Cancellation failed.';
            setFeedback(operation.operationId, message, true);
            await refreshStatus();
        } finally {
            cancellationInFlight = '';
            scheduleRender();
        }
    }

    function removeControls(dock) {
        dock.querySelector('.ovrp-cancel-control')?.remove();
        dock.querySelector('.ovrp-cancel-note')?.remove();
        dock.querySelector('.ovrp-cancel-feedback')?.remove();
    }

    function renderControls() {
        renderScheduled = false;
        const dock = document.getElementById('ov-reliable-progress');
        if (!dock) return;
        const metadata = dock.querySelector('.ovrp-meta');
        const operation = selectedOperation(latestPayload);
        if (!metadata || !operation) {
            removeControls(dock);
            return;
        }

        let control = metadata.querySelector('.ovrp-cancel-control');
        let note = metadata.querySelector('.ovrp-cancel-note');
        if (!operation.canCancel) {
            control?.remove();
            if (operation.cancelReason) {
                if (!note) {
                    note = document.createElement('span');
                    note.className = 'ovrp-cancel-note';
                    metadata.appendChild(note);
                }
                if (note.textContent !== operation.cancelReason) {
                    note.textContent = operation.cancelReason;
                }
            } else {
                note?.remove();
            }
        } else {
            note?.remove();
            if (!control) {
                control = document.createElement('span');
                control.className = 'ovrp-cancel-control';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ovrp-cancel-button';
                control.appendChild(button);
                metadata.appendChild(control);
            }
            const button = control.querySelector('.ovrp-cancel-button');
            const busy = cancellationInFlight === operation.operationId;
            const label = busy
                ? 'Cancelling…'
                : (operation.cancelMode === 'conversion' ? 'Cancel conversion' : 'Cancel preparation');
            if (button.disabled !== busy) button.disabled = busy;
            if (button.textContent !== label) button.textContent = label;
            const ariaLabel = `${label} for ${operation.modelName}`;
            if (button.getAttribute('aria-label') !== ariaLabel) {
                button.setAttribute('aria-label', ariaLabel);
            }
            button.onclick = () => void cancelOperation(operation);
        }

        let item = metadata.querySelector('.ovrp-cancel-feedback');
        if (feedback && feedback.operationId === operation.operationId) {
            if (!item) {
                item = document.createElement('span');
                item.setAttribute('role', 'status');
                item.setAttribute('aria-live', 'polite');
                metadata.appendChild(item);
            }
            const className = `ovrp-cancel-feedback${feedback.isError ? ' error' : ''}`;
            if (item.className !== className) item.className = className;
            if (item.textContent !== feedback.message) item.textContent = feedback.message;
        } else {
            item?.remove();
        }
    }

    function scheduleRender(payload = latestPayload) {
        latestPayload = payload;
        if (renderScheduled) return;
        renderScheduled = true;
        queueMicrotask(renderControls);
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
    window.fetch = async function cancellationAwareFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const response = await previousFetch(input, init);
        if (
            target.sameOrigin
            && target.path === STATUS_PATH
            && method === 'GET'
            && response.ok
        ) {
            response.clone().json().then(payload => scheduleRender(payload)).catch(() => {});
        }
        return response;
    };

    document.getElementById('model-select')?.addEventListener('change', () => {
        feedback = null;
        scheduleRender();
    });

    if (!attachDockObserver()) {
        rootObserver = new MutationObserver(() => attachDockObserver());
        rootObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
})();
"""


def install_cancellation_ui_extension() -> None:
    """Inject cancellation controls before the main progress controller executes."""

    if getattr(ui_extension, "_MODEL_CANCELLATION_UI_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_cancellation(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{CANCELLATION_UI_JS}\n</script>\n'
        if _PROGRESS_MARKER in html:
            return html.replace(_PROGRESS_MARKER, f"{script}{_PROGRESS_MARKER}", 1)
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_cancellation
    ui_extension._MODEL_CANCELLATION_UI_INSTALLED = True


__all__ = ["CANCELLATION_UI_JS", "install_cancellation_ui_extension"]
