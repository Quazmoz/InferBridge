using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Web-parity chat additions: Eject from the chat model bar, the "Load another
// model?" memory guard, Regenerate, starter prompts, the exact context budget,
// saved chat defaults and theme, and the engine activity feed.
public sealed partial class MainWindow
{
    private bool _ejecting;
    // Set after Eject: background inventory refreshes must not reselect (and so
    // re-offer) the unloaded model. Cleared by any explicit user model choice.
    private string? _ejectedModelId;
    private NativePreferences _preferences = NativePreferences.Default;
    private string? _preferencesPath;
    private bool _applyingTheme;
    private readonly DispatcherTimer _contextBudgetTimer = new() { Interval = TimeSpan.FromMilliseconds(700) };
    private int _contextBudgetSequence;
    private ContextBudget? _contextBudget;
    private bool _activityBusy;

    // ── Eject ──────────────────────────────────────────────────────────────

    private void UpdateChatEjectButton()
    {
        ModelInfo? model = SelectedChatModel;
        // Enabled while a reply streams: Eject then asks to stop it first.
        ChatEjectButton.IsEnabled = !_ejecting && !_isManaging && !RefreshLocksControls
            && !_quitting && _engineConnected && model?.CanUnload == true;
        ChatEjectText.Text = _ejecting ? "Ejecting…" : "Eject";
        string name = model?.Name ?? "the chat model";
        AutomationProperties.SetName(ChatEjectButton, _ejecting
            ? "Ejecting " + name : "Eject " + name + " from memory");
        ChatModelPicker.PlaceholderText = ModelLifecycle.PickerPlaceholder(_shownModels);
    }

    private async void ChatEjectButton_Click(object sender, RoutedEventArgs args)
    {
        ModelInfo? model = SelectedChatModel;
        if (model is not { CanUnload: true } || _ejecting || _isManaging
            || _quitting || _closing.IsCancellationRequested) return;
        _ejecting = true;
        UpdateChatButtons();
        try
        {
            if (_chatBusy)
            {
                var stop = new ContentDialog
                {
                    XamlRoot = ((FrameworkElement)Content).XamlRoot,
                    Title = "Stop the reply and eject?",
                    Content = model.Name + " is still writing a reply. Stop it and unload the model? "
                        + "The unfinished reply is discarded; the rest of the conversation is kept.",
                    PrimaryButtonText = "Stop and eject",
                    CloseButtonText = "Keep generating",
                    DefaultButton = ContentDialogButton.Close,
                };
                if (await stop.ShowAsync() != ContentDialogResult.Primary
                    || _closing.IsCancellationRequested) return;
                _sendCancellation?.Cancel();
                for (int wait = 0; _chatBusy && wait < 100 && !_closing.IsCancellationRequested; wait++)
                    await Task.Delay(50);
                if (_chatBusy)
                {
                    ShowChatInfo("Could not stop the reply",
                        "The model is still busy. Try Eject again in a moment.", InfoBarSeverity.Warning);
                    return;
                }
            }
            if (SelectedChatModel?.Id != model.Id) return; // Selection changed meanwhile.
            await EjectChatModelAsync(model);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _ejecting = false;
            if (!_closing.IsCancellationRequested) UpdateChatButtons();
        }
    }

    private async Task EjectChatModelAsync(ModelInfo model)
    {
        _isManaging = true;
        UpdateChatButtons();
        ShowChatInfo("Ejecting model", "Unloading " + model.Name
            + " from memory. Your conversation stays here.", InfoBarSeverity.Informational);
        bool ejected = false;
        try
        {
            ShowModelsInfo(await _backend.UnloadModelAsync(model, ApiKeyBox.Password, _closing.Token),
                InfoBarSeverity.Success);
            ejected = true;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Could not eject " + model.Name, SafeError(error), InfoBarSeverity.Error);
        }
        finally
        {
            _isManaging = false;
        }
        if (!ejected || _closing.IsCancellationRequested) return;
        // The transcript, turns and conversation model stay as they are, so picking
        // the same model again continues this chat; nothing reloads by itself.
        _ejectedModelId = model.Id;
        _chatLoadError = null;
        _updatingModelPicker = true;
        try { ChatModelPicker.SelectedItem = null; }
        finally { _updatingModelPicker = false; }
        ClearChatStats();
        ResetContextBudget();
        ShowChatInfo("Model ejected", model.Name + " was unloaded from memory. Your conversation is kept; "
            + "pick a model above to continue.", InfoBarSeverity.Success);
        UpdateChatButtons();
        await RefreshAsync();
    }

