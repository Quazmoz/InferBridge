from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.status_split_ui import STATUS_SPLIT_JS
from app.ui_extension import inject_multimodal_ui


def test_split_status_layer_is_injected_before_operation_wrappers() -> None:
    html = '<html><body><div class="chat-column"><div id="chat-area"></div></div></body></html>'

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    split_marker = 'id="ovllm-status-split-extension"'
    operation_marker = 'id="ovllm-progress-operation-extension"'
    cancellation_marker = 'id="ovllm-model-cancellation-extension"'
    progress_marker = 'id="ovllm-model-progress-extension"'
    assert rendered.count(split_marker) == 1
    assert rendered_twice.count(split_marker) == 1
    assert rendered.index(split_marker) < rendered.index(operation_marker)
    assert rendered.index(operation_marker) < rendered.index(cancellation_marker)
    assert rendered.index(cancellation_marker) < rendered.index(progress_marker)


def test_split_status_uses_independent_polling_cadences() -> None:
    script = STATUS_SPLIT_JS

    assert "const MODELS_PATH = '/v1/models/status'" in script
    assert "const TELEMETRY_PATH = '/v1/system/telemetry'" in script
    assert "const EVENTS_PATH = '/v1/events'" in script
    assert "const ACTIVE_MODEL_TTL_MS = 800" in script
    assert "const IDLE_MODEL_TTL_MS = 3000" in script
    assert "const TELEMETRY_TTL_MS = 5000" in script
    assert "const EVENTS_TTL_MS = 10000" in script
    assert "state.eventCursor" in script


def test_split_status_coalesces_requests_and_preserves_auth_partitioning() -> None:
    script = STATUS_SPLIT_JS

    assert "const states = new Map()" in script
    assert "headers.get('authorization')" in script
    assert "state.modelsPromise" in script
    assert "state.telemetryPromise" in script
    assert "state.eventsPromise" in script
    assert "if (state.telemetry)" in script
    assert "return nativeFetch(input, init)" in script


def test_lifecycle_failures_use_legacy_fallback_instead_of_stale_model_state() -> None:
    script = STATUS_SPLIT_JS

    assert "const result = await state.modelsPromise" in script
    assert "if (state.models) return { payload: state.models" not in script
    assert "A failed lifecycle request is never hidden" in script


def test_lifecycle_mutations_invalidate_only_the_matching_auth_cache() -> None:
    script = STATUS_SPLIT_JS

    assert "const MODEL_MUTATION_PATHS = new Set" in script
    assert "'/v1/models/cancel'" in script
    assert "'/v1/models/delete'" in script
    assert "'/v1/model-library/import-converted'" in script
    assert "function invalidateModels(headers = null)" in script
    assert "stateFor(headers).modelsAt = 0" in script
    assert "MODEL_MUTATION_PATHS.has(target.path)" in script
    assert "window.__inferbridgeInvalidateModelStatus" in script


def test_split_status_composes_legacy_shape_without_content_length() -> None:
    script = STATUS_SPLIT_JS

    assert "models: modelPayload.models" in script
    assert "events: eventsResult.payload" in script
    assert "headers.delete('content-length')" in script
    assert "new Response(JSON.stringify(payload)" in script
    assert "inferbridge:status-composed" in script
