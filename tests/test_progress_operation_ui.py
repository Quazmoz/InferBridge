from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.progress_operation_ui import PROGRESS_OPERATION_JS
from app.ui_extension import inject_multimodal_ui


def test_operation_reconciler_is_injected_once_before_progress_controller() -> None:
    html = '<html><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    operation_marker = 'id="ovllm-progress-operation-extension"'
    progress_marker = 'id="ovllm-model-progress-extension"'
    assert rendered.count(operation_marker) == 1
    assert rendered_twice.count(operation_marker) == 1
    assert rendered.index(operation_marker) < rendered.index(progress_marker)


def test_reconciler_rejects_lower_revisions_for_the_same_operation() -> None:
    script = PROGRESS_OPERATION_JS

    assert "const acceptedModels = new Map()" in script
    assert "progress.operation_id" in script
    assert "progress.revision" in script
    assert "identity.operationId === previous.operationId" in script
    assert "identity.revision < previous.revision" in script
    assert "return previous.model" in script
    assert "identity.revision === 0" in script
    assert "acceptedModels.delete(model.id)" in script


def test_reconciler_accepts_newer_operations_after_server_restart() -> None:
    script = PROGRESS_OPERATION_JS

    assert "progress.updated_at" in script
    assert "identity.updatedAt < previous.updatedAt" in script
    assert "identity.updatedAt > previous.updatedAt" in script
    assert "acceptedModels.clear()" in script
    assert "latestPayload = null" in script
    assert "throw error" in script


def test_reconciled_response_preserves_http_contract() -> None:
    script = PROGRESS_OPERATION_JS

    assert "new Headers(response.headers)" in script
    assert "headers.delete('content-length')" in script
    assert "status: response.status" in script
    assert "statusText: response.statusText" in script
    assert "return reconciledResponse(response, payload)" in script


def test_existing_progress_panel_receives_operation_metadata() -> None:
    script = PROGRESS_OPERATION_JS

    assert "dock.dataset.operationId" in script
    assert "dock.dataset.operationRevision" in script
    assert "ovrp-operation-meta" in script
    assert "Operation ${kind} ${suffix} · update ${operation.revision}" in script
    assert "item.textContent = label" in script
    assert "innerHTML" not in script


def test_status_wrapper_is_scoped_to_same_origin_get_requests() -> None:
    script = PROGRESS_OPERATION_JS

    assert "target.sameOrigin" in script
    assert "target.path === STATUS_PATH" in script
    assert "method === 'GET'" in script
    assert "!isStatus || !response.ok" in script


def test_metadata_observer_is_scoped_after_dock_mounts() -> None:
    script = PROGRESS_OPERATION_JS

    assert "function attachDockObserver()" in script
    assert "rootObserver?.disconnect()" in script
    assert "dockObserver.observe(dock" in script
    assert "rootObserver.observe(document.documentElement" in script
