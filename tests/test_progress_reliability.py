from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.progress_reliability import PROGRESS_RELIABILITY_JS
from app.ui_extension import inject_multimodal_ui


def test_reliable_progress_extension_is_injected_once() -> None:
    html = "<html><head></head><body><main>chat</main></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    assert rendered.count('id="ovllm-model-progress-extension"') == 1
    assert rendered_twice.count('id="ovllm-model-progress-extension"') == 1


def test_status_polls_are_coalesced_and_stale_snapshots_are_ignored() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert "sharedStatusRequest" in script
    assert "sharedStatusFetch" in script
    assert "statusRequestKey" in script
    assert "response: response.clone()" in script
    assert "revision < latestStatusRevision" in script
    assert "latestStatusRevision = revision" in script


def test_progress_operation_selection_and_ticker_are_stable() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert "let activeModelId = ''" in script
    assert "chooseActiveModel" in script
    assert "currentWaitingModelId" in script
    assert "setRenderTicker(!!active?.is_loading)" in script
    assert "window.clearInterval(renderTicker)" in script
    assert "if (latestStatus) renderStatus(latestStatus, latestStatusRevision)" in script


def test_progress_detail_and_inline_surfaces_preserve_user_state() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert "detailInteractionState" in script
    assert "logOpen" in script
    assert "logScrollTop" in script
    assert "logFocused" in script
    assert "focus({ preventScroll: true })" in script
    assert "element.dataset.modelId === model.id" in script
    assert "loaderHostFor" in script
    assert "visible[visible.length - 1]" in script


def test_terminal_and_stale_progress_states_are_explicit() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert "cancelled: ['Cancelled'" in script
    assert "dock.classList.toggle('cancelled'" in script
    assert "aria-valuetext" in script
    assert "Taking longer than usual" in script
    assert "No recent progress update" in script
    assert "terminal: ['ready', 'error', 'cancelled'].includes(phase)" in script


def test_progress_announcements_only_change_with_operation_phase() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert "dock.setAttribute('role', 'region')" in script
    assert 'class="ovrp-live" role="status"' in script
    assert "if (text === lastAnnouncement" in script
    assert 'aria-live="polite"' in script


def test_late_ack_and_concurrent_retry_guards_execute_against_real_browser_code() -> None:
    """Exercise the embedded JS with Node: late ACK, deletion, and retry ownership."""

    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable; browser JavaScript behavior cannot be executed")

    def extract(start: str, end: str) -> str:
        assert start in PROGRESS_RELIABILITY_JS and end in PROGRESS_RELIABILITY_JS
        return PROGRESS_RELIABILITY_JS.split(start, 1)[1].split(end, 1)[0]

    clear = "function clearOptimistic(" + extract(
        "    function clearOptimistic(", "\n    function mergeReturnedModel("
    )
    merge = "function mergeReturnedModel(" + extract(
        "    function mergeReturnedModel(", "\n    const previousFetch ="
    )
    # Execute actual source functions rather than reimplementing the reducer in Python.
    js = (
        """
const assert = require('node:assert/strict');
const oldRequest = {id: 'old'};
const currentRequest = {id: 'new'};
const optimistic = new Map([['demo', currentRequest]]);
const modelState = new Map([['demo', {}]]);
let activeModelId = 'demo';
let latestStatus = null;
let latestStatusRevision = 7;
let rendered = null;
function scheduleRender(data) { rendered = data; }
function renderDock() { throw new Error('stale request changed the UI'); }
function renderInline() { throw new Error('stale request changed the UI'); }
function setRenderTicker() { throw new Error('stale request changed the UI'); }
"""
        + clear
        + merge
        + """
clearOptimistic('demo', oldRequest);
assert.equal(optimistic.get('demo'), currentRequest);
assert.equal(activeModelId, 'demo');
assert.equal(modelState.has('demo'), true);

latestStatus = {models: {available: [{
    id: 'demo', status: 'ready_to_load', is_loaded: false,
    is_loading: false, progress: {revision: 0, updated_at: 0}
}]}};
mergeReturnedModel({model: {
    id: 'demo', status: 'converting', is_loading: true,
    progress: {revision: 0, updated_at: 0}
}}, 5);
assert.equal(rendered, null, 'a stale ACK rolled back a completed model');

latestStatus = {models: {available: []}};
mergeReturnedModel({model: {id: 'deleted', status: 'queued'}}, 5);
assert.equal(rendered, null, 'a stale ACK resurrected a deleted model');

latestStatusRevision = 5;
latestStatus = {models: {available: []}};
mergeReturnedModel({model: {id: 'new-model', status: 'queued'}}, 5);
assert.equal(rendered.models.available[0].id, 'new-model');

optimistic.delete('demo');
optimistic.set('demo', currentRequest);
rendered = null;
clearOptimistic('demo', oldRequest);
assert.equal(optimistic.get('demo'), currentRequest);
"""
    )
    result = subprocess.run(
        [node, "-e", js],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preparation_ack_is_scoped_to_current_retry() -> None:
    script = PROGRESS_RELIABILITY_JS
    assert "optimistic.get(optimisticModelId) !== optimisticRequest" in script
    assert "clearOptimistic(optimisticModelId, optimisticRequest)" in script
    assert "if (acknowledged?.is_loading && optimisticModelId)" in script
    assert "if (latestStatusRevision > requestRevision) return;" in script


def test_base_browser_uses_authoritative_failure_label() -> None:
    from app.config import BASE_DIR

    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert "const actionLabel = model.status_label || 'Model preparation failed';" in html
    assert "const actionLabel = isConvertRetry ? 'Conversion failed' : 'Load failed';" not in html
