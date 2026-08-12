"""Visual polish and workspace ergonomics for the bundled browser client."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-ui-polish-extension"

UI_POLISH_CSS = r"""
:root {
    --polish-panel: color-mix(in srgb, var(--surface-1) 94%, transparent);
    --polish-panel-raised: color-mix(in srgb, var(--surface-2) 88%, transparent);
    --polish-border-strong: color-mix(in srgb, var(--border-hover) 75%, transparent);
    --polish-shadow: 0 18px 50px rgba(0, 0, 0, .24);
    --polish-shadow-soft: 0 8px 28px rgba(0, 0, 0, .16);
}

[data-theme="light"] {
    --polish-shadow: 0 18px 50px rgba(15, 23, 42, .10);
    --polish-shadow-soft: 0 8px 28px rgba(15, 23, 42, .08);
}

html.ovllm-polished body::before {
    background:
        radial-gradient(780px 460px at 50% -18%, color-mix(in srgb, var(--primary-glow) 78%, transparent), transparent 68%),
        radial-gradient(680px 420px at 108% 75%, var(--glow-2), transparent 68%),
        radial-gradient(620px 360px at -8% 100%, var(--glow-1), transparent 72%);
}

.ovllm-polished header {
    min-height: 64px;
    padding: 10px 18px;
    background: color-mix(in srgb, var(--surface-1) 86%, transparent);
    border-bottom-color: color-mix(in srgb, var(--border-hover) 68%, transparent);
    box-shadow: 0 1px 0 rgba(255, 255, 255, .018), 0 10px 32px rgba(0, 0, 0, .10);
}

.ovllm-polished .logo-icon {
    width: 34px;
    height: 34px;
    border-radius: 11px;
    box-shadow: 0 8px 24px var(--primary-glow);
}

.ovllm-polished .logo-text {
    font-size: 15.5px;
    font-weight: 680;
    letter-spacing: -.35px;
}

.ovllm-polished .logo-sub {
    margin-top: 2px;
    letter-spacing: .18px;
}

.ovllm-polished .header-right {
    gap: 7px;
}

.ovllm-polished .stat-chip {
    min-height: 34px;
    padding: 5px 10px;
    background: color-mix(in srgb, var(--surface-2) 82%, transparent);
    border-color: color-mix(in srgb, var(--border-hover) 62%, transparent);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, .025);
}

.ovllm-polished .control-field {
    position: relative;
    min-width: 0;
}

.ovllm-polished .control-field::before {
    content: attr(data-label);
    position: absolute;
    z-index: 2;
    left: 11px;
    top: 5px;
    color: var(--text-3);
    font-size: 8px;
    font-weight: 750;
    letter-spacing: .75px;
    line-height: 1;
    text-transform: uppercase;
    pointer-events: none;
}

.ovllm-polished .control-field #model-select,
.ovllm-polished .control-field #device-select {
    min-height: 42px;
    padding-top: 15px;
    padding-bottom: 3px;
    background: color-mix(in srgb, var(--surface-2) 84%, transparent);
    border-color: color-mix(in srgb, var(--border-hover) 66%, transparent);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, .025);
}

.ovllm-polished .control-field .model-arrow,
.ovllm-polished .control-field .device-arrow {
    margin-top: 3px;
}

.ovllm-polished .icon-btn {
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-2) 76%, transparent);
    border-color: color-mix(in srgb, var(--border-hover) 62%, transparent);
}

.ovllm-polished .icon-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 7px 18px rgba(0, 0, 0, .14);
}

.ovllm-polished .main-body {
    background: color-mix(in srgb, var(--bg) 90%, transparent);
}

.ovllm-polished #chats-sidebar,
.ovllm-polished #settings-sidebar {
    background: var(--polish-panel);
    backdrop-filter: blur(18px) saturate(1.08);
}

.ovllm-polished #chats-sidebar {
    width: 272px;
    border-right-color: color-mix(in srgb, var(--border-hover) 58%, transparent);
}

.ovllm-polished #chats-sidebar.collapsed {
    margin-left: -272px;
}

.ovllm-polished .chats-header {
    min-height: 58px;
    padding: 13px 13px 10px 15px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 74%, transparent);
}

