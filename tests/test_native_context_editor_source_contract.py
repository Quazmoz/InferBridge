"""Source contracts for the shared per-model context editor (native and web).

The old Settings-only picker (ContextSizePicker, ContextApplyButton,
ContextResetButton, ContextMemoryGuidance) was deliberately replaced by one
editor reachable from model cards, the load step, Chat and the Settings list.
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


def _named(root: ET.Element, name: str) -> ET.Element:
    return next(node for node in root.iter() if node.get(NAME) == name)


def _all_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PROJECT.glob("*.cs"))


def test_old_settings_only_picker_is_gone_and_settings_lists_every_model() -> None:
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    sources = _all_sources()
    for retired in (
        "ContextSizePicker",
        "ContextApplyButton",
        "ContextResetButton",
        "ContextMemoryGuidance",
    ):
        assert retired not in xaml, retired
        assert retired not in sources, retired
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    settings = _named(root, "SettingsPanel")
    inside = {node.get(NAME): node.tag.replace(XAML, "") for node in settings.iter()}
    assert inside["ContextConfigurationInfo"] == "InfoBar"
    assert inside["ContextModelsPanel"] == "StackPanel"
    assert inside["ContextRefreshButton"] == "Button"
    assert "Per-model context window" in xaml
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    assert "private void RebuildContextModelsList()" in editor
    assert "edit.Click += ContextListEdit_Click;" in editor
    assert 'AutomationProperties.SetName(edit, "Edit context window for " + row.Name);' in editor


def test_chat_shows_context_with_change_and_load_with_context() -> None:
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    chat = _named(root, "ChatPanel")
    pane = _named(chat, "ChatSettingsPane")
    pane_names = {node.get(NAME) for node in pane.iter()}
    assert {"ChatContextMeter", "ChatPaneContextText", "ChatPaneContextButton"} <= pane_names
    chat_names = {node.get(NAME) for node in chat.iter()}
    assert {"ChatContextSummary", "ChatLoadWithContextButton"} <= chat_names
    for name, handler in (
        ("ChatPaneContextButton", "ChatPaneContextButton_Click"),
        ("ChatLoadWithContextButton", "ChatLoadWithContextButton_Click"),
    ):
        button = _named(chat, name)
        assert button.get("Click") == handler
        assert (button.get("AutomationProperties.Name") or "").strip()
    for name in ("ChatPaneContextText", "ChatContextSummary"):
        assert _named(chat, name).get("AutomationProperties.LiveSetting") == "Polite"
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    assert '"Load with context…" : "Context…"' in editor
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    # The ordinary chat auto-load states which saved context it uses.
    assert "LoadContextNote(model.Id, action, contextTokens)" in window


def test_model_cards_show_editable_context_and_load_with_context() -> None:
    cards = (PROJECT / "MainWindow.ModelCards.cs").read_text(encoding="utf-8")
    assert "_modelContextLines[model.Id] = (contextLine, contextEdit, contextBusy);" in cards
    assert "ContextLineText(ContextSummaryFor(model.Id))" in cards
    assert 'Tag = (model.Id, "context")' in cards
    assert 'Add("loadctx", "\\uE768", "Load with context…", model.CanLoad);' in cards
    assert "OpenContextEditorAsync(modelId, ContextEditorMode.Edit, ReportContextToModels)" in cards
    assert (
        "OpenContextEditorAsync(modelId, ContextEditorMode.LoadWith, ReportContextToModels)"
        in cards
    )
    assert (
        'AutomationProperties.SetName(contextEdit, "Change context window for " + model.Name);'
        in cards
    )
    # Row state updates stay property-only (no item replacement on polls).
    states = cards.split("private void UpdateModelRowStates()", 1)[1].split("\n    private ", 1)[0]
    assert "Items.Clear()" not in states and "Items.Add(" not in states


def test_one_shared_editor_and_rules_serve_every_surface() -> None:
    sources = _all_sources()
    assert (
        sources.count("private async Task<(int Tokens, string Kv)?> ShowContextEditorDialogAsync(")
        == 1
    )
    assert sources.count("private async Task OpenContextEditorAsync(") == 1
    assert len(re.findall(r"OpenContextEditorAsync\(", sources)) >= 6
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    for rule in (
        "ContextEditor.Presets(context)",
        "ContextEditor.Validate(context, box.Value, SelectedKv())",
        "ContextEditor.CanCommit(",
        "ContextEditor.PrimaryAction(mode, context.Loaded)",
        "ContextEditor.WhenItApplies(mode, context.Loaded)",
        "_backend.SetContextAsync(",
        "ValidationMode = NumberBoxValidationMode.Disabled",
    ):
        assert rule in editor, rule
    assert "InfoBarSeverity.Error" in editor and "InfoBarSeverity.Warning" in editor
    logic = (PROJECT / "ContextEditor.cs").read_text(encoding="utf-8")
    assert "{ 2048, 4096, 8192, 16384, 32768, 65536, 131072 }" in logic
    assert '"Apply now (reloads engine, chat kept)"' in logic
    assert '"Used next time this model loads."' in logic
    # Code-built text keeps the theme brush (safe Ui.Text helper, no null styles).
    assert "new TextBlock" not in editor
    assert not re.search(r"\b(Foreground|Style)\s*=\s*null\b", editor)


def test_background_context_refresh_never_disables_controls() -> None:
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    refresh = editor.split("private async Task RefreshContextSummariesAsync(", 1)[1].split(
        "\n    /// <summary>", 1
    )[0]
    assert "IsEnabled" not in refresh
    assert "_contextOperationBusy = true" not in refresh
    controls = editor.split("private void UpdateContextControls()", 1)[1].split(
        "\n    private ", 1
    )[0]
    assert "_contextSummariesLoading" not in controls
    assert "_isRefreshing" not in controls


def test_contract_harness_runs_the_editor_rules() -> None:
    project = (CONTRACTS / "InferBridge.Native.ContractTests.csproj").read_text(encoding="utf-8")
    assert "../InferBridge.Native/ContextEditor.cs" in project
    program = (CONTRACTS / "Program.cs").read_text(encoding="utf-8")
    assert "await ContextEditorContractTests.RunAsync(root);" in program
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "ModelAction.Load when contextTokens is int context" in client
    assert "context_tokens = context" in client
    contexts = (PROJECT / "ContextConfigurationClient.cs").read_text(encoding="utf-8")
    assert '"/v1/models/contexts"' in contexts
    assert "public async Task<ContextConfiguration> SetContextAsync(" in contexts


def test_web_model_library_has_the_same_per_model_editor() -> None:
    web = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")
    for needle in (
        "/v1/models/contexts",
        "data-context-edit",
        "data-load-context",
        "Load with context…",
        "context_tokens: choice.tokens",
        "expected_context_tokens: details.configured_context_tokens",
        "Apply now (reloads engine, chat kept)",
        "Used next time this model loads.",
        "function contextChoice(details, raw, precision = details.kv_cache_precision || 'auto')",
        "setAttribute('role', 'alert')",
    ):
        assert needle in web, needle


def test_kv_cache_precision_is_in_both_editors() -> None:
    """KV precision sits beside context in the native and web editors (CPU/GPU only)."""
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    for needle in (
        'Header = "KV cache precision"',
        "ContextEditor.KvChoices(context)",
        '" (unavailable on NPU)"',
        "IsEnabled = option.Available",
        "kvPicker.SelectionChanged += (_, _) => Refresh();",
        "ContextEditor.Validate(context, box.Value, SelectedKv())",
        "context.Guidance",
        "await SaveContextAsync(context, tokens, report, kv);",
    ):
        assert needle in editor, needle
    logic = (PROJECT / "ContextEditor.cs").read_text(encoding="utf-8")
    assert 'new[] { "auto", "f16", "u8", "u4" }' in logic
    assert '"u8" => 1.125' in logic and '"u4" => 0.625' in logic
    assert 'if (ModelMax) tags.Add("max for this model");' in logic
    client = (PROJECT / "ContextConfigurationClient.cs").read_text(encoding="utf-8")
    assert '"kv_cache_precision_options"' in client
    assert "kv_cache_precision = kvPrecision" in client
    web = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")
    for needle in (
        "data-ctx-kv",
        "kv_cache_precision: precision",
        "(unavailable on NPU)",
        "details.context_guidance",
        "max for this model",
    ):
        assert needle in web, needle


def test_native_apply_shows_progress_until_the_engine_confirms() -> None:
    """Applying a value keeps the dialog open with a ring and locked controls, then succeeds or restores."""
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    dialog = editor.split("ShowContextEditorDialogAsync(ContextConfiguration context,", 1)[1].split(
        "\n    private async Task<bool> SaveContextAsync(", 1
    )[0]
    for needle in (
        "new ProgressRing",
        'Foreground = Ui.ResBrush("InferBridgeBrandBrush")',
        "picker.IsEnabled = box.IsEnabled = kvPicker.IsEnabled = !on;",
        "dialog.IsPrimaryButtonEnabled = !on;",
        'dialog.CloseButtonText = on ? "" : "Cancel";',
        "if (applying) args.Cancel = true;",  # no Esc/close mid-rebuild
        "args.Cancel = true; // Stay open with progress until the engine answers.",
        "if (applying) return;",  # no double submit
        "ContextEditor.ApplyingText(choice.Tokens, context.Loaded, kvChange)",
        "AnnounceContext(busyRow, busy);",
        "bool ok = await apply(choice.Tokens, kv, Local);",
        "dialog.Hide();",
        'SetApplying(false, "");',
        "AutomationLiveSetting.Polite",
    ):
        assert needle in dialog, needle
    save = editor.split("private async Task<bool> SaveContextAsync(", 1)[1].split(
        "\n    private async void ContextListEdit_Click", 1
    )[0]
    assert "_contextApplying = (baseline.ModelId, tokens, baseline.Loaded, kvChange);" in save
    assert "_contextApplying = null;" in save.split("finally", 1)[1]
    assert "ContextEditor.AppliedText(tokens, kvChange)" in save
    assert "return confirmed;" in save
    # Every surface showing the model reflects the busy state; polls never set it.
    controls = editor.split("private void UpdateContextControls()", 1)[1].split(
        "\n    private ", 1
    )[0]
    for needle in (
        "ShowBusy(entry.Busy, ContextBusyText(modelId) is not null);",
        "ShowBusy(ChatContextBusyRing, busy is not null);",
        "ShowBusy(ChatPaneContextBusyRing, busy is not null);",
        "UpdateChatRebuildState();",
    ):
        assert needle in controls, needle
    refresh = editor.split("private async Task RefreshContextSummariesAsync(", 1)[1].split(
        "\n    /// <summary>", 1
    )[0]
    assert "_contextApplying" not in refresh
    # The engine's own "changing" flag shows an apply started elsewhere (e.g. the browser).
    assert "ContextSummaryFor(modelId) is { Changing: true } row" in editor
    client = (PROJECT / "ContextConfigurationClient.cs").read_text(encoding="utf-8")
    assert 'row.TryGetProperty("changing", out var changing)' in client
    assert "new TextBlock" not in editor
    assert not re.search(r"\b(Foreground|Style)\s*=\s*null\b", editor)


def test_native_busy_indicators_and_chat_guard_are_wired() -> None:
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    chat = _named(root, "ChatPanel")
    for name in ("ChatContextBusyRing", "ChatPaneContextBusyRing"):
        ring = _named(chat, name)
        assert ring.tag == XAML + "ProgressRing"
        assert ring.get("Visibility") == "Collapsed" and ring.get("IsActive") == "False"
        assert ring.get("Foreground") == "{ThemeResource InferBridgeBrandBrush}"
        assert (ring.get("AutomationProperties.Name") or "").strip()
    rebuild = _named(chat, "ChatRebuildText")
    assert rebuild.get("AutomationProperties.LiveSetting") == "Polite"
    assert rebuild.get("TextWrapping") == "Wrap"  # 150 % scale wraps instead of clipping
    cards = (PROJECT / "MainWindow.ModelCards.cs").read_text(encoding="utf-8")
    assert "var contextBusy = new ProgressRing" in cards
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "&& !rebuilding && SelectedChatModel?.Status" in window
    assert "if (!_chatBusy && ChatModelRebuilding)" in window
    assert "ContextEditor.SendPausedText(_contextApplying?.Tokens)" in window
    logic = (PROJECT / "ContextEditor.cs").read_text(encoding="utf-8")
    assert '"… rebuilding engine"' in logic and '"Context set to "' in logic


def test_web_editors_show_progress_and_composer_guards_sends() -> None:
    library = (ROOT / "app" / "model_library_ui.py").read_text(encoding="utf-8")
    for needle in (
        'data-ctx-progress role="status" aria-live="polite"',
        '<span class="spinner" aria-hidden="true"></span>',
        "editor.setAttribute('aria-busy', busy ? 'true' : 'false');",
        "[preset, input, kvSelect, save, cancel, cardButton].forEach(",
        "if (editor.getAttribute('aria-busy') === 'true') return;",
        "… rebuilding engine.",
        "'Context set to'",
        "note.className = 'ml-ctx-note ok';",
        "setBusy(false);",
    ):
        assert needle in library, needle
    composer = (ROOT / "app" / "context_budget_ui.py").read_text(encoding="utf-8")
    for needle in (
        "rebuild.id = 'ov-context-rebuild';",
        "rebuild.setAttribute('role', 'status');",
        "rebuild.setAttribute('aria-live', 'polite');",
        "target.path === '/v1/models/context' && method === 'POST'",
        "return trackContextApply(previousFetch, input, init);",
        "sendMessage = function contextRebuildAwareSendMessage(",
        "closest('.meta-btn.regen')",
        "configPanel.setAttribute('aria-busy', text ? 'true' : 'false');",
        "… rebuilding engine.",
        "Context set to ${shortTokens(tokens)}.",
    ):
        assert needle in composer, needle
