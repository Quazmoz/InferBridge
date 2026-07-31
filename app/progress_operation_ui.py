"""Operation-aware reconciliation for the existing browser progress controller."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-progress-operation-extension"
_PROGRESS_MARKER = '<script id="ovllm-model-progress-extension">'

PROGRESS_OPERATION_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmProgressOperationsInstalled) return;
    window.__ovllmProgressOperationsInstalled = true;

    const STATUS_PATH = '/v1/system/status';
    const acceptedModels = new Map();
    let latestPayload = null;
    let metadataScheduled = false;
    let dockObserver = null;
    let rootObserver = null;

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

    function progressIdentity(model) {
        const progress = model?.progress || {};
        const operationId = typeof progress.operation_id === 'string'
            ? progress.operation_id
            : '';
        const revision = Number.isInteger(progress.revision) && progress.revision >= 0
            ? progress.revision
            : 0;
        const updatedAt = Number.isFinite(Number(progress.updated_at))
            ? Number(progress.updated_at)
            : 0;
        return { operationId, revision, updatedAt };
    }

    function isTerminal(model) {
        return ['error', 'cancelled'].includes(String(model?.status || ''));
    }

    function shouldKeepPrevious(previous, identity, model) {
        if (!previous) return false;
        if (identity.operationId === previous.operationId) {
            return identity.revision < previous.revision;
        }
        if (!identity.operationId || !previous.operationId) return false;

        // Across a normal retry, the server's per-model revision keeps increasing.
        // Across a process restart, revisions reset but the new operation timestamp is
        // later. Reject only a genuinely older operation snapshot.
        if (identity.updatedAt < previous.updatedAt) return true;
        if (identity.updatedAt > previous.updatedAt) return false;
        if (identity.revision > previous.revision) return false;
        return !!(previous.model?.is_loading && model?.is_loading);
    }

    function reconcilePayload(payload) {
        const source = payload?.models?.available;
        if (!Array.isArray(source)) return payload;

        const present = new Set();
        const models = source.map(model => {
            if (!model?.id) return model;
            present.add(model.id);
            const identity = progressIdentity(model);
            const previous = acceptedModels.get(model.id);

            // A cleared operation intentionally returns to the default revision-zero
            // shape after unload/delete. Do not pin a completed operation forever.
            if (
                !identity.operationId
                && identity.revision === 0
                && !model.is_loading
                && !isTerminal(model)
            ) {
                acceptedModels.delete(model.id);
                return model;
            }

            if (shouldKeepPrevious(previous, identity, model)) {
                return previous.model;
            }

            acceptedModels.set(model.id, { ...identity, model });
            return model;
        });

        for (const modelId of acceptedModels.keys()) {
            if (!present.has(modelId)) acceptedModels.delete(modelId);
        }
        return {
            ...payload,
            models: { ...(payload.models || {}), available: models },
        };
    }

    function reconciledResponse(response, payload) {
        const headers = new Headers(response.headers);
        headers.delete('content-length');
        return new Response(JSON.stringify(payload), {
            status: response.status,
            statusText: response.statusText,
            headers,
        });
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
            || models.find(item => item.id === selectedId && isTerminal(item))
            || null;
        if (!model) return null;
        const identity = progressIdentity(model);
        if (!identity.operationId) return null;
        return {
            modelId: model.id,
            modelName: String(model.name || model.id).split(' — ')[0],
            operationType: String(model.progress?.operation_type || 'operation'),
            ...identity,
        };
    }

    function operationLabel(operation) {
        const suffix = operation.operationId.slice(-8);
        const kind = operation.operationType === 'convert'
            ? 'convert'
            : (operation.operationType === 'load' ? 'load' : 'operation');
        return `Operation ${kind} ${suffix} · update ${operation.revision}`;
    }

    function renderMetadata() {
        metadataScheduled = false;
        const dock = document.getElementById('ov-reliable-progress');
        if (!dock) return;
        const operation = selectedOperation(latestPayload);
        if (!operation) {
            delete dock.dataset.operationId;
            delete dock.dataset.operationRevision;
            dock.querySelector('.ovrp-operation-meta')?.remove();
            return;
        }

        dock.dataset.operationId = operation.operationId;
        dock.dataset.operationRevision = String(operation.revision);
        dock.title = `${operation.modelName}. ${operationLabel(operation)}.`;
        const metadata = dock.querySelector('.ovrp-meta');
        if (!metadata) return;
        let item = metadata.querySelector('.ovrp-operation-meta');
        if (!item) {
            item = document.createElement('span');
            item.className = 'ovrp-operation-meta';
            metadata.appendChild(item);
        }
        const label = operationLabel(operation);
        if (item.textContent !== label) item.textContent = label;
    }

    function scheduleMetadata(payload = latestPayload) {
        latestPayload = payload;
        if (metadataScheduled) return;
        metadataScheduled = true;
        queueMicrotask(renderMetadata);
    }

    function attachDockObserver() {
        const dock = document.getElementById('ov-reliable-progress');
        if (!dock || dockObserver) return !!dock;
        rootObserver?.disconnect();
        rootObserver = null;
        dockObserver = new MutationObserver(() => scheduleMetadata());
        dockObserver.observe(dock, { childList: true, subtree: true });
        scheduleMetadata();
        return true;
    }

    const previousFetch = window.fetch.bind(window);
    window.fetch = async function operationAwareFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const isStatus = target.sameOrigin && target.path === STATUS_PATH && method === 'GET';
        let response;
        try {
            response = await previousFetch(input, init);
        } catch (error) {
            if (isStatus) {
                acceptedModels.clear();
                latestPayload = null;
            }
            throw error;
        }
        if (!isStatus || !response.ok) return response;

        try {
            const payload = reconcilePayload(await response.clone().json());
            scheduleMetadata(payload);
            return reconciledResponse(response, payload);
        } catch {
            return response;
        }
    };

    if (!attachDockObserver()) {
        rootObserver = new MutationObserver(() => attachDockObserver());
        rootObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
})();
"""


def install_progress_operation_ui_extension() -> None:
    """Inject reconciliation before the main progress controller executes."""

    if getattr(ui_extension, "_PROGRESS_OPERATION_UI_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_progress_operations(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{PROGRESS_OPERATION_JS}\n</script>\n'
        if _PROGRESS_MARKER in html:
            return html.replace(_PROGRESS_MARKER, f"{script}{_PROGRESS_MARKER}", 1)
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_progress_operations
    ui_extension._PROGRESS_OPERATION_UI_INSTALLED = True


__all__ = ["PROGRESS_OPERATION_JS", "install_progress_operation_ui_extension"]
