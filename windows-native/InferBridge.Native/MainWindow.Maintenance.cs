using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Web-parity maintenance surfaces: network/API listener settings, engine
// restart, runtime model-health remediation and opt-in update settings. Every
// action goes through the engine endpoint the web GUI uses; the engine keeps
// enforcing its own authentication, local-UI and safety rules.
public sealed partial class MainWindow
{
    private NetworkSettingsStatus? _networkStatus;
    private bool _networkBusy;
    private bool _networkLoaded;
    private RuntimeHealthReport? _modelHealth;
    private bool _modelHealthBusy;
    private ReleaseStatusInfo? _releaseStatus;
    private ReleaseCheckInfo? _lastUpdateCheck;
    private bool _releaseBusy;

    private XamlRoot DialogRoot => ((FrameworkElement)Content).XamlRoot;

    private async Task<bool> ConfirmAsync(string title, string message, string action)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = DialogRoot, Title = title,
            Content = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap },
            PrimaryButtonText = action, CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary && !_closing.IsCancellationRequested;
    }

    private bool MaintenanceReady => _engineConnected && _rejectedApiKey is null
        && !_quitting && !_closing.IsCancellationRequested;

    // --- Network and API listener ------------------------------------------

    private void UpdateNetworkControls()
    {
        bool editable = MaintenanceReady && !_networkBusy && _networkStatus is not null;
        bool keyLocked = _networkStatus?.ApiKeyEnvironmentOverride == true;
        NetworkAllowLanToggle.IsEnabled = editable && _networkStatus?.HostEnvironmentOverride != true;
        NetworkCorsBox.IsEnabled = editable && _networkStatus?.CorsEnvironmentOverride != true;
        NetworkApiKeyBox.IsEnabled = editable && !keyLocked && NetworkGenerateKeyCheck.IsChecked != true
            && NetworkRemoveKeyCheck.IsChecked != true;
        NetworkGenerateKeyCheck.IsEnabled = editable && !keyLocked && NetworkRemoveKeyCheck.IsChecked != true;
        NetworkRemoveKeyCheck.IsEnabled = editable && !keyLocked && NetworkGenerateKeyCheck.IsChecked != true
            && _networkStatus?.ApiKeySource == "secure_store";
        NetworkApplyButton.IsEnabled = editable;
        NetworkRefreshButton.IsEnabled = MaintenanceReady && !_networkBusy;
    }

    private void NetworkKeyOption_Changed(object sender, RoutedEventArgs args) => UpdateNetworkControls();

    private void RenderNetworkStatus(NetworkSettingsStatus status)
    {
        _networkStatus = status;
        NetworkStatusText.Text =
            "Listening on: " + status.ActiveBindHost + " · Local endpoint: " + status.LocalEndpoint
            + "\nLAN: " + (status.LanActive ? "active" : "not active")
            + (status.LanSettingEnabled != status.LanActive ? " (saved setting: "
                + (status.LanSettingEnabled ? "enabled" : "disabled") + ")" : "")
            + (status.LanEndpoints.Count > 0 ? "\nLAN endpoints: " + string.Join(", ", status.LanEndpoints) : "")
            + "\nAPI key: " + (status.ApiKeyConfigured ? "configured (" + (status.ApiKeySource ?? "unknown")
                + ")" : "not configured")
            + "\nBrowser origins: " + (status.CorsOrigins.Length == 0 ? "none (same-origin only)" : status.CorsOrigins)
            + (status.HostEnvironmentOverride || status.CorsEnvironmentOverride || status.ApiKeyEnvironmentOverride
                ? "\nSome values are controlled by environment variables and cannot be changed here." : "")
            + (status.RestartRequired ? "\nRestart the engine to apply saved changes." : "")
            + string.Concat(status.Warnings.Select(warning => "\n⚠ " + warning));
        NetworkAllowLanToggle.IsOn = status.LanSettingEnabled;
        NetworkCorsBox.Text = status.CorsOrigins;
        NetworkApiKeyBox.Password = string.Empty;
        NetworkGenerateKeyCheck.IsChecked = false;
        NetworkRemoveKeyCheck.IsChecked = false;
    }

    private async Task RefreshNetworkSettingsAsync()
    {
        if (_networkBusy || !MaintenanceReady) return;
        _networkBusy = true;
        UpdateNetworkControls();
        try
        {
            RenderNetworkStatus(await _backend.GetNetworkSettingsAsync(ApiKeyBox.Password, _closing.Token));
            _networkLoaded = true;
            NetworkInfo.Severity = _networkStatus!.RestartRequired ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
            NetworkInfo.Message = _networkStatus.RestartRequired
                ? "Saved settings differ from the running listener. Restart the engine to apply them."
                : "Settings loaded from the engine.";
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _networkStatus = null;
            NetworkInfo.Severity = InfoBarSeverity.Error;
            NetworkInfo.Message = "Could not load network settings: " + SafeError(error);
        }
        finally
        {
            _networkBusy = false;
            if (!_closing.IsCancellationRequested) UpdateNetworkControls();
        }
    }

    private async void NetworkRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshNetworkSettingsAsync();

    private async void NetworkApplyButton_Click(object sender, RoutedEventArgs args)
    {
        if (_networkBusy || !MaintenanceReady || _networkStatus is not { } current) return;
        string cors = NetworkCorsBox.Text.Trim();
        string? newKey = string.IsNullOrEmpty(NetworkApiKeyBox.Password) ? null : NetworkApiKeyBox.Password;
        bool generate = NetworkGenerateKeyCheck.IsChecked == true;
        bool remove = NetworkRemoveKeyCheck.IsChecked == true;
        bool wildcard = cors == "*";
        try
        {
            var update = new NetworkSettingsUpdate(NetworkAllowLanToggle.IsOn, cors, newKey, generate, remove, wildcard);
            update.Validate();
            if (remove && !await ConfirmAsync("Remove API key?",
                    "Remove the desktop-managed API key? LAN access cannot remain enabled without authentication.",
                    "Remove key")) return;
            if (wildcard && current.CorsOrigins != "*" && !await ConfirmAsync("Allow any browser origin?",
                    "Wildcard CORS allows browser scripts from any origin to attempt requests. Keep API authentication enabled and use this only when required. Continue?",
                    "Allow wildcard")) return;
            if (update.AllowLan && !current.LanSettingEnabled && !await ConfirmAsync("Enable LAN access?",
                    "Other devices on your network will be able to reach the API with the API key. Windows Firewall may prompt; allow InferBridge only on trusted Private networks.",
                    "Enable LAN")) return;
            _networkBusy = true;
            UpdateNetworkControls();
            NetworkInfo.Severity = InfoBarSeverity.Informational;
            NetworkInfo.Message = "Saving network settings…";
            var result = await _backend.UpdateNetworkSettingsAsync(update, ApiKeyBox.Password, _closing.Token);
            RenderNetworkStatus(result.Status);
            string? sessionKey = result.GeneratedApiKey ?? newKey;
            if (sessionKey is not null)
                ApiKeyBox.Password = sessionKey; // Session-only, as for a typed key; never written to disk.
            NetworkInfo.Severity = InfoBarSeverity.Success;
            NetworkInfo.Message = result.Message;
            if (result.GeneratedApiKey is { } generated)
            {
                var keyBox = new TextBox
                {
                    Text = generated, IsReadOnly = true,
                    FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(keyBox, "Generated API key");
                var panel = new StackPanel { Spacing = 10 };
                panel.Children.Add(new TextBlock
                {
                    Text = "Copy this key into your API clients now. It will not be shown again. This window uses it for the current session only.",
                    TextWrapping = TextWrapping.Wrap,
                });
                panel.Children.Add(keyBox);
                var copy = new Button { Content = "Copy key" };
                copy.Click += (_, _) => CopyChatMessage(generated, "API key");
                panel.Children.Add(copy);
                await new ContentDialog
                {
                    XamlRoot = DialogRoot, Title = "New API key", Content = panel, CloseButtonText = "Done",
                }.ShowAsync();
                keyBox.Text = string.Empty;
            }
            if (result.Status.RestartRequired && await ConfirmAsync("Restart engine now?",
                    "Listener changes apply after the engine restarts. Loaded models will be unloaded.", "Restart engine"))
                await RequestEngineRestartAsync();
        }
        catch (ArgumentException error)
        {
            NetworkInfo.Severity = InfoBarSeverity.Warning;
            NetworkInfo.Message = error.Message;
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            // A lost response may still have saved: refresh, never replay.
            NetworkInfo.Severity = InfoBarSeverity.Error;
            NetworkInfo.Message = "Save not confirmed: " + SafeError(error) + " Load settings again to check.";
        }
        finally
        {
            NetworkApiKeyBox.Password = string.Empty;
            _networkBusy = false;
            if (!_closing.IsCancellationRequested) UpdateNetworkControls();
        }
    }

    // --- Engine restart -----------------------------------------------------

    private async Task RequestEngineRestartAsync()
    {
        try
        {
            string message = await _backend.RestartEngineAsync(ApiKeyBox.Password, _closing.Token);
            OperationsInfo.Severity = InfoBarSeverity.Informational;
            OperationsInfo.Message = message;
            NetworkInfo.Message = message;
            _networkStatus = null;
            _networkLoaded = false;
            UpdateNetworkControls();
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            OperationsInfo.Severity = InfoBarSeverity.Error;
            OperationsInfo.Message = "Restart not accepted: " + SafeError(error);
            NetworkInfo.Message = OperationsInfo.Message;
        }
    }

    private async void RestartEngineButton_Click(object sender, RoutedEventArgs args)
    {
        if (!MaintenanceReady || _chatBusy || _isManaging) return;
        RestartEngineButton.IsEnabled = false;
        try
        {
            if (await ConfirmAsync("Restart the local engine?",
                    "The tray controller restarts the engine. Loaded models are unloaded and in-progress requests end. The engine refuses while a benchmark or model preparation is running.",
                    "Restart engine"))
                await RequestEngineRestartAsync();
        }
        finally
        {
            if (!_closing.IsCancellationRequested) RestartEngineButton.IsEnabled = MaintenanceReady;
        }
    }

    // --- Runtime upgrade and model health ----------------------------------

    private static string HealthConfirmation(string action) => action switch
    {
        "rebuild_compiled_cache" => "Clear the shared OpenVINO compiled cache, then compile and validate this model again? Other models will compile again on their next load.",
        "reconvert" => "Reconvert this model? Existing Hugging Face cache will be reused when available, and transactional recovery remains enabled.",
        "leave_unchanged" => "Leave this model unchanged for the current runtime and acknowledge the compatibility warning?",
        _ => "Revalidate this model with the current runtime? It must be unloaded.",
    };

    private void RenderModelHealth(RuntimeHealthReport report)
    {
        _modelHealth = report;
        ModelHealthSummary.Text = "InferBridge " + (report.Application.Length > 0 ? report.Application : "unknown")
            + " · OpenVINO " + (report.OpenVino.Length > 0 ? report.OpenVino : "not detected")
            + " · OpenVINO GenAI " + (report.OpenVinoGenAi.Length > 0 ? report.OpenVinoGenAi : "not detected")
            + "\n" + report.NeedsAttention + " model(s) need maintenance."
            + (report.RuntimeChangeDetected ? " OpenVINO changed since one or more conversions." : "");
        ModelHealthRows.Children.Clear();
        foreach (var model in report.Models
            .Where(row => row.Action != "leave_unchanged" || row.CanLeaveUnchanged).Take(200))
        {
            var row = new StackPanel { Spacing = 6 };
            row.Children.Add(new TextBlock
            {
                Text = model.Name + " (" + model.ModelId + ")\nHealth: " + model.HealthLabel
                    + " · Recommended: " + model.ActionLabel
                    + (model.Reason.Length > 0 ? "\n" + model.Reason : ""),
                TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
            });
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            if (model.Action != "leave_unchanged")
            {
                string label = model.ActionLabel.Length > 0 ? model.ActionLabel : model.Action;
                var primary = new Button
                {
                    Content = label, Tag = (model.ModelId, model.Action),
                    IsEnabled = model.ActionAvailable && !_modelHealthBusy,
                };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(primary, label + " " + model.Name);
                primary.Click += ModelHealthAction_Click;
                actions.Children.Add(primary);
            }
            if (model.CanLeaveUnchanged && model.Action != "leave_unchanged")
            {
                var leave = new Button
                {
                    Content = "Leave unchanged", Tag = (model.ModelId, "leave_unchanged"),
                    IsEnabled = !_modelHealthBusy,
                };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(leave, "Leave " + model.Name + " unchanged");
                leave.Click += ModelHealthAction_Click;
                actions.Children.Add(leave);
            }
            row.Children.Add(actions);
            ModelHealthRows.Children.Add(new Border
            {
                Child = row, Padding = new Thickness(12), CornerRadius = new CornerRadius(8),
                BorderThickness = new Thickness(1),
                BorderBrush = Ui.ResBrush("CardStrokeColorDefaultBrush"),
                Background = Ui.ResBrush("CardBackgroundFillColorDefaultBrush"),
            });
        }
        if (ModelHealthRows.Children.Count == 0)
            ModelHealthRows.Children.Add(Ui.Text("No model needs maintenance.", secondary: true));
        UpdateModelHealthButtons();
    }

    private void UpdateModelHealthButtons()
    {
        bool ready = MaintenanceReady && !_modelHealthBusy;
        ModelHealthRefreshButton.IsEnabled = ready;
        ModelHealthRevalidateButton.IsEnabled = ready && _modelHealth?.BatchCandidates("revalidate").Count > 0;
        ModelHealthCacheButton.IsEnabled = ready && _modelHealth?.BatchCandidates("rebuild_compiled_cache").Count > 0;
        UpdateModelRowStates();
    }

    private async Task RefreshModelHealthAsync()
    {
        if (_modelHealthBusy || !MaintenanceReady) return;
        _modelHealthBusy = true;
        UpdateModelHealthButtons();
        try
        {
            RenderModelHealth(await _backend.GetRuntimeHealthAsync(ApiKeyBox.Password, _closing.Token));
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ModelHealthSummary.Text = "Model health unavailable: " + SafeError(error);
        }
        finally
        {
            _modelHealthBusy = false;
            if (!_closing.IsCancellationRequested) UpdateModelHealthButtons();
        }
    }

    private async void ModelHealthRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshModelHealthAsync();

    private async void ModelHealthAction_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: ValueTuple<string, string> target } || _modelHealthBusy || !MaintenanceReady)
            return;
        (string modelId, string action) = target;
        if (!await ConfirmAsync("Model maintenance", HealthConfirmation(action), "Continue")) return;
        _modelHealthBusy = true;
        UpdateModelHealthButtons();
        string message;
        try
        {
            ModelHealthSummary.Text = "Applying model maintenance…";
            message = await _backend.RunRuntimeHealthActionAsync(modelId, action, ApiKeyBox.Password, _closing.Token);
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            message = "Maintenance not completed: " + SafeError(error);
        }
        finally
        {
            _modelHealthBusy = false;
        }
        if (_closing.IsCancellationRequested) return;
        await RefreshModelHealthAsync();
        ModelHealthSummary.Text = message + "\n" + ModelHealthSummary.Text;
    }

    private async void ModelHealthBatch_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: string action } || _modelHealthBusy || !MaintenanceReady
            || _modelHealth is null) return;
        int count = _modelHealth.BatchCandidates(action).Count;
        string text = action == "rebuild_compiled_cache"
            ? "Clear the shared compiled cache and warm " + count + " affected model(s)? All models must be unloaded. Models outside this set will compile again on their next load."
            : "Revalidate " + count + " model(s) sequentially with the current runtime? All models must be unloaded.";
        if (count == 0 || !await ConfirmAsync("Model maintenance batch", text, "Run batch")) return;
        _modelHealthBusy = true;
        UpdateModelHealthButtons();
        string message;
        try
        {
            ModelHealthSummary.Text = "Running maintenance batch…";
            var (_, failures) = await _backend.RunRuntimeHealthBatchAsync(action, ApiKeyBox.Password, _closing.Token);
            message = failures > 0
                ? "Batch completed with " + failures + " model(s) still needing attention."
                : "Batch completed successfully.";
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            message = "Batch not completed: " + SafeError(error);
        }
        finally
        {
            _modelHealthBusy = false;
        }
        if (_closing.IsCancellationRequested) return;
        await RefreshModelHealthAsync();
        ModelHealthSummary.Text = message + "\n" + ModelHealthSummary.Text;
    }

    // --- About & updates ------------------------------------------------------

    private void UpdateReleaseControls()
    {
        bool editable = MaintenanceReady && !_releaseBusy && _releaseStatus is { StoreManaged: false };
        UpdateChecksToggle.IsEnabled = editable;
        UpdateChannelPicker.IsEnabled = editable;
        SaveUpdateSettingsButton.IsEnabled = editable;
        CheckUpdatesButton.IsEnabled = editable && _releaseStatus!.Settings.Enabled;
        ReleaseRefreshButton.IsEnabled = MaintenanceReady && !_releaseBusy;
        SkipUpdateButton.IsEnabled = editable;
    }

    private void RenderReleaseStatus(ReleaseStatusInfo status)
    {
        _releaseStatus = status;
        ReleaseStatusText.Text = "InferBridge " + status.Version + " · " + status.Channel + " build · "
            + status.InstallationMode + " installation"
            + (status.StoreManaged ? "\nMicrosoft Store delivers updates for this installation."
                : "\nUpdate checks: " + (status.Settings.Enabled ? "enabled (" + status.Settings.Channel + ")" : "disabled")
                    + (status.LatestCheckedVersion is { } latest ? " · last seen version " + latest : ""));
        UpdateChecksToggle.IsOn = status.Settings.Enabled;
        UpdateChannelPicker.SelectedItem = UpdateChannelPicker.Items.Cast<ComboBoxItem>()
            .FirstOrDefault(item => (string?)item.Tag == status.Settings.Channel);
        UpdateReleaseControls();
    }

    private async Task RunReleaseOperationAsync(Func<Task> operation)
    {
        if (_releaseBusy || !MaintenanceReady) return;
        _releaseBusy = true;
        UpdateReleaseControls();
        try { await operation(); }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            UpdateCheckResult.Text = SafeError(error);
        }
        finally
        {
            _releaseBusy = false;
            if (!_closing.IsCancellationRequested) UpdateReleaseControls();
        }
    }

    private async Task RefreshReleaseStatusAsync() =>
        await RunReleaseOperationAsync(async () =>
            RenderReleaseStatus(await _backend.GetReleaseStatusAsync(ApiKeyBox.Password, _closing.Token)));

    private async void ReleaseRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshReleaseStatusAsync();

    private async Task SaveReleaseSettingsAsync(ReleaseUpdateSettings settings)
    {
        await _backend.SaveReleaseSettingsAsync(settings, ApiKeyBox.Password, _closing.Token);
        RenderReleaseStatus(await _backend.GetReleaseStatusAsync(ApiKeyBox.Password, _closing.Token));
        UpdateCheckResult.Text = "Update settings saved.";
    }

    private async void SaveUpdateSettingsButton_Click(object sender, RoutedEventArgs args)
    {
        if (_releaseStatus is not { } status) return;
        string channel = (UpdateChannelPicker.SelectedItem as ComboBoxItem)?.Tag as string ?? status.Settings.Channel;
        await RunReleaseOperationAsync(() => SaveReleaseSettingsAsync(
            status.Settings with { Enabled = UpdateChecksToggle.IsOn, Channel = channel }));
    }

    private async void CheckUpdatesButton_Click(object sender, RoutedEventArgs args) =>
        await RunReleaseOperationAsync(async () =>
        {
            UpdateCheckResult.Text = "Checking the official release feed through the engine…";
            UpdateDownloadLink.Visibility = Visibility.Collapsed;
            SkipUpdateButton.Visibility = Visibility.Collapsed;
            var check = await _backend.CheckForUpdateAsync(ApiKeyBox.Password, _closing.Token);
            _lastUpdateCheck = check;
            UpdateCheckResult.Text = check.Status switch
            {
                "available" => "Update " + check.LatestVersion + " is available."
                    + (check.Sha256 is { } sha ? "\nSHA-256: " + sha + "\nVerify the checksum and Authenticode signature after download." : "")
                    + (check.CompatibilityWarning is { } warning ? "\n⚠ " + warning : ""),
                "current" => "InferBridge is up to date.",
                "offline" => "The update check could not reach GitHub. Local inference remains available.",
                "disabled" => check.Message.Length > 0 ? check.Message : "Update checks are disabled.",
                _ => check.Message.Length > 0 ? check.Message : "Update check result: " + check.Status,
            };
            if (check.Status == "available" && check.DownloadUrl is { } url && BackendClient.IsOfficialReleaseUrl(url))
            {
                UpdateDownloadLink.NavigateUri = new Uri(url);
                UpdateDownloadLink.Visibility = Visibility.Visible;
            }
            SkipUpdateButton.Visibility = check.Status == "available" && check.LatestVersion is not null
                ? Visibility.Visible : Visibility.Collapsed;
        });

    private async void SkipUpdateButton_Click(object sender, RoutedEventArgs args)
    {
        if (_releaseStatus is not { } status || _lastUpdateCheck?.LatestVersion is not { } version) return;
        await RunReleaseOperationAsync(async () =>
        {
            await SaveReleaseSettingsAsync(status.Settings with
            {
                SkippedVersions = status.Settings.SkippedVersions.Append(version).Distinct().TakeLast(50).ToArray(),
            });
            SkipUpdateButton.Visibility = Visibility.Collapsed;
            UpdateDownloadLink.Visibility = Visibility.Collapsed;
            UpdateCheckResult.Text = "Version " + version + " will be skipped.";
        });
    }
}