.ovllm-polished .chats-header h3,
.ovllm-polished .sidebar-header h3 {
    font-size: 11px;
    font-weight: 750;
    letter-spacing: .8px;
}

.ovllm-polished #chats-list {
    gap: 5px;
    padding: 9px 8px 12px;
}

.ovllm-polished .chat-item {
    min-height: 54px;
    border-radius: 11px;
    transition: background .18s ease, border-color .18s ease, transform .18s ease, box-shadow .18s ease;
}

.ovllm-polished .chat-item:hover {
    background: color-mix(in srgb, var(--surface-2) 82%, transparent);
    transform: translateX(1px);
}

.ovllm-polished .chat-item.active {
    background: linear-gradient(100deg, color-mix(in srgb, var(--primary) 13%, var(--surface-2)), var(--surface-2));
    border-color: color-mix(in srgb, var(--primary) 28%, var(--border));
    box-shadow: inset 3px 0 0 var(--primary), 0 5px 16px rgba(0, 0, 0, .09);
}

.ovllm-polished .chat-item-main {
    padding: 9px 7px 9px 12px;
}

.ovllm-polished .chat-item-title-row {
    display: flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
    width: 100%;
}

.ovllm-polished .chat-item-title-row .chat-item-title {
    min-width: 0;
    flex: 1;
}

.ovllm-polished .chat-item-state {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    min-height: 18px;
    padding: 2px 6px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--amber) 35%, transparent);
    background: color-mix(in srgb, var(--amber) 11%, transparent);
    color: var(--amber);
    font-size: 8px;
    font-weight: 750;
    letter-spacing: .35px;
    text-transform: uppercase;
}

.ovllm-polished .chat-item-state.draft {
    border-color: color-mix(in srgb, var(--primary) 32%, transparent);
    background: color-mix(in srgb, var(--primary) 10%, transparent);
    color: var(--primary);
}

.ovllm-polished .chat-item-delete {
    border-radius: 9px;
}

.ovllm-polished .chats-footer {
    padding: 11px 15px calc(11px + env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--surface-1) 76%, transparent);
}

.ovllm-polished .chat-column {
    position: relative;
    background:
        linear-gradient(180deg, color-mix(in srgb, var(--surface-1) 18%, transparent), transparent 22%),
        var(--bg);
}

.ovllm-polished #workspace-context-bar {
    min-height: 58px;
    padding: 9px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    background: color-mix(in srgb, var(--surface-1) 80%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--border-hover) 52%, transparent);
    backdrop-filter: blur(14px);
    z-index: 3;
}

.ovllm-polished .workspace-context-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.ovllm-polished #workspace-chat-title {
    overflow: hidden;
    color: var(--text-1);
    font-size: 13.5px;
    font-weight: 680;
    letter-spacing: -.12px;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.ovllm-polished #workspace-chat-meta {
    overflow: hidden;
    color: var(--text-3);
    font-size: 10.5px;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.ovllm-polished #workspace-state {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-height: 28px;
    padding: 5px 9px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: color-mix(in srgb, var(--surface-2) 80%, transparent);
    color: var(--text-2);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .25px;
}

.ovllm-polished #workspace-state::before {
    content: '';
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-3);
}

.ovllm-polished #workspace-state.ready::before {
    background: var(--green);
    box-shadow: 0 0 8px var(--green-glow);
}

.ovllm-polished #workspace-state.preparing::before,
.ovllm-polished #workspace-state.generating::before {
    background: var(--amber);
    box-shadow: 0 0 8px var(--amber-glow);
    animation: dot-pulse 1.4s ease infinite;
}

.ovllm-polished #workspace-state.error::before {
    background: var(--red);
}

.ovllm-polished #chat-area {
    padding: 28px 0 36px;
    background:
        linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 10%, transparent), transparent 18%),
        radial-gradient(700px 320px at 50% 0%, color-mix(in srgb, var(--primary-glow) 22%, transparent), transparent 72%);
}

.ovllm-polished .chat-inner {
    max-width: 900px;
    padding: 0 28px;
    gap: 24px;
}

.ovllm-polished .empty-state {
    min-height: min(620px, 70vh);
    gap: 18px;
    padding: 48px 20px;
}

