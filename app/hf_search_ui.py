"""Safe, accessible Hugging Face model-search rendering for the browser client."""

HF_SEARCH_EXTENSION_JS = r"""
(() => {
    'use strict';
    if (window.__inferbridgeHfSearchInstalled) return;
    window.__inferbridgeHfSearchInstalled = true;

    const searchButton = document.getElementById('hf-search-btn');
    const searchInput = document.getElementById('hf-search-input');
    const searchTask = document.getElementById('hf-search-task');
    const searchResults = document.getElementById('hf-search-results');
    const customModal = document.getElementById('custom-model-modal');
    if (!searchButton || !searchInput || !searchTask || !searchResults) return;

    const style = document.createElement('style');
    style.id = 'ovllm-hf-search-style';
    style.textContent = `
        #hf-search-results[aria-busy="true"]{opacity:.72}
        #hf-search-results .search-empty.error{color:var(--red)}
        #hf-search-results .search-result-item>div:first-child{min-width:0}
        #hf-search-results .search-result-meta span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    `;
    document.head.appendChild(style);

    searchResults.setAttribute('role', 'region');
    searchResults.setAttribute('aria-label', 'Hugging Face search results');
    searchResults.setAttribute('aria-live', 'polite');
    searchResults.setAttribute('aria-busy', 'false');

    let activeSearch = null;

    function emptyMessage(message, error = false) {
        const node = document.createElement('div');
        node.className = `search-empty${error ? ' error' : ''}`;
        node.textContent = message;
        searchResults.replaceChildren(node);
    }

    function setBusy(busy) {
        searchResults.setAttribute('aria-busy', String(busy));
        searchButton.disabled = busy;
        searchButton.textContent = busy ? 'Searching…' : 'Search';
    }

    function formatCount(value) {
        const count = Number(value);
        return Number.isFinite(count) && count >= 0
            ? Math.floor(count).toLocaleString()
            : '0';
    }

    function allowedBackend(value) {
        return value === 'openvino-embeddings'
            ? 'openvino-embeddings'
            : 'openvino-genai';
    }

    function chooseResult(item) {
        const modelId = String(item?.id || '').trim();
        if (!modelId) return;

        const sourceInput = document.getElementById('custom-source-model');
        const backendSelect = document.getElementById('custom-backend');
        const contextInput = document.getElementById('custom-max-context-len');
        const nameInput = document.getElementById('custom-model-name');
        if (!sourceInput || !backendSelect || !contextInput) return;

        sourceInput.value = modelId;
        backendSelect.value = allowedBackend(item?.backend);
        if (typeof updateAutofilledFields === 'function') updateAutofilledFields();

        const normalizedId = modelId.toLowerCase();
        if (normalizedId.includes('llama-3.2') || normalizedId.includes('qwen2.5')) {
            contextInput.value = '4096';
        } else if (normalizedId.includes('bge') || normalizedId.includes('embedding')) {
            contextInput.value = '512';
        } else {
            contextInput.value = '2048';
        }

        if (typeof selectCustomModelTab === 'function') selectCustomModelTab('manual');
        else document.getElementById('tab-btn-manual')?.click();
        nameInput?.focus();
    }

    function renderResults(items) {
        const fragment = document.createDocumentFragment();
        for (const item of items) {
            const modelId = String(item?.id || '').trim();
            if (!modelId) continue;

            const row = document.createElement('div');
            row.className = 'search-result-item';

            const left = document.createElement('div');
            const name = document.createElement('div');
            name.className = 'search-result-name';
            name.textContent = modelId;
            left.appendChild(name);

            const meta = document.createElement('div');
            meta.className = 'search-result-meta';

            const downloads = document.createElement('span');
            downloads.textContent = `Downloads ${formatCount(item?.downloads)}`;
            const likes = document.createElement('span');
            likes.textContent = `Likes ${formatCount(item?.likes)}`;
            const badge = document.createElement('span');
            badge.className = 'search-result-badge';
            badge.textContent = String(item?.pipeline_tag || 'model');

            meta.append(downloads, likes, badge);
            left.appendChild(meta);

            const selectButton = document.createElement('button');
            selectButton.type = 'button';
            selectButton.className = 'search-result-select-btn';
            selectButton.textContent = 'Select';
            selectButton.setAttribute('aria-label', `Select ${modelId}`);
            selectButton.addEventListener('click', () => chooseResult(item));

            row.append(left, selectButton);
            fragment.appendChild(row);
        }

        if (!fragment.childNodes.length) {
            emptyMessage('No models found matching your query.');
            return;
        }
        searchResults.replaceChildren(fragment);
    }

    async function secureSearch() {
        const query = searchInput.value.trim();
        if (!query) {
            emptyMessage('Enter a model name or Hugging Face repository ID.');
            searchInput.focus();
            return;
        }

        activeSearch?.abort();
        const controller = new AbortController();
        activeSearch = controller;
        setBusy(true);
        emptyMessage('Searching Hugging Face Hub…');

        try {
            const response = await fetch(
                `/v1/models/search-hf?query=${encodeURIComponent(query)}&task=${encodeURIComponent(searchTask.value)}`,
                {
                    headers: typeof authHeaders === 'function' ? authHeaders() : {},
                    signal: controller.signal,
                },
            );
            if (response.status === 401) {
                if (typeof setCustomModelModalOpen === 'function') {
                    setCustomModelModalOpen(false);
                }
                if (typeof handleAuthRequired === 'function') handleAuthRequired();
                return;
            }
            const payload = await response.json().catch(() => null);
            if (!response.ok || !Array.isArray(payload)) {
                throw new Error(`HTTP ${response.status}`);
            }
            if (activeSearch !== controller) return;
            renderResults(payload);
        } catch (error) {
            if (error?.name === 'AbortError') return;
            emptyMessage(
                'Search failed. Check the local server and Hugging Face access, then try again.',
                true,
            );
            if (typeof showToast === 'function') {
                showToast('Hugging Face search failed', 'error');
            }
        } finally {
            if (activeSearch === controller) {
                activeSearch = null;
                setBusy(false);
            }
        }
    }

    searchButton.addEventListener('click', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        secureSearch();
    }, true);

    searchInput.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        secureSearch();
    }, true);

    if (customModal) {
        new MutationObserver(() => {
            if (customModal.classList.contains('hidden')) {
                activeSearch?.abort();
                activeSearch = null;
                setBusy(false);
            }
        }).observe(customModal, { attributes: true, attributeFilter: ['class', 'aria-hidden'] });
    }
})();
"""

__all__ = ["HF_SEARCH_EXTENSION_JS"]
