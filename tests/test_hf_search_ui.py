from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.hf_search_ui import HF_SEARCH_EXTENSION_JS
from app.ui_extension import inject_multimodal_ui


def test_hf_search_extension_is_injected_once_in_core_ui_order() -> None:
    html = "<html><head></head><body></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    marker = 'id="ovllm-hf-search-extension"'
    assert rendered.count(marker) == 1
    assert rendered_twice.count(marker) == 1
    assert rendered.index('id="ovllm-release-extension"') < rendered.index(marker)
    assert rendered.index(marker) < rendered.index('id="ovllm-responsive-extension"')


def test_hf_search_renders_external_metadata_without_html_injection() -> None:
    script = HF_SEARCH_EXTENSION_JS

    assert "innerHTML" not in script
    assert "name.textContent = modelId" in script
    assert "badge.textContent = String(item?.pipeline_tag || 'model')" in script
    assert "searchResults.replaceChildren(fragment)" in script
    assert "selectButton.setAttribute('aria-label', `Select ${modelId}`)" in script
    assert "allowedBackend(item?.backend)" in script
    assert "value === 'openvino-embeddings'" in script


def test_hf_search_coalesces_requests_and_reports_accessible_state() -> None:
    script = HF_SEARCH_EXTENSION_JS

    assert "const controller = new AbortController()" in script
    assert "activeSearch?.abort()" in script
    assert "signal: controller.signal" in script
    assert "error?.name === 'AbortError'" in script
    assert "searchResults.setAttribute('aria-live', 'polite')" in script
    assert "searchResults.setAttribute('aria-busy', String(busy))" in script
    assert "searchButton.disabled = busy" in script
    assert "Search failed. Check the local server" in script


def test_hf_search_replaces_legacy_handlers_without_double_requests() -> None:
    script = HF_SEARCH_EXTENSION_JS

    assert "event.stopImmediatePropagation()" in script
    assert "searchButton.addEventListener('click'" in script
    assert "searchInput.addEventListener('keydown'" in script
    assert "}, true);" in script
    assert "if (customModal.classList.contains('hidden'))" in script
