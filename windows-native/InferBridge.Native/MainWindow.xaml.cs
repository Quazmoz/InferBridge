using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;
using WinRT.Interop;

namespace InferBridge.Native;

public sealed partial class MainWindow : Window
{
    private readonly BackendClient _backend = new();
    private readonly CancellationTokenSource _closing = new();
    private readonly DispatcherTimer _pollTimer = new() { Interval = TimeSpan.FromSeconds(8) };
    private readonly Stopwatch _startupElapsed = new();
    private readonly List<ChatTurn> _turns = new();
    private IReadOnlyList<ModelInfo> _shownModels = Array.Empty<ModelInfo>();
    private CancellationTokenSource? _sendCancellation;
    private bool _isRefreshing;
    private bool _updatingModelPicker;
    private bool _isManaging;
    private bool _chatBusy;
    private bool _startRequested;
    private string? _conversationModelId;
    private string? _rejectedApiKey;
    private ModelRecommendation? _recommendation;
    private bool _recommendationRequested;
    private bool _allowClose;
    private bool _closePromptOpen;
    private bool _quitting;
    private bool _engineConnected;
    private bool _openingWebUi;
    private bool _scanningDevices;
    private bool _operationsBusy;
    private bool _operationsLoaded;
    private bool _devicesScanned;
    private bool _devicesAvailable;
    private string? _chatLoadError;

