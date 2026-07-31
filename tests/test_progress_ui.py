from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.ui_extension import inject_multimodal_ui


def test_progress_extension_is_injected_after_polish_once():
    html = '<html><head></head><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    marker = 'id="ovllm-model-progress-extension"'
    assert rendered.count(marker) == 1
    assert rendered_twice.count(marker) == 1
    assert "ovllm-model-progress-dock-extension" not in rendered
    assert "ovllm-model-progress-semantics-extension" not in rendered
    assert rendered.index("ovllm-ui-polish-extension") < rendered.index(marker)


def test_progress_controller_has_persistent_and_inline_surfaces():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "ov-reliable-progress" in rendered
    assert "chatColumn.insertBefore(dock, chatArea)" in rendered
    assert "ovrp-inline" in rendered
    assert "renderDock(active, operationCount)" in rendered
    assert "renderInline(active, info)" in rendered
    assert "renderFooter(active, info)" in rendered


def test_progress_controller_exposes_stage_elapsed_and_activity_details():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "Stage ${info.meta.stage + 1} of 3" in rendered
    assert "Elapsed ${duration(info.elapsed)}" in rendered
    assert "No recent progress update for" in rendered
    assert "Taking longer than usual" in rendered
    assert "Recent preparation activity" in rendered
    assert "1. Download" in rendered
    assert "2. Convert" in rendered
    assert "3. Load" in rendered


def test_progress_controller_uses_truthful_phase_progress():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "function progressCount" in rendered
    assert "progress?.completed" in rendered
    assert "progress?.total" in rendered
    assert "progress.overall_percent" in rendered
    assert "% of current phase" in rendered
    assert "progress is not measurable for this phase" in rendered
    assert "aggregateDownloadPercent" not in rendered
    assert "meta.start" not in rendered
    assert "meta.end" not in rendered
    assert "· overall ${Math.round(info.overall)}%" not in rendered


def test_progress_controller_renders_optimistically_before_first_poll():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "function renderOptimistic" in rendered
    assert "'/v1/models/load'" in rendered
    assert "'/v1/models/convert'" in rendered
    assert "'/v1/models/download-custom'" in rendered
    assert "Queued ${baseName(base)}" in rendered
    assert "optimistic.set(modelId" in rendered


def test_progress_controller_handles_request_objects_and_failed_requests():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "input?.method" in rendered
    assert "input instanceof Request" in rendered
    assert "input.clone().json()" in rendered
    assert "clearOptimistic(optimisticModelId)" in rendered
    assert "if (!response.ok)" in rendered


def test_progress_controller_resets_retry_state():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "modelState.delete(modelId)" in rendered
    assert "operationId !== previous.operationId" in rendered
    assert "reportedStart !== previous.startedAt" in rendered
    assert "previous.terminal" in rendered


def test_progress_controller_uses_text_content_for_server_values():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert ".textContent = String(" in rendered
    assert "output.textContent = logs.join" in rendered
    assert "footer.textContent" in rendered
