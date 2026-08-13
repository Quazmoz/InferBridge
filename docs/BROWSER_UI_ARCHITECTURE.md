# Browser UI architecture

The InferBridge browser client is a static document plus a set of CSS/JavaScript payloads
held in Python modules. There is no Node toolchain, no bundler, and no frontend framework.
This document describes how the page is assembled, how features share a request pipeline,
and what to do when adding a surface.

## The three pieces

| Module | Responsibility |
|---|---|
| [`web/index.html`](../web/index.html) | The static shell: markup, base styles, and the core chat script. |
| [`app/ui_registry.py`](../app/ui_registry.py) | The registry and the two renderers. Owns **document order**. |
| [`app/ui_composition.py`](../app/ui_composition.py) | The declared list of everything in the page, in order. |
| [`app/ui_runtime.py`](../app/ui_runtime.py) | The shared browser runtime. Owns **request order**. |

Each feature lives in an `app/*_ui.py` module that exposes a `UiExtension` named `EXTENSION`
plus an `install_*()` function that registers it.

## Document order is declared, not emergent

Read [`app/ui_composition.py`](../app/ui_composition.py) top to bottom and you have the page.
`COMPOSITION` is the order the browser receives payloads.

When one surface must execute before another, the extension says so:

```python
EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=STATUS_SPLIT_JS,
    before=("ovllm-progress-operation-extension", "ovllm-model-progress-extension"),
)
```

`before` lists the extensions this payload must precede, most preferred first. The renderers
resolve those requirements into one order, and
`tests/test_ui_registry.py::test_composition_order_is_the_declared_order` asserts the result
against an explicit list, so a reordering cannot happen silently.

> **History.** Extensions used to compose themselves by rebinding
> `app.ui_extension.inject_multimodal_ui` to a closure wrapping the previous value, and five
> of them additionally searched the half-built page for another feature's `<script>` tag to
> insert themselves before. Document order was a side effect of import order, `app.server`
> captured a half-built chain when it imported the function by name, and two modules
> compensated by reaching into `sys.modules` to repair that binding and clear a cache. None
> of that is needed now: `inject_multimodal_ui` is a stable dispatcher over the registry.

## Request order is one stack

[`app/ui_runtime.py`](../app/ui_runtime.py) assigns `window.fetch` exactly once, for the
whole application, and exposes the stack that layers register into:

```js
const previousFetch = InferBridge.chain();   // every layer registered before me
InferBridge.use(async function myAwareFetch(input, init = {}) {
    // ...inspect or transform...
    return previousFetch(input, init);
});
```

`chain()` snapshots the current top of the stack; `use()` pushes a new top. A layer that
calls `previousFetch` reaches only the layers below it, and a layer that calls `window.fetch`
traverses the whole stack — the same semantics the sixteen individual `window.fetch`
reassignments had, now inspectable via `InferBridge.middleware()`.

The runtime also provides what the old arrangement had nowhere to put:

| API | Use |
|---|---|
| `InferBridge.on(event, fn)` / `emit(event, detail)` | Event bus. The runtime emits `request`, `response`, and `request:error`. |
| `InferBridge.get/set/subscribe(key, fn)` | Shared state, so features stop re-deriving facts from the DOM. `set` only notifies on an actual change. |
| `InferBridge.observe(matcher, fn)` | Read a JSON response passively. The response is cloned **only** when something is listening. |
| `InferBridge.el(name)` | The shell elements features reach for. `#model-select` alone had fifteen separate call sites. |

Prefer `observe()` over `use()` when you only need to read a response — it needs no
interception layer at all.

## Capability gating

Surfaces that need routes only the desktop launcher registers carry a capability:

```python
EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=_STORAGE_MANAGER_JS,
    css=_STORAGE_MANAGER_CSS,
    style_id="ovllm-storage-manager-style",
    capability="desktop",
)
```

They are **always registered** — so they are always composed, syntax-checked, and testable —
and render only when the capability is active. `app/desktop_server.py` activates it.

To see them on the development server or in a browser test:

```bash
INFERBRIDGE_UI_CAPABILITIES=desktop python -m app.server --mock
```

> **History.** The storage manager and runtime health center previously existed only if
> `app.desktop_server` happened to be the module that imported them, which is why neither had
> browser coverage. See [`browser_tests/test_desktop_surfaces_browser.py`](../browser_tests/test_desktop_surfaces_browser.py).

## Two renderers, one registry

| Renderer | Output | Used by |
|---|---|---|
| `render_inline` | Every payload embedded in the document. | Source-level tests, the JavaScript syntax gate. This is `inject_multimodal_ui`. |
| `render_document` | `<script src>` / `<link rel=stylesheet>` references to `/ui/` assets, and a nonce on the shell's own inline blocks. | What the server serves. |

`renderer_disagreements()` returns the ways they differ; a test asserts it stays empty, so
the surface tests read cannot drift from the surface the browser loads.

Asset URLs are content-addressed (`/ui/<id>.<sha256-prefix>.js`), so responses are served
`Cache-Control: public, max-age=31536000, immutable`. A payload edit produces a new URL.

External scripts are deliberately **not** `defer`red: classic scripts execute in document
order and interleave correctly with the shell's own inline script, which preserves the fully
inline render's execution order exactly.

Composition contributes **no** inline script — every payload is an asset. The only inline
blocks in the served page are the shell's own, so the hardened `script-src` below is a
structural property rather than something each new surface must remember to preserve.
`UiExtension` rejects a `head_html` that carries script, which is what keeps it that way.

Compression is precomputed per asset rather than applied by a response middleware, because
this server streams Server-Sent Events for chat and a global compressor would buffer those
chunks and delay tokens.

## Content-Security-Policy

`script-src` is `'self'` plus a per-response nonce — no `'unsafe-inline'`. That requires two
things to stay true, both asserted by tests:

1. Every inline `<script>` in the served document carries the response nonce.
2. No element uses an inline event-handler attribute (`onclick=`, `onsubmit=`, …). A nonce
   cannot cover those. Forms that must not navigate use `data-inferbridge-no-submit`, which
   the runtime enforces.

`style-src` keeps `'unsafe-inline'` because the static shell uses `style` attributes, which a
nonce cannot cover; adding a nonce there would make browsers ignore `'unsafe-inline'` and
drop that styling.

## Adding a surface

1. Create `app/my_feature_ui.py` with the CSS/JS payload constants.
2. Export an `EXTENSION = UiExtension(...)` and an idempotent `install_my_feature_extension()`
   that calls `ui_registry.register(EXTENSION)`.
3. Add it to `COMPOSITION` in [`app/ui_composition.py`](../app/ui_composition.py) at the
   position you want, with `before=` if it has an ordering requirement.
4. Add its id to `SERVER_ORDER` in `tests/test_ui_registry.py`.
5. If it needs desktop-only routes, set `capability="desktop"`.

Payload constants are captured **by value** when `EXTENSION` is constructed. If a module
rewrites a payload after defining it (branding substitutions, for example), that rewrite must
appear above the `EXTENSION` assignment.

## Verifying a change

```bash
pytest tests/test_ui_registry.py tests/test_ui_runtime.py tests/test_ui_assets_http.py
python scripts/check_injected_javascript.py      # needs Node.js; syntax-checks every payload
pytest browser_tests                             # needs playwright + chromium
```

The syntax gate matters: nothing else compiles the JavaScript, so one unbalanced parenthesis
would silently kill a whole `<script>` element while the Python suite stayed green. Failures
are reported under the owning extension's id.
