using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow : Window
{
    private readonly BackendClient _backend = new();
    private readonly CancellationTokenSource _closing = new();
    private bool _isRefreshing;

    public MainWindow()
    {
        InitializeComponent();
        MainNavigation.SelectedItem = MainNavigation.MenuItems[0];
        Closed += (_, _) =>
        {
            _closing.Cancel();
            _backend.Dispose();
        };
        _ = RefreshAsync();
    }

    private void MainNavigation_SelectionChanged(
        NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        bool models = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString() == "models";
        OverviewPanel.Visibility = models ? Visibility.Collapsed : Visibility.Visible;
        ModelsPanel.Visibility = models ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs args)
    {
        await RefreshAsync();
    }

    private void StartButton_Click(object sender, RoutedEventArgs args)
    {
        var result = EngineLauncher.Start();
        ConnectionInfo.Severity = result.Started
            ? InfoBarSeverity.Informational : InfoBarSeverity.Warning;
        ConnectionInfo.Message = result.Message;
        ConnectionDetails.Text = result.Message;
        // No optimistic "connected" state: identity and liveness must be checked
        // on a later refresh once the Python server has finished starting.
    }

    private async Task RefreshAsync()
    {
        if (_isRefreshing || _closing.IsCancellationRequested) return;
        _isRefreshing = true;
        RefreshButton.IsEnabled = false;
        ConnectionProgress.IsActive = true;
        try
        {
            BackendSnapshot status = await _backend.InspectAsync(_closing.Token);
            ConnectionInfo.Severity = status.Connected
                ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            ConnectionInfo.Message = status.Message;
            ConnectionDetails.Text = status.Connected
                ? $"Verified loopback endpoint: {status.Origin}"
                : "The native client never connects to an unverified local server.";
            StartButton.IsEnabled = !status.Connected;
            ModelsList.Items.Clear();
            foreach (ModelInfo model in status.Models)
            {
                ModelsList.Items.Add(new ListViewItem
                {
                    Content = $"{model.Name}  ·  {model.Status}  ({model.Id})",
                });
            }

            ModelsSummary.Text = status.RequiresAuthentication
                ? "Model inventory is protected by an API key. Native authentication " +
                  "and management actions are not enabled in this first milestone."
                : status.Connected
                    ? $"{status.Models.Count} models reported by the local engine."
                    : "Connect to your local engine to view its models.";
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested)
        {
            // Window closure cancels pending local requests.
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            ConnectionInfo.Severity = InfoBarSeverity.Error;
            ConnectionInfo.Message = "The local engine status could not be displayed.";
            ModelsSummary.Text = "Refresh status after checking the engine.";
        }
        finally
        {
            if (!_closing.IsCancellationRequested)
            {
                _isRefreshing = false;
                RefreshButton.IsEnabled = true;
                ConnectionProgress.IsActive = false;
            }
        }
    }
}
