using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Native controls only: all network, credentials and inference remain in the engine.
public sealed partial class MainWindow
{
    private bool _hfBusy;
    private bool _hfLoaded;
    private bool _hfSearching;
    private HuggingFaceAccount? _hfAccount;

    private void UpdateHuggingFaceButtons()
    {
        bool idle = !_hfBusy && !_quitting && !_closing.IsCancellationRequested;
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