.ovllm-polished .empty-eyebrow {
    margin-bottom: -7px;
    color: var(--primary);
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 1.4px;
    text-transform: uppercase;
}

.ovllm-polished .empty-icon {
    display: grid;
    width: 82px;
    height: 82px;
    place-items: center;
    border: 1px solid color-mix(in srgb, var(--primary) 25%, var(--border));
    border-radius: 25px;
    background: linear-gradient(145deg, color-mix(in srgb, var(--primary) 10%, var(--surface-2)), color-mix(in srgb, var(--surface-1) 92%, transparent));
    box-shadow: 0 18px 46px rgba(0, 0, 0, .18), inset 0 1px 0 rgba(255, 255, 255, .05);
}

.ovllm-polished .empty-state h2 {
    max-width: 640px;
    font-size: clamp(25px, 3vw, 34px);
    font-weight: 720;
    letter-spacing: -.85px;
}

.ovllm-polished .empty-state p {
    max-width: 560px;
}

.ovllm-polished .feature-cards {
    gap: 9px;
}

.ovllm-polished .feature-card {
    min-height: 36px;
    padding: 8px 12px;
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-2) 72%, transparent);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, .02);
}

.ovllm-polished .suggestion-chips {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: min(640px, 100%);
    gap: 9px;
    margin-top: 6px;
}

.ovllm-polished .chip {
    min-height: 44px;
    padding: 10px 14px;
    border-radius: 12px;
    text-align: left;
    line-height: 1.35;
    background: color-mix(in srgb, var(--surface-2) 70%, transparent);
}

.ovllm-polished .msg-row {
    gap: 11px;
}

.ovllm-polished .avatar {
    width: 34px;
    height: 34px;
    border-radius: 11px;
}

.ovllm-polished .msg-col {
    max-width: min(82%, 740px);
}

.ovllm-polished .bubble {
    padding: 13px 16px;
    border-radius: 16px;
    line-height: 1.68;
}

.ovllm-polished .msg-row.ai .bubble {
    background: color-mix(in srgb, var(--bubble-ai) 92%, transparent);
    border-color: color-mix(in srgb, var(--border-hover) 56%, transparent);
    box-shadow: 0 8px 30px rgba(0, 0, 0, .12), inset 0 1px 0 rgba(255, 255, 255, .018);
}

.ovllm-polished .msg-row.user .bubble {
    background: linear-gradient(140deg, #0ea5e9, #3b5ccc 82%);
    box-shadow: 0 9px 26px rgba(14, 165, 233, .18);
}

.ovllm-polished .msg-meta {
    margin-top: 6px;
}

.ovllm-polished .tool-call-card,
.ovllm-polished .bubble pre {
    border-color: color-mix(in srgb, var(--border-hover) 58%, transparent);
    border-radius: 11px;
}

.ovllm-polished #input-area {
    padding: 13px 20px calc(14px + env(safe-area-inset-bottom));
    border-top: 0;
    background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--bg) 88%, transparent) 22%);
    backdrop-filter: none;
}

.ovllm-polished .composer-shell {
    width: min(900px, 100%);
    margin: 0 auto;
    padding: 7px 8px 6px;
    border: 1px solid color-mix(in srgb, var(--border-hover) 76%, transparent);
    border-radius: 18px;
    background: color-mix(in srgb, var(--surface-1) 94%, transparent);
    box-shadow: var(--polish-shadow);
    backdrop-filter: blur(18px) saturate(1.08);
    transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
}

.ovllm-polished .composer-shell:focus-within {
    border-color: color-mix(in srgb, var(--primary) 72%, var(--border));
    box-shadow: var(--polish-shadow), 0 0 0 3px var(--primary-glow);
}

.ovllm-polished .composer-shell.has-content {
    border-color: color-mix(in srgb, var(--primary) 42%, var(--border));
}

.ovllm-polished .composer-shell .input-row {
    max-width: none;
    gap: 7px;
}

.ovllm-polished .composer-shell #user-input {
    min-height: 44px;
    padding: 11px 12px;
    border: 0;
    border-radius: 12px;
    background: transparent;
    box-shadow: none;
}

