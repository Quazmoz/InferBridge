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
    assert "_modelContextLines[model.Id] = (contextLine, contextEdit);" in cards
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
    assert sources.count("private async Task<int?> ShowContextEditorDialogAsync(") == 1
    assert sources.count("private async Task OpenContextEditorAsync(") == 1
    assert len(re.findall(r"OpenContextEditorAsync\(", sources)) >= 6
    editor = (PROJECT / "MainWindow.ContextEditor.cs").read_text(encoding="utf-8")
    for rule in (
        "ContextEditor.Presets(context)",
        "ContextEditor.Validate(context, box.Value)",
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
        "function contextChoice(details, raw)",
        "setAttribute('role', 'alert')",
    ):
        assert needle in web, needle
