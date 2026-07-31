"""Browser compatibility layer for split lifecycle, telemetry, and event polling."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-status-split-extension"
_OPERATION_MARKER = '<script id="ovllm-progress-operation-extension">'
_PROGRESS_MARKER = '<script id="ovllm-model-progress-extension">'

STATUS_SPLIT_JS = r"""
(() => {
    'use strict';
    if (window.__inferbridgeSplitStatusInstalled) return;
    window.__inferbridgeSplitStatusInstalled = true;

    const LEGACY_PATH = '/v1/system/status';
    const MODELS_PATH = '/v1/models/status';
    const TELEMETRY_PATH = '/v1/system/telemetry';
    const EVENTS_PATH = '/v1/events';
    const MODEL_MUTATION_PATHS = new Set([
        '/v1/models/load',
        '/v1/models/convert',
        '/v1/models/download-custom',
        '/v1/models/cancel',
        '/v1/models/unload',
        '/v1/models/delete',
        '/v1/model-library/import-definitions',
        '/v1/model-library/import-converted',
    ]);
    const ACTIVE_MODEL_TTL_MS = 800;
    const IDLE_MODEL_TTL_MS = 3000;
    const TELEMETRY_TTL_MS = 5000;
    const EVENTS_TTL_MS = 10000;
    const EVENT_LIMIT = 50;
    const states = new Map();

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

    function mergedHeaders(input, init) {
        const headers = new Headers(input instanceof Request ? input.headers : undefined);
        if (init?.headers) {
            new Headers(init.headers).forEach((value, key) => headers.set(key, value));
        }
        return headers;
    }

    function stateFor(headers) {
        const key = headers.get('authorization') || '';
        let state = states.get(key);
        if (!state) {
            state = {
                models: null,
                modelsAt: 0,
                modelsPromise: null,
                telemetry: null,
                telemetryAt: 0,
                telemetryPromise: null,
                events: [],
                eventCursor: 0,
                eventsAt: 0,
                eventsPromise: null,
            };
            states.set(key, state);
        }
        return state;
    }

    function invalidateModels(headers = null) {
        if (headers) {
            stateFor(headers).modelsAt = 0;
            return;
        }
        for (const state of states.values()) state.modelsAt = 0;
    }

    window.__inferbridgeInvalidateModelStatus = () => invalidateModels();
    window.addEventListener('inferbridge:model-status-invalidated', () => invalidateModels());

    function hasActiveModels(payload) {
        const models = payload?.models?.available;
        return Array.isArray(models) && models.some(model => model?.is_loading);
    }

    function internalInit(headers, init) {
        return {
            headers,
            cache: 'no-store',
            credentials: init?.credentials,
            signal: init?.signal,
        };
    }

    async function readJson(response, label) {
        if (!response.ok) throw new Error(`${label} failed with HTTP ${response.status}.`);
        return response.json();
    }

    const nativeFetch = window.fetch.bind(window);

    async function modelsSnapshot(state, headers, init) {
        const now = Date.now();
        const ttl = hasActiveModels(state.models) ? ACTIVE_MODEL_TTL_MS : IDLE_MODEL_TTL_MS;
        if (state.models && now - state.modelsAt < ttl) {
            return { payload: state.models, response: null, cacheHit: true };
        }
        if (!state.modelsPromise) {
            state.modelsPromise = nativeFetch(MODELS_PATH, internalInit(headers, init))
                .then(async response => ({
                    payload: await readJson(response.clone(), 'Model status'),
                    response,
                }))
                .then(result => {
                    state.models = result.payload;
                    state.modelsAt = Date.now();
                    return result;
                })
                .finally(() => { state.modelsPromise = null; });
        }
        const result = await state.modelsPromise;
        return { ...result, cacheHit: false };
    }

    async function telemetrySnapshot(state, headers, init) {
        const now = Date.now();
        if (state.telemetry && now - state.telemetryAt < TELEMETRY_TTL_MS) {
            return { payload: state.telemetry, cacheHit: true };
        }
        if (!state.telemetryPromise) {
            state.telemetryPromise = nativeFetch(TELEMETRY_PATH, internalInit(headers, init))
                .then(response => readJson(response, 'System telemetry'))
                .then(payload => {
                    state.telemetry = payload;
                    state.telemetryAt = Date.now();
                    return payload;
                })
                .finally(() => { state.telemetryPromise = null; });
        }
        try {
            const payload = await state.telemetryPromise;
            return { payload, cacheHit: false };
        } catch (error) {
            if (state.telemetry) return { payload: state.telemetry, cacheHit: true };
            throw error;
        }
    }

    function mergeEvents(state, payload) {
        const incoming = Array.isArray(payload?.data) ? payload.data : [];
        if (payload?.reset_required) state.events = [];
        const byId = new Map();
        for (const event of [...state.events, ...incoming]) {
            const id = Number(event?.id);
            const key = Number.isInteger(id) && id > 0
                ? `id:${id}`
                : `legacy:${event?.timestamp || 0}:${event?.level || ''}:${event?.message || ''}`;
            byId.set(key, event);
        }
        state.events = [...byId.values()]
            .sort((left, right) => Number(left?.id || 0) - Number(right?.id || 0))
            .slice(-EVENT_LIMIT);
        const nextCursor = Number(payload?.next_cursor);
        if (Number.isInteger(nextCursor) && nextCursor >= 0) state.eventCursor = nextCursor;
        state.eventsAt = Date.now();
        return state.events;
    }

    async function eventsSnapshot(state, headers, init) {
        const now = Date.now();
        if (now - state.eventsAt < EVENTS_TTL_MS) {
            return { payload: state.events, cacheHit: true };
        }
        if (!state.eventsPromise) {
            const query = `${EVENTS_PATH}?cursor=${encodeURIComponent(state.eventCursor)}&limit=${EVENT_LIMIT}`;
            state.eventsPromise = nativeFetch(query, internalInit(headers, init))
                .then(response => readJson(response, 'Events'))
                .then(payload => mergeEvents(state, payload))
                .finally(() => { state.eventsPromise = null; });
        }
        try {
            const payload = await state.eventsPromise;
            return { payload, cacheHit: false };
        } catch {
            return { payload: state.events, cacheHit: true };
        }
    }

    function mergeModelAdvisor(models, telemetry) {
        const source = models || { loaded: [], count: 0, loading_count: 0, available: [] };
        const advisors = telemetry?.model_advisor;
        if (!advisors || typeof advisors !== 'object') return source;
        const available = Array.isArray(source.available)
            ? source.available.map(model => {
                const advisor = advisors[model?.id];
                return advisor && typeof advisor === 'object' ? { ...model, advisor } : model;
            })
            : [];
        return { ...source, available };
    }

    function composedResponse(modelResult, telemetryResult, eventsResult) {
        const modelPayload = modelResult.payload || {};
        const telemetry = telemetryResult.payload || {};
        const headers = new Headers(modelResult.response?.headers || undefined);
        headers.set('content-type', 'application/json');
        headers.set('cache-control', 'no-store');
        headers.delete('content-length');
        const payload = {
            ...telemetry,
            device: {
                ...(telemetry.device || {}),
                ...(modelPayload.device || {}),
            },
            models: mergeModelAdvisor(modelPayload.models, telemetry),
            events: eventsResult.payload || [],
            split_status: {
                models_endpoint: MODELS_PATH,
                telemetry_endpoint: TELEMETRY_PATH,
                events_endpoint: EVENTS_PATH,
                model_cache_hit: modelResult.cacheHit,
                telemetry_cache_hit: telemetryResult.cacheHit,
                events_cache_hit: eventsResult.cacheHit,
                telemetry_ttl_seconds: TELEMETRY_TTL_MS / 1000,
                events_ttl_seconds: EVENTS_TTL_MS / 1000,
            },
        };
        window.dispatchEvent(new CustomEvent('inferbridge:status-composed', {
            detail: {
                activeOperations: Number(payload.models?.loading_count || 0),
                modelCacheHit: modelResult.cacheHit,
                telemetryCacheHit: telemetryResult.cacheHit,
                eventsCacheHit: eventsResult.cacheHit,
            },
        }));
        return new Response(JSON.stringify(payload), {
            status: modelResult.response?.status || 200,
            statusText: modelResult.response?.statusText || 'OK',
            headers,
        });
    }

    window.fetch = async function splitStatusFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const headers = mergedHeaders(input, init);
        if (
            target.sameOrigin
            && MODEL_MUTATION_PATHS.has(target.path)
            && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
        ) {
            const response = await nativeFetch(input, init);
            invalidateModels(headers);
            return response;
        }

        const isLegacyStatus = target.sameOrigin
            && target.path === LEGACY_PATH
            && method === 'GET';
        if (!isLegacyStatus) return nativeFetch(input, init);

        const state = stateFor(headers);
        try {
            const modelResult = await modelsSnapshot(state, headers, init);
            const [telemetryResult, eventsResult] = await Promise.all([
                telemetrySnapshot(state, headers, init),
                eventsSnapshot(state, headers, init),
            ]);
            return composedResponse(modelResult, telemetryResult, eventsResult);
        } catch {
            // The compatibility route remains the safe fallback when a split
            // endpoint is unavailable. A failed lifecycle request is never hidden
            // behind stale model state.
            return nativeFetch(input, init);
        }
    };
})();
"""


def install_status_split_ui_extension() -> None:
    """Inject split polling before operation-aware fetch wrappers execute."""

    if getattr(ui_extension, "_STATUS_SPLIT_UI_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_split_status(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{STATUS_SPLIT_JS}\n</script>\n'
        if _OPERATION_MARKER in html:
            return html.replace(_OPERATION_MARKER, f"{script}{_OPERATION_MARKER}", 1)
        if _PROGRESS_MARKER in html:
            return html.replace(_PROGRESS_MARKER, f"{script}{_PROGRESS_MARKER}", 1)
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_split_status
    ui_extension._STATUS_SPLIT_UI_INSTALLED = True


__all__ = ["STATUS_SPLIT_JS", "install_status_split_ui_extension"]