.ovllm-polished .composer-shell #user-input:focus {
    border: 0;
    box-shadow: none;
}

.ovllm-polished .composer-shell #send-btn,
.ovllm-polished .composer-shell #vision-attach-btn {
    width: 42px;
    height: 42px;
    flex-basis: 42px;
    border-radius: 12px;
}

.ovllm-polished .composer-shell #send-btn:hover:not(:disabled) {
    transform: translateY(-1px) scale(1.02);
}

.ovllm-polished .composer-shell #vision-preview-tray {
    padding: 4px 4px 9px;
}

.ovllm-polished .composer-shell .footer-meta {
    max-width: none;
    margin: 4px 4px 0;
    padding: 6px 4px 1px;
    border-top: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
}

.ovllm-polished #jump-btn {
    top: -52px;
    background: color-mix(in srgb, var(--surface-2) 94%, transparent);
    backdrop-filter: blur(12px);
}

.ovllm-polished #settings-sidebar {
    width: 300px;
    padding: 0 14px 18px;
    gap: 14px;
}

.ovllm-polished #settings-sidebar.closed {
    margin-right: -300px;
}

.ovllm-polished #settings-sidebar .sidebar-header {
    position: sticky;
    top: 0;
    z-index: 2;
    min-height: 58px;
    margin: 0 -14px 1px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: color-mix(in srgb, var(--surface-1) 94%, transparent);
    backdrop-filter: blur(16px);
}

.ovllm-polished #settings-sidebar > .setting-group {
    padding: 12px;
    border: 1px solid color-mix(in srgb, var(--border-hover) 52%, transparent);
    border-radius: 12px;
    background: color-mix(in srgb, var(--surface-2) 55%, transparent);
}

.ovllm-polished #settings-sidebar > .sidebar-divider {
    display: none;
}

.ovllm-polished .setting-group textarea,
.ovllm-polished .setting-group input[type="password"] {
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface-1) 66%, transparent);
}

.ovllm-polished .modal-overlay {
    background: rgba(2, 6, 14, .72);
    backdrop-filter: blur(10px);
}

.ovllm-polished .modal-card {
    border-color: color-mix(in srgb, var(--border-hover) 76%, transparent);
    border-radius: 18px;
    box-shadow: 0 30px 90px rgba(0, 0, 0, .48);
}

.ovllm-polished .modal-header {
    padding: 18px 20px;
}

.ovllm-polished .modal-tabs {
    padding: 0 10px;
    gap: 4px;
}

.ovllm-polished .modal-tab-btn {
    border-radius: 10px 10px 0 0;
}

.ovllm-polished #toast {
    padding: 10px 16px;
    border-radius: 12px;
    background: color-mix(in srgb, var(--surface-2) 96%, transparent);
    border-color: color-mix(in srgb, var(--border-hover) 75%, transparent);
    box-shadow: var(--polish-shadow-soft);
    backdrop-filter: blur(14px);
}

@media (max-width: 950px) {
    .ovllm-polished #chats-sidebar,
    .ovllm-polished #settings-sidebar {
        width: min(350px, 92vw);
        box-shadow: 20px 0 60px rgba(0, 0, 0, .34);
    }

    .ovllm-polished #chats-sidebar.collapsed {
        margin-left: min(-350px, -92vw);
    }

    .ovllm-polished #settings-sidebar.closed {
        margin-right: min(-350px, -92vw);
    }
}

