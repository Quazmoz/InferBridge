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
    workflow = (ROOT / ".github/workflows/native-windows.yml").read_text(encoding="utf-8")

    assert "NativeDataPaths.ResolveMetadataFile()" in client
    assert "GetCurrentPackageFullName" in resolver
    assert "portable.flag" in resolver
    assert "OpenVINOWindowsLLM" in resolver
    assert "INFERBRIDGE_NATIVE_DATA_DIR" in resolver
    assert "OV_LLM_DATA_DIR" in resolver
    assert "Path.IsPathFullyQualified(requested)" in resolver
    assert "InferBridge.Native.ContractTests.csproj" in workflow


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
    assert "await ManageModelAsync(ModelAction.Load, model);" in selection
    assert "if (!model.AvailableInChat) continue;" in window
    assert 'SelectedChatModel?.Status == "loaded"' in window
    assert 'SelectedChatModel?.Status != "loaded"' in window
    assert "_chatLoadError" in window


def test_native_web_ui_is_discoverable_and_opens_only_the_verified_engine_url():
    xaml = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    assert any(node.get("Title") == "More features in the web UI" for node in xaml.iter())
    assert any(node.get("Content") == "Open web UI" for node in xaml.iter())
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
    assert "_closing.Token, SelectedInferenceDevice" in window
    assert "LibraryDevicePicker.IsEnabled = idle && _devicesAvailable;" in window
    assert "new { model = model.Id, device }" in client
    assert "new { model = model.Id, load_after = true, device }" in client
    assert "Unsupported inference device." in client


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
    assert named["OperationsRefreshButton"] == XAML + "Button"
    assert named["DiagnosticsExportButton"] == XAML + "Button"
    assert named["DeleteButton"] == XAML + "Button"
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    backend = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert 'page == "operations"' in window
    assert "GetDesktopOperationsAsync" in window
    assert "ExportDiagnosticsAsync" in window
    assert "await dialog.ShowAsync() != ContentDialogResult.Primary" in window
    assert "model?.CanDelete == true" in window
    assert "DeleteModelAsync" in window
    assert "if (!model.CanDelete)" in backend
    assert 'ReadString(entry, "status") == "ready_to_load"' in backend
    assert '"/v1/models/delete"' in backend
    assert '"/v1/desktop/operations/status"' in backend
    assert '"/v1/desktop/operations/diagnostics/export"' in backend
    assert "RequireVerifiedOriginAsync" in backend
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
