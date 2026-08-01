from app.config import Settings  # noqa: F401 - installs composed UI extensions
from app.generation_state_ui import GENERATION_STATE_JS
from app.ui_extension import inject_multimodal_ui


def test_generation_state_extension_is_injected_once_before_chat_context() -> None:
    html = "<html><head></head><body></body></html>"

    rendered = inject_multimodal_ui(html)
    rendered_twice = inject_multimodal_ui(rendered)

    assert rendered.count('id="ovllm-generation-state-extension"') == 1
    assert rendered_twice.count('id="ovllm-generation-state-extension"') == 1
    assert rendered.index('id="ovllm-generation-state-extension"') < rendered.index(
        'id="ovllm-chat-context-extension"'
    )
    assert rendered.index('id="ovllm-generation-state-extension"') < rendered.index(
        'id="ovllm-ui-polish-extension"'
    )


def test_generation_state_tracks_the_target_chat_and_always_clears() -> None:
    assert "activeGenerationCounts" in GENERATION_STATE_JS
    assert "generationCount(chat?.id) > 0" in GENERATION_STATE_JS
    assert "beginGeneration(targetChat)" in GENERATION_STATE_JS
    assert "Promise.resolve(result).finally" in GENERATION_STATE_JS
    assert "finishGeneration(chatId)" in GENERATION_STATE_JS
    assert "state.textContent !== next.label" in GENERATION_STATE_JS
    assert "state.className !== next.className" in GENERATION_STATE_JS


def test_generation_state_clears_after_sync_and_async_failures() -> None:
    assert "catch (error)" in GENERATION_STATE_JS
    assert "finishGeneration(chatId);" in GENERATION_STATE_JS
    assert "throw error;" in GENERATION_STATE_JS
    assert "finally(() => finishGeneration(chatId))" in GENERATION_STATE_JS