    public MainWindow()
    {
        InitializeComponent();
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "InferBridge.ico"));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = 640;
            presenter.PreferredMinimumHeight = 480;
        }
        // The OS default size clips the library actions; open at a comfortable
        // size for this monitor's DPI, centered within its work area.
        RectInt32 area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        double scale = GetDpiForWindow(WindowNative.GetWindowHandle(this)) / 96.0;
        int width = Math.Min((int)(1100 * scale), area.Width);
        int height = Math.Min((int)(760 * scale), area.Height);
        AppWindow.MoveAndResize(new RectInt32(
            area.X + (area.Width - width) / 2, area.Y + (area.Height - height) / 2, width, height));
        MainNavigation.SelectedItem = MainNavigation.MenuItems[0];
        _pollTimer.Tick += async (_, _) =>
        {
            // Polling updates presentation only. The Python tray owns server
            // startup, crash recovery, and model lifecycle transitions.
            // Re-sending a rejected key every tick trips the engine's invalid-key
            // throttle (HTTP 429); wait for a new key or an explicit refresh.
            if (!_closing.IsCancellationRequested && _rejectedApiKey != ApiKeyBox.Password)
                await RefreshAsync();
        };
        _pollTimer.Start();
        AppWindow.Closing += (_, args) =>
        {
            if (_allowClose) return;
            args.Cancel = true;
            if (_closePromptOpen) return;
            _closePromptOpen = true;
            // Return from Closing before showing an asynchronous XAML dialog.
            if (!DispatcherQueue.TryEnqueue(async () => await ConfirmCloseAsync()))
                _closePromptOpen = false;
        };
        Closed += (_, _) =>
        {
            _pollTimer.Stop();
            _closing.Cancel();
            _sendCancellation?.Cancel();
            _backend.Dispose();
        };
        _ = ConnectOrStartAsync();
    }

    private async Task ConfirmCloseAsync()
    {
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Close this window or quit InferBridge?",
                Content = "Close window only keeps InferBridge running in the system tray. Your local engine, downloads, and web UI stay available.\n\nQuit InferBridge stops the engine and tray too. Active downloads and AI requests, including those from the web UI or other apps, will stop.",
                PrimaryButtonText = "Close window only",
                SecondaryButtonText = "Quit InferBridge",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            var choice = await dialog.ShowAsync();
            if (choice == ContentDialogResult.None) return;
            if (choice == ContentDialogResult.Secondary)
            {
                _quitting = true;
                MainNavigation.IsEnabled = false;
                _sendCancellation?.Cancel();
                UpdateChatButtons();
                ShowChatInfo("Quitting InferBridge", "Waiting for the local engine and tray to stop…", InfoBarSeverity.Informational);
                ShowModelsInfo("Quitting InferBridge. Waiting for the local engine and tray to stop…", InfoBarSeverity.Informational);
                ConnectionInfo.Message = "Quitting InferBridge. Waiting for the local engine and tray to stop…";
                await _backend.QuitApplicationAsync(_closing.Token, _startRequested);
            }
            // Let the dialog finish tearing down before closing its XAML host.
            _allowClose = true;
            DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
            {
                Close();
            });
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            const string message = "Could not confirm that InferBridge quit. Check the tray and retry, or choose Close window only.";
            ShowChatInfo("Window kept open", message, InfoBarSeverity.Warning);
            ShowModelsInfo(message, InfoBarSeverity.Warning);
            ConnectionInfo.Message = message;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _closePromptOpen = false;
            _quitting = false;
            if (!_closing.IsCancellationRequested)
            {
                MainNavigation.IsEnabled = true;
                UpdateChatButtons();
            }
        }
    }

    private async void WebUiButton_Click(object sender, RoutedEventArgs args)
    {
        if (_openingWebUi || _quitting || _closing.IsCancellationRequested) return;
        _openingWebUi = true;
        WebUiButton.IsEnabled = false;
        try
        {
            var status = await _backend.InspectAsync(_closing.Token, ApiKeyBox.Password);
            if (!status.Connected) throw new IOException("No verified local engine.");
            if (_quitting || _closing.IsCancellationRequested) return;
            // Only the verified literal loopback origin; keys never enter the URL.
            using var browser = Process.Start(new ProcessStartInfo(status.Origin + "/") { UseShellExecute = true });
            WebUiInfo.Severity = InfoBarSeverity.Informational;
            WebUiInfo.Message = "Web UI opened in your browser. Advanced settings, benchmarks, and more are available there.";
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            WebUiInfo.Severity = InfoBarSeverity.Warning;
            WebUiInfo.Message = "Could not open the web UI. Check the engine and your default browser, then retry.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _openingWebUi = false;
            if (!_closing.IsCancellationRequested) UpdateChatButtons();
        }
    }

    private async Task ConnectOrStartAsync()
    {
        await RefreshAsync();
        // A Start-menu launch of the combined package starts its engine without a
        // second click. The Python tray's instance lock still arbitrates ownership.
        if (!_quitting && !_allowClose && StartButton.IsEnabled && EngineLauncher.IsBundled)
            StartButton_Click(StartButton, new RoutedEventArgs());
    }

    private string? SelectedModelId =>
        (ChatModelPicker.SelectedItem as ComboBoxItem)?.Tag as string;

    private ModelInfo? SelectedChatModel => _shownModels.FirstOrDefault(model => model.Id == SelectedModelId);

    private ModelInfo? SelectedLibraryModel =>
        (ModelsList.SelectedItem as ListViewItem)?.Tag as ModelInfo;

    private void MainNavigation_SelectionChanged(
        NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        string? page = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString();
        OverviewPanel.Visibility = page == "overview" ? Visibility.Visible : Visibility.Collapsed;
        ModelsPanel.Visibility = page == "models" ? Visibility.Visible : Visibility.Collapsed;
        ChatPanel.Visibility = page == "chat" ? Visibility.Visible : Visibility.Collapsed;
        OperationsPanel.Visibility = page == "operations" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "operations" && !_operationsLoaded)
            _ = RefreshOperationsAsync();
    }

    private async void OperationsRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshOperationsAsync();

    private async Task RefreshOperationsAsync()
    {
        if (_operationsBusy || _closing.IsCancellationRequested || _quitting) return;
        _operationsBusy = true;
        OperationsProgress.IsActive = true;
        OperationsRefreshButton.IsEnabled = false;
        DiagnosticsExportButton.IsEnabled = false;
        try
        {
            DesktopOperationsInfo state = await _backend.GetDesktopOperationsAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _operationsLoaded = true;
            OperationsInfo.Severity = state.ControllerAvailable
                ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            OperationsInfo.Message = state.ControllerAvailable
                ? "Verified local desktop controller and engine."
                : "Local engine is available, but the tray controller is unavailable.";
            OperationsDetails.Text =
                "InferBridge " + state.Version +
                "\\nInstallation: " + state.InstallationMode +
                "\\nServer: " + state.ServerStatus + " · port " + state.ServerPort +
                "\\nTray controller: " + (state.ControllerAvailable ? "Available" : "Unavailable") +
                "\\nStart with Windows: " + (state.StartWithWindows ? "Enabled" : "Disabled") +
                "\\nInference mode: " + (state.Mock ? "Mock (no hardware inference)" : "Hardware backend");
            OperationsBenchmarks.Text = state.BenchmarkRunning
                ? "A benchmark is currently running. Refresh to check its result."
                : state.LatestBenchmark;
            DiagnosticsResult.Text = "Last diagnostics export: " + state.LastDiagnosticsExport;
            DiagnosticsExportButton.IsEnabled = true;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _operationsLoaded = false;
            OperationsInfo.Severity = InfoBarSeverity.Warning;
            OperationsInfo.Message = "Could not read desktop operations: " + SafeError(error);
            OperationsDetails.Text = "Reconnect to the local engine, then refresh.";
            OperationsBenchmarks.Text = "Unavailable until the engine responds.";
            DiagnosticsExportButton.IsEnabled = false;
        }
        finally
        {
            _operationsBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                OperationsProgress.IsActive = false;
                OperationsRefreshButton.IsEnabled = true;
            }
        }
    }

    private async void DiagnosticsExportButton_Click(object sender, RoutedEventArgs args)
    {
        if (_operationsBusy || _closing.IsCancellationRequested || _quitting) return;
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Create a sanitized diagnostics ZIP?",
                Content = "The engine saves a support archive locally. It includes sanitized status, configuration, model and hardware details, and logs. Prompts, conversations, keys, tokens and model files are excluded. Review the ZIP before sharing.",
                PrimaryButtonText = "Create ZIP",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            _operationsBusy = true;
            DiagnosticsExportButton.IsEnabled = false;
            OperationsRefreshButton.IsEnabled = false;
            OperationsProgress.IsActive = true;
            DiagnosticsResult.Text = "Creating the local diagnostics archive…";
            DiagnosticsExportInfo created = await _backend.ExportDiagnosticsAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            DiagnosticsResult.Text = "Created " + created.Filename +
                ". Review the local ZIP before attaching it to a support request." +
                (created.CollectionErrors.Count > 0
                    ? " Some diagnostic categories could not be collected (" +
                      created.CollectionErrors.Count + ")." : "");
            OperationsInfo.Severity = InfoBarSeverity.Success;
            OperationsInfo.Message = "Diagnostics archive created on this computer.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            DiagnosticsResult.Text = "Export failed: " + SafeError(error);
            OperationsInfo.Severity = InfoBarSeverity.Error;
            OperationsInfo.Message = "No successful diagnostics export was confirmed.";
        }
        finally
        {
            _operationsBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                OperationsProgress.IsActive = false;
                OperationsRefreshButton.IsEnabled = true;
                DiagnosticsExportButton.IsEnabled = _operationsLoaded;
            }
        }
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshAsync();

    private async void StartButton_Click(object sender, RoutedEventArgs args)
    {
        if (_startRequested || _quitting || _allowClose || _closing.IsCancellationRequested) return;
        var result = EngineLauncher.Start();
        ConnectionInfo.Severity = result.Started
            ? InfoBarSeverity.Informational : InfoBarSeverity.Warning;
        ConnectionInfo.Message = result.Message;
        ConnectionDetails.Text = result.Message;
        if (!result.Started) return;
        _startRequested = true;
        _startupElapsed.Restart();
        StartButton.IsEnabled = false;
        // Only successful nonce + liveness verification clears the pending launch.
        await RefreshAsync();
    }

    private async void ChatModelPicker_SelectionChanged(
        object sender, SelectionChangedEventArgs args)
    {
        // Inventory polling is not an intentional conversation/model change.
        if (_updatingModelPicker) return;
        _chatLoadError = null;
        string? modelId = SelectedModelId;
        if (modelId is not null && _conversationModelId != modelId)
        {
            _turns.Clear();
            ChatHistory.Items.Clear();
            _conversationModelId = modelId;
        }
        UpdateChatButtons();
        if (SelectedChatModel is { CanLoad: true } model)
            await ManageModelAsync(ModelAction.Load, model);
    }

    private void ModelsList_SelectionChanged(
        object sender, SelectionChangedEventArgs args) => UpdateModelButtons();

    private string? SelectedInferenceDevice =>
        (LibraryDevicePicker.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } device
            ? device : null;

    private async void ScanHardwareButton_Click(object sender, RoutedEventArgs args) =>
        await ScanHardwareAsync();

    private async Task ScanHardwareAsync()
    {
        if (_scanningDevices || !_engineConnected || _closing.IsCancellationRequested || _quitting)
            return;
        _scanningDevices = true;
        ScanHardwareButton.IsEnabled = false;
        try
        {
            DeviceInventory inventory = await _backend.GetDevicesAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            string? selected = SelectedInferenceDevice;
            var options = new[] { "NPU", "GPU", "CPU" };
            var supported = options.Where(device => inventory.Mock ||
                inventory.Available.Any(found => found == device ||
                    found.StartsWith(device + ".", StringComparison.Ordinal))).ToArray();
            LibraryDevicePicker.Items.Clear();
            LibraryDevicePicker.Items.Add(new ComboBoxItem
            {
                Content = "Automatic (engine default)", Tag = string.Empty,
            });
            foreach (string device in supported)
                LibraryDevicePicker.Items.Add(new ComboBoxItem { Content = device, Tag = device });
            LibraryDevicePicker.SelectedItem = LibraryDevicePicker.Items
                .Cast<ComboBoxItem>().FirstOrDefault(item => (string?)item.Tag == selected)
                ?? (ComboBoxItem)LibraryDevicePicker.Items[0];
            _devicesAvailable = true;
            _devicesScanned = true;
            string loadedSummary = await _backend.GetRuntimeSummaryAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            HardwareSummary.Text = loadedSummary + "\nEngine default: " + inventory.Default
                + ". Detected devices: "
                + (inventory.Available.Count > 0
                    ? string.Join(", ", inventory.Available) : "none reported")
                + (inventory.Mock ? " (mock inference mode)" : "")
                + ". Only detected targets are offered; compatibility also depends on the model.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            _devicesAvailable = false;
            _devicesScanned = false;
            HardwareSummary.Text = "Hardware scan unavailable. Retry to select a verified target.";
            LibraryDevicePicker.Items.Clear();
            LibraryDevicePicker.Items.Add(new ComboBoxItem
            {
                Content = "Automatic (engine default)", Tag = string.Empty,
            });
            LibraryDevicePicker.SelectedIndex = 0;
        }
        finally
        {
            _scanningDevices = false;
            if (!_closing.IsCancellationRequested) UpdateChatButtons();
        }
    }

    private void UpdateModelButtons()
    {
        var model = SelectedLibraryModel;
        bool idle = !_isManaging && !_chatBusy && !_isRefreshing && !_quitting;
        LoadButton.IsEnabled = idle && model?.CanLoad == true;
        UnloadButton.IsEnabled = idle && model?.CanUnload == true;
        DeleteButton.IsEnabled = idle && model?.CanDelete == true;
        DownloadButton.IsEnabled = idle && model?.CanDownload == true;
        LibraryDevicePicker.IsEnabled = idle && _devicesAvailable;
    }

    private void UpdateChatButtons()
    {
        SendButton.IsEnabled = !_chatBusy && !_isManaging && !_isRefreshing && !_quitting
            && SelectedChatModel?.Status == "loaded";
        CancelButton.IsEnabled = _chatBusy;
        ChatModelPicker.IsEnabled = !_chatBusy && !_isManaging && !_isRefreshing && !_quitting;
        WebUiButton.IsEnabled = _engineConnected && !_quitting && !_openingWebUi;
        ScanHardwareButton.IsEnabled = _engineConnected && !_quitting && !_scanningDevices;
        PromptBox.IsEnabled = !_chatBusy;
        ChatProgress.IsActive = _chatBusy;
        UpdateModelButtons();
    }

    private async Task RefreshAsync()
    {
        if (_isRefreshing || _isManaging || _chatBusy || _quitting || _allowClose || _closing.IsCancellationRequested)
            return;
        _isRefreshing = true;
        UpdateChatButtons();
        RefreshButton.IsEnabled = false;
        ConnectionProgress.IsActive = true;
        try
        {
            string apiKey = ApiKeyBox.Password;
            var status = await _backend.InspectAsync(_closing.Token, apiKey);
            _engineConnected = status.Connected;
            if (!status.Connected || status.RequiresAuthentication)
            {
                _devicesScanned = false;
                _devicesAvailable = false;
            }
            _rejectedApiKey = status.RequiresAuthentication ? apiKey : null;
            ConnectionInfo.Severity = status.Connected
                ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            ConnectionInfo.Message = status.Message;
            ConnectionDetails.Text = status.Connected
                ? "Verified loopback endpoint: " + status.Origin
                : "The client will not attach to an unverified engine process.";
            if (status.Connected)
            {
                _startRequested = false;
                _startupElapsed.Reset();
            }
            else if (_startRequested && _startupElapsed.Elapsed >= TimeSpan.FromSeconds(90))
            {
                _startRequested = false;
                ConnectionInfo.Message =
                    "Engine startup could not be verified. Check the Python tray and retry.";
            }
            else if (_startRequested)
            {
                ConnectionInfo.Message = "Starting the local engine. The first start can take a minute…";
            }
            StartButton.IsEnabled = !status.Connected && !_startRequested;

            // Rebuilding ComboBox items raises SelectionChanged. Suppress it so
            // polling, reconnects and temporary model absence keep the transcript.
            string? selected = SelectedModelId ?? _conversationModelId;
            string? selectedLibraryId = SelectedLibraryModel?.Id;
            // Rebuild only on a real inventory change: replacing items every poll
            // steals keyboard and screen-reader focus from the lists.
            if (!status.Models.SequenceEqual(_shownModels))
            {
                _shownModels = status.Models;
                _updatingModelPicker = true;
                try
                {
                    ChatModelPicker.Items.Clear();
                    ModelsList.Items.Clear();
                    // The engine's recommendation leads the library and is preselected.
                    string? recommended = _recommendation?.Id;
                    foreach (ModelInfo model in status.Models.OrderBy(m => m.Id != recommended))
                    {
                        var libraryItem = new ListViewItem
                        {
                            Content = (model.Id == recommended ? "Recommended  ·  " : string.Empty)
                                + model.Name + "  ·  " + model.StatusLabel
                                + (model.Name.Contains(model.Id, StringComparison.Ordinal)
                                    ? string.Empty : "  (" + model.Id + ")"),
                            Tag = model,
                        };
                        ModelsList.Items.Add(libraryItem);
                        if (model.Id == (selectedLibraryId ?? recommended))
                            ModelsList.SelectedItem = libraryItem;
                        if (!model.AvailableInChat) continue;
                        var option = new ComboBoxItem
                        {
                            Content = model.Name + "  ·  " + model.StatusLabel,
                            Tag = model.Id,
                            IsEnabled = model.CanLoad || model.CanUnload,
                        };
                        ChatModelPicker.Items.Add(option);
                        if (model.Id == selected)
                            ChatModelPicker.SelectedItem = option;
                    }
                    // Never silently move an existing conversation to a different model.
                    // When its model returns after a restart, it is selected again.
                    if (ChatModelPicker.SelectedItem is null && _conversationModelId is null
                        && ChatModelPicker.Items.Count > 0)
                        ChatModelPicker.SelectedItem = ChatModelPicker.Items.Cast<ComboBoxItem>()
                            .FirstOrDefault(option => status.Models.Any(model => model.Id == (option.Tag as string) && model.CanUnload));
                }
                finally
                {
                    _updatingModelPicker = false;
                }
            }

            ModelsSummary.Text = status.RequiresAuthentication
                ? "Authentication required. Enter your API key on the Engine screen."
                : status.Connected
                    ? status.Models.Count + " models. Select one to download, load or unload it. Downloads continue if you close this window."
                        + (_recommendation is { Reason.Length: > 0 } advice
                            ? "\nRecommended for this PC: " + advice.Reason : string.Empty)
                    : "Start or reconnect to the local engine.";
            if (status.Connected && !status.RequiresAuthentication && !_devicesScanned)
            {
                _devicesScanned = true;
                _ = ScanHardwareAsync();
            }
            if (status.Connected && !status.RequiresAuthentication && !_recommendationRequested)
            {
                _recommendationRequested = true;
                _ = LoadRecommendationAsync();
            }

            if (SelectedChatModel?.Status == "loaded") _chatLoadError = null;
            if (!status.Connected || status.RequiresAuthentication)
                ShowChatInfo("Engine unavailable", ConnectionInfo.Message, InfoBarSeverity.Warning);
            else if (_chatLoadError is not null)
                ShowChatInfo("Could not load model", _chatLoadError, InfoBarSeverity.Error);
            else if (ChatModelPicker.Items.Count == 0)
                ShowChatInfo("No downloaded models",
                    "Open Model library, select a model, then choose Download and load (or Load if it is already downloaded).",
                    InfoBarSeverity.Informational);
            else if (SelectedChatModel?.Status == "loaded")
                ShowChatInfo("Ready", "Send a message, or select another downloaded model to load it. Ctrl+Enter sends.",
                    InfoBarSeverity.Success);
            else if (SelectedChatModel?.Status is "loading" or "queued")
                ShowChatInfo("Loading model", "The selected model is loading. Chat becomes available when it is ready.",
                    InfoBarSeverity.Informational);
            else
                ShowChatInfo("Choose a model", "Select a downloaded model above to load it and start chatting.",
                    InfoBarSeverity.Informational);
        }
        catch (Exception) when (_closing.IsCancellationRequested)
        {
            // Closing the window cancels requests, not the authoritative tray.
            // Late failures (e.g. the disposed client) must not crash on exit.
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            ConnectionInfo.Severity = InfoBarSeverity.Error;
            _engineConnected = false;
            ConnectionInfo.Message = "Could not display local engine status.";
            ModelsSummary.Text = "Refresh after checking the engine.";
            ChatModelPicker.Items.Clear();
            _shownModels = Array.Empty<ModelInfo>();
            ShowChatInfo("Connection error", "Check the engine and refresh.",
                InfoBarSeverity.Error);
        }
        finally
        {
            if (!_closing.IsCancellationRequested)
            {
                _isRefreshing = false;
                RefreshButton.IsEnabled = true;
                ConnectionProgress.IsActive = false;
                UpdateChatButtons();
            }
        }
    }

    private async Task LoadRecommendationAsync()
    {
        try
        {
            _recommendation = await _backend.RecommendModelAsync(ApiKeyBox.Password, _closing.Token);
        }
        catch (Exception)
        {
            // Advice only: the full library stays usable without it.
            return;
        }
        if (_recommendation is null || _closing.IsCancellationRequested) return;
        _shownModels = Array.Empty<ModelInfo>(); // rebuild the list with the recommendation
        await RefreshAsync();
    }

    private async void LoadButton_Click(object sender, RoutedEventArgs args) =>
        await ManageModelAsync(ModelAction.Load);

    private async void UnloadButton_Click(object sender, RoutedEventArgs args) =>
        await ManageModelAsync(ModelAction.Unload);

    private async void DeleteButton_Click(object sender, RoutedEventArgs args)
    {
        ModelInfo? model = SelectedLibraryModel;
        if (model?.CanDelete != true || _isManaging || _isRefreshing || _chatBusy
            || _quitting || _closing.IsCancellationRequested) return;
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Delete local model files?",
                Content = "Delete downloaded files for " + model.Name +
                    "? This frees local storage and cannot be undone. You can download the model again later. The model must remain unloaded.",
                PrimaryButtonText = "Delete files",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            _isManaging = true;
            UpdateChatButtons();
            ShowModelsInfo(await _backend.DeleteModelAsync(
                model, ApiKeyBox.Password, _closing.Token), InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowModelsInfo(SafeError(error), InfoBarSeverity.Error);
        }
        finally
        {
            _isManaging = false;
            if (!_closing.IsCancellationRequested)
            {
                UpdateChatButtons();
                await RefreshAsync();
            }
        }
    }

    private async void DownloadButton_Click(object sender, RoutedEventArgs args) =>
        await ManageModelAsync(ModelAction.Download);

    private async Task ManageModelAsync(ModelAction action, ModelInfo? model = null)
    {
        model ??= SelectedLibraryModel;
        if (model is null || _isManaging || _chatBusy || _isRefreshing
            || !model.Permits(action)
            || _quitting || _closing.IsCancellationRequested)
            return;

        _isManaging = true;
        UpdateChatButtons();
        if (model.Id == SelectedModelId && action == ModelAction.Load)
        {
            _chatLoadError = null;
            ShowChatInfo("Loading model", "Requesting the selected model from the local engine…", InfoBarSeverity.Informational);
        }
        try
        {
            ShowModelsInfo(await _backend.ChangeModelAsync(
                model, action, ApiKeyBox.Password, _closing.Token, SelectedInferenceDevice), InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested)
        {
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowModelsInfo(SafeError(error), InfoBarSeverity.Error);
            if (model.Id == SelectedModelId) _chatLoadError = SafeError(error);
        }
        finally
        {
            _isManaging = false;
            if (!_closing.IsCancellationRequested)
            {
                UpdateChatButtons();
                await RefreshAsync();
            }
        }
    }

    private async void SendButton_Click(object sender, RoutedEventArgs args)
    {
        string? modelId = SelectedModelId;
        string prompt = PromptBox.Text.Trim();
        if (_chatBusy || _isManaging || _isRefreshing || modelId is null
            || _quitting || SelectedChatModel?.Status != "loaded"
            || prompt.Length == 0 || _closing.IsCancellationRequested)
            return;

        var pending = new List<ChatTurn>(_turns) { new("user", prompt) };
        // Keep complete user/assistant pairs and stay below backend/UI limits.
        while (pending.Count > 1
            && (pending.Count > 19 || pending.Sum(turn => turn.Content.Length) > 30000))
        {
            pending.RemoveRange(0, Math.Min(2, pending.Count - 1));
        }

        _chatBusy = true;
        _sendCancellation = CancellationTokenSource.CreateLinkedTokenSource(_closing.Token);
        UpdateChatButtons();
        ShowChatInfo("Generating", "Waiting for the local model…", InfoBarSeverity.Informational);
        try
        {
            string answer = await _backend.CompleteChatAsync(
                modelId, pending, ApiKeyBox.Password, _sendCancellation.Token);
            // A late HTTP success must not commit a reply after the user has
            // cancelled this request (server-side inference may still run).
            if (_closing.IsCancellationRequested || _sendCancellation?.IsCancellationRequested != false)
                return;
            _conversationModelId = modelId;
            _turns.Clear();
            _turns.AddRange(pending);
            _turns.Add(new ChatTurn("assistant", answer[..Math.Min(answer.Length, 8000)]));
            AppendChat("You", prompt);
            AppendChat("InferBridge", answer.Length > 8000
                ? answer[..8000] + "\n… Response truncated for the native conversation."
                : answer);
            PromptBox.Text = string.Empty;
            ShowChatInfo("Response complete", "Generated locally.", InfoBarSeverity.Success);
            _ = PromptBox.Focus(FocusState.Programmatic);
        }
        catch (OperationCanceledException) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Request ended",
                "Response cancelled or timed out. The model may still be finishing server-side.",
                InfoBarSeverity.Warning);
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Could not complete chat", SafeError(error), InfoBarSeverity.Error);
        }
        catch (Exception) when (_closing.IsCancellationRequested)
        {
        }
        finally
        {
            _sendCancellation?.Dispose();
            _sendCancellation = null;
            _chatBusy = false;
            if (!_closing.IsCancellationRequested)
                UpdateChatButtons();
        }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs args) =>
        _sendCancellation?.Cancel();

    private void NewChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy) return;
        _turns.Clear();
        ChatHistory.Items.Clear();
        ShowChatInfo("New chat", "Conversation cleared from this window.",
            InfoBarSeverity.Informational);
    }

    private void AppendChat(string speaker, string message)
    {
        ChatHistory.Items.Add(new ListViewItem
        {
            Content = new TextBlock
            {
                Text = speaker + "\n" + message,
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
                Margin = new Thickness(8),
            },
        });
        ChatHistory.ScrollIntoView(ChatHistory.Items[ChatHistory.Items.Count - 1]);
    }

    // Separate from the summary, which every refresh rewrites, so action results stay visible.
    private void ShowModelsInfo(string message, InfoBarSeverity severity)
    {
        ModelsInfo.Severity = severity;
        ModelsInfo.Message = message;
        ModelsInfo.IsOpen = true;
    }

    private void ShowChatInfo(string title, string message, InfoBarSeverity severity)
    {
        // Polling repeats the same status every tick; only real changes update
        // the bar and are announced, so screen readers hear replies and errors.
        if (ChatInfo.Title == title && ChatInfo.Message == message) return;
        ChatInfo.Title = title;
        ChatInfo.Message = message;
        ChatInfo.Severity = severity;
        (FrameworkElementAutomationPeer.FromElement(ChatInfo)
            ?? FrameworkElementAutomationPeer.CreatePeerForElement(ChatInfo))
            ?.RaiseNotificationEvent(AutomationNotificationKind.ActionCompleted,
                AutomationNotificationProcessing.ImportantMostRecent,
                title + ". " + message, "InferBridge.ChatStatus");
    }

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(nint hWnd);

    private static string SafeError(Exception error) => error switch
    {
        UnauthorizedAccessException => "API key rejected. Update it on the Engine screen.",
        EngineRequestException => error.Message,
        InvalidOperationException => "Model state changed or model is busy. Refresh and retry.",
        HttpRequestException { StatusCode: System.Net.HttpStatusCode.TooManyRequests } =>
            "The engine is temporarily refusing requests. Wait a few minutes, check the API key, then retry.",
        HttpRequestException => "The engine rejected the request or disconnected. Refresh status.",
        JsonException => "The engine returned an unexpected response. Refresh and retry.",
        IOException => "Engine connection or response failed. Refresh status.",
        OperationCanceledException => "The engine request timed out.",
        _ => "Unexpected native request failure. Check the engine and refresh.",
    };
}