@media (max-width: 700px) {
    .ovllm-polished header {
        padding: 9px 10px;
    }

    .ovllm-polished .control-field::before {
        top: 6px;
    }

    .ovllm-polished #workspace-context-bar {
        min-height: 52px;
        padding: 8px 12px;
    }

    .ovllm-polished #workspace-chat-title {
        font-size: 12.5px;
    }

    .ovllm-polished #workspace-chat-meta {
        max-width: 68vw;
    }

    .ovllm-polished #workspace-state {
        min-height: 25px;
        padding: 4px 7px;
        font-size: 9px;
    }

    .ovllm-polished #chat-area {
        padding: 18px 0 26px;
    }

    .ovllm-polished .chat-inner {
        padding: 0 12px;
        gap: 18px;
    }

    .ovllm-polished .empty-state {
        min-height: auto;
        padding: 34px 6px 22px;
    }

    .ovllm-polished .empty-icon {
        width: 70px;
        height: 70px;
        border-radius: 21px;
    }

    .ovllm-polished .suggestion-chips {
        grid-template-columns: 1fr;
    }

    .ovllm-polished .msg-row.user .msg-col {
        max-width: 94%;
    }

    .ovllm-polished .bubble {
        padding: 11px 13px;
        font-size: 13.5px;
    }

    .ovllm-polished #input-area {
        padding: 8px 9px calc(9px + env(safe-area-inset-bottom));
    }

    .ovllm-polished .composer-shell {
        padding: 5px 6px 4px;
        border-radius: 15px;
    }

    .ovllm-polished .composer-shell .footer-meta {
        margin-top: 2px;
    }

    .ovllm-polished .composer-shell #model-status {
        max-width: 62vw;
    }
}

