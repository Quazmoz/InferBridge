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
