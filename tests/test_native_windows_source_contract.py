"""Prevent a browser-shell regression while the native Store frontend is built."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "windows-native" / "InferBridge.Native"
MSBUILD = PROJECT / "InferBridge.Native.csproj"
XAML = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def test_native_project_uses_winui_and_a_pinned_windows_sdk():
    root = ET.parse(MSBUILD).getroot()
    props = {
        element.tag: element.text for group in root.findall("PropertyGroup") for element in group
    }
    assert props["UseWinUI"] == "true"
    assert props["WindowsPackageType"] == "None"
    assert props["WindowsAppSDKSelfContained"] == "true"
    packages = root.findall(".//PackageReference")
    assert any(
        item.get("Include") == "Microsoft.WindowsAppSDK" and item.get("Version") == "2.5.1"
        for item in packages
    )


def test_native_primary_views_use_windows_controls_not_a_browser():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    assert root.tag == XAML + "Window"
    assert root.find(".//" + XAML + "NavigationView") is not None
    assert root.find(".//" + XAML + "ListView") is not None
    assert not any("WebView" in node.tag or "Frame" in node.tag for node in root.iter())


def test_native_client_is_loopback_only_and_checks_instance_identity():
    content = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "127.0.0.1" in content
    assert "/desktop/instance" in content
    assert "instance_nonce" in content
    assert "AllowAutoRedirect = false" in content
    assert "UseProxy = false" in content
    assert "MaximumJsonBytes" in content
    assert "/v1/models" in content


def test_native_connection_hub_reuses_verified_backend_without_exporting_credentials():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    named = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    controls = {node.get(named): node.tag for node in root.iter() if node.get(named)}
    for name, kind in (
        ("ConnectionHubPanel", "ScrollViewer"),
        ("HubModelPicker", "ComboBox"),
        ("HubPythonExample", "TextBox"),
        ("HubCurlExample", "TextBox"),
        ("HubTestButton", "Button"),
        ("HubTestResults", "ListView"),
    ):
        assert controls[name] == XAML + kind
    for name in ("HubPythonExample", "HubCurlExample"):
        control = next(node for node in root.iter() if node.get(named) == name)
        assert control.get("IsReadOnly") == "True"
    client = (PROJECT / "ConnectionHubClient.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.ConnectionHub.cs").read_text(encoding="utf-8")
    assert "/internal/connection-hub" in client
    assert 'request.Headers.Add("X-OV-LLM-UI", "1")' in client
    assert "SessionNonce != session.SessionNonce" in client
    assert "SessionNonce != fresh.SessionNonce" in client
    assert "YOUR_INFERBRIDGE_API_KEY" in client
    assert "RunLocalApiSelfTestAsync" in client
    assert "Clipboard.SetContent(data)" in window
    assert "local API checks" in window
    assert "WebView" not in window


def test_native_chat_renders_text_and_code_without_html_or_browser_execution():
    content = (PROJECT / "MainWindow.ChatRendering.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "RichTextBlock" in content
    assert "RenderCodeBlock" in content
    assert "Copy code" in content
    assert '"Copy " + speaker + " message"' in content
    assert "IsReadOnly = true" in content
    assert "WebView" not in content
    assert "Process.Start" not in content
    assert "BuildChatContent" in window
    assert "preview: true" in window
    assert "ChatHistory.Items.Remove(assistantItem)" in window


def test_native_chat_uses_real_accessible_xaml_controls():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    named = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert named["ChatModelPicker"] == XAML + "ComboBox"
    assert named["ChatHistory"] == XAML + "ListView"
    assert named["PromptBox"] == XAML + "TextBox"
    assert named["SendButton"] == XAML + "Button"
    assert named["CancelButton"] == XAML + "Button"
    assert named["ApiKeyBox"] == XAML + "PasswordBox"
    assert named["LoadButton"] == XAML + "Button"
    assert named["UnloadButton"] == XAML + "Button"
    assert not any("WebView" in node.tag for node in root.iter())


def test_native_chat_toolbar_preserves_a_real_transcript_viewport():
    """Desktop controls must never push the transcript below the visible window."""
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    named = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    chat = next(node for node in root.iter() if node.get(named) == "ChatPanel")
    rows = chat.find(XAML + "Grid.RowDefinitions")
    assert rows is not None
    assert [row.get("Height") for row in rows] == ["Auto", "*", "Auto"]

    chat_header = next(node for node in chat if node.get("Grid.Row") == "0")
    assert chat_header.tag == XAML + "StackPanel"
    toolbar = next(node for node in chat_header if node.get(named) == "ChatToolbar")
    assert toolbar.tag == XAML + "Grid"
    assert toolbar.get("Grid.Row") is None
    assert toolbar.get("SizeChanged") == "ChatToolbar_SizeChanged"
    columns = toolbar.find(XAML + "Grid.ColumnDefinitions")
    assert columns is not None
    assert [column.get("Width") for column in columns] == [
        "Auto",
        "*",
        "Auto",
        "Auto",
        "Auto",
        "Auto",
    ]
    model = next(node for node in toolbar.iter() if node.get(named) == "ChatModelPicker")
    assert model.get("Grid.Column") == "1"
    assert model.get("Header") is None
    assert model.get("MinWidth") == "140"

    status = next(node for node in toolbar.iter() if node.get(named) == "ChatStatusButton")
    assert status.get("Grid.Column") == "2"
    status_icons = [node for node in status.iter() if node.tag == XAML + "FontIcon"]
    assert len(status_icons) == 1
    assert status_icons[0].get("Glyph") == chr(0xE946)
    assert status_icons[0].get("FontFamily") == "{StaticResource SymbolThemeFontFamily}"
    assert any(node.get(named) == "ChatInfo" for node in toolbar.iter())
    assert toolbar.find(".//" + XAML + "Button.Flyout/" + XAML + "Flyout") is not None
    assert not any(node.get(named) == "ChatInfo" for node in chat if node is not toolbar)

    for name in (
        "SavedChatsSearch",
        "SavedChatsPicker",
        "SaveHistoryToggle",
        "RenameSavedChatButton",
        "DeleteSavedChatButton",
    ):
        assert any(node.get(named) == name for node in toolbar.iter())
    # The history and status details live in flyouts, never in the growing chat column.
    assert not any(node.get(named) == "SavedChatsSearch" for node in chat if node is not toolbar)

    viewport = next(child for child in chat if child.get("Grid.Row") == "1")
    assert viewport.tag == XAML + "Grid"
    transcript = next(node for node in viewport if node.get(named) == "ChatHistory")
    empty = next(node for node in viewport if node.get(named) == "ChatEmptyState")
    assert transcript.tag == XAML + "ListView"
    assert transcript.get("MinHeight") is None
    assert empty.get("IsHitTestVisible") == "False"

    composer = next(child for child in chat if child.get("Grid.Row") == "2")
    assert composer.tag == XAML + "Grid"
    assert [row.get("Height") for row in composer.find(XAML + "Grid.RowDefinitions")] == [
        "Auto",
        "Auto",
        "Auto",
    ]
    prompt = next(node for node in composer if node.get(named) == "PromptBox")
    assert prompt.get("MaxHeight") == "112"
    assert prompt.get("Header") is None
    for name in (
        "SendButton",
        "CancelButton",
        "ChatProgress",
        "AttachImagesButton",
        "ClearImagesButton",
        "ChatAttachmentSummary",
    ):
        assert any(node.get(named) == name for node in composer.iter())


def test_native_chat_maintains_empty_state_and_status_visibility_without_changing_actions():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "private void UpdateChatEmptyState()" in window
    assert "ChatEmptyState.Visibility = ChatHistory.Items.Count == 0" in window
    assert window.count("UpdateChatEmptyState();") >= 5
    assert "ChatToolbarTitle.Visibility = compact ? Visibility.Collapsed" in window
    assert "ChatStatusText.Visibility = compact ? Visibility.Collapsed" in window
    assert "ChatStatusText.Text = severity switch" in window
    assert "ToolTipService.SetToolTip(ChatStatusButton" in window
    assert "FrameworkElementAutomationPeer.FromElement(ChatStatusButton)" in window
    assert 'InfoBarSeverity.Error => "Error: " + title' in window
    assert "NewChatButton.IsEnabled = !_chatBusy && !_quitting;" in window
    assert "private async void SendButton_Click" in window
    assert "ChatHistory.ScrollIntoView(assistantItem)" in window
    assert "SaveHistoryToggle.IsChecked == true" in window


def test_native_model_library_keeps_inventory_visible_and_actions_scrollable():
    """A short window must not allocate unlimited height to model controls."""
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    named = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    models = next(node for node in root.iter() if node.get(named) == "ModelsPanel")
    rows = models.find(XAML + "Grid.RowDefinitions")
    assert rows is not None
    assert [item.get("Height") for item in rows] == ["Auto", "*", "Auto"]
    inventory = next(node for node in models.iter() if node.get(named) == "ModelsList")
    assert inventory.tag == XAML + "ListView"
    assert inventory.get("Grid.Row") == "1"
    upper = next(node for node in models if node.tag == XAML + "ScrollViewer")
    assert 0 < int(upper.get("MaxHeight")) <= 160
    actions = next(node for node in models if node.get("Grid.Row") == "2")
    assert actions.tag == XAML + "ScrollViewer"
    assert 0 < int(actions.get("MaxHeight")) <= 220
    assert actions.get("HorizontalScrollBarVisibility") == "Disabled"
    buttons = {node.get(named) for node in actions.iter() if node.tag == XAML + "Button"}
    for control in (
        "DownloadButton",
        "LoadButton",
        "UnloadButton",
        "DeleteButton",
        "CancelPreparationButton",
        "RecoverButton",
    ):
        assert control in buttons
    # Buttons must no longer sit in a single nonwrapping horizontal row.
    main_actions = next(node for node in actions.iter() if node.tag == XAML + "Grid")
    assert any(node.get(named) == "DownloadButton" for node in main_actions.iter())
    assert main_actions.find(XAML + "Grid.ColumnDefinitions") is not None


def test_native_navigation_adapts_to_narrow_windows():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    navigation = root.find(".//" + XAML + "NavigationView")
    assert navigation is not None
    assert navigation.get("PaneDisplayMode") == "Auto"
    assert int(navigation.get("CompactModeThresholdWidth")) < int(
        navigation.get("ExpandedModeThresholdWidth")
    )
    assert any(
        node.get("Tag") == "operations" and node.get("Content") == "Operations"
        for node in navigation.iter()
    )


def test_native_detail_pages_do_not_scroll_horizontally_in_narrow_windows():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    named = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    for page in (
        "OverviewPanel",
        "DoctorPanel",
        "OperationsPanel",
        "BenchmarkPanel",
        "StoragePanel",
        "HuggingFacePanel",
        "SettingsPanel",
    ):
        panel = next(node for node in root.iter() if node.get(named) == page)
        assert panel.tag == XAML + "ScrollViewer"
        assert panel.get("HorizontalScrollBarVisibility") == "Disabled"


def test_native_chat_and_models_use_authoritative_bounded_backend_contracts():
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    for route in (
        "/desktop/instance",
        "/health/live",
        "/v1/models",
        "/v1/chat/completions",
        "/v1/models/load",
        "/v1/models/unload",
    ):
        assert route in client
    for safeguard in (
        "AllowAutoRedirect = false",
        "UseProxy = false",
        "Authorization",
        "CancellationTokenSource.CreateLinkedTokenSource",
        "MaximumChatBytes",
        "TimeSpan.FromSeconds(120)",
        "RequireVerifiedOriginAsync",
        "ready_to_load",
    ):
        assert safeguard in client
    assert "X-Desktop-Control" not in client
    assert "ApiKeyBox.Password" in window
    assert "CancelButton_Click" in window
    assert "NewChatButton_Click" in window
    assert "ChangeModelAsync" in window


def test_native_frontend_is_single_instance_per_installation_and_preserves_engine_owner():
    project = MSBUILD.read_text(encoding="utf-8")
    program = (PROJECT / "Program.cs").read_text(encoding="utf-8")
    app = (PROJECT / "App.xaml.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "DISABLE_XAML_GENERATED_MAIN" in project
    assert "InitializeComWrappers" in program
    # An async Main drops [STAThread]; WinUI must start on an STA thread.
    assert "[STAThread]\n    private static int Main(string[] args)" in program
    assert "async Task Main" not in program
    assert "FindOrRegisterForKey" in program
    assert "RedirectActivationToAsync" in program
    assert "AppContext.BaseDirectory" in program
    assert "Application.Start" in program
    assert "ActivateExistingWindow" in app
    assert "SetForegroundWindow" in app
    assert "new MainWindow()" in app
    assert "_pollTimer.Start()" in window
    assert "_pollTimer.Stop()" in window
    assert "_startupElapsed.Elapsed >= TimeSpan.FromSeconds(90)" in window
    assert "status.Connected" in window
    assert "EngineLauncher.Start()" in window
    assert "Process.Kill(" not in program + app + window


def test_native_status_polling_never_reinterprets_inventory_as_user_model_change():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "if (_updatingModelPicker) return;" in window
    assert "string? selected = SelectedModelId ?? _conversationModelId;" in window
    assert "_updatingModelPicker = true;" in window
    assert "_updatingModelPicker = false;" in window
    assert "ChatModelPicker.SelectedItem is null && _conversationModelId is null" in window
    assert "_conversationModelId = modelId;" in window


def test_native_backend_resolves_python_tray_data_roots_instead_of_store_only():
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    resolver = (PROJECT / "NativeDataPaths.cs").read_text(encoding="utf-8")
    builder = (ROOT / "scripts/build_release.ps1").read_text(encoding="utf-8")

    assert "NativeDataPaths.ResolveMetadataFile()" in client
    assert "GetCurrentPackageFullName" in resolver
    assert "portable.flag" in resolver
    assert "OpenVINOWindowsLLM" in resolver
    assert "INFERBRIDGE_NATIVE_DATA_DIR" in resolver
    assert "OV_LLM_DATA_DIR" in resolver
    assert "Path.IsPathFullyQualified(requested)" in resolver
    assert "InferBridge.Native.ContractTests.csproj" in builder


def test_native_generation_controls_use_winui_and_existing_chat_contract():
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    options = (PROJECT / "MainWindow.GenerationSettings.cs").read_text(encoding="utf-8")
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    for name in (
        "SettingsPanel",
        "MaxTokensBox",
        "TemperatureBox",
        "TopPBox",
        "ApplyGenerationSettingsButton",
        "ResetGenerationSettingsButton",
    ):
        assert f'x:Name="{name}"' in xaml
    assert 'Click="ApplyGenerationSettingsButton_Click"' in xaml
    assert 'Click="ResetGenerationSettingsButton_Click"' in xaml
    assert "GenerationOptions.FromNumbers" in options
    assert "CanUseGenerationOptions" in options
    assert "ResetGenerationSettings();" in window
    assert "send.Token, generation" in window
    assert "max_tokens = generation.MaxTokens" in client
    assert "temperature = generation.Temperature" in client
    assert "top_p = generation.TopP" in client


def test_native_chat_disallows_late_success_after_user_cancellation():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    response = window.index("string answer = await _backend.StreamChatAsync(")
    cancelled = window.index("_sendCancellation?.IsCancellationRequested != false", response)
    commit = window.index("_turns.AddRange(pending);", response)
    assert response < cancelled < commit
    assert "PromptBox.Text = string.Empty;" in window[commit:]


def test_windows_harness_executes_production_backend_http_contracts():
    project = ROOT / "windows-native" / "InferBridge.Native.ContractTests"
    csproj = (project / "InferBridge.Native.ContractTests.csproj").read_text(encoding="utf-8")
    entrypoint = (project / "Program.cs").read_text(encoding="utf-8")
    contracts = (project / "BackendContractTests.cs").read_text(encoding="utf-8")
    assert 'Include="../InferBridge.Native/BackendClient.cs"' in csproj
    assert "await BackendContractTests.RunAsync(root);" in entrypoint
    for requirement in (
        "stale nonce was accepted",
        "server-reported port mismatch was accepted",
        "HTTP redirect was treated as an engine",
        "oversized HTTP body was accepted",
        "unauthorized chat mutation",
        "cancelled chat did not end promptly",
    ):
        assert requirement in contracts


def test_native_window_is_production_safe_on_close_auth_throttle_and_high_dpi():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    manifest = (PROJECT / "app.manifest").read_text(encoding="utf-8")
    # Any late failure after close must be swallowed, not crash an async void handler.
    assert (
        "catch (OperationCanceledException) when (_closing.IsCancellationRequested)" not in window
    )
    for method in (
        "private async Task RefreshAsync",
        "private async Task ManageModelAsync",
        "private async void SendButton_Click",
        "private async Task ConfirmCloseAsync",
        "private async void WebUiButton_Click",
    ):
        body = window.split(method, 1)[1].split("\n    private ", 1)[0]
        assert "catch (Exception) when (_closing.IsCancellationRequested)" in body
    # Polling must not keep re-sending a rejected key into the engine's 429 throttle.
    assert "_rejectedApiKey != ApiKeyBox.Password" in window
    assert "EngineLauncher.IsBundled" in window
    assert "<ApplicationManifest>app.manifest</ApplicationManifest>" in MSBUILD.read_text("utf-8")
    assert "PerMonitorV2" in manifest
    assert "PreferredMinimumWidth = Math.Min(area.Width" in window
    assert "PreferredMinimumHeight = Math.Min(area.Height" in window
    assert "(int)(640 * scale)" in window
    assert "(int)(480 * scale)" in window


def test_native_model_library_selection_survives_status_poll():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    # The 8-second poll replaces ListViewItem instances. Preserve selection by
    # stable model ID rather than retaining a stale ListViewItem reference.
    captured = window.index("string? selectedLibraryId = SelectedLibraryModel?.Id;")
    cleared = window.index("ModelsList.Items.Clear();", captured)
    added = window.index("ModelsList.Items.Add(libraryItem);", cleared)
    restored = window.index("if (model.Id == (selectedLibraryId ?? recommended))", added)
    assert captured < cleared < added < restored
    assert "ModelsList.SelectedItem = libraryItem;" in window[restored:]
    assert (
        "UpdateChatButtons();"
        in window[restored : window.index("private async void LoadButton_Click", restored)]
    )


def test_native_close_prompt_distinguishes_window_close_from_whole_application_quit():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    intercept = window.split("AppWindow.Closing +=", 1)[1].split("Closed +=", 1)[0]
    assert "args.Cancel = true;" in intercept
    assert "_closePromptOpen" in intercept
    assert "DispatcherQueue.TryEnqueue" in intercept
    prompt = window.split("private async Task ConfirmCloseAsync", 1)[1].split("\n    private ", 1)[
        0
    ]
    assert 'PrimaryButtonText = "Close window only"' in prompt
    assert 'SecondaryButtonText = "Quit InferBridge"' in prompt
    assert 'CloseButtonText = "Cancel"' in prompt
    assert "keeps InferBridge running in the system tray" in prompt
    assert "web UI or other apps" in prompt
    assert "choice == ContentDialogResult.Secondary" in prompt
    assert "await _backend.QuitApplicationAsync(_closing.Token, _startRequested);" in prompt
    assert "Process.Kill" not in window


def test_native_chat_picker_loads_downloaded_models_without_enabling_chat_early():
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    selection = window.split("private async void ChatModelPicker_SelectionChanged", 1)[1].split(
        "\n    private ", 1
    )[0]
    assert "if (_updatingModelPicker) return;" in selection
    assert "SelectedChatModel is { CanLoad: true } model" in selection
    assert "await ManageModelAsync(ModelAction.Load, model, fromChat: true);" in selection
    assert "if (!model.AvailableInChat) continue;" in window
    assert 'SelectedChatModel?.Status == "loaded"' in window
    assert 'SelectedChatModel?.Status != "loaded"' in window
    assert "_chatLoadError" in window


def test_native_web_ui_is_discoverable_and_opens_only_the_verified_engine_url():
    xaml = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    assert any(node.get("Title") == "Web GUI (optional)" for node in xaml.iter())
    assert any(node.get("Content") == "Open web GUI" for node in xaml.iter())
    overview = next(
        node
        for node in xaml.iter()
        if node.get("{http://schemas.microsoft.com/winfx/2006/xaml}Name") == "OverviewPanel"
    )
    assert any(node.get("Title") == "Web GUI (optional)" for node in overview.iter())
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    handler = window.split("private async void WebUiButton_Click", 1)[1].split("\n    private ", 1)[
        0
    ]
    assert "await _backend.InspectAsync" in handler
    assert "if (!status.Connected)" in handler
    assert 'new ProcessStartInfo(status.Origin + "/")' in handler
    assert "UseShellExecute = true" in handler
    assert 'status.Origin + "/?"' not in handler


def test_native_model_library_selects_inference_device_and_forwards_to_backend():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    picker = next(
        node for node in root.iter() if node.get(xaml_ns + "Name") == "LibraryDevicePicker"
    )
    assert picker.tag == XAML + "ComboBox"
    assert [item.get("Tag") for item in picker] == ["", "NPU", "GPU", "CPU"]
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "SelectedInferenceDevice" in window
    assert "fromChat ? SelectedChatDevice : SelectedInferenceDevice" in window
    assert "LibraryDevicePicker.IsEnabled = idle && _devicesAvailable;" in window
    assert "new { model = model.Id, device }" in client
    assert "new { model = model.Id, load_after = true, device }" in client
    assert "Unsupported inference device." in client


def test_native_chat_device_switch_is_explicit_and_tracks_actual_hardware():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    ns = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    named = {node.get(ns): node for node in root.iter() if node.get(ns)}
    assert named["ChatDevicePicker"].tag == XAML + "ComboBox"
    assert named["ApplyChatDeviceButton"].get("IsEnabled") == "False"
    assert named["ApplyChatDeviceButton"].get("Click") == "ApplyChatDeviceButton_Click"
    assert named["ChatDeviceSummary"].get("TextWrapping") == "Wrap"
    assert named["ChatGenerationSummary"].get("TextTrimming") is None

    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    backend = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "ModelAction.SwitchDevice" in window
    assert "await ManageModelAsync(action, model, fromChat: true);" in window
    assert "SelectedChatDevice" in window
    assert "inventory.Suggestions" in window
    assert "model?.Device" in window
    assert "ChatDevicePicker.IsEnabled = _devicesAvailable" in window
    assert 'ReadString(item, "status")).Permits(action)' in backend
    assert "ModelAction.Load or ModelAction.SwitchDevice" in backend
    assert "IsSupportedInferenceDevice" in backend
    assert "Suggestions = suggestions" in backend


def test_native_device_discovery_shows_available_hardware():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    names = {node.get(ns + "Name") for node in root.iter()}
    assert {"ScanHardwareButton", "HardwareSummary", "LibraryDevicePicker"} <= names
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "GetDevicesAsync" in window
    assert "inventory.Available.Any" in window
    assert '"/v1/devices"' in client
    assert "RequireVerifiedOriginAsync" in client


def test_native_operations_and_model_deletion_are_native_and_guarded():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    named = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert named["OperationsPanel"] == XAML + "ScrollViewer"
    assert named["DoctorPanel"] == XAML + "ScrollViewer"
    assert named["DoctorRefreshButton"] == XAML + "Button"
    assert named["DoctorCopyButton"] == XAML + "Button"
    assert named["DoctorChecks"] == XAML + "ItemsControl"
    assert named["OperationsRefreshButton"] == XAML + "Button"
    assert named["DiagnosticsExportButton"] == XAML + "Button"
    assert named["QuickBenchmarkButton"] == XAML + "Button"
    assert named["DeleteButton"] == XAML + "Button"
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    backend = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert 'page == "operations"' in window
    assert 'page == "doctor"' in window
    assert "RunSystemDoctorAsync" in window
    assert "DoctorCopyButton_Click" in window
    assert "Clipboard.SetContent(package)" in window
    assert "GetDesktopOperationsAsync" in window
    assert "ExportDiagnosticsAsync" in window
    assert "RunQuickBenchmarkAsync" in window
    assert "QuickBenchmarkButton_Click" in window
    assert "await dialog.ShowAsync() != ContentDialogResult.Primary" in window
    assert "model?.CanDelete == true" in window
    assert "DeleteModelAsync" in window
    assert "if (!model.CanDelete)" in backend
    assert 'ReadString(entry, "status") == "ready_to_load"' in backend
    assert '"/v1/models/delete"' in backend
    assert '"/v1/desktop/operations/status"' in backend
    assert '"/v1/desktop/operations/diagnostics/export"' in backend
    assert '"/v1/benchmarks/run"' in backend
    assert "state.ActualDevice != confirmedDevice" in backend
    assert "max_tokens = 32" in backend
    assert "runs = 1" in backend
    assert "RequireVerifiedOriginAsync" in backend
    assert '"/v1/system/status"' in backend
    assert '"/v1/devices"' in backend
    assert "MaximumChatBytes" in backend
    assert "report" in backend
    assert "X-Desktop-Control" not in backend
    assert not any("WebView" in node.tag for node in root.iter())


def test_native_streaming_uses_verified_sse_and_rolls_back_failed_previews():
    backend = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    contracts = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "BackendContractTests.cs"
    ).read_text(encoding="utf-8")
    assert "stream = true" in backend
    assert 'MediaTypeWithQualityHeaderValue("text/event-stream")' in backend
    assert "HttpCompletionOption.ResponseHeadersRead" in backend
    assert "AllowAutoRedirect = false" in backend
    assert "UseProxy = false" in backend
    assert "RequireVerifiedOriginAsync" in backend
    assert "maximumLineBytes" in backend
    assert "maximumAnswerChars" in backend
    assert "if (!done || !finished)" in backend
    assert "limit.Token.ThrowIfCancellationRequested()" in backend
    assert "onPartial(answer.ToString())" in backend
    assert "string answer = await _backend.StreamChatAsync(" in window
    assert "DispatcherQueue.TryEnqueue" in window
    assert "if (!_closing.IsCancellationRequested && !committed)" in window
    assert "ChatHistory.Items.Remove(assistantItem)" in window
    assert "ChatHistory.Items.Remove(userItem)" in window
    assert "SSE progress callbacks are incorrect" in contracts
    assert "cancellation did not stop the active SSE request" in contracts


def test_native_saved_conversations_are_opt_in_and_atomic():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    names = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert names["SavedChatsPicker"] == XAML + "ComboBox"
    assert names["SaveHistoryToggle"] == XAML + "CheckBox"
    assert names["DeleteSavedChatButton"] == XAML + "Button"
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    store = (PROJECT / "NativeConversationStore.cs").read_text(encoding="utf-8")
    assert "NativeConversationStore.ForCurrentInstallation()" in window
    assert "SaveHistoryToggle.IsChecked == true" in window
    assert "TrySaveCurrentChat()" in window
    assert "SavedChatsPicker_SelectionChanged" in window
    assert "DeleteSavedChatButton_Click" in window
    assert "if (!_closing.IsCancellationRequested && !committed)" in window
    assert "File.Move(temporary, _path, overwrite: true)" in store
    assert "MaximumFileBytes" in store
    assert "MaximumConversations" in store
    assert "Invalid saved turn; original file preserved." in store
    assert "RejectReparsePoint" in store
    assert "Guid.TryParseExact" in store
    assert "native-conversations.json" in store
    assert "Password" not in store


def test_native_recovery_uses_xaml_and_verified_backend_action_contract():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    named = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert named["RecoverButton"] == XAML + "Button"
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "RecoverButton_Click" in window
    assert "GetModelRecoveryAsync" in window
    assert "ApplyModelRecoveryAsync" in window
    assert 'action is "restart_download" or "remove_incomplete_files"' in window
    assert "/v1/models/recovery/action" in client
    assert "selected.RecoveryId != current.RecoveryId" in client
    assert "RequireVerifiedOriginAsync" in client
    assert not any("WebView" in node.tag for node in root.iter())


def test_native_storage_manager_uses_winui_and_guarded_engine_cleanup():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    named = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert named["StoragePanel"] == XAML + "ScrollViewer"
    assert named["StorageRows"] == XAML + "StackPanel"
    assert named["StorageRefreshButton"] == XAML + "Button"
    assert any(node.get("Tag") == "storage" for node in root.iter())
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    for handler in (
        "StorageRefreshButton_Click",
        "StorageCleanupButton_Click",
        "RefreshStorageAsync",
    ):
        assert handler in window
    assert "selected.ReclaimableBytes" in window
    assert "GetStorageInventoryAsync" in client
    assert "CleanupStorageAsync" in client
    assert "RequireVerifiedOriginAsync" in client
    assert 'request.Headers.Add("X-OV-LLM-UI", "1")' in client
    assert "current.ReclaimableBytes != selected.ReclaimableBytes" in client
    assert "current.SizeBytes != selected.SizeBytes" in client
    assert not any("WebView" in node.tag for node in root.iter())


def test_native_huggingface_page_uses_real_controls_and_backend_credential_owner():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    names = {
        node.get(xaml_ns + "Name"): node.tag for node in root.iter() if node.get(xaml_ns + "Name")
    }
    assert names["HuggingFacePanel"] == XAML + "ScrollViewer"
    assert names["HfSearchBox"] == XAML + "TextBox"
    assert names["HfSearchResults"] == XAML + "StackPanel"
    assert not any("WebView" in node.tag for node in root.iter())

    client = (PROJECT / "HuggingFaceClient.cs").read_text(encoding="utf-8")
    native = (PROJECT / "MainWindow.HuggingFace.cs").read_text(encoding="utf-8")
    harness = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")
    assert '"/v1/huggingface/status"' in client
    assert '"/v1/huggingface/token"' in client
    assert '"/v1/models/search-hf?query="' in client
    assert "RequireVerifiedOriginAsync" in client
    assert "MaxLength = 503" in native
    assert "secretBox.Password = string.Empty;" in native
    assert "HuggingFaceContractTests.RunAsync(root)" in harness


def test_native_discovery_to_custom_download_remains_winui_and_engine_owned():
    client = (PROJECT / "HuggingFaceClient.cs").read_text(encoding="utf-8")
    view = (PROJECT / "MainWindow.HuggingFace.cs").read_text(encoding="utf-8")
    harness = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")

    assert '"/v1/models/download-custom"' in client
    assert "GetDevicesAsync(apiKey, cancellationToken)" in client
    assert 'origin + "/v1/models"' in client
    assert "trust_remote_code = false" in client
    assert "QueueHuggingFaceModelAsync" in client
    assert "HfPrepareModel_Click" in view
    assert "new ContentDialog" in view
    assert 'PrimaryButtonText = "Register and prepare"' in view
    assert "HuggingFaceDownloadContractTests.RunAsync(root)" in harness
    assert "WebView2" not in view


def test_native_model_preparation_is_operation_scoped_and_engine_authoritative():
    client = (PROJECT / "ModelPreparationClient.cs").read_text(encoding="utf-8")
    view = (PROJECT / "MainWindow.ModelPreparation.cs").read_text(encoding="utf-8")
    main = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    harness = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")

    assert '"/v1/models/status"' in client
    assert '"/v1/models/cancel"' in client
    assert "current.OperationId != selected.OperationId" in client
    assert "current.CanCancel" in client
    assert 'ReadString(result.RootElement, "operation_id")' in client
    assert "CancelModelPreparationAsync" in view
    assert "CancelPreparationButton_Click" in xaml
    assert "RefreshModelPreparationsAsync()" in main
    assert "ModelPreparationContractTests.RunAsync(root)" in harness
    assert "WebView2" not in view


def test_native_model_details_and_session_invalidation_contract():
    client = (PROJECT / "ModelPreparationClient.cs").read_text(encoding="utf-8")
    view = (PROJECT / "MainWindow.ModelPreparation.cs").read_text(encoding="utf-8")
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    tests = (
        ROOT
        / "windows-native"
        / "InferBridge.Native.ContractTests"
        / "ModelPreparationContractTests.cs"
    ).read_text(encoding="utf-8")

    assert "ParseLibraryDetails" in client
    assert "SourceRepository" in client
    assert "ConfiguredDevice" in client
    assert "RuntimeDevice" in client
    assert 'Budget(item, "max_context_len", 128, 262144)' in client
    assert "Not reported" in client
    assert "_modelPreparationEpoch++" in view
    assert "epoch != _modelPreparationEpoch" in view
    assert "ApiKeyBox.Password != requestedKey" in view
    assert 'x:Name="ModelDetailsSummary"' in xaml
    assert "ModelDetailsSummary.Text" in view
    assert "untrusted metadata reached the native model details" in tests


def _native_names() -> dict[str, str]:
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    named = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    return {node.get(named): node.tag for node in root.iter() if node.get(named)}


def test_native_chat_parity_controls_reuse_web_contracts():
    names = _native_names()
    for name, kind in (
        ("SystemPromptBox", "TextBox"),
        ("AttachImagesButton", "Button"),
        ("ClearImagesButton", "Button"),
        ("ExportChatMarkdownButton", "Button"),
        ("ExportChatJsonButton", "Button"),
        ("ImportChatJsonButton", "Button"),
    ):
        assert names[name] == XAML + kind
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    tools = (PROJECT / "MainWindow.ChatTools.cs").read_text(encoding="utf-8")
    markdown = (PROJECT / "ChatMarkdown.cs").read_text(encoding="utf-8")
    store = (PROJECT / "NativeConversationStore.cs").read_text(encoding="utf-8")
    # Images only after the engine's own supports_vision contract confirms support.
    assert '"/v1/models/status"' in client
    assert "vision.ValueKind == JsonValueKind.True" in client
    assert "await RequireVisionModelAsync(origin, modelId, apiKey, cancellationToken);" in client
    assert 'type = "image_url"' in client
    assert "GetModelSupportsVisionAsync" in tools
    # Markdown mirrors the browser subset and never produces markup or remote loads.
    for forbidden in ("Hyperlink", "Process.Start", "WebView", "BitmapImage"):
        assert forbidden not in markdown
    assert '"inferbridge-conversation"' in store


def test_native_maintenance_parity_uses_existing_protected_endpoints():
    names = _native_names()
    for name, kind in (
        ("NetworkAllowLanToggle", "ToggleSwitch"),
        ("NetworkCorsBox", "TextBox"),
        ("NetworkApiKeyBox", "PasswordBox"),
        ("NetworkApplyButton", "Button"),
        ("RestartEngineButton", "Button"),
        ("ModelHealthRows", "StackPanel"),
        ("ModelHealthRevalidateButton", "Button"),
        ("UpdateChecksToggle", "ToggleSwitch"),
        ("CheckUpdatesButton", "Button"),
    ):
        assert names[name] == XAML + kind
    hub = (PROJECT / "ConnectionHubClient.cs").read_text(encoding="utf-8")
    health = (PROJECT / "RuntimeHealthClient.cs").read_text(encoding="utf-8")
    release = (PROJECT / "ReleaseUpdateClient.cs").read_text(encoding="utf-8")
    view = (PROJECT / "MainWindow.Maintenance.cs").read_text(encoding="utf-8")
    assert '"/internal/desktop-network"' in hub
    assert hub.count('request.Headers.Add("X-OV-LLM-UI", "1");') >= 4
    assert "RequireVerifiedOriginAsync(apiKey, cancellationToken)" in hub
    assert '"/v1/desktop/operations/restart-server"' in hub
    assert '"/v1/runtime-health/action"' in health
    assert '"/v1/runtime-health/batch"' in health
    assert '"/desktop/release/settings"' in release
    assert "IsOfficialReleaseUrl" in release
    assert "Wildcard CORS allows browser scripts from any origin" in view
    assert "ConfirmAsync(" in view
    harness = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")
    assert "OperationsParityContractTests.RunAsync(root)" in harness
    assert "ChatRequestContractTests.RunAsync(root)" in harness
    assert "ChatMarkdownContractTests.Run()" in harness


def test_native_huggingface_dialog_offers_web_conversion_options():
    view = (PROJECT / "MainWindow.HuggingFace.cs").read_text(encoding="utf-8")
    client = (PROJECT / "HuggingFaceClient.cs").read_text(encoding="utf-8")
    for option in (
        "INT4 group size",
        "INT4 weight ratio",
        "Symmetric quantization",
        "Max output tokens",
    ):
        assert option in view
    assert 'Tag = "AUTO"' in view
    for field in (
        "group_size = selection.GroupSize",
        "ratio = selection.Ratio",
        "sym = selection.Symmetric",
    ):
        assert field in client
    # Remote code stays disabled in native even though the web offers an opt-in.
    assert "trust_remote_code = false" in client


def test_native_conversation_pin_archive_duplicate_match_browser_flags():
    names = _native_names()
    for name, kind in (
        ("PinSavedChatButton", "Button"),
        ("ArchiveSavedChatButton", "Button"),
        ("DuplicateSavedChatButton", "Button"),
        ("ShowArchivedChatsToggle", "CheckBox"),
    ):
        assert names[name] == XAML + kind
    store = (PROJECT / "NativeConversationStore.cs").read_text(encoding="utf-8")
    flags = (PROJECT / "MainWindow.ConversationFlags.cs").read_text(encoding="utf-8")
    tools = (PROJECT / "MainWindow.ChatTools.cs").read_text(encoding="utf-8")
    # Older history files without flags must still load.
    assert "bool Pinned = false, bool Archived = false" in store
    # Retention never drops pinned chats; a full pinned store refuses the save.
    assert "if (!chats[i].Pinned) chats.RemoveAt(i);" in store
    assert "HistoryFullException" in store
    # The browser's JSON export flags round-trip, and only a literal true counts.
    assert "value.ValueKind == JsonValueKind.True" in store
    assert 'Flag("pinned"), Flag("archived")' in store
    assert "flags?.Pinned == true, flags?.Archived == true" in tools
    assert "ApplyImportedFlags(imported)" in tools
    assert 'Title = title + " copy"' in store
    # Local-only: these actions never call the engine.
    assert "_backend." not in flags


def test_native_model_library_exchange_uses_web_endpoints_without_overwrite():
    names = _native_names()
    for name, kind in (
        ("ModelLibraryToolsButton", "DropDownButton"),
        ("AdoptLocalModelMenuItem", "MenuFlyoutItem"),
        ("ImportConvertedMenuItem", "MenuFlyoutItem"),
        ("ImportDefinitionsMenuItem", "MenuFlyoutItem"),
        ("ExportDefinitionsMenuItem", "MenuFlyoutItem"),
        ("ExportAllDefinitionsMenuItem", "MenuFlyoutItem"),
    ):
        assert names[name] == XAML + kind
    client = (PROJECT / "ModelLibraryClient.cs").read_text(encoding="utf-8")
    view = (PROJECT / "MainWindow.ModelLibrary.cs").read_text(encoding="utf-8")
    for endpoint in (
        '"/v1/model-library/export?include_all="',
        '"/v1/model-library/import-definitions"',
        '"/v1/model-library/unregistered-managed"',
        '"/v1/model-library/adopt-managed"',
        '"/v1/model-library/import-converted"',
    ):
        assert endpoint in client
    # Every call proves the engine identity and sends the user's API key.
    assert client.count("RequireVerifiedOriginAsync(apiKey, cancellationToken)") == 4
    # Never request replacement; never enable remote model code from native.
    assert "overwrite = true" not in client
    assert client.count("overwrite = false") == 2
    assert '"trust_remote_code"' in client
    assert "trust_remote_code = true" not in client
    assert "MaximumDefinitionFileBytes = 1_000_000" in client
    # Imports are confirmed and the library is re-read from the engine after changes.
    assert 'ConfirmAsync("Import model definitions?"' in view
    assert "await RefreshAsync()" in view
    harness = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")
    assert "ModelLibraryContractTests.RunAsync(root)" in harness


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[brace : index + 1]
    raise AssertionError("unterminated method: " + signature)


def test_native_status_poll_never_disables_or_rebuilds_open_selectors():
    # WinUI closes an open ComboBox dropdown (and DropDownButton flyout) when it
    # is disabled or its items are replaced; the 8 s poll did both every tick.
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    helper = (PROJECT / "PickerRefresh.cs").read_text(encoding="utf-8")
    assert "await RefreshAsync(background: true);" in window.split("_pollTimer.Start()", 1)[0]
    refresh = _method_body(window, "private async Task RefreshAsync(bool background = false)")
    head = refresh.split("try", 1)[0]
    assert "if (!background)" in head
    assert head.index("if (!background)") < head.index("UpdateChatButtons();")
    # Rebuild only on a real change, and defer it under an open dropdown.
    assert "PickerRefresh.Decide(" in refresh
    assert "ChatModelPicker.IsDropDownOpen" in refresh
    assert "case PickerUpdate.Defer: _pendingModels = status.Models;" in refresh
    assert "Items.Clear()" not in refresh.split("catch (Exception) when (!_closing", 1)[0]
    scan = _method_body(window, "private async Task ScanHardwareAsync()")
    assert "PickerRefresh.Decide(_shownDevices, devices," in scan
    assert "IsDropDownOpen" in scan
    assert "Items.Clear()" not in scan.split("catch (Exception) when (!_closing", 1)[0]
    assert 'DropDownClosed="ChatModelPicker_DropDownClosed"' in xaml
    assert xaml.count('DropDownClosed="DevicePicker_DropDownClosed"') == 2
    # Control enablement follows user-initiated refreshes only; action guards
    # still refuse to run while any refresh is in flight.
    for name in (
        "ChatModelPicker.IsEnabled",
        "ChatDevicePicker.IsEnabled",
        "SendButton.IsEnabled",
        "ApplyChatDeviceButton.IsEnabled",
    ):
        statement = window.split(name + " =", 1)[1].split(";", 1)[0]
        assert "RefreshLocksControls" in statement
        assert "_isRefreshing" not in statement
    assert "bool idle = !_isManaging && !_chatBusy && !RefreshLocksControls" in window
    assert "if (model is null || _isManaging || _chatBusy || _isRefreshing" in window
    assert "if (_chatBusy || _isManaging || _isRefreshing || modelId is null" in window
    library = (PROJECT / "MainWindow.ModelLibrary.cs").read_text(encoding="utf-8")
    assert "&& !RefreshLocksControls;" in library
    assert "if (!ModelLibraryToolsReady || _isRefreshing) return;" in library
    assert "refreshing && !background" in helper
    contracts = PROJECT.parent / "InferBridge.Native.ContractTests"
    assert "PickerRefresh.cs" in (contracts / "InferBridge.Native.ContractTests.csproj").read_text(
        encoding="utf-8"
    )
    assert "PickerRefreshContractTests.Run();" in (contracts / "Program.cs").read_text(
        encoding="utf-8"
    )


def test_native_polling_paths_do_not_reassign_items_source_unconditionally():
    for path in PROJECT.glob("*.cs"):
        source = path.read_text(encoding="utf-8")
        assert "ItemsSource =" not in source, path.name


def _presentation_tag(node: ET.Element) -> str:
    return node.tag.replace(XAML, "")


def test_native_shell_uses_grouped_navigation_with_icons_and_access_keys():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    navigation = root.find(".//" + XAML + "NavigationView")
    assert navigation is not None
    menu = navigation.find(XAML + "NavigationView.MenuItems")
    footer = navigation.find(XAML + "NavigationView.FooterMenuItems")
    assert menu is not None and footer is not None
    items = [node for node in menu if _presentation_tag(node) == "NavigationViewItem"]
    headers = [
        node.get("Content")
        for node in menu
        if _presentation_tag(node) == "NavigationViewItemHeader"
    ]
    assert headers == ["Models", "Engine"]
    # The first menu entry stays Chat: the window selects MenuItems[0] at startup.
    assert _presentation_tag(menu[0]) == "NavigationViewItem" and menu[0].get("Tag") == "chat"
    tags = [node.get("Tag") for node in items]
    assert tags == [
        "chat",
        "models",
        "huggingface",
        "overview",
        "benchmark",
        "doctor",
        "connection",
        "operations",
        "storage",
    ]
    settings = [node for node in footer if node.get("Tag") == "settings"]
    assert len(settings) == 1
    assert (
        settings[0].get("{http://schemas.microsoft.com/winfx/2006/xaml}Name")
        == "SettingsNavigationItem"
    )
    keys = []
    for item in [*items, *settings]:
        assert item.find(".//" + XAML + "FontIcon") is not None, item.get("Tag")
        keys.append(item.get("AccessKey"))
    assert all(keys) and len(set(keys)) == len(keys)
    pane_footer = navigation.find(XAML + "NavigationView.PaneFooter")
    assert pane_footer is not None
    assert any(
        node.get("{http://schemas.microsoft.com/winfx/2006/xaml}Name") == "ShellStatusPills"
        for node in pane_footer.iter()
    )
    shell = (PROJECT / "MainWindow.Shell.cs").read_text(encoding="utf-8")
    assert "private void NavigateTo(string tag)" in shell
    assert "FooterMenuItems" in shell
    # Pills are rebuilt only when their content changes, never touching pickers.
    assert "signature == _shellStatusSignature" in shell
    assert "Picker" not in shell


def test_native_xaml_uses_theme_resources_not_literal_colors():
    import re

    window = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    assert not re.search(r'"#[0-9A-Fa-f]{3,8}"', window)
    for color in ("Red", "Green", "Blue", "White", "Black", "Gray", "Orange", "Yellow"):
        assert f'="{color}"' not in window
    app = ET.parse(PROJECT / "App.xaml").getroot()
    themes = app.find(".//" + XAML + "ResourceDictionary.ThemeDictionaries")
    assert themes is not None
    keys = {node.get("{http://schemas.microsoft.com/winfx/2006/xaml}Key") for node in themes}
    assert keys == {"Light", "Dark", "HighContrast"}
    # Literal colors live only inside the theme dictionaries.
    app_text = (PROJECT / "App.xaml").read_text(encoding="utf-8")
    theme_block = app_text.split("<ResourceDictionary.ThemeDictionaries>", 1)[1].split(
        "</ResourceDictionary.ThemeDictionaries>", 1
    )[0]
    outside = app_text.replace(theme_block, "")
    assert not re.search(r"#[0-9A-Fa-f]{6,8}", outside)
    high_contrast = next(
        node
        for node in themes
        if node.get("{http://schemas.microsoft.com/winfx/2006/xaml}Key") == "HighContrast"
    )
    assert "#" not in ET.tostring(high_contrast, encoding="unicode")
    for path in PROJECT.glob("*.cs"):
        source = path.read_text(encoding="utf-8")
        assert "Color.FromArgb" not in source, path.name
        assert "Colors." not in source, path.name


def test_native_charts_are_in_house_accessible_and_theme_aware():
    charts = (PROJECT / "ChartControls.cs").read_text(encoding="utf-8")
    math = (PROJECT / "ChartMath.cs").read_text(encoding="utf-8")
    for control in ("class BarChart", "class Sparkline", "class RingMeter"):
        assert control in charts
    assert "AutomationProperties.SetName(this, name)" in charts
    assert "AutomationProperties.SetHelpText(this, help)" in charts
    assert "IsTabStop = true" in charts
    assert "ActualThemeChanged += (_, _) => Render();" in charts
    assert "ChartMath.Describe(" in charts and "ChartMath.DescribeTrend(" in charts
    assert "using Microsoft.UI" not in math  # Pure math, compiled into contract tests.
    assert "NotReported" in math
    project = (PROJECT / "InferBridge.Native.csproj").read_text(encoding="utf-8")
    for package in ("LiveCharts", "OxyPlot", "ScottPlot", "Syncfusion", "Telerik", "WinUIEx"):
        assert package not in project
    contracts = PROJECT.parent / "InferBridge.Native.ContractTests"
    assert "ChartMath.cs" in (contracts / "InferBridge.Native.ContractTests.csproj").read_text(
        encoding="utf-8"
    )
    assert "ChartMathContractTests.Run();" in (contracts / "Program.cs").read_text(encoding="utf-8")
    layout = (PROJECT / "AdaptiveLayout.cs").read_text(encoding="utf-8")
    assert "ChartMath.Columns(" in layout
