using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Native controls only: all network, credentials and inference remain in the engine.
public sealed partial class MainWindow
{
    private bool _hfBusy;
    private bool _hfLoaded;
    private bool _hfSearching;
    private bool _hfPreparing;
    private HuggingFaceAccount? _hfAccount;

    private void UpdateHuggingFaceButtons()
    {
        bool idle = !_hfBusy && !_hfPreparing && !_quitting && !_closing.IsCancellationRequested;
        bool authorized = _engineConnected && _rejectedApiKey is null;
        HfRefreshButton.IsEnabled = idle;
        HfConnectButton.IsEnabled = idle && authorized;
        HfTestButton.IsEnabled = idle && authorized && _hfAccount?.Configured == true;
        HfRemoveButton.IsEnabled = idle && authorized && _hfAccount?.Removable == true;
        HfSearchButton.IsEnabled = idle && !_hfSearching && authorized;
    }

    private void ShowHuggingFaceStatus(HuggingFaceAccount account)
    {
        _hfAccount = account;
        _hfLoaded = true;
        bool connected = account.Configured && account.State == "connected";
        HfAccountText.Text = account.Configured
            ? "Account: " + (account.Username.Length > 0 ? account.Username : "Not verified")
                + "\nVerification: " + account.State.Replace('_', ' ')
                + "\nCredential source: " + (account.Source == "secure_store"
                    ? "InferBridge secure store" : "Environment configuration")
            : "No Hugging Face account token configured.";
        HfInfo.Severity = connected ? InfoBarSeverity.Success
            : account.Configured ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
        HfInfo.Message = connected ? "Account verified by the engine."
            : account.Configured
                ? "Credential requires verification. Use Test access to check it."
                : "Add a read-scoped user token to access restricted models.";
        UpdateHuggingFaceButtons();
    }

    private static string SafeHuggingFaceError(Exception error) => error switch
    {
        HuggingFaceCredentialException => error.Message,
        ArgumentException => "Use a valid local model ID and name, supported device, and compatible token budgets.",
        EngineRequestException => error.Message,
        _ => SafeError(error),
    };

