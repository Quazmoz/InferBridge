"""Multimodal controls injected into the bundled single-file browser client.

The server explicitly injects this extension when serving ``web/index.html``.
No framework classes or global response behavior are monkey-patched.
"""

from __future__ import annotations

_EXTENSION_ID = "ovllm-vision-extension"

VISION_EXTENSION_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmVisionInstalled) return;
    window.__ovllmVisionInstalled = true;

    const MAX_IMAGES = 4;
    const MAX_BYTES = 10 * 1024 * 1024;
    const MAX_TOTAL_BYTES = 24 * 1024 * 1024;
    const MAX_PIXELS = 25000000;
    const MAX_TOTAL_PIXELS = 40000000;
    const MAX_DIMENSION = 16384;
    const SUPPORTED = new Set(['image/jpeg', 'image/png', 'image/webp']);
    const modelCapabilities = new Map();
    let pendingImages = [];
    let fileQueue = Promise.resolve();
    let fileTasksPending = 0;
    let attachmentEpoch = 0;

    const form = document.getElementById('chat-form');
    const inputArea = document.getElementById('input-area');
    const userInput = document.getElementById('user-input');
    const modelSelect = document.getElementById('model-select');
    if (!form || !inputArea || !userInput || !modelSelect) return;

    const style = document.createElement('style');
    style.textContent = `
        #vision-attach-btn {
            width: 42px; height: 42px; flex: 0 0 42px; border-radius: 11px;
            border: 1px solid var(--border); background: var(--surface-2);
            color: var(--text-2); cursor: pointer; display: grid; place-items: center;
            transition: border-color .2s, color .2s, background .2s, opacity .2s;
        }
        #vision-attach-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--text-1); background: var(--surface-3); }
        #vision-attach-btn:disabled { opacity: .42; cursor: not-allowed; }
        #vision-attach-btn.has-images { color: var(--primary); border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
        #vision-preview-tray { display: none; gap: 8px; align-items: center; overflow-x: auto; padding: 0 2px 8px; }
        #vision-preview-tray.visible { display: flex; }
        .vision-preview { position: relative; flex: 0 0 auto; width: 58px; height: 58px; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--surface-2); }
        .vision-preview img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .vision-preview button { position: absolute; top: 3px; right: 3px; width: 20px; height: 20px; border: 0; border-radius: 50%; background: rgba(5,8,15,.82); color: white; cursor: pointer; font-size: 14px; line-height: 20px; }
        .vision-preview-count { flex: 0 0 auto; font-size: 11px; color: var(--text-3); line-height: 1.35; max-width: 220px; }
        #input-area.vision-drag-active { outline: 2px dashed var(--primary); outline-offset: -7px; border-radius: 16px; }
        @media (max-width: 640px) { #vision-attach-btn { width: 40px; height: 40px; flex-basis: 40px; } }
    `;
    document.head.appendChild(style);

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/jpeg,image/png,image/webp';
    fileInput.multiple = true;
    fileInput.hidden = true;
    fileInput.id = 'vision-file-input';

    const attachButton = document.createElement('button');
    attachButton.type = 'button';
    attachButton.id = 'vision-attach-btn';
    attachButton.setAttribute('aria-label', 'Attach images');
    attachButton.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';

    const previewTray = document.createElement('div');
    previewTray.id = 'vision-preview-tray';
    previewTray.setAttribute('aria-live', 'polite');

    form.insertBefore(attachButton, userInput);
    form.appendChild(fileInput);
    inputArea.insertBefore(previewTray, form);

    const customBackend = document.getElementById('custom-backend');
    const customRecommendedDevice = document.getElementById('custom-recommended-device');
    const customMaxContextLen = document.getElementById('custom-max-context-len');
    const customMaxOutputTokens = document.getElementById('custom-max-output-tokens');
    const customModelForm = document.getElementById('custom-model-form');
    let customTrustRemoteCode = document.getElementById('custom-trust-remote-code');
    if (!(customTrustRemoteCode instanceof HTMLInputElement)) {
        customTrustRemoteCode = document.createElement('input');
        customTrustRemoteCode.type = 'checkbox';
        customTrustRemoteCode.id = 'custom-trust-remote-code';
        customTrustRemoteCode.checked = false;
        customTrustRemoteCode.style.cssText = 'cursor:pointer;width:auto;flex:0 0 auto;margin-top:2px;';
    }
    if (customModelForm && !customTrustRemoteCode.isConnected) {
        const trustRow = document.createElement('div');
        trustRow.className = 'form-group';
        const trustLabel = document.createElement('label');
        trustLabel.htmlFor = customTrustRemoteCode.id;
        trustLabel.style.textTransform = 'none';
        trustLabel.style.display = 'flex';
        trustLabel.style.alignItems = 'flex-start';
        trustLabel.style.gap = '8px';
        trustLabel.appendChild(customTrustRemoteCode);
        const trustText = document.createElement('span');
        trustText.textContent = 'Allow trusted repository code during conversion';
        trustLabel.appendChild(trustText);
        const trustWarning = document.createElement('p');
        trustWarning.style.cssText = 'font-size:11px;color:var(--amber);line-height:1.45;margin-top:4px;';
        trustWarning.textContent = 'Off by default. Enabling this may execute Python code from the Hugging Face repository. Use only for a model source you have reviewed and trust.';
        trustRow.append(trustLabel, trustWarning);
        const footer = customModelForm.querySelector('.modal-footer');
        if (footer) customModelForm.insertBefore(trustRow, footer);
        else customModelForm.appendChild(trustRow);
    }
    document.getElementById('add-model-btn')?.addEventListener('click', () => {
        customTrustRemoteCode.checked = false;
    });
    if (customBackend && !customBackend.querySelector('option[value="openvino-vlm"]')) {
        const option = document.createElement('option');
        option.value = 'openvino-vlm';
        option.textContent = 'Vision + Text (openvino-vlm)';
        customBackend.appendChild(option);
    }
    customBackend?.addEventListener('change', () => {
        if (customBackend.value === 'openvino-vlm' && customRecommendedDevice?.value === 'NPU') {
            customRecommendedDevice.value = 'GPU';
        }
        if (customBackend.value === 'openvino-embeddings') {
            if (customMaxContextLen) customMaxContextLen.value = '512';
            if (customMaxOutputTokens) customMaxOutputTokens.value = '0';
        } else if (customMaxOutputTokens?.value === '0') {
            customMaxOutputTokens.value = '512';
        }
    });

    const hfTask = document.getElementById('hf-search-task');
    if (hfTask && !hfTask.querySelector('option[value="image-text-to-text"]')) {
        const option = document.createElement('option');
        option.value = 'image-text-to-text';
        option.textContent = 'Vision Language Models (VLMs)';
        hfTask.appendChild(option);
    }
    document.getElementById('hf-search-results')?.addEventListener('click', event => {
        if (!event.target.closest('.search-result-select-btn')) return;
        queueMicrotask(() => customBackend?.dispatchEvent(new Event('change')));
    });

    function toast(message) {
        const element = document.getElementById('toast');
        if (!element) return;
        element.textContent = message;
        element.classList.add('show');
        window.setTimeout(() => element.classList.remove('show'), 3000);
    }

    function selectedSupportsVision() {
        return modelCapabilities.get(modelSelect.value)?.supports_vision === true;
    }

    function clearPendingImages() {
        attachmentEpoch += 1;
        pendingImages = [];
        renderPreviews();
    }

    function updateAttachState() {
        const known = modelCapabilities.has(modelSelect.value);
        const capable = selectedSupportsVision();
        attachButton.disabled = !known || !capable;
        attachButton.classList.toggle('has-images', pendingImages.length > 0);
        attachButton.title = !known
            ? 'Checking whether this model supports images…'
            : capable
                ? 'Attach up to four JPEG, PNG, or WebP images'
                : 'Select a vision-capable model to attach images';
        if (known && !capable && pendingImages.length) {
            clearPendingImages();
            toast('Image attachments cleared because the selected model is text-only.');
        }
    }

    function renderPreviews() {
        previewTray.replaceChildren();
        previewTray.classList.toggle('visible', pendingImages.length > 0);
        pendingImages.forEach(item => {
            const wrapper = document.createElement('div');
            wrapper.className = 'vision-preview';
            wrapper.title = `${item.name} · ${item.width}×${item.height}`;
            const image = document.createElement('img');
            image.src = item.dataUrl;
            image.alt = '';
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.setAttribute('aria-label', `Remove ${item.name}`);
            remove.textContent = '×';
            remove.addEventListener('click', () => {
                pendingImages = pendingImages.filter(candidate => candidate.id !== item.id);
                renderPreviews();
            });
            wrapper.append(image, remove);
            previewTray.appendChild(wrapper);
        });
        if (pendingImages.length) {
            const note = document.createElement('div');
            note.className = 'vision-preview-count';
            const totalMiB = pendingImages.reduce((sum, item) => sum + item.size, 0) / (1024 * 1024);
            note.textContent = `${pendingImages.length}/${MAX_IMAGES} attached · ${totalMiB.toFixed(1)} MiB · sent with the next request only`;
            previewTray.appendChild(note);
        }
        attachButton.classList.toggle('has-images', pendingImages.length > 0);
    }

    function readAsDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
            reader.onload = () => resolve(String(reader.result || ''));
            reader.readAsDataURL(file);
        });
    }

    function imageDimensions(dataUrl) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
            image.onerror = () => reject(new Error('Image could not be decoded'));
            image.src = dataUrl;
        });
    }

    function inferredMimeType(file) {
        if (SUPPORTED.has(file.type)) return file.type;
        const extension = String(file.name || '').split('.').pop()?.toLowerCase();
        return {
            jpg: 'image/jpeg',
            jpeg: 'image/jpeg',
            png: 'image/png',
            webp: 'image/webp',
        }[extension] || '';
    }

    async function addFilesNow(fileList, targetModel, targetEpoch) {
        if (!selectedSupportsVision()) {
            toast('Select a vision-capable model before attaching an image.');
            return;
        }
        for (const file of Array.from(fileList || [])) {
            if (pendingImages.length >= MAX_IMAGES) {
                toast(`A maximum of ${MAX_IMAGES} images can be attached.`);
                break;
            }
            const mimeType = inferredMimeType(file);
            if (!mimeType) {
                toast(`${file.name}: use JPEG, PNG, or WebP.`);
                continue;
            }
            if (file.size > MAX_BYTES) {
                toast(`${file.name}: images must be 10 MiB or smaller.`);
                continue;
            }
            const currentBytes = pendingImages.reduce((sum, item) => sum + item.size, 0);
            if (currentBytes + file.size > MAX_TOTAL_BYTES) {
                toast('Combined image data must be 24 MiB or smaller.');
                break;
            }
            try {
                let dataUrl = await readAsDataUrl(file);
                if (!dataUrl.startsWith(`data:${mimeType};`)) {
                    dataUrl = dataUrl.replace(/^data:[^;]*;/, `data:${mimeType};`);
                }
                const dimensions = await imageDimensions(dataUrl);
                const pixels = dimensions.width * dimensions.height;
                if (!dimensions.width || !dimensions.height || pixels > MAX_PIXELS) {
                    throw new Error('image exceeds the 25,000,000-pixel safety limit');
                }
                if (dimensions.width > MAX_DIMENSION || dimensions.height > MAX_DIMENSION) {
                    throw new Error('image dimensions may not exceed 16,384 pixels per side');
                }
                const currentPixels = pendingImages.reduce((sum, item) => sum + item.pixels, 0);
                if (currentPixels + pixels > MAX_TOTAL_PIXELS) {
                    throw new Error('combined images exceed the 40,000,000-pixel request limit');
                }
                if (
                    attachmentEpoch !== targetEpoch ||
                    modelSelect.value !== targetModel ||
                    !selectedSupportsVision()
                ) {
                    throw new Error('chat or selected model changed while the image was being prepared');
                }
                pendingImages.push({
                    id: window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
                    name: file.name || `image-${pendingImages.length + 1}`,
                    type: mimeType,
                    size: file.size,
                    dataUrl,
                    width: dimensions.width,
                    height: dimensions.height,
                    pixels,
                });
            } catch (error) {
                toast(`${file.name}: ${error instanceof Error ? error.message : String(error)}`);
            }
        }
        fileInput.value = '';
        renderPreviews();
    }

    function enqueueFiles(fileList) {
        const files = Array.from(fileList || []);
        const targetModel = modelSelect.value;
        const targetEpoch = attachmentEpoch;
        fileTasksPending += 1;
        fileQueue = fileQueue
            .then(() => addFilesNow(files, targetModel, targetEpoch))
            .catch(error => {
                toast(error instanceof Error ? error.message : String(error));
            })
            .finally(() => {
                fileTasksPending = Math.max(0, fileTasksPending - 1);
            });
        return fileQueue;
    }

    function blockSendWhilePreparing(event) {
        if (fileTasksPending < 1) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        toast('Finish preparing the image before sending.');
    }
    form.addEventListener('submit', blockSendWhilePreparing, true);
    userInput.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey) blockSendWhilePreparing(event);
    }, true);

    attachButton.addEventListener('click', () => {
        if (selectedSupportsVision()) fileInput.click();
        else toast('Select a vision-capable model first.');
    });
    fileInput.addEventListener('change', () => enqueueFiles(fileInput.files));

    inputArea.addEventListener('dragover', event => {
        if (!event.dataTransfer?.types?.includes('Files')) return;
        event.preventDefault();
        if (selectedSupportsVision()) inputArea.classList.add('vision-drag-active');
    });
    inputArea.addEventListener('dragleave', () => inputArea.classList.remove('vision-drag-active'));
    inputArea.addEventListener('drop', event => {
        if (!event.dataTransfer?.files?.length) return;
        event.preventDefault();
        inputArea.classList.remove('vision-drag-active');
        enqueueFiles(event.dataTransfer.files);
    });
    userInput.addEventListener('paste', event => {
        const files = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith('image/'));
        if (!files.length) return;
        event.preventDefault();
        enqueueFiles(files);
    });

    modelSelect.addEventListener('change', updateAttachState);
    ['new-chat-btn', 'new-chat-side-btn'].forEach(id => {
        document.getElementById(id)?.addEventListener('click', clearPendingImages);
    });

    function recordCapabilities(data) {
        const models = data?.models?.available;
        if (!Array.isArray(models)) return;
        modelCapabilities.clear();
        models.forEach(model => modelCapabilities.set(model.id, model));
        updateAttachState();
    }

    function responseWithJson(data, source) {
        const headers = new Headers(source.headers);
        headers.delete('content-length');
        headers.delete('content-encoding');
        headers.set('content-type', 'application/json');
        return new Response(JSON.stringify(data), {
            status: source.status,
            statusText: source.statusText,
            headers,
        });
    }

    function requestEndpoint(input) {
        const value = typeof input === 'string'
            ? input
            : input instanceof URL
                ? input.href
                : input?.url || '';
        try {
            const url = new URL(value, window.location.href);
            return {
                path: url.pathname,
                sameOrigin: url.origin === window.location.origin,
            };
        } catch {
            return { path: '', sameOrigin: false };
        }
    }

    function removeSentImages(sentIds) {
        const sent = new Set(sentIds);
        pendingImages = pendingImages.filter(item => !sent.has(item.id));
        renderPreviews();
    }

    async function monitorEventStream(stream, sentIds) {
        if (!stream) return;
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let tail = '';
        let completed = false;
        let failed = false;
        const inspect = text => {
            tail = (tail + text).slice(-4096);
            completed ||= tail.includes('data: [DONE]');
            failed ||= tail.includes('event: response.error') || tail.includes('[error: generation failed');
        };
        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                inspect(decoder.decode(value, { stream: true }));
            }
            inspect(decoder.decode());
            if (completed && !failed) removeSentImages(sentIds);
            else toast('Generation did not complete; image attachments were kept.');
        } catch {
            toast('Generation stream ended early; image attachments were kept.');
        } finally {
            reader.releaseLock();
        }
    }

    const originalFetch = InferBridge.chain();
    InferBridge.use(async function visionAwareFetch(input, init = {}) {
        const endpoint = requestEndpoint(input);
        const path = endpoint.path;
        const isSameOrigin = endpoint.sameOrigin;
        const method = String(init?.method || (typeof input !== 'string' && input?.method) || 'GET').toUpperCase();
        let sentIds = [];

        if (isSameOrigin && path === '/v1/models/download-custom' && method === 'POST' && customModelForm) {
            try {
                const body = JSON.parse(String(init.body || ''));
                body.trust_remote_code = customTrustRemoteCode.checked;
                init = { ...init, body: JSON.stringify(body) };
            } catch { /* let the existing request surface malformed JSON */ }
        }

        if (isSameOrigin && path === '/v1/chat/completions' && method === 'POST' && pendingImages.length) {
            let body;
            try { body = JSON.parse(String(init.body || '')); }
            catch {
                toast('Could not attach images to this request; attachments were kept.');
                return responseWithJson(
                    { detail: 'Could not attach images to the request body.' },
                    new Response('', { status: 400 }),
                );
            }

            const capabilities = modelCapabilities.get(body.model);
            if (!capabilities?.supports_vision) {
                toast('The selected model cannot process images. Choose a vision-capable model.');
                return responseWithJson(
                    { detail: 'Selected model is not vision-capable.' },
                    new Response('', { status: 400 }),
                );
            }

            const messages = Array.isArray(body.messages) ? body.messages : [];
            let userMessage = null;
            for (let index = messages.length - 1; index >= 0; index -= 1) {
                if (messages[index]?.role === 'user') { userMessage = messages[index]; break; }
            }
            if (!userMessage) {
                toast('Images require a user message; attachments were kept.');
                return responseWithJson(
                    { detail: 'Images require a user message.' },
                    new Response('', { status: 400 }),
                );
            }
            if (userMessage) {
                const imagesToSend = [...pendingImages];
                const content = Array.isArray(userMessage.content)
                    ? [...userMessage.content]
                    : [{ type: 'text', text: String(userMessage.content || '') }];
                imagesToSend.forEach(item => content.push({
                    type: 'image_url',
                    image_url: { url: item.dataUrl, detail: 'auto' },
                }));
                userMessage.content = content;
                body.messages = messages;
                init = { ...init, body: JSON.stringify(body) };
                sentIds = imagesToSend.map(item => item.id);
            }
        }

        let response;
        try {
            response = await originalFetch(input, init);
        } catch (error) {
            if (sentIds.length) toast('Request failed before the server accepted it; attachments were kept.');
            throw error;
        }

        if (sentIds.length && response.ok) {
            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('text/event-stream') && response.body) {
                const monitorResponse = response.clone();
                void monitorEventStream(monitorResponse.body, sentIds);
            } else {
                removeSentImages(sentIds);
            }
        }
        if (isSameOrigin && path === '/v1/system/status' && response.ok) {
            response.clone().json().then(recordCapabilities).catch(() => {});
        }
        return response;
    });

    async function refreshCapabilities() {
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        const headers = key ? { Authorization: `Bearer ${key}` } : {};
        try {
            const response = await originalFetch('/v1/system/status', { headers });
            if (response.ok) recordCapabilities(await response.json());
        } catch { /* the existing application continues polling */ }
    }

    updateAttachState();
    refreshCapabilities();
})();
"""


def inject_multimodal_ui(html: str) -> str:
    """Return *html* with the extension inserted once before ``</body>``."""

    if f'id="{_EXTENSION_ID}"' in html:
        return html
    script = f'\n<script id="{_EXTENSION_ID}">\n{VISION_EXTENSION_JS}\n</script>\n'
    if "</body>" in html:
        return html.replace("</body>", f"{script}</body>", 1)
    return html + script
