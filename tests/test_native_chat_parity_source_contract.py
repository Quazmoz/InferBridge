"""Source contracts for the native Chat parity work (2026-10).

Eject from the chat model bar, the "Load another model?" guard, Regenerate, the
exact context budget, saved chat defaults and theme, starter prompts, keyboard
shortcuts and the engine activity feed. Behaviour of the shared logic is covered
by ``ChatParityContractTests.cs`` in the .NET contract harness.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "windows-native" / "InferBridge.Native"
CONTRACTS = ROOT / "windows-native" / "InferBridge.Native.ContractTests"
NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
XAML = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
LOCAL = "{using:InferBridge.Native}"


def _xaml() -> ET.Element:
    return ET.parse(PROJECT / "MainWindow.xaml").getroot()


def _named(root: ET.Element, name: str) -> ET.Element:
    return next(node for node in root.iter() if node.get(NAME) == name)


def _source(name: str) -> str:
    return (PROJECT / name).read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start : index + 1]
    raise AssertionError(signature)


def test_eject_button_sits_beside_the_chat_model_picker_and_is_accessible() -> None:
    root = _xaml()
    toolbar = _named(root, "ChatToolbar")
    eject = _named(toolbar, "ChatEjectButton")
    picker = _named(toolbar, "ChatModelPicker")
    assert eject.tag == XAML + "Button"
    assert eject.get("Grid.Column") == str(int(picker.get("Grid.Column")) + 1)
    assert eject.get("Click") == "ChatEjectButton_Click"
    assert eject.get("IsEnabled") == "False"
    assert "Eject" in eject.get("AutomationProperties.Name")
    assert "conversation is kept" in eject.get("ToolTipService.ToolTip")
    accelerator = eject.find(f"{XAML}Button.KeyboardAccelerators/{XAML}KeyboardAccelerator")
    assert accelerator is not None and accelerator.get("Key") == "E"
    assert _named(eject, "ChatEjectText").get("Text") == "Eject"
    assert picker.get("PlaceholderText") == "No model loaded"
    window = _source("MainWindow.xaml.cs")
    # Compact toolbars keep the icon, tooltip and Narrator name.
    assert "ChatEjectText.Visibility = compact ? Visibility.Collapsed" in window


def test_eject_confirms_while_generating_unloads_and_keeps_the_conversation() -> None:
    code = _source("MainWindow.ChatModelBar.cs")
    click = _body(code, "private async void ChatEjectButton_Click(")
    assert "if (_chatBusy)" in click
    assert '"Stop and eject"' in click and '"Keep generating"' in click
    assert "DefaultButton = ContentDialogButton.Close" in click
    assert click.index("ShowAsync()") < click.index("_sendCancellation?.Cancel();")
    assert "await Task.Delay(50);" in click  # waits for the stream to unwind
    assert "catch (Exception) when (_closing.IsCancellationRequested)" in click
    assert "_ejecting = false;" in click.split("finally", 1)[1]

    eject = _body(code, "private async Task EjectChatModelAsync(ModelInfo model)")
    assert "await _backend.UnloadModelAsync(model, ApiKeyBox.Password, _closing.Token)" in eject
    assert "_ejectedModelId = model.Id;" in eject
    assert "_updatingModelPicker = true;" in eject
    assert "ChatModelPicker.SelectedItem = null;" in eject
    assert "ClearChatStats();" in eject  # stats strip and Context used ring reset
    assert "ResetContextBudget();" in eject
    assert "await RefreshAsync();" in eject
    # History is kept: Eject never clears the transcript or the turns.
    for forbidden in ("_turns.Clear()", "ChatHistory.Items.Clear()", "_conversationModelId ="):
        assert forbidden not in eject
    # Busy state; enabled during a reply so it can offer to stop it.
    state = _body(code, "private void UpdateChatEjectButton()")
    assert "!_ejecting" in state and "model?.CanUnload == true" in state
    assert "_chatBusy" not in state
    assert '"Ejecting…"' in state
    assert "ModelLifecycle.PickerPlaceholder(_shownModels)" in state
    assert "UpdateChatEjectButton();" in _body(
        _source("MainWindow.xaml.cs"), "private void UpdateChatButtons()"
    )


def test_ejected_model_is_never_reselected_or_reloaded_without_a_user_pick() -> None:
    window = _source("MainWindow.xaml.cs")
    inventory = _body(window, "private void ApplyModelInventory(")
    assert (
        "string? selected = SelectedModelId ?? (_ejectedModelId is null ? _conversationModelId : null);"
        in inventory
    )
    assert (
        "ChatModelPicker.PlaceholderText = ModelLifecycle.PickerPlaceholder(models);" in inventory
    )
    selection = _body(window, "private async void ChatModelPicker_SelectionChanged(")
    assert selection.index("if (_updatingModelPicker) return;") < selection.index(
        "_ejectedModelId = null;"
    )
    assert selection.index("_ejectedModelId = null;") < selection.index(
        "await ManageModelAsync(ModelAction.Load, model, fromChat: true);"
    )
    assert "_ejectedModelId = null;" in _body(
        window, "private async void SavedChatsPicker_SelectionChanged("
    )


def test_eject_uses_the_existing_unload_api_and_retries_only_busy_refusals() -> None:
    session = _source("ChatSession.cs")
    unload = _body(session, "public async Task<string> UnloadModelAsync(")
    assert "ChangeModelAsync(model, ModelAction.Unload, apiKey, cancellationToken)" in unload
    assert "when (ModelLifecycle.IsBusyRefusal(error) && attempt < busyRetries)" in unload
    assert '"is busy"' in session
    backend = _source("BackendClient.cs")
    assert '("/v1/models/unload",' in backend


def test_loading_with_other_models_in_memory_asks_like_the_web_guard() -> None:
    window = _source("MainWindow.xaml.cs")
    manage = _body(window, "private async Task ManageModelAsync(")
    assert "await ResolveLoadedPeersAsync(model, action)" in manage
    assert manage.index("ResolveLoadedPeersAsync") < manage.index("_backend.ChangeModelAsync(")
    code = _source("MainWindow.ChatModelBar.cs")
    guard = _body(code, "private async Task<bool> ResolveLoadedPeersAsync(")
    for text in (
        '"Load another model?"',
        '"Unload other and continue"',
        '"Keep loaded and continue"',
        '"Cancel"',
        "ModelLifecycle.LoadedPeers(_shownModels, model.Id)",
        "busyRetries: 0",
    ):
        assert text in guard, text
    web = (ROOT / "app" / "model_lifecycle_ui.py").read_text(encoding="utf-8")
    for text in ("Load another model?", "Keep loaded and continue", "Unload other and continue"):
        assert text in web


def test_regenerate_replaces_the_last_reply_only_after_it_completes() -> None:
    root = _xaml()
    button = _named(_named(root, "ChatPanel"), "RegenerateButton")
    assert button.get("Click") == "RegenerateButton_Click"
    assert button.get("AutomationProperties.Name") == "Regenerate the last reply"
    window = _source("MainWindow.xaml.cs")
    assert "await GenerateReplyAsync(regenerate: false);" in window
    assert "await GenerateReplyAsync(regenerate: true);" in _source("MainWindow.ChatModelBar.cs")
    generate = _body(window, "private async Task GenerateReplyAsync(bool regenerate)")
    assert "ChatTranscript.ForRegenerate(_turns)" in generate
    assert 'ChatTranscript.ForRequest(_turns, new ChatTurn("user", prompt))' in generate
    assert "replacedItem.Visibility = Visibility.Collapsed;" in generate
    commit = generate.index("committed = true;")
    assert generate.index("ChatHistory.Items.Remove(replacedItem);") > commit
    assert "replacedItem.Visibility = Visibility.Visible;" in generate.split("finally", 1)[1]
    assert "if (!regenerate) PromptBox.Text = string.Empty;" in generate
    buttons = _body(window, "private void UpdateChatButtons()")
    assert "ChatTranscript.CanRegenerate(_turns)" in buttons
    web = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "function regenerateLast()" in web


def test_context_budget_chip_uses_the_engine_preflight_without_locking_chat() -> None:
    root = _xaml()
    chat = _named(root, "ChatPanel")
    chip = _named(chat, "ContextBudgetButton")
    flyout = chip.find(f"{XAML}Button.Flyout/{XAML}Flyout")
    assert flyout is not None and flyout.get("Opening") == "ContextBudgetFlyout_Opening"
    names = {node.get(NAME) for node in chip.iter()}
    assert {
        "ContextBudgetText",
        "ContextBudgetDetails",
        "ContextBudgetNotice",
        "ContextBudgetOmissions",
        "ContextBudgetFitButton",
        "ContextBudgetRefreshButton",
    } <= names
    assert _named(chat, "PromptBox").get("TextChanged") == "PromptBox_TextChanged"
    session = _source("ChatSession.cs")
    assert '"/v1/chat/context-budget"' in session
    assert "image_count = context.Images.Count" in session
    code = _source("MainWindow.ChatModelBar.cs")
    refresh = _body(code, "private async Task RefreshContextBudgetAsync()")
    assert "sequence != _contextBudgetSequence" in refresh
    # A background check never disables chat controls.
    assert not re.search(r"\b(SendButton|PromptBox|ChatModelPicker)\.IsEnabled\b", refresh)
    assert "_contextBudgetTimer.Tick += ContextBudgetTimer_Tick;" in _source("MainWindow.xaml.cs")
    web = (ROOT / "app" / "context_budget_ui.py").read_text(encoding="utf-8")
    assert "'/v1/chat/context-budget'" in web


def test_chat_defaults_and_theme_are_saved_locally_like_browser_settings() -> None:
    root = _xaml()
    settings = _named(root, "SettingsPanel")
    theme = _named(settings, "AppThemePicker")
    assert theme.get("SelectionChanged") == "AppThemePicker_SelectionChanged"
    assert [item.get("Tag") for item in theme if item.tag == XAML + "ComboBoxItem"] == [
        "system",
        "light",
        "dark",
    ]
    pane = _named(root, "ChatSettingsPane")
    for name in ("SaveGenerationDefaultsButton", "ClearGenerationDefaultsButton"):
        assert _named(pane, name).tag == XAML + "Button"
    generation = _source("MainWindow.GenerationSettings.cs")
    reset = _body(generation, "private void ResetGenerationSettings()")
    assert "_generationOptions = _preferences.DefaultGeneration;" in reset
    assert "_systemPrompt = _preferences.DefaultSystemPrompt;" in reset
    prefs = _source("NativePreferences.cs")
    assert '"native-preferences.json"' in prefs
    assert "FileOptions.WriteThrough" in prefs and "ReparsePoint" in prefs
    assert "ApiKey" not in prefs and "Password" not in prefs  # never stores a key
    code = _source("MainWindow.ChatModelBar.cs")
    assert "root.RequestedTheme = theme switch" in code


def test_starter_prompts_match_the_browser_and_only_fill_the_prompt() -> None:
    root = _xaml()
    suggestions = _named(root, "ChatSuggestions")
    assert suggestions.tag == LOCAL + "WrapRow"
    prompts = [node.get("Content") for node in suggestions if node.tag == XAML + "Button"]
    assert len(prompts) == 4
    web = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for prompt in prompts:
        assert f'<button type="button" class="chip">{prompt}</button>' in web
        assert prompt
    click = _body(_source("MainWindow.ChatModelBar.cs"), "private void ChatSuggestion_Click(")
    assert "PromptBox.Text = prompt;" in click
    assert "GenerateReplyAsync" not in click


def test_keyboard_shortcuts_match_the_browser_footer() -> None:
    root = _xaml()

    def keys(name: str) -> list[tuple[str | None, str | None]]:
        node = _named(root, name)
        return [
            (accelerator.get("Key"), accelerator.get("Modifiers"))
            for accelerator in node.iter(XAML + "KeyboardAccelerator")
        ]

    assert ("Escape", None) in keys("CancelButton")
    assert ("L", "Control") in keys("NewChatButton")
    assert ("Enter", "Control") in keys("SendButton")
    web = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "<kbd>Ctrl+L</kbd> new chat" in web and "<kbd>Esc</kbd> stop" in web


def test_engine_activity_card_reads_the_event_log() -> None:
    root = _xaml()
    overview = _named(root, "OverviewPanel")
    heading = _named(overview, "EngineActivityHeading")
    assert heading.get("Text") == "Recent engine activity"
    assert _named(overview, "EngineActivityList").get("AutomationProperties.Name")
    assert '"/v1/events?limit=100"' in _source("ChatSession.cs")
    window = _source("MainWindow.xaml.cs")
    assert "_ = RefreshEngineActivityAsync();" in window
    code = _body(
        _source("MainWindow.ChatModelBar.cs"), "private async Task RefreshEngineActivityAsync()"
    )
    assert "AutomationProperties.SetName(row" in code
    assert "EngineEvent.Relative(" in code


def test_best_model_card_shows_the_engine_advisor_and_never_auto_switches_chat() -> None:
    root = _xaml()
    overview = _named(root, "OverviewPanel")
    assert _named(overview, "AdvisorHeading").get("Text") == "Best model for this PC"
    for name in (
        "AdvisorProfilePicker",
        "AdvisorRefreshButton",
        "AdvisorPills",
        "AdvisorPrepareButton",
        "AdvisorShowModelButton",
        "AdvisorModelsList",
    ):
        assert _named(overview, name).get(NAME) == name
    client = _source("HardwareAdvisorClient.cs")
    assert '"/v1/system/telemetry"' in client
    assert '"embedding"' in client  # generation models only, like the web table
    code = _source("MainWindow.HardwareAdvisor.cs")
    assert "!AdvisorProfilePicker.IsDropDownOpen" in code
    assert "await ManageModelAsync(action, model);" in code
    # Display only: it never changes the chat model or the conversation.
    for forbidden in ("ChatModelPicker.SelectedItem", "_conversationModelId", "_turns"):
        assert forbidden not in code
    web = (ROOT / "app" / "advisor_ui" / "script_1.py").read_text(encoding="utf-8")
    assert "latestStatus?.metrics?.advisor" in web


def test_web_chat_still_offers_unload_and_refuses_it_while_generating() -> None:
    web = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="unload-model-btn"' in web
    assert "async function requestModelUnload(modelId)" in web
    assert "fetch('/v1/models/unload'" in web
    assert "unloadModelBtn.disabled = isGenerating || !model.can_unload;" in web


def test_contract_harness_runs_the_chat_parity_contracts() -> None:
    csproj = (CONTRACTS / "InferBridge.Native.ContractTests.csproj").read_text(encoding="utf-8")
    for name in ("ChatSession.cs", "NativePreferences.cs", "HardwareAdvisorClient.cs"):
        assert f'Include="../InferBridge.Native/{name}"' in csproj
    program = (CONTRACTS / "Program.cs").read_text(encoding="utf-8")
    assert "await ChatParityContractTests.RunAsync(root);" in program
    tests = (CONTRACTS / "ChatParityContractTests.cs").read_text(encoding="utf-8")
    for requirement in (
        "unload did not retry the busy refusal until accepted",
        "a non-busy refusal was retried",
        "regenerate did not resend the history without the newest reply",
        "context check did not mirror the chat request",
        "preferences did not round-trip",
        "events were not newest-first, bounded and sanitized",
        "advisor table was not generation-only and sorted by fit",
    ):
        assert requirement in tests


def test_check_for_newly_verified_models_mirrors_the_web_refresh() -> None:
    root = _xaml()
    item = _named(root, "RefreshVerifiedLibraryMenuItem")
    assert item.tag == XAML + "MenuFlyoutItem"
    assert item.get("Text") == "Check for newly verified models…"
    assert item.get("Click") == "RefreshVerifiedLibraryMenuItem_Click"
    handler = _body(
        _source("MainWindow.ModelLibrary.cs"),
        "private async void RefreshVerifiedLibraryMenuItem_Click",
    )
    # Guarded like every other library tool, confirmed first, and never downloads weights.
    assert "RunModelLibraryOperationAsync" in handler
    assert handler.index("ConfirmAsync") < handler.index("RefreshOfficialModelLibraryAsync")
    assert "No model files are downloaded" in handler
    assert "never replaced" in handler
    client = _body(_source("ModelLibraryClient.cs"), "RefreshOfficialModelLibraryAsync(")
    assert '"/v1/model-library/refresh", "{}"' in client
    assert "RequireVerifiedOriginAsync" in client
    assert 'ReadString(root, "status") != "refreshed"' in client
    assert "IsValidModelId" in client
    assert "HttpStatusCode.BadGateway" in client
    web = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")
    assert "await api('/v1/model-library/refresh', {method: 'POST'" in web
    routes = (ROOT / "app" / "model_library_routes.py").read_text(encoding="utf-8")
    assert '{"status": "refreshed", "result": result, "library": snapshot}' in routes
    tests = (CONTRACTS / "ModelLibraryContractTests.cs").read_text(encoding="utf-8")
    for requirement in (
        "refresh differs from the web POST /v1/model-library/refresh",
        "refresh summary did not map added/updated/conflicts",
    ):
        assert requirement in tests
