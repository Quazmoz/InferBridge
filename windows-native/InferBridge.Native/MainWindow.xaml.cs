using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow : Window
{
    private readonly BackendClient _backend = new();
    private readonly CancellationTokenSource _closing = new();
    private readonly DispatcherTimer _pollTimer = new() { Interval = TimeSpan.FromSeconds(8) };
    private readonly Stopwatch _startupElapsed = new();
    private readonly List<ChatTurn> _turns = new();
    private CancellationTokenSource? _sendCancellation;
    private bool _isRefreshing;
    private bool _updatingModelPicker;
    private bool _isManaging;
    private bool _chatBusy;
    private bool _startRequested;
    private string? _conversationModelId;

    public MainWindow()
    {
        InitializeComponent();
        MainNavigation.SelectedItem = MainNavigation.MenuItems[0];
        _pollTimer.Tick += async (_, _) =>
        {
            // Polling updates presentation only. The Python tray owns server
            // startup, crash recovery, and model lifecycle transitions.
            if (!_closing.IsCancellationRequested)
                await RefreshAsync();
        };
        _pollTimer.Start();
        Closed += (_, _) =>
        {
            _pollTimer.Stop();
            _closing.Cancel();
            _sendCancellation?.Cancel();
            _backend.Dispose();
        };
        _ = RefreshAsync();
    }

    private string? SelectedModelId =>
        (ChatModelPicker.SelectedItem as ComboBoxItem)?.Tag as string;

    private ModelInfo? SelectedLibraryModel =>
        (ModelsList.SelectedItem as ListViewItem)?.Tag as ModelInfo;

    private void MainNavigation_SelectionChanged(
        NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        string? page = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString();
        OverviewPanel.Visibility = page == "overview" ? Visibility.Visible : Visibility.Collapsed;
        ModelsPanel.Visibility = page == "models" ? Visibility.Visible : Visibility.Collapsed;
        ChatPanel.Visibility = page == "chat" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshAsync();

    private async void StartButton_Click(object sender, RoutedEventArgs args)
    {
        if (_startRequested || _closing.IsCancellationRequested) return;
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

    private void ChatModelPicker_SelectionChanged(
        object sender, SelectionChangedEventArgs args)
    {
        // Inventory polling is not an intentional conversation/model change.
        if (_updatingModelPicker) return;
        string? modelId = SelectedModelId;
        if (modelId is not null && _conversationModelId != modelId)
        {
            _turns.Clear();
            ChatHistory.Items.Clear();
            _conversationModelId = modelId;
        }
        UpdateChatButtons();
    }

    private void ModelsList_SelectionChanged(
        object sender, SelectionChangedEventArgs args) => UpdateModelButtons();

    private void UpdateModelButtons()
    {
        var model = SelectedLibraryModel;
        LoadButton.IsEnabled = !_isManaging && !_chatBusy && model?.CanLoad == true;
        UnloadButton.IsEnabled = !_isManaging && !_chatBusy && model?.CanUnload == true;
    }

    private void UpdateChatButtons()
    {
        SendButton.IsEnabled = !_chatBusy && !_isManaging && SelectedModelId is not null;
        CancelButton.IsEnabled = _chatBusy;
        ChatModelPicker.IsEnabled = !_chatBusy && !_isManaging;
        PromptBox.IsEnabled = !_chatBusy;
        ChatProgress.IsActive = _chatBusy;
        UpdateModelButtons();
    }

    private async Task RefreshAsync()
    {
        if (_isRefreshing || _isManaging || _chatBusy || _closing.IsCancellationRequested)
            return;
        _isRefreshing = true;
        RefreshButton.IsEnabled = false;
        ConnectionProgress.IsActive = true;
        try
        {
            var status = await _backend.InspectAsync(_closing.Token, ApiKeyBox.Password);
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
            StartButton.IsEnabled = !status.Connected && !_startRequested;

            // Rebuilding ComboBox items raises SelectionChanged. Suppress it so
            // polling, reconnects and temporary model absence keep the transcript.
            string? selected = SelectedModelId ?? _conversationModelId;
            _updatingModelPicker = true;
            try
            {
                ChatModelPicker.Items.Clear();
                ModelsList.Items.Clear();
                foreach (ModelInfo model in status.Models)
                {
                    ModelsList.Items.Add(new ListViewItem
                    {
                        Content = model.Name + "  ·  " + model.Status + "  (" + model.Id + ")",
                        Tag = model,
                    });
                    if (model.Status != "loaded") continue;
                    var option = new ComboBoxItem { Content = model.Name, Tag = model.Id };
                    ChatModelPicker.Items.Add(option);
                    if (model.Id == selected)
                        ChatModelPicker.SelectedItem = option;
                }
                // Never silently move an existing conversation to a different model.
                // When its model returns after a restart, it is selected again.
                if (ChatModelPicker.SelectedItem is null && _conversationModelId is null
                    && ChatModelPicker.Items.Count > 0)
                    ChatModelPicker.SelectedIndex = 0;
            }
            finally
            {
                _updatingModelPicker = false;
            }

            ModelsSummary.Text = status.RequiresAuthentication
                ? "Authentication required. Enter your API key on the Engine screen."
                : status.Connected
                    ? status.Models.Count + " catalog models; select a ready model to load or an active model to unload."
                    : "Start or reconnect to the local engine.";

            if (!status.Connected || status.RequiresAuthentication)
                ShowChatInfo("Engine unavailable", status.Message, InfoBarSeverity.Warning);
            else if (ChatModelPicker.Items.Count == 0)
                ShowChatInfo("No model loaded",
                    "Choose a downloaded model in Model library and load it, then refresh.",
                    InfoBarSeverity.Informational);
            else
                ShowChatInfo("Ready", "Select a loaded model and send a message. Ctrl+Enter sends.",
                    InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested)
        {
            // Closing the window cancels requests, not the authoritative tray.
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            ConnectionInfo.Severity = InfoBarSeverity.Error;
            ConnectionInfo.Message = "Could not display local engine status.";
            ModelsSummary.Text = "Refresh after checking the engine.";
            ChatModelPicker.Items.Clear();
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

    private async void LoadButton_Click(object sender, RoutedEventArgs args) =>
        await ManageModelAsync(true);

    private async void UnloadButton_Click(object sender, RoutedEventArgs args) =>
        await ManageModelAsync(false);

    private async Task ManageModelAsync(bool load)
    {
        var model = SelectedLibraryModel;
        if (model is null || _isManaging || _chatBusy || _isRefreshing
            || !(load ? model.CanLoad : model.CanUnload)
            || _closing.IsCancellationRequested)
            return;

        _isManaging = true;
        UpdateChatButtons();
        try
        {
            ModelsSummary.Text = await _backend.ChangeModelAsync(
                model, load, ApiKeyBox.Password, _closing.Token);
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested)
        {
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ModelsSummary.Text = SafeError(error);
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
            if (_closing.IsCancellationRequested) return;
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

    private void ShowChatInfo(string title, string message, InfoBarSeverity severity)
    {
        ChatInfo.Title = title;
        ChatInfo.Message = message;
        ChatInfo.Severity = severity;
    }

    private static string SafeError(Exception error) => error switch
    {
        UnauthorizedAccessException => "API key rejected. Update it on the Engine screen.",
        InvalidOperationException => "Model state changed or model is busy. Refresh and retry.",
        HttpRequestException => "The engine rejected the request or disconnected. Refresh status.",
        JsonException => "The engine returned an unexpected response. Refresh and retry.",
        IOException => "Engine connection or response failed. Refresh status.",
        OperationCanceledException => "The engine request timed out.",
        _ => "Unexpected native request failure. Check the engine and refresh.",
    };
}
