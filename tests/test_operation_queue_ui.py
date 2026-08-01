from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.operation_queue_ui import OPERATION_QUEUE_JS
from app.ui_extension import inject_multimodal_ui


def test_operation_queue_is_injected_once_before_primary_progress_controller() -> None:
    html = '<html><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    queue_marker = 'id="ovllm-operation-queue-extension"'
    progress_marker = 'id="ovllm-model-progress-extension"'
    assert rendered.count(queue_marker) == 1
    assert rendered_twice.count(queue_marker) == 1
    assert rendered.index(queue_marker) < rendered.index(progress_marker)


def test_queue_only_appears_for_multiple_active_operations() -> None:
    script = OPERATION_QUEUE_JS

    assert "model?.is_loading" in script
    assert "operations.length <= 1" in script
    assert "${operations.length} operations active" in script
    assert "queueExpanded" in script
    assert "aria-expanded" in script
    assert "aria-controls" in script


def test_queue_preserves_stable_primary_selection() -> None:
    script = OPERATION_QUEUE_JS

    assert "operations.find(model => model.id === waitingId)" in script
    assert "operations.find(model => model.id === selectedId)" in script
    assert "operations[0]" in script
    assert "model.id === primary?.id" in script
    assert "aria-current" in script
    assert "select.dispatchEvent(new Event('change'" in script


def test_queue_renders_safe_accessible_rows_without_mutation_loop() -> None:
    script = OPERATION_QUEUE_JS

    assert "document.createElement('button')" in script
    assert "row.setAttribute(" in script
    assert "'aria-label'" in script
    assert "list.setAttribute('role', 'group')" in script
    assert "list.replaceChildren(fragment)" in script
    assert "signature === lastSignature" in script
    assert "panelWasMissing" in script
    assert "signature === lastSignature && !panelWasMissing" in script
    assert "dockObserver.observe(dock" in script
    assert "innerHTML" not in script


def test_queue_rows_show_truthful_progress_without_clipping_current_badge() -> None:
    script = OPERATION_QUEUE_JS

    assert "function progressPercent(model)" in script
    assert "progress.overall_percent" in script
    assert "progress.completed" in script
    assert "progress.overall_percent !== ''" in script
    assert "const row = document.createElement('div')" in script
    assert "const selectButton = document.createElement('button')" in script
    assert "row.append(selectButton, track)" in script
    assert "track.setAttribute('role', 'progressbar')" in script
    assert "track.setAttribute('aria-valuenow'" in script
    assert "track.setAttribute('aria-valuetext'" in script
    assert "row.style.setProperty('--ovrp-queue-progress'" in script
    assert "title.appendChild(badge)" in script
    assert "name.appendChild(badge)" not in script
    assert ".ovrp-queue-row.indeterminate" in script
    assert "@media(prefers-reduced-motion:reduce)" in script
