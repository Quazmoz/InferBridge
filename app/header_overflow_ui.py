"""Responsive overflow menu for secondary browser-header actions."""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-header-overflow-extension"

HEADER_OVERFLOW_CSS = r"""
#ov-header-more-wrap {
    position: relative;
    display: none;
    flex: 0 0 auto;
}
#ov-header-more-menu {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    z-index: 40;
    width: min(250px, calc(100vw - 24px));
    padding: 6px;
    border: 1px solid var(--border-hover);
    border-radius: 12px;
    background: var(--surface-1);
    box-shadow: var(--shadow-md);
}
#ov-header-more-menu[hidden] {
    display: none !important;
}
.ov-header-overflow-item {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr);
    align-items: center;
    min-height: 46px;
    border-radius: 9px;
    color: var(--text-1);
    cursor: pointer;
}
.ov-header-overflow-item:hover,
.ov-header-overflow-item:focus-within {
    background: var(--surface-2);
}
.ov-header-overflow-item .icon-btn {
    width: 42px;
    height: 42px;
    border: 0;
    background: transparent;
}
.ov-header-overflow-label {
    min-width: 0;
    padding-right: 12px;
    overflow: hidden;
    color: var(--text-2);
    font-size: 12px;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.ov-header-overflow-item:has(.icon-btn:disabled) {
    cursor: not-allowed;
    opacity: .55;
}
@media (max-width: 760px) {
    #ov-header-more-wrap {
        display: block;
        order: 4;
    }
}
"""

