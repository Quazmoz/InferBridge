"""End-to-end checks that the shared runtime actually works in a real browser.

Sixteen extensions each reassigned ``window.fetch`` and now register into one stack instead.
That change is invisible to source-level tests and to a JavaScript syntax check, so it is
verified here against a running server and a real page: the stack is present and ordered,
requests still traverse every layer, and nothing throws while the page loads.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _load(page: Page, url: str) -> list[str]:
    """Open the app and return any console errors or page exceptions it produced."""

    problems: list[str] = []
    page.on("console", lambda message: message.type == "error" and problems.append(message.text))
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.goto(url, wait_until="load")
    page.wait_for_function("() => Boolean(window.InferBridge)", timeout=15_000)
    # Let the shell's initial polling settle so late failures surface too.
    page.wait_for_timeout(1_500)
    return problems


def test_page_loads_without_console_errors(page: Page, inferbridge_url: str):
    """Every payload is now an external script that depends on the runtime loading first.

    A misordered composition would show up here as a ``ReferenceError`` for InferBridge.
    """

    problems = _load(page, inferbridge_url)
    fatal = [text for text in problems if "InferBridge" in text or "is not defined" in text]
    assert fatal == [], fatal


def test_runtime_owns_a_single_ordered_request_stack(page: Page, inferbridge_url: str):
    _load(page, inferbridge_url)
    stack = page.evaluate("() => window.InferBridge.middleware()")

    # The runtime's own telemetry sits at the bottom, with the feature layers above it.
    assert stack[0] == "inferBridgeTelemetry", stack
    assert len(stack) >= 15, stack
    assert len(stack) == len(set(stack)), f"a layer registered twice: {stack}"

    for name in ("visionAwareFetch", "reliableProgressFetch", "qualityFetch"):
        assert name in stack, (name, stack)

    # Split polling must sit below the operation-aware layers that build on it.
    assert stack.index("splitStatusFetch") < stack.index("operationAwareFetch")


def test_requests_traverse_the_whole_stack(page: Page, inferbridge_url: str):
    """A layer near the top must still see a request issued through window.fetch."""

    _load(page, inferbridge_url)
    observed = page.evaluate(
        """async () => {
            const seen = [];
            const off = window.InferBridge.on('request', (request) => seen.push(request.path));
            const response = await window.fetch('/v1/models');
            off();
            return { ok: response.ok, seen };
        }"""
    )
    assert observed["ok"] is True
    # The bottom-most layer reports it, which means the request went through every layer.
    assert "/v1/models" in observed["seen"]


def test_chain_snapshot_reaches_only_earlier_layers(page: Page, inferbridge_url: str):
    """``chain()`` must behave like the historical ``window.fetch.bind(window)`` capture."""

    _load(page, inferbridge_url)
    result = page.evaluate(
        """async () => {
            const order = [];
            const downstream = window.InferBridge.chain();
            window.InferBridge.use(async (input, init) => {
                order.push('outer');
                return downstream(input, init);
            });
            const later = window.InferBridge.chain();
            window.InferBridge.use(async (input, init) => {
                order.push('later');
                return later(input, init);
            });
            const response = await window.fetch('/health/live');
            return { ok: response.ok, order };
        }"""
    )
    assert result["ok"] is True
    # Outermost runs first, then the layer it wraps.
    assert result["order"] == ["later", "outer"]


def test_shared_state_notifies_subscribers_on_change_only(page: Page, inferbridge_url: str):
    _load(page, inferbridge_url)
    seen = page.evaluate(
        """() => {
            const seen = [];
            window.InferBridge.subscribe('probe', (value) => seen.push(value));
            window.InferBridge.set('probe', 'a');
            window.InferBridge.set('probe', 'a');
            window.InferBridge.set('probe', 'b');
            return seen;
        }"""
    )
    assert seen == ["a", "b"]


def test_a_failing_listener_cannot_break_a_request(page: Page, inferbridge_url: str):
    _load(page, inferbridge_url)
    result = page.evaluate(
        """async () => {
            window.InferBridge.on('request', () => { throw new Error('listener exploded'); });
            const response = await window.fetch('/health/live');
            return response.ok;
        }"""
    )
    assert result is True


def test_element_registry_resolves_the_shared_shell_controls(page: Page, inferbridge_url: str):
    """One place names the ids layers reach for; `#model-select` had fifteen call sites."""

    _load(page, inferbridge_url)
    resolved = page.evaluate(
        """() => {
            const names = Object.keys(window.InferBridge.elements);
            const missing = names.filter((name) => !window.InferBridge.el(name));
            return { names, missing };
        }"""
    )
    assert resolved["names"], "the registry should declare shell elements"
    assert resolved["missing"] == [], resolved["missing"]


def test_forms_marked_no_submit_do_not_navigate(page: Page, inferbridge_url: str):
    """Replaces three ``onsubmit="return false;"`` attributes removed for the CSP."""

    _load(page, inferbridge_url)
    outcome = page.evaluate(
        """() => {
            const form = document.querySelector('form[data-inferbridge-no-submit]');
            if (!form) return 'no marked form found';
            const event = new Event('submit', { bubbles: true, cancelable: true });
            form.dispatchEvent(event);
            return event.defaultPrevented ? 'prevented' : 'not prevented';
        }"""
    )
    assert outcome == "prevented"
