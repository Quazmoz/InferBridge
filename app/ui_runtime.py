"""The shared browser runtime every other extension builds on.

Before this existed, sixteen extensions each did the same thing independently::

    const previousFetch = window.fetch.bind(window);
    window.fetch = async function somethingAwareFetch(input, init = {}) { ... };

That produced sixteen stacked reassignments of ``window.fetch``. The stack worked, but it
was invisible: nothing could list it, nothing could say which layer ran in which order, and
every layer had to re-derive shared facts by reading the DOM because there was nowhere to
put state. The registry now owns *document* order; this runtime owns *request* order.

``window.fetch`` is assigned exactly once, here. Layers register with the same two-step
idiom they already used, so the behavior is unchanged and the composition becomes
inspectable::

    const previousFetch = InferBridge.chain();   // everything registered before me
    InferBridge.use(async function somethingAwareFetch(input, init = {}) { ... });

``chain()`` snapshots the current top of the stack, exactly as ``window.fetch.bind(window)``
used to, and ``use()`` pushes a new top, exactly as assigning ``window.fetch`` used to. A
layer that issues its own request through ``previousFetch`` still reaches only the layers
below it, and a layer that calls ``window.fetch`` still traverses the whole stack.

On top of that the runtime offers what the old arrangement had no place for: a shared state
store, an event bus, a passive response observer that clones a response only when something
is actually listening, and one registry of the element ids that layers reach for.
"""

from __future__ import annotations

from app.ui_registry import UiExtension

__all__ = ["EXTENSION", "RUNTIME_EXTENSION_ID", "RUNTIME_JS"]

RUNTIME_EXTENSION_ID = "inferbridge-runtime"