@media (prefers-reduced-motion: reduce) {
    .ovllm-polished .chat-item:hover,
    .ovllm-polished .icon-btn:hover:not(:disabled) {
        transform: none;
    }
}
"""

UI_POLISH_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmUiPolishInstalled) return;
    window.__ovllmUiPolishInstalled = true;

    if (
        typeof activeChat !== 'function' || typeof renderChat !== 'function' ||
        typeof renderChatList !== 'function' || typeof getSelectedModelMeta !== 'function'
    ) return;

    document.documentElement.classList.add('ovllm-polished');

    const chatColumn = document.querySelector('.chat-column');
    const chatAreaElement = document.getElementById('chat-area');
    const inputAreaElement = document.getElementById('input-area');
    const formElement = document.getElementById('chat-form');
    const footerElement = inputAreaElement?.querySelector('.footer-meta');
    const previewTray = document.getElementById('vision-preview-tray');
    const inputElement = document.getElementById('user-input');

    const modelWrap = document.querySelector('.model-select-wrap');
    const deviceWrap = document.querySelector('.device-select-wrap');
    modelWrap?.classList.add('control-field');
    deviceWrap?.classList.add('control-field');
    if (modelWrap) modelWrap.dataset.label = 'Model';
    if (deviceWrap) deviceWrap.dataset.label = 'Device';

    let contextBar = document.getElementById('workspace-context-bar');
    if (!contextBar && chatColumn && chatAreaElement) {
        contextBar = document.createElement('div');
        contextBar.id = 'workspace-context-bar';
        contextBar.setAttribute('aria-live', 'polite');
        contextBar.innerHTML = `
            <div class="workspace-context-copy">
                <div id="workspace-chat-title">New chat</div>
                <div id="workspace-chat-meta">Local conversation</div>
            </div>
            <div id="workspace-state">Checking</div>
        `;
        chatColumn.insertBefore(contextBar, chatAreaElement);
    }

    let composerShell = inputAreaElement?.querySelector('.composer-shell');
    if (!composerShell && inputAreaElement && formElement) {
        composerShell = document.createElement('div');
        composerShell.className = 'composer-shell';
        inputAreaElement.insertBefore(composerShell, formElement);
        if (previewTray) composerShell.appendChild(previewTray);
        composerShell.appendChild(formElement);
        if (footerElement) composerShell.appendChild(footerElement);
    }

    const emptyStateElement = document.getElementById('empty-state');
    if (emptyStateElement && !emptyStateElement.querySelector('.empty-eyebrow')) {
        const eyebrow = document.createElement('div');
        eyebrow.className = 'empty-eyebrow';
        eyebrow.textContent = 'Private local workspace';
        const heading = emptyStateElement.querySelector('h2');
        if (heading) emptyStateElement.insertBefore(eyebrow, heading);
    }

    function activeModelForChat(chat) {
        const id = chat?.modelId || modelSelect.value;
        return availableModels.get(id) || getSelectedModelMeta() || null;
    }

    function messageCountLabel(chat) {
        const count = Array.isArray(chat?.messages) ? chat.messages.length : 0;
        return `${count} message${count === 1 ? '' : 's'}`;
    }

    function workspaceState(chat, model) {
        if (chat?.pendingModelId) return { label: 'Preparing', className: 'preparing' };
        if (isGenerating && chat?.id === activeChatId) return { label: 'Generating', className: 'generating' };
        if (model?.status === 'error') return { label: 'Needs attention', className: 'error' };
        if (model?.is_loaded) return { label: 'Ready', className: 'ready' };
        if (model?.is_loading) return { label: 'Preparing', className: 'preparing' };
        return { label: 'Model setup', className: 'idle' };
    }

    function updateWorkspaceHeader() {
        const chat = activeChat();
        const model = activeModelForChat(chat);
        const title = document.getElementById('workspace-chat-title');
        const meta = document.getElementById('workspace-chat-meta');
        const state = document.getElementById('workspace-state');
        if (!chat || !title || !meta || !state) return;

        title.textContent = chat.title || 'New chat';
        const modelName = model?.name || chat.modelId || 'No model selected';
        const device = model?.device || selectedDevice || defaultDevice || '';
        meta.textContent = `${messageCountLabel(chat)} · ${modelName}${device ? ` · ${device}` : ''}`;
        meta.title = meta.textContent;

        const nextState = workspaceState(chat, model);
        state.textContent = nextState.label;
        state.className = nextState.className;
        state.title = model?.status_label || nextState.label;
        document.title = `${chat.title || 'New chat'} · OpenVINO LLM`;
    }

    function decorateChatList() {
        document.querySelectorAll('.chat-item').forEach(item => {
            const chat = chats.find(candidate => candidate.id === item.dataset.chatId);
            const main = item.querySelector('.chat-item-main');
            const title = main?.querySelector('.chat-item-title');
            if (!chat || !main || !title) return;

            let row = main.querySelector('.chat-item-title-row');
            if (!row) {
                row = document.createElement('span');
                row.className = 'chat-item-title-row';
                main.insertBefore(row, title);
                row.appendChild(title);
            }

            row.querySelector('.chat-item-state')?.remove();
            const hasDraft = typeof chat.draft === 'string' && chat.draft.trim().length > 0;
            if (chat.pendingModelId || hasDraft) {
                const badge = document.createElement('span');
                badge.className = `chat-item-state${chat.pendingModelId ? '' : ' draft'}`;
                badge.textContent = chat.pendingModelId ? 'Waiting' : 'Draft';
                badge.title = chat.pendingModelId ? 'Waiting for model preparation' : 'This chat has an unsent draft';
                row.appendChild(badge);
            }
        });
    }

    function updateComposerState() {
        if (!composerShell || !inputElement) return;
        composerShell.classList.toggle('has-content', inputElement.value.trim().length > 0);
    }

    const previousRenderChatList = renderChatList;
    renderChatList = function polishedRenderChatList(...args) {
        const result = previousRenderChatList(...args);
        decorateChatList();
        updateWorkspaceHeader();
        return result;
    };

    const previousRenderChat = renderChat;
    renderChat = function polishedRenderChat(...args) {
        const result = previousRenderChat(...args);
        updateWorkspaceHeader();
        updateComposerState();
        return result;
    };

    const previousSaveConversation = saveConversation;
    saveConversation = function polishedSaveConversation(...args) {
        const result = previousSaveConversation(...args);
        decorateChatList();
        updateWorkspaceHeader();
        return result;
    };

    modelSelect.addEventListener('change', updateWorkspaceHeader);
    deviceSelect.addEventListener('change', updateWorkspaceHeader);
    inputElement?.addEventListener('input', () => {
        updateComposerState();
        window.setTimeout(decorateChatList, 140);
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
    InferBridge.use(async function polishedFetch(input, init = {}) {
        const response = await previousFetch(input, init);
        const target = endpoint(input);
        if (target.sameOrigin && target.path === '/v1/system/status' && response.ok) {
            window.setTimeout(() => {
                updateWorkspaceHeader();
                decorateChatList();
            }, 90);
        }
        return response;
    });

    updateComposerState();
    decorateChatList();
    updateWorkspaceHeader();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=UI_POLISH_JS,
    css=UI_POLISH_CSS,
    description="Visual workspace polish applied after behavioral extensions.",
)


def install_ui_polish_extension() -> None:
    """Register the visual workspace polish."""

    ui_registry.register(EXTENSION)
