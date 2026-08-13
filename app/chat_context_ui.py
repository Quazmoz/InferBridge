"""Per-chat browser context isolation for the bundled multi-conversation UI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-chat-context-extension"

CHAT_CONTEXT_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmChatContextInstalled) return;
    window.__ovllmChatContextInstalled = true;

    if (
        typeof makeChat !== 'function' || typeof activeChat !== 'function' ||
        typeof executeGeneration !== 'function' || typeof saveChats !== 'function'
    ) return;

    const CONTEXT_VERSION = 2;
    const MAX_SYSTEM_PROMPT_CHARS = 32768;
    const MAX_DRAFT_CHARS = 100000;
    const MAX_TITLE_CHARS = 120;
    const CHAT_ID_PATTERN = /^chat-[A-Za-z0-9._-]{1,120}$/;
    let saveTimer = null;
    let generationTail = Promise.resolve();

    function boundedText(value, limit) {
        return String(value ?? '').slice(0, limit);
    }

    function freshChatId(seen = new Set()) {
        let id;
        do {
            id = `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        } while (seen.has(id));
        return id;
    }

    function sanitizeStoredChats() {
        const seen = new Set();
        let remappedActiveId = null;
        const now = Date.now();
        chats = chats.filter(chat => chat && typeof chat === 'object' && Array.isArray(chat.messages));
        chats.forEach(chat => {
            const originalId = typeof chat.id === 'string' ? chat.id : '';
            const id = CHAT_ID_PATTERN.test(originalId) && !seen.has(originalId)
                ? originalId
                : freshChatId(seen);
            if (originalId === activeChatId && remappedActiveId === null) remappedActiveId = id;
            chat.id = id;
            seen.add(id);

            chat.title = boundedText(chat.title || 'New chat', MAX_TITLE_CHARS) || 'New chat';
            chat.created = Number.isFinite(Number(chat.created)) ? Number(chat.created) : now;
            chat.updated = Number.isFinite(Number(chat.updated)) ? Number(chat.updated) : chat.created;
            chat.messages = chat.messages.filter(message => message && typeof message === 'object');
        });

        if (!chats.length) {
            const chat = makeChat([]);
            chats = [chat];
            activeChatId = chat.id;
            return;
        }
        if (remappedActiveId && chats.some(chat => chat.id === remappedActiveId)) {
            activeChatId = remappedActiveId;
        } else if (!chats.some(chat => chat.id === activeChatId)) {
            activeChatId = chats[0].id;
        }
    }

    function ensureChatContext(chat, useCurrentDefaults = false) {
        if (!chat || typeof chat !== 'object') return null;
        if (!Array.isArray(chat.messages)) chat.messages = [];
        if (!Object.prototype.hasOwnProperty.call(chat, 'modelId')) {
            chat.modelId = useCurrentDefaults ? (modelSelect.value || null) : null;
        }
        if (typeof chat.modelId !== 'string' || !chat.modelId.trim()) chat.modelId = null;
        if (!Object.prototype.hasOwnProperty.call(chat, 'systemPrompt')) {
            chat.systemPrompt = useCurrentDefaults
                ? boundedText(settingsSystemPrompt.value, MAX_SYSTEM_PROMPT_CHARS)
                : boundedText(localStorage.getItem('ovllm.system_prompt.v1') || '', MAX_SYSTEM_PROMPT_CHARS);
        }
        if (!Object.prototype.hasOwnProperty.call(chat, 'draft')) chat.draft = '';
        chat.systemPrompt = boundedText(chat.systemPrompt, MAX_SYSTEM_PROMPT_CHARS);
        chat.draft = boundedText(chat.draft, MAX_DRAFT_CHARS);
        chat.contextVersion = CONTEXT_VERSION;
        return chat;
    }

    function fallbackModelId() {
        if (modelSelect.value && availableModels.has(modelSelect.value)) return modelSelect.value;
        const loaded = Array.from(availableModels.values()).find(model => model?.is_loaded);
        if (loaded?.id) return loaded.id;
        return availableModels.keys().next().value || null;
    }

    function resolveChatModel(chat) {
        chat = ensureChatContext(chat, true);
        if (!chat) return null;
        if (chat.modelId && availableModels.has(chat.modelId)) return chat.modelId;
        chat.modelId = fallbackModelId();
        return chat.modelId;
    }

    function scheduleSave() {
        clearTimeout(saveTimer);
        saveTimer = window.setTimeout(() => saveChats(), 120);
    }

    function captureVisibleContext(chat = activeChat(), includeDraft = true) {
        chat = ensureChatContext(chat, true);
        if (!chat) return null;
        if (modelSelect.value && availableModels.has(modelSelect.value)) chat.modelId = modelSelect.value;
        chat.systemPrompt = boundedText(settingsSystemPrompt.value, MAX_SYSTEM_PROMPT_CHARS);
        if (includeDraft) chat.draft = boundedText(userInput.value, MAX_DRAFT_CHARS);
        return chat;
    }

    function applyChatContext(chat, { restoreDraft = true } = {}) {
        chat = ensureChatContext(chat, true);
        if (!chat) return;
        const modelId = resolveChatModel(chat);
        if (modelId) modelSelect.value = modelId;
        settingsSystemPrompt.value = chat.systemPrompt;
        if (restoreDraft) {
            userInput.value = chat.draft;
            autoResize();
        }
        updateModelUi();
        updateSendButtonState();
    }

    // Repair malformed/duplicated browser records before assigning active aliases.
    sanitizeStoredChats();
    chats.forEach(chat => ensureChatContext(chat, chat.id === activeChatId));
    conversation = activeChat()?.messages || [];
    applyChatContext(activeChat());
    saveChats();

    const originalMakeChat = makeChat;
    makeChat = function contextAwareMakeChat(messages = []) {
        const chat = originalMakeChat(messages);
        chat.modelId = fallbackModelId();
        chat.systemPrompt = boundedText(settingsSystemPrompt.value, MAX_SYSTEM_PROMPT_CHARS);
        chat.draft = '';
        chat.contextVersion = CONTEXT_VERSION;
        return chat;
    };

    const originalSwitchChat = switchChat;
    switchChat = function contextAwareSwitchChat(id) {
        if (id === activeChatId) return;
        captureVisibleContext(activeChat());
        const result = originalSwitchChat(id);
        applyChatContext(activeChat());
        saveChats();
        return result;
    };

    const originalNewChat = newChat;
    newChat = function contextAwareNewChat() {
        captureVisibleContext(activeChat());
        const result = originalNewChat();
        applyChatContext(activeChat());
        saveChats();
        return result;
    };

    const originalDeleteChat = deleteChat;
    deleteChat = function contextAwareDeleteChat(id) {
        captureVisibleContext(activeChat());
        if (queuedChat?.id === id) {
            queuedPrompt = null;
            queuedChat = null;
            activeLoaderBubble = null;
        }
        const result = originalDeleteChat(id);
        applyChatContext(activeChat());
        saveChats();
        return result;
    };

    const originalRenderModelOptions = renderModelOptions;
    renderModelOptions = function contextAwareRenderModelOptions(models) {
        const result = originalRenderModelOptions(models);
        const modelId = resolveChatModel(activeChat());
        if (modelId) modelSelect.value = modelId;
        return result;
    };

    const originalStartQueuedLoad = startQueuedLoad;
    startQueuedLoad = function contextAwareQueuedLoad(text, selectedModel) {
        const chat = ensureChatContext(activeChat(), true);
        if (chat) {
            chat.modelId = selectedModel?.id || fallbackModelId();
            chat.systemPrompt = boundedText(settingsSystemPrompt.value, MAX_SYSTEM_PROMPT_CHARS);
            chat.draft = '';
            saveChats();
        }
        return originalStartQueuedLoad(text, selectedModel);
    };

    const originalSendMessage = sendMessage;
    sendMessage = function contextAwareSendMessage(...args) {
        const chat = captureVisibleContext(activeChat());
        const promise = originalSendMessage(...args);
        if (chat && userInput.value === '') {
            chat.draft = '';
            saveChats();
        }
        return promise;
    };

    function patchAssistantMetadata(chat, modelId) {
        if (!chat || !Array.isArray(chat.messages)) return;
        for (let index = chat.messages.length - 1; index >= 0; index -= 1) {
            const message = chat.messages[index];
            if (message?.role !== 'assistant') continue;
            message.meta = { ...(message.meta || {}), model: modelId };
            break;
        }
    }

    const originalExecuteGeneration = executeGeneration;
    executeGeneration = function contextAwareExecuteGeneration(aiBubble, genChat = activeChat()) {
        const targetChat = ensureChatContext(genChat || activeChat(), true);
        if (!targetChat) return originalExecuteGeneration(aiBubble, genChat);

        const context = {
            chatId: targetChat.id,
            modelId: resolveChatModel(targetChat) || modelSelect.value || null,
            systemPrompt: boundedText(targetChat.systemPrompt, MAX_SYSTEM_PROMPT_CHARS),
        };
        targetChat.modelId = context.modelId;
        targetChat.systemPrompt = context.systemPrompt;
        saveChats();

        const run = async () => {
            const visibleChat = activeChat();
            if (context.modelId && availableModels.has(context.modelId)) modelSelect.value = context.modelId;
            settingsSystemPrompt.value = context.systemPrompt;

            let generationPromise;
            const previousRequestChatId = window.__ovllmRequestChatId;
            window.__ovllmRequestChatId = targetChat.id;
            try {
                // The base function builds and dispatches its request synchronously before
                // its first await. The request-chat marker lets outer extensions enforce
                // attachment ownership without relying on whichever chat is visible.
                generationPromise = originalExecuteGeneration(aiBubble, targetChat);
            } catch (error) {
                applyChatContext(activeChat());
                throw error;
            } finally {
                if (previousRequestChatId === undefined) delete window.__ovllmRequestChatId;
                else window.__ovllmRequestChatId = previousRequestChatId;
            }
            if (visibleChat?.id !== targetChat.id) applyChatContext(visibleChat);

            try {
                return await generationPromise;
            } finally {
                targetChat.modelId = context.modelId;
                targetChat.systemPrompt = context.systemPrompt;
                patchAssistantMetadata(targetChat, context.modelId);
                saveChats();
                applyChatContext(activeChat());
            }
        };

        // Model-load completion can enqueue a response while another chat is still
        // generating. Serialize those jobs so global AbortController/UI state cannot
        // cross wires between conversations.
        const queued = generationTail.then(run, run);
        generationTail = queued.catch(() => undefined);
        return queued;
    };

    modelSelect.addEventListener('change', () => {
        const chat = ensureChatContext(activeChat(), true);
        if (!chat) return;
        chat.modelId = modelSelect.value || null;
        scheduleSave();
    });

    settingsSystemPrompt.addEventListener('input', () => {
        const chat = ensureChatContext(activeChat(), true);
        if (!chat) return;
        chat.systemPrompt = boundedText(settingsSystemPrompt.value, MAX_SYSTEM_PROMPT_CHARS);
        scheduleSave();
    });

    userInput.addEventListener('input', () => {
        const chat = ensureChatContext(activeChat(), true);
        if (!chat) return;
        chat.draft = boundedText(userInput.value, MAX_DRAFT_CHARS);
        scheduleSave();
    });

    window.addEventListener('beforeunload', () => {
        captureVisibleContext(activeChat());
        saveChats();
    });

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
    InferBridge.use(async function chatContextFetch(input, init = {}) {
        const target = endpoint(input);
        let registeredModelId = null;
        if (target.sameOrigin && target.path === '/v1/models/download-custom') {
            try { registeredModelId = JSON.parse(String(init.body || '')).model_id || null; }
            catch { registeredModelId = null; }
        }

        const response = await previousFetch(input, init);
        if (registeredModelId && response.ok) {
            const chat = ensureChatContext(activeChat(), true);
            if (chat) {
                chat.modelId = registeredModelId;
                saveChats();
            }
        }
        if (target.sameOrigin && target.path === '/v1/system/status' && response.ok) {
            // Base status rendering may rebuild the select and prefer a model loading
            // for another chat. Reassert the active chat's selection after that task.
            window.setTimeout(() => applyChatContext(activeChat(), { restoreDraft: false }), 80);
        }
        return response;
    });
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=CHAT_CONTEXT_JS,
    description="Per-chat context isolation for the browser conversation store.",
)


def install_chat_context_extension() -> None:
    """Register per-chat context isolation."""

    ui_registry.register(EXTENSION)
