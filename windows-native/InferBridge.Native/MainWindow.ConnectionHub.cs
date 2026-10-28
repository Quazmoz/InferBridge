using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private LocalApiMetadata? _hubMetadata;
    private bool _hubBusy;
    private int _hubGeneration;

    private string? SelectedHubModelId =>
        (HubModelPicker.SelectedItem as ComboBoxItem)?.Tag as string;

    private void InvalidateConnectionHub()
    {
        _hubGeneration++;
        _hubMetadata = null;
        HubBaseUrl.Text = "Not connected";
        HubListenerStatus.Text = "";
        HubAuthenticationStatus.Text = "";
        HubLanStatus.Text = "";
        HubModelPicker.Items.Clear();
        HubModelPicker.IsEnabled = false;
        HubModelHint.Text = "Reconnect and refresh to inspect available models.";
        HubPythonExample.Text = "";
        HubCurlExample.Text = "";
        HubTestResults.Items.Clear();
        HubTestSummary.Text = "No self-test run for this engine session.";
        UpdateHubButtons();
        RenderToolCards();
    }

    private void UpdateHubButtons()
    {
        bool ready = _hubMetadata is not null && !_hubBusy && !_quitting
            && !_closing.IsCancellationRequested;
        HubProgress.IsActive = _hubBusy;
        HubRefreshButton.IsEnabled = !_hubBusy && !_quitting;
        HubCopyBaseUrlButton.IsEnabled = ready;
        HubCopyPythonButton.IsEnabled = ready && SelectedHubModelId is not null;
        HubCopyCurlButton.IsEnabled = ready;
        HubModelPicker.IsEnabled = ready && HubModelPicker.Items.Count > 0;
        HubTestButton.IsEnabled = ready
            && (!_hubMetadata!.AuthenticationRequired || !string.IsNullOrWhiteSpace(ApiKeyBox.Password))
            && (_hubMetadata.UsableModels.Count < 2 || SelectedHubModelId is not null);
        UpdateToolButtons();
    }

    private async Task RefreshConnectionHubAsync()
    {
        if (_hubBusy || _quitting || _closing.IsCancellationRequested) return;
        _hubBusy = true;
        int generation = ++_hubGeneration;
        UpdateHubButtons();
        HubInfo.Severity = InfoBarSeverity.Informational;
        HubInfo.Message = "Verifying the running engine and reading connection details…";
        try
        {
            LocalApiMetadata metadata = await _backend.GetLocalApiMetadataAsync(
                ApiKeyBox.Password, _closing.Token);
            if (generation != _hubGeneration || _closing.IsCancellationRequested) return;
            string? previous = SelectedHubModelId;
            _hubMetadata = metadata;
            HubBaseUrl.Text = metadata.BaseUrl;
            HubListenerStatus.Text = "Active port: " + metadata.Port
                + " · Configured listener: " + metadata.ListenHost;
            HubAuthenticationStatus.Text = metadata.AuthenticationLabel;
            HubLanStatus.Text = metadata.LanLabel + ". " + metadata.LanDetail;
            HubModelPicker.Items.Clear();
            foreach (var model in metadata.UsableModels)
            {
                var option = new ComboBoxItem
                {
                    Tag = model.Id,
                    Content = model.Name + " (" + model.Id + ")"
                        + (model.Busy ? " · Busy" : ""),
                };
                HubModelPicker.Items.Add(option);
                if (model.Id == previous) HubModelPicker.SelectedItem = option;
            }
            if (HubModelPicker.SelectedItem is null && metadata.UsableModels.Count == 1)
                HubModelPicker.SelectedIndex = 0;
            HubModelHint.Text = metadata.UsableModels.Count switch
            {
                0 => "No loaded generation model. Model listing and authentication can still be tested.",
                1 => "One loaded generation-capable model is available.",
                _ => "Choose the exact loaded model to use in the example and self-test.",
            };
            HubCurlExample.Text = metadata.WindowsCurlExample();
            UpdateHubPythonExample();
            HubTestResults.Items.Clear();
            HubTestSummary.Text = "No self-test run for this engine session.";
            HubInfo.Severity = InfoBarSeverity.Success;
            HubInfo.Message = "Connection information is current. No API key is displayed or copied.";
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            if (generation == _hubGeneration)
            {
                InvalidateConnectionHub();
                HubInfo.Severity = InfoBarSeverity.Warning;
                HubInfo.Message = SafeError(error);
            }
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _hubBusy = false;
            if (!_closing.IsCancellationRequested) UpdateHubButtons();
        }
    }

    private void UpdateHubPythonExample()
    {
        HubPythonExample.Text = _hubMetadata is not null && SelectedHubModelId is string id
            ? _hubMetadata.PythonExample(id) : "Select a loaded chat model to get a working example.";
        UpdateHubButtons();
        RenderToolCards();
    }

    private void HubModelPicker_SelectionChanged(object sender, SelectionChangedEventArgs args) =>
        UpdateHubPythonExample();

    private async void HubRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshConnectionHubAsync();

    private async void HubTestButton_Click(object sender, RoutedEventArgs args) =>
        await RunHubSelfTestAsync();

    /// <summary>
    /// The one engine-owned self-test, shared by the self-test card and every
    /// "Test connection" button. Returns the summary, or null if it did not run.
    /// </summary>
    private async Task<string?> RunHubSelfTestAsync()
    {
        if (_hubBusy || _hubMetadata is not { } metadata || _quitting
            || _closing.IsCancellationRequested) return null;
        string? selected = SelectedHubModelId;
        _hubBusy = true;
        int generation = ++_hubGeneration;
        UpdateHubButtons();
        HubTestResults.Items.Clear();
        HubTestSummary.Text = "Running synthetic local API checks…";
        HubInfo.Severity = InfoBarSeverity.Informational;
        HubInfo.Message = "The engine owns the self-test. Closing this page does not cancel its inference worker.";
        try
        {
            var report = await _backend.RunLocalApiSelfTestAsync(
                metadata, selected, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested || generation != _hubGeneration) return null;
            foreach (var result in report.Tests)
            {
                string heading = result.Label + " · " + result.Status.ToUpperInvariant()
                    + (result.DurationMs is int duration ? " · " + duration + " ms" : "");
                var content = new StackPanel { Spacing = 3, Margin = new Thickness(4, 7, 4, 7) };
                content.Children.Add(new TextBlock
                {
                    Text = heading, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    TextWrapping = TextWrapping.Wrap,
                });
                content.Children.Add(new TextBlock
                {
                    Text = result.Detail, TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                });
                var row = new ListViewItem { Content = content };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(
                    row, heading + ". " + result.Detail);
                HubTestResults.Items.Add(row);
            }
            HubTestSummary.Text = report.Tests.Count(t => t.Status == "passed") + " passed, "
                + report.Tests.Count(t => t.Status == "failed") + " failed, "
                + report.Tests.Count(t => t.Status == "skipped") + " skipped.";
            HubInfo.Severity = report.Tests.Any(t => t.Status == "failed")
                ? InfoBarSeverity.Warning : InfoBarSeverity.Success;
            HubInfo.Message = "Self-test completed. Review every result independently.";
            return HubTestSummary.Text;
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            if (generation == _hubGeneration)
            {
                HubTestSummary.Text = "Self-test interrupted or failed. Results are unknown; refresh before retrying.";
                HubInfo.Severity = InfoBarSeverity.Warning;
                HubInfo.Message = SafeError(error);
            }
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _hubBusy = false;
            if (!_closing.IsCancellationRequested) UpdateHubButtons();
        }
        return null;
    }

    private void CopyHubText(string value, string description)
    {
        if (_hubBusy || _hubMetadata is null || _quitting || string.IsNullOrWhiteSpace(value))
            return;
        try
        {
            var data = new DataPackage();
            data.SetText(value);
            Clipboard.SetContent(data);
            HubInfo.Severity = InfoBarSeverity.Success;
            HubInfo.Message = description + " copied. No stored API key was included.";
        }
        catch (Exception)
        {
            HubInfo.Severity = InfoBarSeverity.Warning;
            HubInfo.Message = "Clipboard unavailable. Select the text and copy it manually.";
        }
    }

    private void HubCopyBaseUrlButton_Click(object sender, RoutedEventArgs args) =>
        CopyHubText(_hubMetadata?.BaseUrl ?? "", "Base URL");

    private void HubCopyPythonButton_Click(object sender, RoutedEventArgs args) =>
        CopyHubText(SelectedHubModelId is string id
            ? _hubMetadata?.PythonExample(id) ?? "" : "", "Python example");

    private void HubCopyCurlButton_Click(object sender, RoutedEventArgs args) =>
        CopyHubText(_hubMetadata?.WindowsCurlExample() ?? "", "curl.exe example");
}