RUNTIME_JS = r"""
(() => {
'use strict';
if (window.InferBridge) return;

// --- request middleware ---------------------------------------------------------------
// `top` is the current outermost handler and is what window.fetch delegates to. It starts
// as the native implementation, so a page with no registered layers behaves natively.
const nativeFetch = window.fetch.bind(window);
const registered = [];
let top = nativeFetch;

function chain() {
    // Snapshot the present top of the stack. Equivalent to the historical
    // `window.fetch.bind(window)` captured at install time: the returned function reaches
    // every layer registered before this call and none registered after it.
    const captured = top;
    return function downstream(input, init) {
        return captured(input, init);
    };
}

function use(handler) {
    if (typeof handler !== 'function') {
        throw new TypeError('InferBridge.use expects a function.');
    }
    registered.push(handler.name || `layer-${registered.length}`);
    top = handler;
    return handler;
}

function middleware() {
    // Diagnostics: the request stack, outermost last. Nothing could ask this before.
    return registered.slice();
}

// window.fetch is assigned exactly once in the whole application, right here.
window.fetch = function inferBridgeFetch(input, init) {
    return top(input, init);
};

// --- events ---------------------------------------------------------------------------
const listeners = new Map();

function on(event, handler) {
    if (typeof handler !== 'function') return () => {};
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(handler);
    return function off() {
        listeners.get(event)?.delete(handler);
    };
}

function emit(event, detail) {
    const handlers = listeners.get(event);
    if (!handlers || !handlers.size) return;
    // Iterate a copy so a handler may unsubscribe during dispatch, and isolate throws so
    // one broken listener cannot stop the others or fail the request that emitted.
    for (const handler of Array.from(handlers)) {
        try {
            handler(detail);
        } catch (error) {
            console.error(`InferBridge listener for "${event}" failed`, error);
        }
    }
}

// --- shared state ---------------------------------------------------------------------
// Somewhere to keep facts every layer needs, so features stop rediscovering them from the
// DOM. Writes are change-gated, so subscribers only run on an actual transition.
const state = new Map();

function get(key) {
    return state.get(key);
}

function set(key, value) {
    if (state.has(key) && state.get(key) === value) return value;
    state.set(key, value);
    emit(`state:${key}`, value);
    emit('state', { key, value });
    return value;
}

function subscribe(key, handler) {
    const unsubscribe = on(`state:${key}`, handler);
    if (state.has(key)) {
        try {
            handler(state.get(key));
        } catch (error) {
            console.error(`InferBridge subscriber for "${key}" failed`, error);
        }
    }
    return unsubscribe;
}

// --- element registry -----------------------------------------------------------------
// One place naming the shell elements that layers reach into. `#model-select` alone was
// looked up from fifteen separate call sites, each restating the coupling.
const ELEMENTS = {
    app: 'app',
    modelSelect: 'model-select',
    userInput: 'user-input',
    chatForm: 'chat-form',
    inputArea: 'input-area',
    settingsSidebar: 'settings-sidebar',
    settingsToggle: 'settings-toggle-btn',
    headerMoreMenu: 'ov-header-more-menu',
    headerMoreButton: 'ov-header-more-btn',
};

function el(name) {
    const id = ELEMENTS[name];
    if (!id) throw new RangeError(`Unknown InferBridge element "${name}".`);
    return document.getElementById(id);
}

// --- forms that must never navigate ----------------------------------------------------
// The static shell used to carry `onsubmit="return false;"` on three forms. An inline event
// handler attribute cannot be covered by a nonce, so keeping them would have forced
// script-src to allow 'unsafe-inline' for the whole page. The markup now declares the
// intent and this listener enforces it. Capture phase matches the old attribute's timing,
// and not stopping propagation keeps every other submit listener running as before.
document.addEventListener('submit', (event) => {
    const form = event.target;
    if (form instanceof HTMLFormElement && form.hasAttribute('data-inferbridge-no-submit')) {
        event.preventDefault();
    }
}, true);

// --- request telemetry and passive observers -------------------------------------------
const observers = new Set();

function observe(matcher, handler) {
    if (typeof handler !== 'function') return () => {};
    const test = typeof matcher === 'function'
        ? matcher
        : (request) => request.path === String(matcher);
    const entry = { test, handler };
    observers.add(entry);
    return function unobserve() {
        observers.delete(entry);
    };
}

function describe(input, init) {
    let url = '';
    if (typeof input === 'string') url = input;
    else if (input instanceof URL) url = input.href;
    else if (input && typeof input.url === 'string') url = input.url;
    let path = url;
    let sameOrigin = true;
    try {
        const resolved = new URL(url, window.location.href);
        sameOrigin = resolved.origin === window.location.origin;
        path = resolved.pathname;
    } catch { /* a non-URL input keeps the raw value and is treated as same-origin */ }
    const method = String(
        init?.method || (typeof input !== 'string' && input?.method) || 'GET',
    ).toUpperCase();
    return { url, path, method, sameOrigin };
}

// The bottom-most layer. Everything registered later stacks above it, so what it reports is
// the request as it actually goes to the network, after every transformation.
use(async function inferBridgeTelemetry(input, init = {}) {
    const request = describe(input, init);
    emit('request', request);
    let response;
    try {
        response = await nativeFetch(input, init);
    } catch (error) {
        emit('request:error', { request, error });
        throw error;
    }
    emit('response', { request, status: response.status, ok: response.ok });

    const matched = [];
    for (const entry of observers) {
        try {
            if (entry.test(request)) matched.push(entry.handler);
        } catch { /* a throwing matcher simply does not match */ }
    }
    // Clone only when something is listening. The historical layers cloned unconditionally.
    if (matched.length && response.ok) {
        const copy = response.clone();
        const contentType = copy.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            copy.json().then(
                (body) => {
                    for (const handler of matched) {
                        try {
                            handler(body, request);
                        } catch (error) {
                            console.error('InferBridge observer failed', error);
                        }
                    }
                },
                () => { /* a body that is not valid JSON is not an observable event */ },
            );
        }
    }
    return response;
});

window.InferBridge = Object.freeze({
    chain,
    use,
    middleware,
    on,
    emit,
    get,
    set,
    subscribe,
    el,
    elements: Object.freeze({ ...ELEMENTS }),
    observe,
    describe,
    native: nativeFetch,
});
})();
"""

EXTENSION = UiExtension(
    extension_id=RUNTIME_EXTENSION_ID,
    javascript=RUNTIME_JS,
    description="Shared request middleware, state store, event bus, and element registry.",
)