    private async Task RefreshHuggingFaceAsync()
    {
        if (_hfBusy || _quitting || _closing.IsCancellationRequested) return;
        _hfBusy = true;
        UpdateHuggingFaceButtons();
        HfProgress.IsActive = true;
        try
        {
            ShowHuggingFaceStatus(await _backend.GetHuggingFaceAccountAsync(
                ApiKeyBox.Password, _closing.Token));
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _hfLoaded = false;
            _hfAccount = null;
            HfAccountText.Text = "Account status unavailable.";
            HfInfo.Severity = InfoBarSeverity.Warning;
            HfInfo.Message = SafeHuggingFaceError(error);
        }
        finally
        {
            _hfBusy = false;
            HfProgress.IsActive = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }

    private async Task ChangeHuggingFaceAsync(
        Func<Task<HuggingFaceAccount>> action, string success)
    {
        if (_hfBusy || _quitting || _closing.IsCancellationRequested) return;
        _hfBusy = true;
        UpdateHuggingFaceButtons();
        HfProgress.IsActive = true;
        try
        {
            var account = await action();
            if (_closing.IsCancellationRequested) return;
            ShowHuggingFaceStatus(account);
            HfInfo.Message = success;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _hfLoaded = false; // a timeout may occur after a successful mutation
            HfInfo.Severity = InfoBarSeverity.Warning;
            HfInfo.Message = SafeHuggingFaceError(error)
                + " Refresh account status before trying again.";
        }
        finally
        {
            _hfBusy = false;
            HfProgress.IsActive = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }

    private async void HfRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshHuggingFaceAsync();

    private async void HfTestButton_Click(object sender, RoutedEventArgs args)
    {
        if (_hfAccount?.Configured != true) return;
        await ChangeHuggingFaceAsync(
            () => _backend.TestHuggingFaceAccountAsync(ApiKeyBox.Password, _closing.Token),
            "Hugging Face verified the current account token.");
    }

    private async void HfConnectButton_Click(object sender, RoutedEventArgs args)
    {
        if (_hfBusy || !_engineConnected || _quitting || _closing.IsCancellationRequested)
            return;
        _hfBusy = true;
        UpdateHuggingFaceButtons();
        var secretBox = new PasswordBox
        {
            Header = "Read-scoped Hugging Face token",
            PlaceholderText = "hf_…",
            MaxLength = 503,
            Width = 340,
        };
        try
        {
            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Connect Hugging Face",
                Content = secretBox,
                PrimaryButtonText = "Verify and save",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            string token = secretBox.Password;
            secretBox.Password = string.Empty;
            _hfBusy = false;
            await ChangeHuggingFaceAsync(
                () => _backend.SaveHuggingFaceTokenAsync(
                    token, ApiKeyBox.Password, _closing.Token),
                "Hugging Face token verified and stored by the local engine.");
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            HfInfo.Severity = InfoBarSeverity.Warning;
            HfInfo.Message = SafeHuggingFaceError(error);
        }
        finally
        {
            secretBox.Password = string.Empty;
            _hfBusy = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }

    private async void HfRemoveButton_Click(object sender, RoutedEventArgs args)
    {
        if (_hfBusy || _hfAccount?.Removable != true
            || _quitting || _closing.IsCancellationRequested) return;
        _hfBusy = true;
        UpdateHuggingFaceButtons();
        try
        {
            var confirmation = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Remove Hugging Face token?",
                Content = "Remove the locally stored account token from the Python engine? Environment-managed tokens cannot be removed here. Downloads already in progress may continue.",
                PrimaryButtonText = "Remove token",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await confirmation.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            _hfBusy = false;
            await ChangeHuggingFaceAsync(
                () => _backend.RemoveHuggingFaceTokenAsync(
                    ApiKeyBox.Password, _closing.Token),
                "Stored token removed. Refresh if an environment fallback remains.");
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            HfInfo.Severity = InfoBarSeverity.Warning;
            HfInfo.Message = SafeHuggingFaceError(error);
        }
        finally
        {
            _hfBusy = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }

    private async void HfSearchButton_Click(object sender, RoutedEventArgs args)
    {
        if (_hfSearching || _hfBusy || !_engineConnected
            || _quitting || _closing.IsCancellationRequested) return;
        string query = HfSearchBox.Text.Trim();
        string task = (HfTaskPicker.SelectedItem as ComboBoxItem)?.Tag as string
            ?? "text-generation";
        if (query.Length is < 1 or > 200)
        {
            HfSearchSummary.Text = "Enter a model name or repository ID.";
            return;
        }
        _hfSearching = true;
        UpdateHuggingFaceButtons();
        HfSearchSummary.Text = "Searching the Hugging Face Hub through the local engine…";
        try
        {
            IReadOnlyList<HuggingFaceModel> models = await _backend.SearchHuggingFaceAsync(
                query, task, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            HfSearchResults.Children.Clear();
            foreach (HuggingFaceModel model in models)
            {
                var row = new StackPanel { Spacing = 5 };
                row.Children.Add(new TextBlock
                {
                    Text = model.Id,
                    TextWrapping = TextWrapping.Wrap,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    IsTextSelectionEnabled = true,
                });
                row.Children.Add(new TextBlock
                {
                    Text = model.Task + " · " + model.Backend
                        + " · " + model.Downloads.ToString("N0") + " downloads · "
                        + model.Likes.ToString("N0") + " likes",
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.8,
                });
                var open = new Button
                {
                    Content = "Review model on Hugging Face",
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Tag = model.ModelUrl,
                };
                open.Click += HfOpenModel_Click;
                row.Children.Add(open);
                var prepare = new Button
                {
                    Content = "Configure download",
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Tag = model,
                };
                AutomationProperties.SetName(prepare, "Configure download for " + model.Id);
                prepare.Click += HfPrepareModel_Click;
                row.Children.Add(prepare);
                HfSearchResults.Children.Add(row);
            }
            HfSearchSummary.Text = models.Count == 0
                ? "No models found. Try another search."
                : models.Count + " remote candidate models. These are not verified as compatible, downloaded or loaded.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            // Preserve existing results when a new query fails.
            HfSearchSummary.Text = "Search failed: " + SafeHuggingFaceError(error);
        }
        finally
        {
            _hfSearching = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }


    private async void HfPrepareModel_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: HuggingFaceModel model }
            || _hfBusy || _hfPreparing || _hfSearching || _isManaging
            || !_engineConnected || _rejectedApiKey is not null
            || _quitting || _closing.IsCancellationRequested)
            return;

        _hfPreparing = true;
        UpdateHuggingFaceButtons();
        HfProgress.IsActive = true;
        try
        {
            // Build choices from current backend discovery, not assumed GPU/NPU support.
            DeviceInventory devices = await _backend.GetDevicesAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            string[] available = new[] { "CPU", "GPU", "NPU" }.Where(target =>
                devices.Available.Any(found => found == target
                    || found.StartsWith(target + ".", StringComparison.Ordinal))).ToArray();
            if (available.Length == 0)
                throw new InvalidOperationException(
                    "The engine reported no supported inference devices. Scan hardware first.");

            var modelId = new TextBox
            {
                Header = "Local model ID",
                Text = model.SuggestedLocalId,
                MaxLength = 128,
                PlaceholderText = "Unique filesystem-safe identifier",
            };
            string suggestedName = model.Id.Split('/')[^1];
            var modelName = new TextBox
            {
                Header = "Model display name",
                Text = suggestedName[..Math.Min(160, suggestedName.Length)],
                MaxLength = 160,
            };
            var target = new ComboBox
            {
                Header = "Conversion and inference device",
                HorizontalAlignment = HorizontalAlignment.Left,
                MinWidth = 230,
            };
            foreach (string device in available)
                target.Items.Add(new ComboBoxItem { Content = device, Tag = device });
            target.SelectedIndex = 0;

            var format = new ComboBox
            {
                Header = "Weight format",
                HorizontalAlignment = HorizontalAlignment.Left,
                MinWidth = 230,
            };
            foreach (string weight in new[] { "int4", "int8", "fp16" })
                format.Items.Add(new ComboBoxItem { Content = weight, Tag = weight });
            format.SelectedIndex = model.Backend == "openvino-embeddings" ? 1 : 0;

            var context = new ComboBox
            {
                Header = "Requested context tokens",
                HorizontalAlignment = HorizontalAlignment.Left,
                MinWidth = 230,
            };
            foreach (int tokenLimit in model.Backend == "openvino-embeddings"
                ? new[] { 512, 1024, 2048 } : new[] { 2048, 4096, 8192 })
                context.Items.Add(new ComboBoxItem
                {
                    Content = tokenLimit.ToString("N0") + " tokens", Tag = tokenLimit,
                });
            context.SelectedIndex = 0;

            var loadAfter = new CheckBox
            {
                Content = "Load automatically after preparation",
                IsChecked = model.Backend != "openvino-embeddings",
            };
            var layout = new StackPanel { Spacing = 12 };
            layout.Children.Add(new TextBlock
            {
                Text = "Remote source: " + model.Id + "\nBackend: " + model.Backend
                    + (devices.Mock ? "\nMock device discovery: not verified on hardware." : "")
                    + "\nCheck the publisher license, repository files, memory and disk requirements before proceeding. InferBridge will not execute remote repository code.",
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            });
            layout.Children.Add(modelId);
            layout.Children.Add(modelName);
            layout.Children.Add(target);
            layout.Children.Add(format);
            layout.Children.Add(context);
            layout.Children.Add(loadAfter);

            var dialog = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Configure model download",
                Content = new ScrollViewer
                {
                    Content = layout,
                    MaxHeight = 420,
                    VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                },
                PrimaryButtonText = "Register and prepare",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            var request = new HuggingFaceDownloadRequest(
                modelId.Text.Trim(), modelName.Text.Trim(), model.Id,
                model.Backend,
                (format.SelectedItem as ComboBoxItem)?.Tag as string ?? "int4",
                (target.SelectedItem as ComboBoxItem)?.Tag as string ?? "",
                (context.SelectedItem as ComboBoxItem)?.Tag is int tokens ? tokens : 0,
                model.Backend == "openvino-embeddings" ? 0 : 512,
                loadAfter.IsChecked == true);

            HfSearchSummary.Text = "Rechecking local catalog and device, then requesting preparation…";
            string result = await _backend.QueueHuggingFaceModelAsync(
                request, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            HfSearchSummary.Text = result;
            HfInfo.Severity = InfoBarSeverity.Success;
            HfInfo.Message = "Registration accepted. The Python engine owns all download and conversion progress.";
            // The model library is the authoritative place to monitor progress.
            await RefreshAsync();
            if (_closing.IsCancellationRequested) return;
            var selected = ModelsList.Items.OfType<ListViewItem>().FirstOrDefault(item =>
                (item.Tag as ModelInfo)?.Id == request.LocalId);
            if (selected is not null) ModelsList.SelectedItem = selected;
            var libraryPage = MainNavigation.MenuItems.OfType<NavigationViewItem>()
                .FirstOrDefault(item => (string?)item.Tag == "models");
            if (libraryPage is not null) MainNavigation.SelectedItem = libraryPage;
            ShowModelsInfo(result, InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            HfInfo.Severity = InfoBarSeverity.Warning;
            HfInfo.Message = SafeHuggingFaceError(error);
            HfSearchSummary.Text = "Request not confirmed. Check the Model library before trying again; a timeout can occur after registration succeeds.";
        }
        finally
        {
            _hfPreparing = false;
            HfProgress.IsActive = false;
            if (!_closing.IsCancellationRequested) UpdateHuggingFaceButtons();
        }
    }

    private void HfOpenModel_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: string link }
            || !link.StartsWith("https://huggingface.co/", StringComparison.Ordinal))
            return;
        try
        {
            using var browser = Process.Start(new ProcessStartInfo(link)
            {
                UseShellExecute = true,
            });
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            HfSearchSummary.Text = "Could not open model page: " + SafeHuggingFaceError(error);
        }
    }
}
