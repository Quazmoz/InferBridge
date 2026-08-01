"""Synchronize the workspace status badge with the real generation lifecycle."""

from __future__ import annotations


GENERATION_STATE_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmGenerationStateInstalled) return;
    window.__ovllmGenerationStateInstalled = true;

    if (typeof executeGeneration !== 'function' || typeof activeChat !== 'function') return;

    const activeGenerationCounts = new Map();

    function modelForChat(chat) {
        const selectedId = chat?.modelId || (
            typeof modelSelect !== 'undefined' ? modelSelect.value : ''
        );
        if (!selectedId || typeof availableModels === 'undefined') return null;
        return availableModels.get(selectedId) || null;
    }

    function generationCount(chatId) {
        return chatId ? Number(activeGenerationCounts.get(chatId) || 0) : 0;
    }

    function workspaceState(chat, model) {
        if (chat?.pendingModelId) return { label: 'Preparing', className: 'preparing' };
        if (generationCount(chat?.id) > 0) {
            return { label: 'Generating', className: 'generating' };
        }
        if (model?.status === 'error') {
            return { label: 'Needs attention', className: 'error' };
        }
        if (model?.is_loaded) return { label: 'Ready', className: 'ready' };
        if (model?.is_loading) return { label: 'Preparing', className: 'preparing' };
        return { label: 'Model setup', className: 'idle' };
    }

    function refreshWorkspaceState() {
        const state = document.getElementById('workspace-state');
        const chat = activeChat();
        if (!state || !chat) return;

        const model = modelForChat(chat);
        const next = workspaceState(chat, model);
        if (state.textContent !== next.label) state.textContent = next.label;
        if (state.className !== next.className) state.className = next.className;
        state.title = model?.status_label || next.label;
    }

    function beginGeneration(chat) {
        const chatId = chat?.id || null;
        if (!chatId) return null;
        activeGenerationCounts.set(chatId, generationCount(chatId) + 1);
        refreshWorkspaceState();
        return chatId;
    }

    function finishGeneration(chatId) {
        if (chatId) {
            const remaining = generationCount(chatId) - 1;
            if (remaining > 0) activeGenerationCounts.set(chatId, remaining);
            else activeGenerationCounts.delete(chatId);
        }
        refreshWorkspaceState();
    }

    const previousExecuteGeneration = executeGeneration;
    executeGeneration = function generationStateAwareExecuteGeneration(
        aiBubble,
        genChat = activeChat(),
    ) {
        const targetChat = genChat || activeChat();
        const chatId = beginGeneration(targetChat);
        let result;
        try {
            result = previousExecuteGeneration(aiBubble, targetChat);
        } catch (error) {
            finishGeneration(chatId);
            throw error;
        }
        return Promise.resolve(result).finally(() => finishGeneration(chatId));
    };
})();
"""


__all__ = ["GENERATION_STATE_JS"]
