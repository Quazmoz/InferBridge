using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
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
    private readonly NativeConversationStore? _conversationStore;
    private string _currentConversationId = Guid.NewGuid().ToString("N");
    private bool _updatingSavedChats;
    private bool _historyAvailable = true;
    private IReadOnlyList<ModelInfo> _shownModels = Array.Empty<ModelInfo>();
    private CancellationTokenSource? _sendCancellation;
    private bool _isRefreshing;
    private bool _backgroundRefresh;
    // Timer polls keep action guards (_isRefreshing) but never lock controls.
    private bool RefreshLocksControls => PickerRefresh.LocksControls(_isRefreshing, _backgroundRefresh);
    private IReadOnlyList<ModelInfo>? _pendingModels;
    private bool _forceModelRebuild;
    private IReadOnlyList<(string Label, string Target, string Note)>? _shownDevices;
    private IReadOnlyList<(string Label, string Target, string Note)>? _pendingDevices;
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
    private bool _doctorBusy;
    private bool _doctorLoaded;
    private bool _storageBusy;
    private bool _storageLoaded;
    private DoctorReport? _doctorReport;
    private string? _quickBenchmarkModelId;
    private string? _quickBenchmarkDevice;
    private bool _devicesScanned;
    private bool _devicesAvailable;
    private string? _chatLoadError;

    public MainWindow()
    {
        InitializeComponent();
        ClearChatStats();
        ContextSizePicker.SelectionChanged += ContextSizePicker_SelectionChanged;
        BenchmarkPresetPicker.SelectionChanged += BenchmarkPresetPicker_SelectionChanged;
        try
        {
            _conversationStore = NativeConversationStore.ForCurrentInstallation();
            RefreshSavedChats();
            _updatingSavedChats = true;
            SaveHistoryToggle.IsChecked = _conversationStore.Exists;
            _updatingSavedChats = false;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or ArgumentException or System.Text.Json.JsonException)
        {
            _historyAvailable = false;
            _updatingSavedChats = true;
            SaveHistoryToggle.IsChecked = false;
            SaveHistoryToggle.IsEnabled = false;
            _updatingSavedChats = false;
            SavedChatsPicker.IsEnabled = false;
            SavedChatsSearch.IsEnabled = false;
            RenameSavedChatButton.IsEnabled = false;
            DeleteSavedChatButton.IsEnabled = false;
            PinSavedChatButton.IsEnabled = false;
            ArchiveSavedChatButton.IsEnabled = false;
            DuplicateSavedChatButton.IsEnabled = false;
            ShowArchivedChatsToggle.IsEnabled = false;
            ShowChatInfo("Saved history unavailable",
                "Could not read this installation's history storage. Existing files were not modified.",
                InfoBarSeverity.Warning);
        }
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "InferBridge.ico"));
        // AppWindow minimum dimensions are physical pixels, while XAML layout
        // uses DIPs. Scale the minimum as well as the initial size to prevent
        // high-DPI users from resizing below a usable logical viewport.
        RectInt32 area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        double scale = GetDpiForWindow(WindowNative.GetWindowHandle(this)) / 96.0;
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = Math.Min(area.Width, (int)(640 * scale));
            presenter.PreferredMinimumHeight = Math.Min(area.Height, (int)(480 * scale));
        }
        // Open at a comfortable size for this monitor's DPI, centered within its work area.
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
                await RefreshAsync(background: true);
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
            _benchmarkActivityTimer.Stop();
            _benchmarkActivityTimer.Tick -= BenchmarkActivityTimer_Tick;
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
        if (page == "overview" && !_doctorLoaded && _engineConnected && _rejectedApiKey is null)
            _ = RefreshDoctorAsync(); // Read-only status for the memory gauge.
        ConnectionHubPanel.Visibility = page == "connection" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "connection")
            _ = RefreshConnectionHubAsync();
        if (page == "connection" && !_networkLoaded)
            _ = RefreshNetworkSettingsAsync();
        else
            _hubGeneration++; // A response from a page we left must not repaint the Hub.
        ModelsPanel.Visibility = page == "models" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "models" && _engineConnected && _rejectedApiKey is null)
            _ = RefreshModelPreparationsAsync();
        // Read-only: lets model cards offer Revalidate only where the engine recommends it.
        if (page == "models" && _modelHealth is null && MaintenanceReady)
            _ = RefreshModelHealthAsync();
        HuggingFacePanel.Visibility = page == "huggingface" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "huggingface" && !_hfLoaded)
            _ = RefreshHuggingFaceAsync();
        ChatPanel.Visibility = page == "chat" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "chat" && _engineConnected && _rejectedApiKey is null)
            _ = RefreshModelPreparationsAsync();
        SettingsPanel.Visibility = page == "settings" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "settings" && _engineConnected && _rejectedApiKey is null)
            _ = RefreshContextConfigurationAsync();
        if (page == "settings" && _engineConnected && _rejectedApiKey is null)
            _ = RefreshModelPreparationsAsync();
        if (page == "settings" && _releaseStatus is null)
            _ = RefreshReleaseStatusAsync();
        UpdateGenerationSettingsInfo();
        OperationsPanel.Visibility = page == "operations" ? Visibility.Visible : Visibility.Collapsed;
        BenchmarkPanel.Visibility = page == "benchmark" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "benchmark" && !_benchmarkHistoryLoaded)
            _ = RefreshBenchmarkHistoryAsync();
        if (page == "benchmark" && !_benchmarkGate.Active)
            _ = RefreshBenchmarkConfigurationAsync();
        if (page == "operations" && !_operationsLoaded)
            _ = RefreshOperationsAsync();
        DoctorPanel.Visibility = page == "doctor" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "doctor" && !_doctorLoaded)
            _ = RefreshDoctorAsync();
        if (page == "doctor" && _modelHealth is null)
            _ = RefreshModelHealthAsync();
        StoragePanel.Visibility = page == "storage" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "storage" && !_storageLoaded)
            _ = RefreshStorageAsync();
    }

    private static string FormatStorageBytes(long bytes)
    {
        if (bytes < 1024) return bytes + " B";
        double amount = bytes;
        string[] units = ["B", "KiB", "MiB", "GiB", "TiB"];
        int index = 0;
        while (amount >= 1024 && index < units.Length - 1)
        {
            amount /= 1024;
            index++;
        }
        return amount.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture)
            + " " + units[index];
    }

    private async void StorageRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshStorageAsync();

    private async Task RefreshStorageAsync()
    {
        if (_storageBusy || _quitting || _closing.IsCancellationRequested) return;
        _storageBusy = true;
        StorageRefreshButton.IsEnabled = false;
        StorageProgress.IsActive = true;
        try
        {
            StorageInventory snapshot = await _backend.GetStorageInventoryAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _storageLoaded = true;
            StorageSummary.Text = "Managed storage: " + FormatStorageBytes(snapshot.ManagedBytes)
                + "\nCurrently reclaimable: " + FormatStorageBytes(snapshot.ReclaimableBytes)
                + "\nProtected transaction backups: "
                + FormatStorageBytes(snapshot.ProtectedBytes);
            const double gib = 1024d * 1024 * 1024;
            StorageChart.SetData(new (string, double?)[]
            {
                ("Managed storage", snapshot.ManagedBytes / gib),
                ("Currently reclaimable", snapshot.ReclaimableBytes / gib),
                ("Protected transaction backups", snapshot.ProtectedBytes / gib),
            });
            StorageRows.Children.Clear();
            foreach (StorageCleanupOption option in snapshot.Options)
            {
                var row = new StackPanel { Spacing = 4 };
                row.Children.Add(new TextBlock
                {
                    Text = option.Name + " · " + FormatStorageBytes(option.SizeBytes),
                    TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                    Style = Ui.ResStyle("BodyStrongTextBlockStyle"),
                });
                row.Children.Add(new TextBlock
                {
                    Text = option.Detail + " · "
                        + (option.Available
                            ? FormatStorageBytes(option.ReclaimableBytes) + " reclaimable"
                            : option.Reason.Length > 0 ? option.Reason : "Not currently reclaimable"),
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = Ui.ResBrush("TextFillColorSecondaryBrush"),
                });
                var button = new Button
                {
                    Content = option.Action switch
                    {
                        "delete_converted_model" => "Delete converted model",
                        "remove_huggingface_cache" => "Remove source cache",
                        "remove_incomplete_data" => "Remove incomplete preparation files",
                        "clear_compiled_cache" => "Clear compiled cache",
                        _ => "Unavailable",
                    },
                    Tag = option,
                    IsEnabled = option.Available,
                    HorizontalAlignment = HorizontalAlignment.Left,
                };
                button.Click += StorageCleanupButton_Click;
                row.Children.Add(button);
                StorageRows.Children.Add(Ui.Card(row));
            }
            StorageInfo.Severity = snapshot.Truncated
                ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
            StorageInfo.Message = snapshot.Truncated
                ? "Large inventory: up to 60 entries per storage category are shown. All cleanup eligibility is rechecked."
                : "Inventory refreshed. Actions marked unavailable are not executable.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _storageLoaded = false;
            StorageRows.Children.Clear(); // stale cleanup options must not remain actionable.
            StorageChart.SetData(Array.Empty<(string, double?)>());
            StorageSummary.Text = "Storage inventory unavailable.";
            StorageInfo.Severity = InfoBarSeverity.Warning;
            StorageInfo.Message = SafeError(error);
        }
        finally
        {
            _storageBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                StorageProgress.IsActive = false;
                StorageRefreshButton.IsEnabled = !_quitting;
            }
        }
    }

    private async void StorageCleanupButton_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: StorageCleanupOption selected }
            || !selected.Available || _storageBusy || _isManaging || _chatBusy
            || _isRefreshing || _quitting || _closing.IsCancellationRequested) return;
        _storageBusy = true;
        _isManaging = true;
        StorageRefreshButton.IsEnabled = false;
        UpdateChatButtons();
        string? message = null;
        InfoBarSeverity severity = InfoBarSeverity.Informational;
        bool requestedCleanup = false;
        try
        {
            string consequence = selected.Action switch
            {
                "delete_converted_model" =>
                    "The converted model must be prepared again before use.",
                "remove_huggingface_cache" =>
                    "A future conversion will download these source files again.",
                "remove_incomplete_data" =>
                    "Incomplete files and recovery metadata will be removed. Protected transaction backups stay intact.",
                "clear_compiled_cache" =>
                    "The next model load may be slower while OpenVINO recompiles.",
                _ => throw new InvalidOperationException("Unsupported storage action."),
            };
            var confirmation = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Confirm managed storage cleanup",
                Content = selected.Name + "\n\nPotentially reclaim "
                    + FormatStorageBytes(selected.ReclaimableBytes) + ".\n\n"
                    + consequence + "\n\nThe engine will recheck file safety before deleting anything.",
                PrimaryButtonText = "Remove selected files",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await confirmation.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            requestedCleanup = true;
            message = await _backend.CleanupStorageAsync(
                selected, ApiKeyBox.Password, _closing.Token);
            severity = InfoBarSeverity.Success;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            message = "Cleanup not confirmed. " + SafeError(error)
                + " Refresh the inventory before retrying; a timed-out request may already have succeeded.";
            severity = InfoBarSeverity.Warning;
        }
        finally
        {
            _isManaging = false;
            _storageBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                UpdateChatButtons();
                // Read authoritative state after an attempt, including timeouts.
                if (requestedCleanup) await RefreshStorageAsync();
                else StorageRefreshButton.IsEnabled = !_quitting;
                if (message is not null)
                {
                    StorageInfo.Severity = severity;
                    StorageInfo.Message = message;
                }
            }
        }
    }

    private async void DoctorRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshDoctorAsync();

    private async Task RefreshDoctorAsync()
    {
        if (_doctorBusy || _closing.IsCancellationRequested || _quitting) return;
        _doctorBusy = true;
        _doctorReport = null;
        DoctorRefreshButton.IsEnabled = false;
        DoctorCopyButton.IsEnabled = false;
        DoctorProgress.IsActive = true;
        DoctorInfo.Severity = InfoBarSeverity.Informational;
        DoctorInfo.Message = "Checking engine, OpenVINO devices, model state, memory and storage…";
        try
        {
            DoctorReport report = await _backend.RunSystemDoctorAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _doctorReport = report;
            _doctorLoaded = true;
            DoctorSummary.Text = report.Overall + " · " + report.Errors +
                " errors · " + report.Warnings + " warnings";
            DoctorInfo.Severity = report.Errors > 0 ? InfoBarSeverity.Error
                : report.Warnings > 0 ? InfoBarSeverity.Warning : InfoBarSeverity.Success;
            DoctorInfo.Message = "Read-only System Doctor checks finished.";
            DoctorDevices.Text = report.Devices;
            DoctorModels.Text = report.ModelSummary;
            DoctorChecks.Items.Clear();
            foreach (DoctorCheck check in report.Checks)
            {
                var item = new StackPanel { Spacing = 4 };
                var title = new WrapRow { Spacing = 8 };
                title.Children.Add(Ui.Pill(check.Level switch
                {
                    "pass" => "Passed", "warning" => "Warning", "error" => "Error", _ => "Info",
                }, check.Level switch
                {
                    "pass" => "success", "warning" => "warning", "error" => "error", _ => "neutral",
                }));
                title.Children.Add(Ui.Text(check.Title, "BodyStrongTextBlockStyle"));
                item.Children.Add(title);
                var detail = Ui.Text(check.Detail);
                detail.IsTextSelectionEnabled = true;
                item.Children.Add(detail);
                if (check.NextStep.Length > 0)
                {
                    var next = Ui.Text("Next: " + check.NextStep, secondary: true);
                    next.IsTextSelectionEnabled = true;
                    item.Children.Add(next);
                }
                var card = Ui.Card(item);
                card.Margin = new Thickness(0, 0, 0, 8);
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(card,
                    check.Level + ": " + check.Title + ". " + check.Detail);
                DoctorChecks.Items.Add(card);
            }
            DoctorLevelChart.SetData(ChartMath.LevelCounts(report.Checks.Select(check => check.Level)));
            UpdateMemoryGauges(report);
            DoctorCopyButton.IsEnabled = true;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _doctorLoaded = false;
            DoctorInfo.Severity = InfoBarSeverity.Error;
            DoctorInfo.Message = "Unable to complete System Doctor: " + SafeError(error);
            DoctorSummary.Text = "Status unavailable. Reconnect to the engine and retry.";
            DoctorDevices.Text = "Not checked";
            DoctorModels.Text = "Not checked";
            DoctorChecks.Items.Clear();
            DoctorLevelChart.SetData(Array.Empty<(string, double?)>());
            UpdateMemoryGauges(null);
        }
        finally
        {
            _doctorBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                DoctorProgress.IsActive = false;
                DoctorRefreshButton.IsEnabled = true;
            }
        }
    }

    private void DoctorCopyButton_Click(object sender, RoutedEventArgs args)
    {
        if (_doctorBusy || _quitting || _closing.IsCancellationRequested
            || _doctorReport is null) return;
        try
        {
            var package = new DataPackage();
            package.SetText(_doctorReport.SupportReport);
            Clipboard.SetContent(package);
            Clipboard.Flush();
            DoctorInfo.Severity = InfoBarSeverity.Success;
            DoctorInfo.Message = "Sanitized report copied. Review it before sharing.";
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            DoctorInfo.Severity = InfoBarSeverity.Error;
            DoctorInfo.Message = "Could not copy the report: " + SafeError(error);
        }
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
        QuickBenchmarkButton.IsEnabled = false;
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
                "\nInstallation: " + state.InstallationMode +
                "\nServer: " + state.ServerStatus + " · port " + state.ServerPort +
                "\nTray controller: " + (state.ControllerAvailable ? "Available" : "Unavailable") +
                "\nStart with Windows: " + (state.StartWithWindows ? "Enabled" : "Disabled") +
                "\nInference mode: " + (state.Mock ? "Mock (no hardware inference)" : "Hardware backend");
            OperationsBenchmarks.Text = state.BenchmarkRunning
                ? "A benchmark is currently running. Refresh to check its result."
                : state.LatestBenchmark;
            DiagnosticsResult.Text = "Last diagnostics export: " + state.LastDiagnosticsExport;
            DiagnosticsExportButton.IsEnabled = true;
            // The engine refuses a restart without its tray controller; mirror that.
            RestartEngineButton.IsEnabled = state.ControllerAvailable && !state.BenchmarkRunning;
            _quickBenchmarkModelId = state.ActiveModelId;
            _quickBenchmarkDevice = state.ActualDevice;
            if (string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
                || string.IsNullOrWhiteSpace(_quickBenchmarkDevice))
                BenchmarkRunResult.Text = "Load a generation model to enable a local benchmark.";
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
            RestartEngineButton.IsEnabled = false;
            _quickBenchmarkModelId = null;
            _quickBenchmarkDevice = null;
        }
        finally
        {
            _operationsBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                OperationsProgress.IsActive = false;
                OperationsRefreshButton.IsEnabled = true;
                QuickBenchmarkButton.IsEnabled = _operationsLoaded
                    && !_benchmarkGate.Active && !_benchmarkGate.OutcomeUnknown
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkDevice);
            }
        }
    }

    private async void QuickBenchmarkButton_Click(object sender, RoutedEventArgs args)
    {
        if (_operationsBusy || _benchmarkGate.Active || _benchmarkGate.OutcomeUnknown
            || _quitting || _closing.IsCancellationRequested
            || string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
            || string.IsNullOrWhiteSpace(_quickBenchmarkDevice)) return;
        // Take the operation lease before opening the confirmation dialog.
        _operationsBusy = true;
        QuickBenchmarkButton.IsEnabled = false;
        UpdateBenchmarkWorkload();
        string model = _quickBenchmarkModelId;
        string device = _quickBenchmarkDevice;
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Run a local quick benchmark?",
                Content = "Measure " + model + " on " + device +
                    " with one 32-token generation. This may take several minutes, use substantial system resources, and keep running on the engine if the window closes. No model download or conversion is requested.",
                PrimaryButtonText = "Run benchmark",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            OperationsRefreshButton.IsEnabled = false;
            DiagnosticsExportButton.IsEnabled = false;
            QuickBenchmarkButton.IsEnabled = false;
            OperationsProgress.IsActive = true;
            BenchmarkRunResult.Text = "Benchmark running on the selected loaded model…";
            string result = await _backend.RunQuickBenchmarkAsync(
                model, device, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            BenchmarkRunResult.Text = result;
            OperationsInfo.Severity = InfoBarSeverity.Informational;
            OperationsInfo.Message = "Benchmark request finished. Check the evidence label below.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            BenchmarkRunResult.Text = "Benchmark could not complete: " + SafeError(error);
        }
        finally
        {
            _operationsBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                OperationsProgress.IsActive = false;
                OperationsRefreshButton.IsEnabled = true;
                DiagnosticsExportButton.IsEnabled = _operationsLoaded;
                QuickBenchmarkButton.IsEnabled = _operationsLoaded
                    && !_benchmarkGate.Active && !_benchmarkGate.OutcomeUnknown
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkDevice);
                UpdateBenchmarkWorkload();
                await RefreshOperationsAsync();
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
                QuickBenchmarkButton.IsEnabled = _operationsLoaded
                    && !_benchmarkGate.Active && !_benchmarkGate.OutcomeUnknown
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkDevice);
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

    private void RefreshSavedChats(string? selectedId = null)
    {
        if (_conversationStore is null || !_historyAvailable) return;
        var chats = _conversationStore.Find(SavedChatsSearch.Text,
            archived: ShowArchivedChatsToggle.IsChecked == true);
        _updatingSavedChats = true;
        try
        {
            SavedChatsPicker.Items.Clear();
            foreach (var chat in chats)
            {
                var item = new ComboBoxItem
                {
                    Content = (chat.Pinned ? "📌 " : "") + chat.Title + " · "
                        + chat.UpdatedUtc.ToLocalTime().ToString("g"),
                    Tag = chat,
                };
                SavedChatsPicker.Items.Add(item);
                if (chat.Id == selectedId) SavedChatsPicker.SelectedItem = item;
            }
            bool canEdit = !_chatBusy && !_quitting
                && SavedChatsPicker.SelectedItem is ComboBoxItem;
            DeleteSavedChatButton.IsEnabled = canEdit;
            PinSavedChatButton.IsEnabled = canEdit;
            ArchiveSavedChatButton.IsEnabled = canEdit;
            DuplicateSavedChatButton.IsEnabled = canEdit;
            RenameSavedChatButton.IsEnabled = canEdit;
            UpdateSavedChatFlagLabels();
        }
        finally { _updatingSavedChats = false; }
    }

    private void SavedChatsSearch_TextChanged(object sender, TextChangedEventArgs args)
    {
        if (_updatingSavedChats || !_historyAvailable) return;
        try { RefreshSavedChats(); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or ArgumentException or System.Text.Json.JsonException)
        {
            // External edits or damaged files must not crash the UI or overwrite history.
            _historyAvailable = false;
            SavedChatsSearch.IsEnabled = false;
            SavedChatsPicker.IsEnabled = false;
            RenameSavedChatButton.IsEnabled = false;
            DeleteSavedChatButton.IsEnabled = false;
            PinSavedChatButton.IsEnabled = false;
            ArchiveSavedChatButton.IsEnabled = false;
            DuplicateSavedChatButton.IsEnabled = false;
            ShowArchivedChatsToggle.IsEnabled = false;
            SaveHistoryToggle.IsEnabled = false;
            SaveHistoryToggle.IsChecked = false;
            ShowChatInfo("Saved history unavailable",
                "Could not safely read local history. Existing files were preserved.",
                InfoBarSeverity.Warning);
        }
    }

    private void SaveHistoryToggle_Changed(object sender, RoutedEventArgs args)
    {
        if (_updatingSavedChats || !_historyAvailable || _conversationStore is null) return;
        if (SaveHistoryToggle.IsChecked == true && _turns.Count > 0
            && _conversationModelId is not null)
        {
            if (!TrySaveCurrentChat()) return;
        }
        ShowChatInfo("Local history preference",
            SaveHistoryToggle.IsChecked == true
                ? "Completed chats are saved locally as readable JSON. Disable this to stop saving new replies."
                : "Saving is paused. Previously saved chats remain until explicitly deleted.",
            InfoBarSeverity.Informational);
    }

    private bool TrySaveCurrentChat()
    {
        if (_conversationStore is null || !_historyAvailable
            || SaveHistoryToggle.IsChecked != true
            || _turns.Count == 0 || _conversationModelId is null)
            return true;
        try
        {
            _conversationStore.Save(NativeConversationStore.Create(
                _currentConversationId, _conversationModelId, _turns));
            RefreshSavedChats(_currentConversationId);
            return true;
        }
        catch (NativeConversationStore.HistoryFullException error)
        {
            // Nothing was written; history stays usable once a pin is removed.
            ShowChatInfo("Chat generated, but not saved", error.Message, InfoBarSeverity.Warning);
            return false;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or ArgumentException or System.Text.Json.JsonException)
        {
            _historyAvailable = false;
            SaveHistoryToggle.IsEnabled = false;
            SaveHistoryToggle.IsChecked = false;
            SavedChatsSearch.IsEnabled = false;
            SavedChatsPicker.IsEnabled = false;
            RenameSavedChatButton.IsEnabled = false;
            DeleteSavedChatButton.IsEnabled = false;
            PinSavedChatButton.IsEnabled = false;
            ArchiveSavedChatButton.IsEnabled = false;
            DuplicateSavedChatButton.IsEnabled = false;
            ShowArchivedChatsToggle.IsEnabled = false;
            ShowChatInfo("Chat generated, but not saved",
                "History storage could not be written. The current chat remains open; existing files were preserved.",
                InfoBarSeverity.Warning);
            return false;
        }
    }

    private async void SavedChatsPicker_SelectionChanged(
        object sender, SelectionChangedEventArgs args)
    {
        if (_updatingSavedChats || _chatBusy || _closing.IsCancellationRequested) return;
        UpdateSavedChatFlagLabels();
        if (SavedChatsPicker.SelectedItem is not ComboBoxItem { Tag: NativeConversationStore.SavedChat saved })
            return;
        _currentConversationId = saved.Id;
        _conversationModelId = saved.ModelId;
        _turns.Clear();
        _turns.AddRange(saved.Turns);
        ResetGenerationSettings();
        ChatHistory.Items.Clear();
        UpdateChatEmptyState();
        foreach (var turn in saved.Turns)
            AppendChat(turn.Role == "user" ? "You" : "InferBridge", turn.Content);
        PromptBox.Text = string.Empty;
        // Never mix a restored transcript with a different model. Loading an
        // unavailable model remains an explicit Model library operation.
        var option = ChatModelPicker.Items.Cast<ComboBoxItem>()
            .FirstOrDefault(item => (string?)item.Tag == saved.ModelId);
        _updatingModelPicker = true;
        try { ChatModelPicker.SelectedItem = option; }
        finally { _updatingModelPicker = false; }
        UpdateChatButtons();
        ShowChatInfo("Conversation restored", option is null
            ? "This conversation's model is not available. Install it before continuing."
            : "Saved conversation restored. Its original model is selected.",
            InfoBarSeverity.Informational);
        if (SelectedChatModel is { CanLoad: true } model)
            await ManageModelAsync(ModelAction.Load, model, fromChat: true);
    }

    private async void DeleteSavedChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || !_historyAvailable || _conversationStore is null
            || SavedChatsPicker.SelectedItem is not ComboBoxItem
                { Tag: NativeConversationStore.SavedChat saved }) return;
        try
        {
            var confirmation = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Delete this saved conversation?",
                Content = "Permanently delete \"" + saved.Title +
                    "\" from this installation's local history? This cannot be undone.",
                PrimaryButtonText = "Delete saved chat",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await confirmation.ShowAsync() != ContentDialogResult.Primary) return;
            if (!_conversationStore.Delete(saved.Id))
                throw new IOException("Saved chat is no longer available.");
            if (saved.Id == _currentConversationId)
            {
                _turns.Clear();
                ChatHistory.Items.Clear();
        UpdateChatEmptyState();
                _currentConversationId = Guid.NewGuid().ToString("N");
                _conversationModelId = SelectedModelId;
            }
            RefreshSavedChats();
            ShowChatInfo("Saved conversation deleted", "Local saved file updated.",
                InfoBarSeverity.Success);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or ArgumentException or System.Text.Json.JsonException)
        {
            _historyAvailable = false;
            SaveHistoryToggle.IsChecked = false;
            SaveHistoryToggle.IsEnabled = false;
            SavedChatsSearch.IsEnabled = false;
            SavedChatsPicker.IsEnabled = false;
            RenameSavedChatButton.IsEnabled = false;
            DeleteSavedChatButton.IsEnabled = false;
            PinSavedChatButton.IsEnabled = false;
            ArchiveSavedChatButton.IsEnabled = false;
            DuplicateSavedChatButton.IsEnabled = false;
            ShowArchivedChatsToggle.IsEnabled = false;
            ShowChatInfo("Could not delete saved conversation",
                "Local history was not safely updated. The existing file was preserved.",
                InfoBarSeverity.Error);
        }
    }

    private async void RenameSavedChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || !_historyAvailable || _conversationStore is null
            || SavedChatsPicker.SelectedItem is not ComboBoxItem
                { Tag: NativeConversationStore.SavedChat saved }) return;
        var name = new TextBox
        {
            Header = "Conversation title",
            Text = saved.Title,
            MaxLength = 80,
            MinWidth = 300,
        };
        var dialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = "Rename saved conversation",
            Content = name,
            PrimaryButtonText = "Save title",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
        };
        name.TextChanged += (_, _) =>
            dialog.IsPrimaryButtonEnabled = !string.IsNullOrWhiteSpace(name.Text);
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        try
        {
            if (!_conversationStore.Rename(saved.Id, name.Text))
                throw new IOException("Saved conversation is no longer available.");
            SavedChatsSearch.Text = string.Empty;
            RefreshSavedChats(saved.Id);
            ShowChatInfo("Conversation renamed", "The title was saved locally.",
                InfoBarSeverity.Success);
        }
        catch (ArgumentException)
        {
            ShowChatInfo("Invalid conversation title",
                "Enter a readable title with up to 80 characters.",
                InfoBarSeverity.Warning);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or System.Text.Json.JsonException)
        {
            _historyAvailable = false;
            SaveHistoryToggle.IsChecked = false;
            SaveHistoryToggle.IsEnabled = false;
            SavedChatsSearch.IsEnabled = false;
            SavedChatsPicker.IsEnabled = false;
            RenameSavedChatButton.IsEnabled = false;
            DeleteSavedChatButton.IsEnabled = false;
            PinSavedChatButton.IsEnabled = false;
            ArchiveSavedChatButton.IsEnabled = false;
            DuplicateSavedChatButton.IsEnabled = false;
            ShowArchivedChatsToggle.IsEnabled = false;
            ShowChatInfo("Could not rename saved conversation",
                "History storage was not safely updated; existing files were preserved.",
                InfoBarSeverity.Error);
        }
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
        UpdateChatEmptyState();
            _currentConversationId = Guid.NewGuid().ToString("N");
            _conversationModelId = modelId;
            ResetGenerationSettings();
            RefreshSavedChats();
        }
        UpdateChatButtons();
        if (SelectedChatModel is { CanLoad: true } model)
            await ManageModelAsync(ModelAction.Load, model, fromChat: true);
    }

    private void ModelsList_SelectionChanged(
        object sender, SelectionChangedEventArgs args) => UpdateModelButtons();

    private async void RecoverButton_Click(object sender, RoutedEventArgs args)
    {
        ModelInfo? model = SelectedLibraryModel;
        if (model is null || !_engineConnected || _isManaging || _isRefreshing
            || _chatBusy || _quitting || _closing.IsCancellationRequested) return;
        _isManaging = true;
        UpdateChatButtons();
        try
        {
            ModelRecoveryInfo recovery = await _backend.GetModelRecoveryAsync(
                model.Id, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;

            string[] actions =
            [
                "resume", "retry_failed_stage", "restart_download", "remove_incomplete_files",
            ];
            static string ActionTitle(string action) => action switch
            {
                "resume" => "Resume preparation",
                "retry_failed_stage" => "Retry failed stage",
                "restart_download" => "Restart from download (remove cached source)",
                "remove_incomplete_files" => "Remove incomplete output only",
                _ => "Unknown action",
            };

            var layout = new StackPanel { Spacing = 10, MaxWidth = 540 };
            layout.Children.Add(new TextBlock
            {
                Text = "Failed stage: " + recovery.FailedStage
                    + "\nLast completed: " + recovery.LastCompletedStage
                    + "\nCached source files: " + recovery.DownloadedFiles
                    + "\nConverted output: " + recovery.ConversionOutput
                    + (recovery.RecommendedAction.Length > 0
                        ? "\nEngine recommendation: " + ActionTitle(recovery.RecommendedAction)
                        : ""),
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            });
            if (recovery.FailureMessage.Length > 0 || recovery.LogTail.Count > 0)
                layout.Children.Add(new TextBlock
                {
                    Text = "Sanitized failure details:\n" + recovery.FailureMessage
                        + (recovery.LogTail.Count > 0
                            ? "\n" + string.Join("\n", recovery.LogTail) : ""),
                    TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                });
            var picker = new ComboBox
            {
                Header = "Recovery action",
                MinWidth = 280,
                HorizontalAlignment = HorizontalAlignment.Left,
            };
            foreach (string candidateAction in actions.Where(recovery.CanRun)
                .OrderBy(name => name != recovery.RecommendedAction))
                picker.Items.Add(new ComboBoxItem { Content = ActionTitle(candidateAction), Tag = candidateAction });
            if (picker.Items.Count > 0) picker.SelectedIndex = 0;
            layout.Children.Add(picker);
            if (picker.Items.Count == 0)
                layout.Children.Add(new TextBlock
                {
                    Text = "No safe recovery action is currently available.",
                    TextWrapping = TextWrapping.Wrap,
                });
            var scroll = new ScrollViewer
            {
                MaxHeight = 430,
                Content = layout,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            };
            var review = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Recovery: " + recovery.ModelName,
                Content = scroll,
                PrimaryButtonText = "Continue",
                IsPrimaryButtonEnabled = picker.Items.Count > 0,
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await review.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            string? action = (picker.SelectedItem as ComboBoxItem)?.Tag as string;
            if (action is null || !recovery.CanRun(action)) return;

            if (action is "restart_download" or "remove_incomplete_files")
            {
                var confirmation = new ContentDialog
                {
                    XamlRoot = ((FrameworkElement)Content).XamlRoot,
                    Title = "Confirm file removal",
                    Content = action == "restart_download"
                        ? "Delete reusable source cache files and incomplete output for "
                            + recovery.ModelName + "? This forces a fresh download."
                        : "Permanently remove incomplete conversion output for "
                            + recovery.ModelName + "? Reusable source cache is preserved.",
                    PrimaryButtonText = "Confirm removal",
                    CloseButtonText = "Cancel",
                    DefaultButton = ContentDialogButton.Close,
                };
                if (await confirmation.ShowAsync() != ContentDialogResult.Primary
                    || _closing.IsCancellationRequested) return;
            }
            ShowModelsInfo(await _backend.ApplyModelRecoveryAsync(
                recovery, action, SelectedInferenceDevice, ApiKeyBox.Password,
                _closing.Token), InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowModelsInfo(SafeError(error), InfoBarSeverity.Warning);
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

    private string? SelectedInferenceDevice =>
        (LibraryDevicePicker.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } device
            ? device : null;

    private string? SelectedChatDevice =>
        (ChatDevicePicker.SelectedItem as ComboBoxItem)?.Tag as string is { Length: > 0 } device
            ? device : null;

    private void ChatDevicePicker_SelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (ChatDeviceSummary is not null && ApplyChatDeviceButton is not null)
            UpdateChatDeviceSummary();
    }

    private void UpdateChatDeviceSummary()
    {
        string target = SelectedChatDevice ?? "engine default";
        ModelInfo? model = SelectedChatModel;
        string actual = model?.Device is { Length: > 0 } device ? device
            : model?.Status == "loaded" ? "not reported" : "not loaded";
        ChatDeviceSummary.Text = "Next load target: " + target + " · Reported active device: " + actual
            + (SelectedChatDevice is { } choice
                && (choice.StartsWith("MULTI:", StringComparison.Ordinal)
                    || choice.StartsWith("HETERO:", StringComparison.Ordinal))
                ? " · Experimental: multi-device support depends on the model and OpenVINO runtime."
                : ". AUTO routes to one compatible device; it does not combine processors.");
        ApplyChatDeviceButton.IsEnabled = _devicesAvailable && _engineConnected
            && !_scanningDevices && !_chatBusy && !_isManaging && !RefreshLocksControls
            && !_quitting && !_closing.IsCancellationRequested
            && model is { Status: "loaded" or "ready_to_load" };
    }

    private async void ApplyChatDeviceButton_Click(object sender, RoutedEventArgs args)
    {
        ModelInfo? model = SelectedChatModel;
        if (model is null || !_devicesAvailable || !_engineConnected) return;
        ModelAction action = model.CanUnload ? ModelAction.SwitchDevice : ModelAction.Load;
        await ManageModelAsync(action, model, fromChat: true);
    }

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
            var options = new[] { "NPU", "GPU", "CPU" };
            var supported = options.Where(device => inventory.Mock ||
                inventory.Available.Any(found => found == device ||
                    found.StartsWith(device + ".", StringComparison.Ordinal))).ToArray();
            var devices = new List<(string Label, string Target, string Note)>
            {
                ("Engine default", string.Empty, "Use the engine's configured default device."),
                ("AUTO · automatic routing", "AUTO",
                    "OpenVINO chooses one compatible processor. It does not use all processors at once."),
            };
            foreach (string device in supported)
                devices.Add((device, device, "Load on the detected " + device + " processor."));
            foreach (DeviceSuggestion suggestion in inventory.Suggestions)
                devices.Add((suggestion.Device + (suggestion.Experimental ? " · experimental" : ""),
                    suggestion.Device,
                    suggestion.Note.Length > 0 ? suggestion.Note : "OpenVINO advanced routing."));
            // Mirror the web UI: rebuild device selectors only when the detected
            // inventory changes, and never under an open dropdown.
            switch (PickerRefresh.Decide(_shownDevices, devices,
                LibraryDevicePicker.IsDropDownOpen || ChatDevicePicker.IsDropDownOpen))
            {
                case PickerUpdate.Apply: ApplyDeviceOptions(devices); break;
                case PickerUpdate.Defer: _pendingDevices = devices; break;
                default: _pendingDevices = null; break;
            }
            _devicesAvailable = true;
            _devicesScanned = true;
            string loadedSummary = await _backend.GetRuntimeSummaryAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            DevicePills.Children.Clear();
            foreach (string device in inventory.Available)
                DevicePills.Children.Add(Ui.Pill(device + (device == inventory.Default ? " · default" : ""),
                    device.StartsWith("NPU", StringComparison.Ordinal) ? "accent" : "success"));
            if (inventory.Mock) DevicePills.Children.Add(Ui.Pill("Mock inference mode", "warning"));
            if (inventory.Available.Count == 0) DevicePills.Children.Add(Ui.Pill("No devices reported", "error"));
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
            DevicePills.Children.Clear();
            _shownDevices = null;
            _pendingDevices = null;
            LibraryDevicePicker.Items.Clear();
            ChatDevicePicker.Items.Clear();
            LibraryDevicePicker.Items.Add(new ComboBoxItem
            {
                Content = "Engine default", Tag = string.Empty,
            });
            ChatDevicePicker.Items.Add(new ComboBoxItem
            {
                Content = "Engine default", Tag = string.Empty,
            });
            LibraryDevicePicker.SelectedIndex = 0;
            ChatDevicePicker.SelectedIndex = 0;
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
        bool idle = !_isManaging && !_chatBusy && !RefreshLocksControls && !_quitting;
        LoadButton.IsEnabled = idle && model?.CanLoad == true;
        UnloadButton.IsEnabled = idle && model?.CanUnload == true;
        DeleteButton.IsEnabled = idle && model?.CanDelete == true;
        DownloadButton.IsEnabled = idle && model?.CanDownload == true;
        RecoverButton.IsEnabled = idle && _engineConnected && model is not null;
        LibraryDevicePicker.IsEnabled = idle && _devicesAvailable;
        ModelLibraryToolsButton.IsEnabled = ModelLibraryToolsReady;
        UpdateModelPreparationUi();
    }

    private void ChatToolbar_SizeChanged(object sender, SizeChangedEventArgs args)
    {
        // WinUI NavigationView uses a compact pane at narrow widths; toolbar
        // chrome must yield space to the model picker, transcript and composer.
        bool compact = args.NewSize.Width < 560;
        ChatToolbarTitle.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        ChatStatusText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
    }

    private void UpdateChatButtons()
    {
        SendButton.IsEnabled = !_chatBusy && !_isManaging && !RefreshLocksControls && !_quitting
            && SelectedChatModel?.Status == "loaded"
            && (_turns.Count == 0 || SelectedModelId == _conversationModelId);
        CancelButton.IsEnabled = _chatBusy;
        CopyLastReplyButton.IsEnabled = !_chatBusy && !_quitting
            && _turns.LastOrDefault() is { Role: "assistant" };
        SavedChatsPicker.IsEnabled = !_chatBusy && !_quitting && _historyAvailable;
        SavedChatsSearch.IsEnabled = !_chatBusy && !_quitting && _historyAvailable;
        bool canEditSavedChat = !_chatBusy && !_quitting && _historyAvailable
            && SavedChatsPicker.SelectedItem is ComboBoxItem;
        DeleteSavedChatButton.IsEnabled = canEditSavedChat;
        PinSavedChatButton.IsEnabled = canEditSavedChat;
        ArchiveSavedChatButton.IsEnabled = canEditSavedChat;
        DuplicateSavedChatButton.IsEnabled = canEditSavedChat;
        RenameSavedChatButton.IsEnabled = canEditSavedChat;
        ChatModelPicker.IsEnabled = !_chatBusy && !_isManaging && !RefreshLocksControls && !_quitting;
        ChatDevicePicker.IsEnabled = _devicesAvailable && !_chatBusy && !_isManaging
            && !RefreshLocksControls && !_quitting;
        UpdateChatDeviceSummary();
        WebUiButton.IsEnabled = _engineConnected && !_quitting && !_openingWebUi;
        ScanHardwareButton.IsEnabled = _engineConnected && !_quitting && !_scanningDevices;
        PromptBox.IsEnabled = !_chatBusy;
        NewChatButton.IsEnabled = !_chatBusy && !_quitting;
        ChatProgress.IsActive = _chatBusy;
        UpdateAttachmentSummary();
        UpdateGenerationSettingsInfo();
        UpdateModelButtons();
        UpdateHuggingFaceButtons();
        UpdateShellStatus();
    }

    private void ApplyModelInventory(IReadOnlyList<ModelInfo> models)
    {
        _pendingModels = null;
        _forceModelRebuild = false;
        // Rebuilding ComboBox items raises SelectionChanged. Suppress it so
        // polling, reconnects and temporary model absence keep the transcript.
        string? selected = SelectedModelId ?? _conversationModelId;
        string? selectedLibraryId = SelectedLibraryModel?.Id;
        _shownModels = models;
        _updatingModelPicker = true;
        try
        {
            ChatModelPicker.Items.Clear();
            ModelsList.Items.Clear();
            _modelRowButtons.Clear();
            _modelRowDetails.Clear();
            // The engine's recommendation leads the library and is preselected.
            string? recommended = _recommendation?.Id;
            foreach (ModelInfo model in models.OrderBy(m => m.Id != recommended))
            {
                var libraryItem = new ListViewItem
                {
                    Content = BuildModelCard(model, model.Id == recommended),
                    Tag = model,
                    HorizontalContentAlignment = HorizontalAlignment.Stretch,
                };
                // Narrator reads the same one-line summary the list used to show.
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(
                    libraryItem, ModelRowLabel(model, model.Id == recommended));
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
                    .FirstOrDefault(option => models.Any(model => model.Id == (option.Tag as string) && model.CanUnload));
        }
        finally
        {
            _updatingModelPicker = false;
        }
        ModelsFilterBox_TextChanged(ModelsFilterBox, null!);
    }

    private void ChatModelPicker_DropDownClosed(object sender, object args)
    {
        // Apply an inventory change deferred while the dropdown was open. If an
        // action started meanwhile, drop it: the next refresh re-reads status.
        if (_pendingModels is not { } pending) return;
        if (_isManaging || _chatBusy || _isRefreshing || _closing.IsCancellationRequested)
        {
            _pendingModels = null;
            return;
        }
        ApplyModelInventory(pending);
        UpdateChatButtons();
    }

    private void DevicePicker_DropDownClosed(object sender, object args)
    {
        if (_pendingDevices is not { } pending
            || LibraryDevicePicker.IsDropDownOpen || ChatDevicePicker.IsDropDownOpen
            || _closing.IsCancellationRequested) return;
        ApplyDeviceOptions(pending);
        UpdateChatButtons();
    }

    private void ApplyDeviceOptions(IReadOnlyList<(string Label, string Target, string Note)> devices)
    {
        _pendingDevices = null;
        _shownDevices = devices;
        string? selectedLibrary = SelectedInferenceDevice;
        string? selectedChat = SelectedChatDevice;
        LibraryDevicePicker.Items.Clear();
        ChatDevicePicker.Items.Clear();
        foreach (var (label, target, note) in devices)
        {
            foreach (ComboBox picker in new[] { LibraryDevicePicker, ChatDevicePicker })
            {
                var option = new ComboBoxItem { Content = label, Tag = target };
                ToolTipService.SetToolTip(option, note);
                picker.Items.Add(option);
            }
        }
        LibraryDevicePicker.SelectedItem = LibraryDevicePicker.Items
            .Cast<ComboBoxItem>().FirstOrDefault(item =>
                (string?)item.Tag == (selectedLibrary ?? string.Empty))
            ?? (ComboBoxItem)LibraryDevicePicker.Items[0];
        ChatDevicePicker.SelectedItem = ChatDevicePicker.Items
            .Cast<ComboBoxItem>().FirstOrDefault(item =>
                (string?)item.Tag == (selectedChat ?? string.Empty))
            ?? (ComboBoxItem)ChatDevicePicker.Items[0];
    }

    private async Task RefreshAsync(bool background = false)
    {
        if (_isRefreshing || _isManaging || _chatBusy || _quitting || _allowClose || _closing.IsCancellationRequested)
            return;
        _isRefreshing = true;
        _backgroundRefresh = background;
        // A timer poll must not flip IsEnabled: disabling a ComboBox or
        // DropDownButton closes its open dropdown/flyout every tick.
        if (!background)
        {
            UpdateChatButtons();
            RefreshButton.IsEnabled = false;
            ConnectionProgress.IsActive = true;
        }
        try
        {
            string apiKey = ApiKeyBox.Password;
            var status = await _backend.InspectAsync(_closing.Token, apiKey);
            if (_hubMetadata is not null && (!status.Connected
                || status.SessionNonce != _hubMetadata.SessionNonce))
            {
                InvalidateConnectionHub();
                HubInfo.Severity = InfoBarSeverity.Warning;
                HubInfo.Message = "The engine session changed. Refresh the connection before copying or testing.";
            }
            bool wasConnected = _engineConnected;
            _engineConnected = status.Connected;
            _engineOrigin = status.Connected ? status.Origin : null;
            if ((!status.Connected || status.RequiresAuthentication)
                && (_hfLoaded || wasConnected))
            {
                _hfLoaded = false;
                _hfAccount = null;
                HfAccountText.Text = "Engine unavailable or authorization required. Refresh account after reconnecting.";
                HfInfo.Severity = InfoBarSeverity.Warning;
                HfInfo.Message = "Hugging Face account status is no longer verified for this engine session.";
            }
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

            // Rebuild only on a real inventory change: replacing items every poll
            // steals keyboard and screen-reader focus from the lists and closes an
            // open model dropdown. While it is open, apply the change on close.
            switch (PickerRefresh.Decide(
                _forceModelRebuild ? null : _shownModels, status.Models, ChatModelPicker.IsDropDownOpen))
            {
                case PickerUpdate.Apply: ApplyModelInventory(status.Models); break;
                case PickerUpdate.Defer: _pendingModels = status.Models; break;
                default: _pendingModels = null; break;
            }

            if (!status.Connected || status.RequiresAuthentication)
                ResetModelPreparations();
            else if (ModelsPanel.Visibility == Visibility.Visible
                || SettingsPanel.Visibility == Visibility.Visible)
                await RefreshModelPreparationsAsync();

            ModelsSummary.Text = status.RequiresAuthentication
                ? "Authentication required. Enter your API key on the Engine & devices page."
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
            ResetModelPreparations();
            ConnectionInfo.Message = "Could not display local engine status.";
            ModelsSummary.Text = "Refresh after checking the engine.";
            ChatModelPicker.Items.Clear();
            _shownModels = Array.Empty<ModelInfo>();
            _pendingModels = null;
            ShowChatInfo("Connection error", "Check the engine and refresh.",
                InfoBarSeverity.Error);
        }
        finally
        {
            if (!_closing.IsCancellationRequested)
            {
                _isRefreshing = false;
                _backgroundRefresh = false;
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
        _forceModelRebuild = true; // rebuild the list with the recommendation
        await RefreshAsync(background: true);
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

    private async Task ManageModelAsync(ModelAction action, ModelInfo? model = null,
        bool fromChat = false)
    {
        model ??= SelectedLibraryModel;
        if (model is null || _isManaging || _chatBusy || _isRefreshing
            || !model.Permits(action)
            || _quitting || _closing.IsCancellationRequested)
            return;

        _isManaging = true;
        UpdateChatButtons();
        if (model.Id == SelectedModelId
            && action is ModelAction.Load or ModelAction.SwitchDevice)
        {
            _chatLoadError = null;
            ShowChatInfo(action == ModelAction.SwitchDevice ? "Switching device" : "Loading model",
                "Requesting " + (fromChat ? SelectedChatDevice ?? "engine default"
                    : SelectedInferenceDevice ?? "engine default") + " from the local engine…",
                InfoBarSeverity.Informational);
        }
        try
        {
            ShowModelsInfo(await _backend.ChangeModelAsync(
                model, action, ApiKeyBox.Password, _closing.Token,
                fromChat ? SelectedChatDevice : SelectedInferenceDevice), InfoBarSeverity.Success);
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
            || (_turns.Count > 0 && modelId != _conversationModelId)
            || prompt.Length == 0 || _closing.IsCancellationRequested)
            return;

        var pending = new List<ChatTurn>(_turns) { new("user", prompt) };
        // Keep complete user/assistant pairs and stay below backend/UI limits.
        while (pending.Count > 1
            && (pending.Count > 19 || pending.Sum(turn => turn.Content.Length) > 30000))
        {
            pending.RemoveRange(0, Math.Min(2, pending.Count - 1));
        }

        if (!CanUseGenerationOptions(modelId, _generationOptions, out string reason))
        {
            ShowChatInfo("Unsupported inference options", reason, InfoBarSeverity.Warning);
            return;
        }
        GenerationOptions generation = _generationOptions;
        ChatRequestContext context = CurrentChatContext();
        _chatBusy = true;
        _sendCancellation = CancellationTokenSource.CreateLinkedTokenSource(_closing.Token);
        CancellationTokenSource send = _sendCancellation;
        ListViewItem? userItem = null;
        ListViewItem? assistantItem = null;
        bool committed = false;
        UpdateChatButtons();
        ShowChatInfo("Generating", "Streaming locally from the selected model…",
            InfoBarSeverity.Informational);
        try
        {
            // Display preview only. Until [DONE], neither the context nor the
            // prompt is committed, and cancelled/error output is discarded.
            userItem = AppendChat("You", context.Images.Count == 0 ? prompt
                : prompt + "\n\n(" + context.Images.Count + " image(s) attached)");
            assistantItem = AppendChat("InferBridge", "Generating…", preview: true);
            long lastPreviewTick = 0;
            // Client-side timing for the stats strip (includes loopback HTTP).
            var timer = Stopwatch.StartNew();
            long firstTokenTicks = -1;
            ChatUsage? usage = null;
            string answer = await _backend.StreamChatAsync(
                modelId, pending, ApiKeyBox.Password, partial =>
                {
                    Interlocked.CompareExchange(ref firstTokenTicks, timer.ElapsedTicks, -1);
                    long now = Environment.TickCount64;
                    if (now - lastPreviewTick < 60) return; // Bound UI work during fast streams.
                    lastPreviewTick = now;
                    string preview = partial.Length > 8000
                        ? partial[..8000] + "\n… Preview truncated." : partial;
                    DispatcherQueue.TryEnqueue(() =>
                    {
                        if (_closing.IsCancellationRequested || send.IsCancellationRequested
                            || !ReferenceEquals(_sendCancellation, send) || !_chatBusy)
                            return;
                        if (assistantItem?.Content is Border { Child: TextBlock text })
                        {
                            text.Text = "InferBridge\n" + preview;
                            ChatHistory.ScrollIntoView(assistantItem);
                        }
                    });
                }, send.Token, generation, context, value => usage = value);
            timer.Stop();
            // A late HTTP success must not commit a reply after cancellation.
            if (_closing.IsCancellationRequested || _sendCancellation?.IsCancellationRequested != false)
                return;
            _conversationModelId = modelId;
            _turns.Clear();
            _turns.AddRange(pending);
            _turns.Add(new ChatTurn("assistant", answer[..Math.Min(answer.Length, 8000)]));
            if (assistantItem?.Content is Border assistantBubble)
                assistantBubble.Child = BuildChatContent("InferBridge", answer.Length > 8000
                    ? answer[..8000] + "\n… Response truncated for the native conversation."
                    : answer);
            committed = true;
            long firstTicks = Interlocked.Read(ref firstTokenTicks);
            UpdateChatStats(modelId,
                firstTicks < 0 ? -1 : firstTicks * 1000.0 / Stopwatch.Frequency,
                timer.Elapsed.TotalMilliseconds, usage);
            PromptBox.Text = string.Empty;
            // Like the web client, attachments are kept until a reply completes.
            if (context.Images.Count > 0) ClearPendingImages();
            if (TrySaveCurrentChat())
                ShowChatInfo("Response complete",
                    SaveHistoryToggle.IsChecked == true
                        ? "Generated and saved locally." : "Generated locally (not saved).",
                    InfoBarSeverity.Success);
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
            if (!_closing.IsCancellationRequested && !committed)
            {
                if (assistantItem is not null) ChatHistory.Items.Remove(assistantItem);
                if (userItem is not null) ChatHistory.Items.Remove(userItem);
                UpdateChatEmptyState();
            }
            send.Dispose();
            _sendCancellation = null;
            _chatBusy = false;
            if (!_closing.IsCancellationRequested)
                UpdateChatButtons();
        }
    }

    private void CancelButton_Click(object sender, RoutedEventArgs args) =>
        _sendCancellation?.Cancel();

    private void CopyLastReplyButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested
            || _turns.LastOrDefault() is not { Role: "assistant" } reply)
            return;
        try
        {
            var data = new DataPackage();
            data.SetText(reply.Content);
            Clipboard.SetContent(data);
            ShowChatInfo("Reply copied", "The last completed assistant reply is on your clipboard.",
                InfoBarSeverity.Success);
        }
        catch (Exception)
        {
            ShowChatInfo("Clipboard unavailable", "Select text in the conversation to copy it.",
                InfoBarSeverity.Warning);
        }
    }

    private void NewChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy) return;
        _turns.Clear();
        ChatHistory.Items.Clear();
        UpdateChatEmptyState();
        ClearChatStats();
        _currentConversationId = Guid.NewGuid().ToString("N");
        _conversationModelId = SelectedModelId;
        ResetGenerationSettings();
        RefreshSavedChats();
        ShowChatInfo("New chat", "A fresh conversation is ready. Previously saved chats remain in the picker.",
            InfoBarSeverity.Informational);
    }

    private void UpdateChatEmptyState() =>
        ChatEmptyState.Visibility = ChatHistory.Items.Count == 0
            ? Visibility.Visible : Visibility.Collapsed;

    private ListViewItem AppendChat(string speaker, string message, bool preview = false)
    {
        bool isUser = speaker == "You";
        var bubble = new Border
        {
            Child = preview ? new TextBlock
            {
                Text = speaker + "\n" + message,
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
                Margin = new Thickness(8),
            } : BuildChatContent(speaker, message),
            CornerRadius = new CornerRadius(10),
            Padding = new Thickness(10, 8, 10, 8),
            Margin = isUser ? new Thickness(56, 5, 8, 5) : new Thickness(8, 5, 56, 5),
            MaxWidth = 980,
            HorizontalAlignment = isUser ? HorizontalAlignment.Right : HorizontalAlignment.Left,
            // Theme resources keep bubbles legible in light, dark and high contrast.
            Background = Ui.ResBrush(isUser ? "SystemFillColorAttentionBackgroundBrush"
                : "CardBackgroundFillColorDefaultBrush"),
            BorderBrush = Ui.ResBrush("CardStrokeColorDefaultBrush"),
            BorderThickness = new Thickness(1),
        };
        var item = new ListViewItem
        {
            Content = bubble,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Padding = new Thickness(0),
        };
        ChatHistory.Items.Add(item);
        UpdateChatEmptyState();
        ChatHistory.ScrollIntoView(item);
        return item;
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
        if (ChatInfo.Title == title && ChatInfo.Message == message
            && ChatInfo.Severity == severity) return;
        ChatStatusText.Text = severity switch
        {
            InfoBarSeverity.Error => "Error: " + title,
            InfoBarSeverity.Warning => "Warning: " + title,
            _ => title,
        };
        ChatInfo.Title = title;
        ChatInfo.Message = message;
        ToolTipService.SetToolTip(ChatStatusButton, title + ". " + message);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(
            ChatStatusButton, title + ". " + message);
        ChatInfo.Severity = severity;
        (FrameworkElementAutomationPeer.FromElement(ChatStatusButton)
            ?? FrameworkElementAutomationPeer.CreatePeerForElement(ChatStatusButton))
            ?.RaiseNotificationEvent(AutomationNotificationKind.ActionCompleted,
                AutomationNotificationProcessing.ImportantMostRecent,
                title + ". " + message, "InferBridge.ChatStatus");
    }

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(nint hWnd);

    private static string SafeError(Exception error) => error switch
    {
        UnauthorizedAccessException => "API key rejected. Update it on the Engine & devices page.",
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