HEADER_OVERFLOW_JS = r"""(() => {
'use strict';
if (window.__ovllmHeaderOverflowInstalled) return;
window.__ovllmHeaderOverflowInstalled = true;
const header = document.querySelector('.header-right');
if (!header) return;
const definitions = [
    ['add-model-btn', 'Add custom model'],
    ['export-chat-btn', 'Export conversation'],
    ['theme-toggle-btn', 'Change theme'],
    ['advisor-open-btn', 'Best model for this PC'],
    ['doctor-btn', 'System Doctor'],
];
const actions = definitions
    .map(([id, label]) => ({ button: document.getElementById(id), label }))
    .filter(item => item.button);
if (!actions.length) return;
const settingsButton = document.getElementById('settings-toggle-btn');
const wrap = document.createElement('div');
wrap.id = 'ov-header-more-wrap';
const trigger = document.createElement('button');
trigger.type = 'button';
trigger.id = 'ov-header-more-btn';
trigger.className = 'icon-btn';
trigger.title = 'More actions';
trigger.setAttribute('aria-label', 'Open more actions');
trigger.setAttribute('aria-haspopup', 'menu');
trigger.setAttribute('aria-expanded', 'false');
trigger.innerHTML = '<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
const menu = document.createElement('div');
menu.id = 'ov-header-more-menu';
menu.setAttribute('role', 'menu');
menu.setAttribute('aria-label', 'More actions');
menu.hidden = true;
wrap.append(trigger, menu);
header.insertBefore(wrap, settingsButton || null);

function enabledMenuButtons() {
    return actions
        .map(({ button }) => button)
        .filter(button => button.parentElement?.classList.contains('ov-header-overflow-item') && !button.disabled);
}

function closeMenu({ restoreFocus = false } = {}) {
    if (menu.hidden) {
        if (restoreFocus) trigger.focus();
        return;
    }
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', 'Open more actions');
    if (restoreFocus) trigger.focus();
}

function openMenu({ focus = 'first' } = {}) {
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    trigger.setAttribute('aria-label', 'Close more actions');
    const buttons = enabledMenuButtons();
    const target = focus === 'last' ? buttons.at(-1) : buttons[0];
    target?.focus();
}

function closeAfterAction() {
    queueMicrotask(() => {
        const focusStayedInMenu = menu.contains(document.activeElement);
        closeMenu({ restoreFocus: focusStayedInMenu });
    });
}

const items = actions.map(({ button, label }) => {
    const marker = document.createComment(`ov-header-placeholder-${button.id}`);
    button.parentNode?.insertBefore(marker, button);
    const item = document.createElement('div');
    item.className = 'ov-header-overflow-item';
    item.setAttribute('role', 'none');
    const text = document.createElement('span');
    text.className = 'ov-header-overflow-label';
    text.textContent = label;
    item.append(button, text);
    item.addEventListener('click', event => {
        if (button.disabled || event.target === button || button.contains(event.target)) return;
        button.click();
        closeAfterAction();
    });
    menu.appendChild(item);
    return { button, marker, item };
});
const compactQuery = window.matchMedia('(max-width: 760px)');

function moveIntoMenu() {
    items.forEach(({ button, item }) => {
        if (button.parentNode !== item) item.prepend(button);
        button.setAttribute('role', 'menuitem');
        button.tabIndex = -1;
    });
}

function restoreHeader() {
    closeMenu();
    items.forEach(({ button, marker }) => {
        marker.parentNode?.insertBefore(button, marker.nextSibling);
        button.removeAttribute('role');
        button.removeAttribute('tabindex');
    });
}

function syncLayout() {
    if (compactQuery.matches) moveIntoMenu();
    else restoreHeader();
}

function focusMenuButton(direction) {
    const buttons = enabledMenuButtons();
    if (!buttons.length) return;
    const currentIndex = buttons.indexOf(document.activeElement);
    let nextIndex = 0;
    if (direction === 'first') nextIndex = 0;
    else if (direction === 'last') nextIndex = buttons.length - 1;
    else if (direction === 'next') nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % buttons.length;
    else if (direction === 'previous') nextIndex = currentIndex < 0 ? buttons.length - 1 : (currentIndex - 1 + buttons.length) % buttons.length;
    buttons[nextIndex]?.focus();
}

trigger.addEventListener('click', () => {
    if (menu.hidden) openMenu();
    else closeMenu({ restoreFocus: true });
});
trigger.addEventListener('keydown', event => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    openMenu({ focus: event.key === 'ArrowUp' ? 'last' : 'first' });
});
menu.addEventListener('click', event => {
    if (!event.target.closest('button')) return;
    closeAfterAction();
});
menu.addEventListener('keydown', event => {
    const keyActions = {
        ArrowDown: 'next',
        ArrowRight: 'next',
        ArrowUp: 'previous',
        ArrowLeft: 'previous',
        Home: 'first',
        End: 'last',
    };
    const direction = keyActions[event.key];
    if (direction) {
        event.preventDefault();
        focusMenuButton(direction);
        return;
    }
    if (event.key === 'Tab') {
        event.preventDefault();
        closeMenu();
        if (event.shiftKey) trigger.focus();
        else (settingsButton || trigger).focus();
    }
});
document.addEventListener('pointerdown', event => {
    if (!menu.hidden && !wrap.contains(event.target)) closeMenu();
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) {
        event.preventDefault();
        closeMenu({ restoreFocus: true });
    }
});
compactQuery.addEventListener?.('change', syncLayout);
syncLayout();
})();
"""


def install_header_overflow_extension() -> None:
    """Compose a compact secondary-action menu into the browser UI."""

    if getattr(ui_extension, "_HEADER_OVERFLOW_EXTENSION_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_header_overflow(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        payload = (
            f'\n<style id="{_EXTENSION_ID}-styles">\n{HEADER_OVERFLOW_CSS}\n</style>\n'
            f'<script id="{_EXTENSION_ID}">\n{HEADER_OVERFLOW_JS}\n</script>\n'
        )
        if "</body>" in html:
            return html.replace("</body>", f"{payload}</body>", 1)
        return html + payload

    ui_extension.inject_multimodal_ui = inject_with_header_overflow
    ui_extension._HEADER_OVERFLOW_EXTENSION_INSTALLED = True
