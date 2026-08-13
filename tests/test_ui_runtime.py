"""Contract checks for the shared browser runtime.

Sixteen extensions each used to reassign ``window.fetch``, wrapping whatever the previous one
left behind. These tests keep that from coming back: one owner of ``window.fetch``, one way
to register into the request stack, and no module reaching back into the composition to
patch it.
"""

from __future__ import annotations

import re
from pathlib import Path

from app import (
    config,  # noqa: F401 - importing registers the browser composition
    ui_registry,
)
from app.ui_composition import DESKTOP_CAPABILITY
from app.ui_runtime import RUNTIME_EXTENSION_ID, RUNTIME_JS

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _payload_sources() -> dict[str, str]:
    """Return every registered extension's JavaScript, keyed by extension id."""

    ui_registry.activate(DESKTOP_CAPABILITY)
    payloads = {}
    for extension in ui_registry.extensions({DESKTOP_CAPABILITY}):
        payloads[extension.extension_id] = extension.javascript
    return payloads


def test_only_the_runtime_assigns_window_fetch():
    """One interception point. Previously there were sixteen, stacked invisibly."""

    offenders = {
        name: payload
        for name, payload in _payload_sources().items()
        if re.search(r"window\.fetch\s*=", payload)
    }
    assert list(offenders) == [RUNTIME_EXTENSION_ID], sorted(offenders)
    # And the runtime assigns it exactly once.
    assert len(re.findall(r"^window\.fetch = ", RUNTIME_JS, re.MULTILINE)) == 1


def test_layers_register_through_the_runtime():
    """Every layer that intercepts requests does so via the documented two-step idiom."""

    payloads = _payload_sources()
    users = {name for name, payload in payloads.items() if "InferBridge.use(" in payload}
    # Every feature layer that intercepted requests before the refactor still does, now
    # through the runtime. Sixteen wrappers were migrated; the advisor's capture and
    # registration live in two concatenated source files but compose into one payload.
    assert len(users) >= 15, sorted(users)
    assert RUNTIME_EXTENSION_ID not in users, "the runtime registers its own layer directly"
    for name in users:
        payload = payloads[name]
        # A layer that continues the chain must have captured it, or its `previousFetch`
        # would be undefined at call time.
        if re.search(r"\b(previousFetch|originalFetch|nativeFetch|upstreamFetch)\b", payload):
            assert "InferBridge.chain()" in payload, name

    # The runtime's own telemetry sits at the bottom of the stack.
    assert "use(async function inferBridgeTelemetry(input, init = {}) {" in RUNTIME_JS


def test_no_module_rebinds_the_composition_function():
    """The chain-of-wrappers pattern must not return.

    ``app.server`` imports ``inject_multimodal_ui`` by name. When modules rebound that
    attribute, the server captured a half-built chain, and two modules compensated by
    reaching into ``sys.modules`` to repair it and clear a cache.
    """

    offenders = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "ui_extension.inject_multimodal_ui = " in source:
            offenders.append(f"{path.name}: rebinds the composition function")
        if 'sys.modules.get("app.server")' in source:
            offenders.append(f"{path.name}: reaches into app.server to repair a binding")
    assert offenders == []


def test_runtime_exposes_the_documented_surface():
    exported = RUNTIME_JS.split("window.InferBridge = Object.freeze({", 1)[1]
    for member in (
        "chain",
        "use",
        "middleware",
        "on",
        "emit",
        "get",
        "set",
        "subscribe",
        "el",
        "elements",
        "observe",
        "describe",
        "native",
    ):
        assert re.search(rf"^\s+{member}[,:]", exported, re.MULTILINE), member
    assert "window.InferBridge = Object.freeze({" in RUNTIME_JS


def test_runtime_isolates_listener_and_observer_failures():
    """One broken listener must not break a request or the other listeners."""

    assert "for (const handler of Array.from(handlers))" in RUNTIME_JS
    assert RUNTIME_JS.count("console.error") >= 3


def test_runtime_clones_a_response_only_when_something_observes_it():
    """The layers this replaces cloned unconditionally; there were 17 such call sites."""

    assert "if (matched.length && response.ok)" in RUNTIME_JS
    assert RUNTIME_JS.count("response.clone()") == 1


def test_runtime_enforces_the_no_submit_marker():
    """Replaces three ``onsubmit="return false;"`` attributes a nonce cannot cover."""

    assert "data-inferbridge-no-submit" in RUNTIME_JS
    assert "event.preventDefault();" in RUNTIME_JS
    index_html = (_APP_DIR.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "onsubmit=" not in index_html
    assert index_html.count("data-inferbridge-no-submit") == 3


def test_no_inline_event_handlers_remain_anywhere_in_the_page():
    """Inline handlers are blocked by the hardened script-src regardless of the nonce."""

    page = ui_registry.render_document(
        (_APP_DIR.parent / "web" / "index.html").read_text(encoding="utf-8"),
        "nonce-value",
        {DESKTOP_CAPABILITY},
    )
    handlers = re.findall(r"<[a-zA-Z][^>]*?\son[a-z]+\s*=", page)
    assert handlers == [], handlers[:5]


def test_runtime_has_no_remote_dependencies():
    for host in ("fonts.googleapis.com", "cdn.jsdelivr.net", "unpkg.com", "http://", "https://"):
        assert host not in RUNTIME_JS
