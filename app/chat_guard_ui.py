"""Fail-closed browser guards for chat-scoped image attachments and navigation."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-chat-guard-extension"

CHAT_GUARD_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmChatGuardInstalled) return;
    window.__ovllmChatGuardInstalled = true;

    if (typeof activeChat !== 'function' || typeof switchChat !== 'function') return;

    const previewTray = document.getElementById('vision-preview-tray');
    const fileInput = document.getElementById('vision-file-input');
    const inputArea = document.getElementById('input-area');
    const userInputElement = document.getElementById('user-input');
    const modelSelectElement = document.getElementById('model-select');
    let ownerChatId = null;
    let preparing = false;
    let prepareTimer = null;
    let hadPreview = false;
    let cancelledUntil = 0;
    let lastModelId = modelSelectElement?.value || '';

    function activeId() {
        return activeChat()?.id || activeChatId || null;
    }

    function previews() {
        return Array.from(previewTray?.querySelectorAll('.vision-preview') || []);
    }

    function hasPreviews() {
        return previews().length > 0;
    }

    function markOwner() {
        const chatId = activeId();
        if (!chatId) return;
        ownerChatId = chatId;
        preparing = true;
        cancelledUntil = 0;
        clearTimeout(prepareTimer);
        prepareTimer = window.setTimeout(() => {
            preparing = false;
            if (!hasPreviews()) ownerChatId = null;
        }, 120000);
    }

    function clearAttachmentDom() {
        previews().forEach(preview => preview.querySelector('button')?.click());
        if (fileInput) fileInput.value = '';
    }

    function clearForNavigation(message = 'Image attachments were cleared when changing chats.') {
        const hadAttachments = preparing || hasPreviews();
        if (ownerChatId) cancelledUntil = Date.now() + 120000;
        clearTimeout(prepareTimer);
        preparing = false;
        ownerChatId = null;
        hadPreview = false;
        clearAttachmentDom();
        if (hadAttachments && message) showToast(message);
    }

    function hasPendingForChat(chatId) {
        return !!chatId && ownerChatId === chatId && (preparing || hasPreviews());
    }

    function hasAnyPending() {
        return preparing || hasPreviews();
    }

    function isSupportedImageFile(file) {
        const type = String(file?.type || '').toLowerCase();
        const name = String(file?.name || '').toLowerCase();
        return type.startsWith('image/') || /\.(?:jpe?g|png|webp)$/.test(name);
    }

    function noteFiles(fileList) {
        if (Array.from(fileList || []).some(isSupportedImageFile)) markOwner();
    }

    fileInput?.addEventListener('change', event => noteFiles(event.target?.files), true);
    inputArea?.addEventListener('drop', event => noteFiles(event.dataTransfer?.files), true);
    userInputElement?.addEventListener('paste', event => noteFiles(event.clipboardData?.files), true);

    if (previewTray) {
        const observer = new MutationObserver(() => {
            const present = hasPreviews();
            if (present) {
                clearTimeout(prepareTimer);
                preparing = false;
                hadPreview = true;
                if (!ownerChatId) {
                    if (Date.now() < cancelledUntil) {
                        clearAttachmentDom();
                        return;
                    }
                    ownerChatId = activeId();
                }
                if (ownerChatId && ownerChatId !== activeId()) {
                    clearForNavigation('Images selected in another chat were discarded for safety.');
                }
            } else if (hadPreview) {
                hadPreview = false;
                if (!preparing) ownerChatId = null;
            }
        });
        observer.observe(previewTray, { childList: true, subtree: true });
    }

    const previousSwitchChat = switchChat;
    switchChat = function attachmentAwareSwitchChat(id) {
        if (id !== activeChatId) clearForNavigation();
        return previousSwitchChat(id);
    };

    const previousNewChat = newChat;
    newChat = function attachmentAwareNewChat() {
        clearForNavigation('Image attachments were cleared when starting a new chat.');
        return previousNewChat();
    };

    const previousDeleteChat = deleteChat;
    deleteChat = function attachmentAwareDeleteChat(id) {
        if (ownerChatId === id) clearForNavigation('Image attachments for the deleted chat were cleared.');
        return previousDeleteChat(id);
    };

    modelSelectElement?.addEventListener('change', () => {
        const nextModelId = modelSelectElement.value || '';
        if (nextModelId !== lastModelId && hasAnyPending()) {
            clearForNavigation('Image attachments were cleared because the selected model changed.');
        }
        lastModelId = nextModelId;
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

    function blockedResponse(detail) {
        return new Response(JSON.stringify({ detail }), {
            status: 409,
            headers: { 'Content-Type': 'application/json' },
        });
    }

    const previousFetch = InferBridge.chain();
    InferBridge.use(async function attachmentOwnershipFetch(input, init = {}) {
        const target = endpoint(input);
        const method = String(init?.method || (typeof input !== 'string' && input?.method) || 'GET').toUpperCase();
        if (
            target.sameOrigin && target.path === '/v1/chat/completions' && method === 'POST' &&
            hasAnyPending()
        ) {
            const requestChatId = window.__ovllmRequestChatId || activeId();
            if (ownerChatId && requestChatId && ownerChatId !== requestChatId) {
                showToast('Blocked image attachments from crossing into another chat.');
                return blockedResponse('Image attachments belong to a different chat.');
            }
        }
        return previousFetch(input, init);
    });

    window.__ovllmVisionGuard = Object.freeze({
        hasPendingForChat,
        hasAnyPending,
        ownerChatId: () => ownerChatId,
        clearForNavigation,
    });

    // Context migration runs after the base page's initial render. Rebuild both
    // surfaces once all wrappers are installed so repaired IDs and pending state are
    // reflected atomically in the sidebar and active transcript.
    renderChatList();
    renderChat();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=CHAT_GUARD_JS,
    description="Chat and attachment safety checks before a request is sent.",
)


def install_chat_guard_extension() -> None:
    """Register chat and attachment safety checks."""

    ui_registry.register(EXTENSION)