    // ── "Load another model?" (web model-lifecycle guard) ─────────────────

    private enum PeerChoice { Cancel, KeepLoaded, UnloadOthers }

    /// <summary>
    /// Before a load (or download-then-load), mirror the browser: offer to unload
    /// the other models still in memory. Returns false when the user cancels.
    /// </summary>
    private async Task<bool> ResolveLoadedPeersAsync(ModelInfo model, ModelAction action)
    {
        if (action is not (ModelAction.Load or ModelAction.Download)) return true;
        IReadOnlyList<ModelInfo> peers = ModelLifecycle.LoadedPeers(_shownModels, model.Id);
        if (peers.Count == 0) return true;
        var list = new StackPanel { Spacing = 6 };
        list.Children.Add(Ui.Text(model.Name + " will be " + (action == ModelAction.Download
            ? "downloaded and then loaded" : "loaded") + " while "
            + (peers.Count == 1 ? "another model stays" : peers.Count + " other models stay")
            + " in memory. Unloading the other " + (peers.Count == 1 ? "model" : "models")
            + " first is recommended for most PCs."));
        foreach (ModelInfo peer in peers.Take(8))
            list.Children.Add(Ui.Text("• " + peer.Name + (peer.Device is { Length: > 0 } device
                ? " on " + device : ""), secondary: true));
        var dialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = "Load another model?",
            Content = list,
            PrimaryButtonText = peers.Count == 1 ? "Unload other and continue"
                : "Unload " + peers.Count + " others and continue",
            SecondaryButtonText = "Keep loaded and continue",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
        };
        PeerChoice choice = await dialog.ShowAsync() switch
        {
            ContentDialogResult.Primary => PeerChoice.UnloadOthers,
            ContentDialogResult.Secondary => PeerChoice.KeepLoaded,
            _ => PeerChoice.Cancel,
        };
        if (choice == PeerChoice.Cancel || _closing.IsCancellationRequested) return false;
        if (choice == PeerChoice.UnloadOthers)
            foreach (ModelInfo peer in peers)
                await _backend.UnloadModelAsync(peer, ApiKeyBox.Password, _closing.Token, busyRetries: 0);
        return true;
    }

    // ── Regenerate and starter prompts ─────────────────────────────────────

    private async void RegenerateButton_Click(object sender, RoutedEventArgs args) =>
        await GenerateReplyAsync(regenerate: true);

    private void ChatSuggestion_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Content: string prompt } || _chatBusy) return;
        PromptBox.Text = prompt;
        PromptBox.SelectionStart = prompt.Length;
        _ = PromptBox.Focus(FocusState.Programmatic);
    }

    // ── Context budget (POST /v1/chat/context-budget) ─────────────────────

    private void PromptBox_TextChanged(object sender, TextChangedEventArgs args) => ScheduleContextBudget();

    private void ScheduleContextBudget()
    {
        _contextBudgetTimer.Stop();
        _contextBudgetTimer.Start();
    }

    private async void ContextBudgetTimer_Tick(object? sender, object args)
    {
        _contextBudgetTimer.Stop();
        await RefreshContextBudgetAsync();
    }

    private void ResetContextBudget()
    {
        _contextBudgetSequence++;
        _contextBudget = null;
        ContextBudgetText.Text = SelectedChatModel?.Status == "loaded"
            ? "Context: type a message" : "Context after model load";
        ContextBudgetDetails.Text = "Load a model and type a message to see its exact token use.";
        ContextBudgetNotice.Text = string.Empty;
        ContextBudgetOmissions.Children.Clear();
        ContextBudgetFitButton.IsEnabled = false;
    }

    /// <summary>A background check: it never disables chat controls and a stale answer is dropped.</summary>
    private async Task RefreshContextBudgetAsync()
    {
        string? modelId = SelectedModelId;
        string prompt = PromptBox.Text.Trim();
        if (modelId is null || SelectedChatModel?.Status != "loaded" || !_engineConnected
            || _closing.IsCancellationRequested
            || (_turns.Count > 0 && modelId != _conversationModelId)
            || (prompt.Length == 0 && _turns.Count == 0))
        {
            ResetContextBudget();
            return;
        }
        int sequence = ++_contextBudgetSequence;
        List<ChatTurn> request = prompt.Length > 0
            ? ChatTranscript.ForRequest(_turns, new ChatTurn("user", prompt))
            : ChatTranscript.ForRequest(_turns, null);
        if (_contextBudget is null) ContextBudgetText.Text = "Checking context…";
        try
        {
            ContextBudget budget = await _backend.GetContextBudgetAsync(modelId, request,
                ApiKeyBox.Password, _closing.Token, _generationOptions, CurrentChatContext());
            if (sequence != _contextBudgetSequence || _closing.IsCancellationRequested) return;
            ShowContextBudget(budget);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            if (sequence != _contextBudgetSequence) return;
            _contextBudget = null;
            ContextBudgetText.Text = "Context check unavailable";
            ContextBudgetDetails.Text = SafeError(error);
            ContextBudgetNotice.Text = string.Empty;
            ContextBudgetOmissions.Children.Clear();
            ContextBudgetFitButton.IsEnabled = false;
        }
    }

    private void ShowContextBudget(ContextBudget budget)
    {
        static string N(int value) => value.ToString("N0", System.Globalization.CultureInfo.InvariantCulture);
        _contextBudget = budget;
        ContextBudgetText.Text = budget.Summary + (budget.State == "danger" ? " · no room"
            : budget.State == "warning" ? " · check" : "");
        ContextBudgetDetails.Text = N(budget.PromptTokens) + " prompt tokens ("
            + budget.PromptBudgetPercent.ToString("0.#", System.Globalization.CultureInfo.InvariantCulture)
            + "% of the " + N(budget.MaxPromptTokens) + "-token prompt budget)\n"
            + "Context window: " + N(budget.MaxContextTokens) + " tokens\n"
            + "Reply: " + N(budget.RequestedOutputTokens) + " requested, " + N(budget.AvailableOutputTokens)
            + " available\nRetained messages: " + N(budget.RetainedMessageCount) + " of " + N(budget.MessageCount)
            + (budget.AttachmentCount > 0 ? "\nImages: " + budget.AttachmentCount + " (estimated)" : "");
        ContextBudgetNotice.Text = budget.Notice;
        ContextBudgetOmissions.Children.Clear();
        if (budget.Omitted.Count > 0)
        {
            ContextBudgetOmissions.Children.Add(Ui.Text("Will be omitted", "BodyStrongTextBlockStyle"));
            foreach (ContextBudgetOmission omission in budget.Omitted)
                ContextBudgetOmissions.Children.Add(Ui.Text(omission.Role + ": " + omission.Preview, secondary: true));
            if (budget.OmittedPreviewTruncated)
                ContextBudgetOmissions.Children.Add(Ui.Text("More omitted messages are not shown.", secondary: true));
        }
        ContextBudgetFitButton.IsEnabled = budget.OutputLimited && budget.EffectiveOutputTokens >= 1 && !_chatBusy;
        AutomationProperties.SetName(ContextBudgetButton, "Context budget: " + budget.Summary + ". " + budget.Notice);
    }

    private async void ContextBudgetFlyout_Opening(object? sender, object args) =>
        await RefreshContextBudgetAsync();

    private async void ContextBudgetRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshContextBudgetAsync();

    private async void ContextBudgetFitButton_Click(object sender, RoutedEventArgs args)
    {
        if (_contextBudget is not { OutputLimited: true } budget || budget.EffectiveOutputTokens < 1 || _chatBusy)
            return;
        int fitted = Math.Clamp(budget.EffectiveOutputTokens, 1, 4096);
        _generationOptions = _generationOptions with { MaxTokens = fitted };
        MaxTokensBox.Value = fitted;
        GenerationSettingsInfo.Title = "Reply limit reduced";
        GenerationSettingsInfo.Message = "Maximum reply tokens set to " + fitted + " so the reply fits this chat's context.";
        GenerationSettingsInfo.Severity = InfoBarSeverity.Success;
        UpdateGenerationSettingsInfo();
        await RefreshContextBudgetAsync();
    }

    // ── Preferences: theme and chat defaults ───────────────────────────────

    private void LoadPreferences()
    {
        try
        {
            _preferencesPath = NativePreferences.PathForCurrentInstallation();
            _preferences = NativePreferences.Load(_preferencesPath);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        {
            _preferencesPath = null; // Defaults only; the controls explain nothing was saved.
        }
        ApplyTheme(_preferences.Theme);
        _applyingTheme = true;
        try
        {
            AppThemePicker.SelectedItem = AppThemePicker.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(item => (string?)item.Tag == _preferences.Theme);
        }
        finally { _applyingTheme = false; }
    }

    private void ApplyTheme(string theme)
    {
        if (Content is FrameworkElement root)
            root.RequestedTheme = theme switch
            {
                "light" => ElementTheme.Light,
                "dark" => ElementTheme.Dark,
                _ => ElementTheme.Default,
            };
    }

    private bool TrySavePreferences(NativePreferences preferences, out string error)
    {
        error = "";
        try
        {
            if (_preferencesPath is null) throw new IOException("Preferences storage is unavailable.");
            preferences.Save(_preferencesPath);
            _preferences = preferences.Validated();
            return true;
        }
        catch (Exception problem) when (problem is IOException or UnauthorizedAccessException
            or ArgumentException or InvalidDataException)
        {
            error = problem is ArgumentException ? problem.Message
                : "Preferences could not be saved on this PC. The current values still apply now.";
            return false;
        }
    }

    private void AppThemePicker_SelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_applyingTheme || AppThemePicker.SelectedItem is not ComboBoxItem { Tag: string theme }) return;
        ApplyTheme(theme);
        if (!TrySavePreferences(_preferences with { Theme = theme }, out string error))
            ShowChatInfo("Theme not saved", error, InfoBarSeverity.Warning);
    }

    private void SaveGenerationDefaultsButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested) return;
        try
        {
            var requested = GenerationOptions.FromNumbers(MaxTokensBox.Value, TemperatureBox.Value, TopPBox.Value);
            string? prompt = string.IsNullOrWhiteSpace(SystemPromptBox.Text) ? null : SystemPromptBox.Text;
            if (!TrySavePreferences(_preferences with { DefaultGeneration = requested, DefaultSystemPrompt = prompt },
                out string error))
                throw new ArgumentException(error);
            // Saving also applies them to this chat, like the browser settings panel.
            _generationOptions = requested;
            _systemPrompt = prompt;
            GenerationSettingsInfo.Title = "Saved as your defaults";
            GenerationSettingsInfo.Message = "New chats start with these values. They also apply to this chat now.";
            GenerationSettingsInfo.Severity = InfoBarSeverity.Success;
        }
        catch (ArgumentException error)
        {
            GenerationSettingsInfo.Title = "Defaults not saved";
            GenerationSettingsInfo.Message = error.Message;
            GenerationSettingsInfo.Severity = InfoBarSeverity.Warning;
        }
        UpdateGenerationSettingsInfo();
    }

    private void ClearGenerationDefaultsButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested) return;
        if (!TrySavePreferences(_preferences with
            {
                DefaultGeneration = GenerationOptions.Default, DefaultSystemPrompt = null,
            }, out string error))
        {
            GenerationSettingsInfo.Title = "Defaults not changed";
            GenerationSettingsInfo.Message = error;
            GenerationSettingsInfo.Severity = InfoBarSeverity.Warning;
            return;
        }
        ResetGenerationSettings();
    }

    // ── Engine activity (GET /v1/events), like the browser activity feed ───

    private async void EngineActivityRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshEngineActivityAsync();

    private async Task RefreshEngineActivityAsync()
    {
        if (_activityBusy || !_engineConnected || _rejectedApiKey is not null
            || _closing.IsCancellationRequested) return;
        _activityBusy = true;
        EngineActivityRefreshButton.IsEnabled = false;
        try
        {
            IReadOnlyList<EngineEvent> events = await _backend.GetEngineEventsAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            EngineActivityList.Children.Clear();
            DateTimeOffset now = DateTimeOffset.UtcNow;
            foreach (EngineEvent item in events)
            {
                var row = new WrapRow { Spacing = 8 };
                row.Children.Add(Ui.Pill(item.Level switch
                {
                    "error" => "Error", "warning" => "Warning", _ => "Info",
                }, item.Level switch { "error" => "error", "warning" => "warning", _ => "neutral" }));
                row.Children.Add(Ui.Text(item.Message));
                row.Children.Add(Ui.Text(EngineEvent.Relative(item.Time, now), "CaptionTextBlockStyle", secondary: true));
                AutomationProperties.SetName(row, item.Level + ": " + item.Message + ", "
                    + EngineEvent.Relative(item.Time, now));
                EngineActivityList.Children.Add(row);
            }
            EngineActivitySummary.Text = events.Count == 0
                ? "No engine activity yet. Loads, unloads, downloads and errors appear here."
                : "Newest first. Showing the last " + events.Count + " engine events.";
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            EngineActivitySummary.Text = "Engine activity unavailable: " + SafeError(error);
        }
        finally
        {
            _activityBusy = false;
            if (!_closing.IsCancellationRequested) EngineActivityRefreshButton.IsEnabled = true;
        }
    }
}
