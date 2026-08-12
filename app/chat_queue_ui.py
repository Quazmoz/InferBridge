"""Per-chat pending prompt queue for model download/load workflows."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-chat-queue-extension"

CHAT_QUEUE_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmChatQueueInstalled) return;
    window.__ovllmChatQueueInstalled = true;

    if (typeof startQueuedLoad !== 'function' || typeof executeGeneration !== 'function') return;

    const MAX_PENDING_AGE_MS = 24 * 60 * 60 * 1000;
    const PREPARATION_RETRY_MS = 15000;
    const pendingChats = new Map();

    function chatStillExists(chat) {
        return !!chat && chats.some(candidate => candidate === chat || candidate.id === chat.id);
    }

    function detachedBubble() {
        const bubble = document.createElement('div');
        const content = document.createElement('div');
        bubble.appendChild(content);
        return bubble;
    }

    function modelFor(job) {
        return availableModels.get(job.modelId) || {
            id: job.modelId,
            name: job.modelId,
            status: job.running ? 'generating' : job.ready ? 'ready' : 'loading',
            is_loaded: job.ready,
            can_convert: false,
        };
    }

    function loaderBubble(model, loadDevice, running = false) {
        const bubble = appendMessage('ai', '', true);
        const action = running
            ? 'Generating response'
            : model.is_loaded
                ? 'Ready to generate'
                : model.status === 'converting' || model.can_convert
                    ? 'Downloading and converting'
                    : 'Loading';
        const suffix = running || model.is_loaded ? '' : ` on <strong>${escapeHtml(loadDevice)}</strong>`;
        bubble.firstElementChild.innerHTML = `
            <div class="model-loader-status" style="display:flex;align-items:center;gap:10px;padding:4px 0;color:var(--amber);">
                <div class="spinner"></div>
                <span>${escapeHtml(action)} with <strong>${escapeHtml(model.name || model.id)}</strong>${suffix}…</span>
            </div>
        `;
        return bubble;
    }

    function rememberPending(chat, modelId, bubble = null) {
        chat.pendingModelId = modelId;
        chat.pendingSince = Number(chat.pendingSince) || Date.now();
        const job = {
            chat,
            modelId,
            bubble: bubble || detachedBubble(),
            running: false,
            ready: chat.pendingReady === true,
            resumeStarted: false,
            resumeAttemptedAt: 0,
        };
        pendingChats.set(chat.id, job);
        saveChats();
        return job;
    }

    function clearPending(job) {
        pendingChats.delete(job.chat.id);
        if (chatStillExists(job.chat)) {
            delete job.chat.pendingModelId;
            delete job.chat.pendingSince;
            delete job.chat.pendingReady;
            saveChats();
        }
    }

    function ensureVisibleBubble(job) {
        if (activeChatId !== job.chat.id) return job.bubble;
        if (job.bubble?.isConnected) return job.bubble;
        job.bubble = loaderBubble(modelFor(job), modelLoadDevice(), job.running);
        scrollToBottom(true);
        return job.bubble;
    }

    function showPreparationError(job, message) {
        ensureVisibleBubble(job);
        const content = job.bubble?.firstElementChild;
        if (content) {
            content.innerHTML = `<span style="color:var(--red)">⚠ Failed to prepare model: ${escapeHtml(message || 'Unknown error')}</span>`;
        }
        if (activeChatId === job.chat.id) showToast('Model preparation failed');
        clearPending(job);
    }

    function markReady(job) {
        if (job.ready) return;
        job.ready = true;
        job.chat.pendingReady = true;
        saveChats();
        renderChatList();
    }

    function runPending(job) {
        if (job.running || !chatStillExists(job.chat)) {
            if (!chatStillExists(job.chat)) clearPending(job);
            return;
        }
        if (activeChatId !== job.chat.id) {
            markReady(job);
            return;
        }

        job.running = true;
        job.ready = false;
        delete job.chat.pendingReady;
        const initialMessageCount = job.chat.messages.length;
        ensureVisibleBubble(job);
        void executeGeneration(job.bubble, job.chat)
            .catch(() => undefined)
            .finally(() => {
                const completed = job.chat.messages.length > initialMessageCount &&
                    job.chat.messages.at(-1)?.role === 'assistant';
                clearPending(job);
                if (activeChatId !== job.chat.id) return;
                if (completed) {
                    renderChat();
                } else if (!job.bubble?.isConnected) {
                    const bubble = appendMessage('ai', '', false);
                    bubble.firstElementChild.innerHTML =
                        '<span style="color:var(--red)">⚠ Generation did not complete. Try sending the message again.</span>';
                    scrollToBottom(true);
                }
            });
    }

    function startPreparation(model, job = null) {
        if (!model || model.is_loaded || model.is_loading) return;
        if (job) {
            job.ready = false;
            delete job.chat.pendingReady;
            job.resumeStarted = true;
            job.resumeAttemptedAt = Date.now();
        }
        let request = null;
        if (model.can_load) request = requestModelLoad(model.id, true);
        else if (model.can_convert) request = requestModelConvert(model.id, true);
        Promise.resolve(request).catch(() => {
            if (job) job.resumeStarted = false;
        });
    }

    function processStatus(data) {
        const models = data?.models?.available;
        if (!Array.isArray(models)) return;
        const byId = new Map(models.map(model => [model.id, model]));

        pendingChats.forEach(job => {
            if (!chatStillExists(job.chat)) {
                clearPending(job);
                return;
            }
            if (Date.now() - Number(job.chat.pendingSince || 0) > MAX_PENDING_AGE_MS) {
                showPreparationError(job, 'The pending request expired. Send the message again.');
                return;
            }
            const model = byId.get(job.modelId);
            if (!model) {
                showPreparationError(job, `Model '${job.modelId}' is no longer available.`);
                return;
            }
            if (model.is_loaded) {
                markReady(job);
                if (activeChatId === job.chat.id) runPending(job);
                return;
            }
            job.ready = false;
            delete job.chat.pendingReady;
            if (model.status === 'error') {
                showPreparationError(job, model.error || model.status_label);
                return;
            }
            if (
                job.resumeStarted && !model.is_loading &&
                Date.now() - job.resumeAttemptedAt > PREPARATION_RETRY_MS
            ) {
                job.resumeStarted = false;
            }
            if (job.resumeStarted || model.is_loading) return;
            startPreparation(model, job);
        });
    }

    // Recover pending chats after a browser refresh. The user message is already
    // persisted in that chat, so only the eventual assistant response is resumed.
    chats.forEach(chat => {
        const pendingAge = Date.now() - Number(chat?.pendingSince || 0);
        if (
            chat?.pendingModelId && pendingAge <= MAX_PENDING_AGE_MS &&
            Array.isArray(chat.messages) && chat.messages.at(-1)?.role === 'user'
        ) {
            rememberPending(chat, chat.pendingModelId);
        } else if (chat && (chat.pendingModelId || chat.pendingSince || chat.pendingReady)) {
            delete chat.pendingModelId;
            delete chat.pendingSince;
            delete chat.pendingReady;
        }
    });
    saveChats();

    const previousRenderChat = renderChat;
    renderChat = function pendingAwareRenderChat() {
        const result = previousRenderChat();
        const job = pendingChats.get(activeChatId);
        if (job) {
            ensureVisibleBubble(job);
            const model = modelFor(job);
            if (job.ready || model.is_loaded) window.setTimeout(() => runPending(job), 0);
        }
        return result;
    };

    startQueuedLoad = function perChatQueuedLoad(text, selectedModel) {
        const chat = activeChat();
        if (!chat || !selectedModel) return;
        if (pendingChats.has(chat.id)) {
            showToast('This chat already has a prompt waiting for its model.');
            return;
        }

        // Data-URL image attachments are intentionally in-memory only. Do not move a
        // prompt containing them into a background queue where another visible chat
        // could own the shared attachment tray by the time generation starts.
        if (window.__ovllmVisionGuard?.hasPendingForChat?.(chat.id)) {
            chat.modelId = selectedModel.id;
            waitingForModelId = selectedModel.id;
            startPreparation(selectedModel);
            setStatusPolling(1000);
            showToast('Model preparation started. Images and draft were kept; send again when ready.');
            return;
        }

        chat.modelId = selectedModel.id;
        chat.systemPrompt = String(settingsSystemPrompt.value || '').slice(0, 32768);
        chat.draft = '';
        userInput.value = '';
        autoResize();

        chat.messages.push({ role: 'user', content: text });
        saveConversation(chat);
        appendMessage('user', text);
        scrollToBottom(true);

        const bubble = loaderBubble(selectedModel, modelLoadDevice());
        rememberPending(chat, selectedModel.id, bubble);
        scrollToBottom(true);

        // Disable the legacy single-slot queue. Status processing below owns every
        // pending chat independently, including several chats waiting on one model.
        queuedPrompt = null;
        queuedChat = null;
        activeLoaderBubble = null;
        waitingForModelId = selectedModel.id;

        startPreparation(selectedModel, pendingChats.get(chat.id));
        setStatusPolling(1000);
    };

    const previousDeleteChat = deleteChat;
    deleteChat = function queuedChatAwareDelete(id) {
        const job = pendingChats.get(id);
        if (job) clearPending(job);
        return previousDeleteChat(id);
    };

    function endpoint(input) {
        const value = typeof input === 'string' ? input : input instanceof URL ? input.href : input?.url || '';
        try {
            const url = new URL(value, window.location.href);
            return { path: url.pathname, sameOrigin: url.origin === window.location.origin };
        } catch {
            return { path: '', sameOrigin: false };
        }
    }

    const previousFetch = InferBridge.chain();
    InferBridge.use(async function queuedChatFetch(input, init = {}) {
        const response = await previousFetch(input, init);
        const target = endpoint(input);
        if (target.sameOrigin && target.path === '/v1/system/status' && response.ok) {
            response.clone().json().then(processStatus).catch(() => undefined);
        }
        return response;
    });

    async function initialStatus() {
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        const headers = key ? { Authorization: `Bearer ${key}` } : {};
        try {
            const response = await previousFetch('/v1/system/status', { headers });
            if (response.ok) processStatus(await response.json());
        } catch { /* base UI owns connectivity errors */ }
    }
    renderChat();
    void initialStatus();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=CHAT_QUEUE_JS,
    description="Per-chat pending message queue.",
)


def install_chat_queue_extension() -> None:
    """Register the per-chat pending queue."""

    ui_registry.register(EXTENSION)
