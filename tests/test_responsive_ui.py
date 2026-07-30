from app.responsive_ui import RESPONSIVE_EXTENSION_JS
from app.ui_extension import inject_multimodal_ui


def test_responsive_extension_is_injected_once_after_release_extension():
    html = "<html><body><main>chat</main></body></html>"

    injected = inject_multimodal_ui(html)

    assert injected.count('id="ovllm-responsive-extension"') == 1
    assert injected.index('id="ovllm-release-extension"') < injected.index(
        'id="ovllm-responsive-extension"'
    )
    assert inject_multimodal_ui(injected) == injected


def test_responsive_styles_cover_width_height_and_dynamic_viewports():
    script = RESPONSIVE_EXTENSION_JS

    assert "height:100dvh" in script
    assert "--ovllm-viewport-height" in script
    assert "@media (max-width:950px)" in script
    assert "@media (max-width:620px)" in script
    assert "@media (max-height:720px)" in script
    assert "@media (max-height:540px)" in script
    assert "window.visualViewport" in script
    assert "ResizeObserver" in script
    assert "typeof autoResize === 'function'" in script


def test_compact_layout_closes_overlays_without_losing_desktop_state():
    script = RESPONSIVE_EXTENSION_JS

    assert "desktopChatsOpen" in script
    assert "desktopSettingsOpen" in script
    assert "compact && !wasCompact" in script
    assert "!compact && wasCompact" in script
    assert "setChatsOpenDirect(false)" in script
    assert "setSettingsOpenDirect(false)" in script
    assert "keepCompactChoice" in script
    assert "keepCompactChoice ? compactChatsOpen : desktopChatsOpen" in script
    assert "keepCompactChoice ? compactSettingsOpen : desktopSettingsOpen" in script


def test_compact_sidebars_have_scrim_and_accessible_state_sync():
    script = RESPONSIVE_EXTENSION_JS

    assert "ovllm-panel-scrim" in script
    assert "Close open side panel" in script
    assert "aria-expanded" in script
    assert "aria-hidden" in script
    assert ".inert" in script
    assert "MutationObserver" in script
    assert "setChatsSidebarCollapsed(true)" in script
    assert "setSettingsSidebarOpen(false, true)" in script
