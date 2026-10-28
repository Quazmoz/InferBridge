using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private GenerationOptions _generationOptions = GenerationOptions.Default;
    private ContextConfiguration? _contextBaseline;
    private bool _contextOperationBusy;

    private bool _chatPaneOpenWide = true;
    private bool _chatPaneOpenNarrow;
    private bool _chatPaneNarrow;
    private bool _syncingGenerationControls;

    // The pane is inline from 900 px of chat width and overlays from the right
    // below that, so the transcript never collapses to an unreadable column.
    private void ChatPanel_SizeChanged(object sender, SizeChangedEventArgs args)
    {
        bool narrow = args.NewSize.Width < 900;
        if (narrow == _chatPaneNarrow && args.PreviousSize.Width > 0) return;
        _chatPaneNarrow = narrow;
        LayoutChatSettingsPane();
    }

    private void LayoutChatSettingsPane()
    {
        Grid.SetColumn(ChatSettingsPane, _chatPaneNarrow ? 0 : 1);
        Grid.SetColumnSpan(ChatSettingsPane, _chatPaneNarrow ? 2 : 1);
        ChatSettingsPane.HorizontalAlignment = _chatPaneNarrow
            ? HorizontalAlignment.Right : HorizontalAlignment.Stretch;
        ChatSettingsPane.Margin = _chatPaneNarrow ? new Thickness(0) : new Thickness(16, 0, 0, 0);
        // The overlay needs an opaque surface; inline it uses the card layer.
        ChatSettingsPane.Background = Ui.ResBrush(_chatPaneNarrow
            ? "SolidBackgroundFillColorBaseBrush" : "CardBackgroundFillColorDefaultBrush");
        Canvas.SetZIndex(ChatSettingsPane, _chatPaneNarrow ? 10 : 0);
        bool open = _chatPaneNarrow ? _chatPaneOpenNarrow : _chatPaneOpenWide;
        ChatSettingsPane.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
    }

    private void SetChatSettingsPane(bool open)
    {
        if (_chatPaneNarrow) _chatPaneOpenNarrow = open;
        else _chatPaneOpenWide = open;
        LayoutChatSettingsPane();
        if (open) _ = SystemPromptBox.Focus(FocusState.Programmatic);
        else _ = ChatSettingsPaneButton.Focus(FocusState.Programmatic);
    }

    private void OpenGenerationSettingsButton_Click(object sender, RoutedEventArgs args) =>
        SetChatSettingsPane(ChatSettingsPane.Visibility != Visibility.Visible);

    private void CloseChatSettingsPaneButton_Click(object sender, RoutedEventArgs args) =>
        SetChatSettingsPane(false);

    private void OpenChatSettingsFromSettings_Click(object sender, RoutedEventArgs args)
    {
        NavigateTo("chat");
        SetChatSettingsPane(true);
    }

    // Sliders and number boxes edit the same value; Apply still validates and commits.
    private void GenerationSlider_ValueChanged(object sender,
        Microsoft.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs args)
    {
        if (_syncingGenerationControls || TemperatureBox is null || TopPBox is null || MaxTokensBox is null)
            return;
        _syncingGenerationControls = true;
        try
        {
            if (ReferenceEquals(sender, TemperatureSlider)) TemperatureBox.Value = Math.Round(args.NewValue, 2);
            else if (ReferenceEquals(sender, TopPSlider)) TopPBox.Value = Math.Round(args.NewValue, 2);
            else if (ReferenceEquals(sender, MaxTokensSlider)) MaxTokensBox.Value = Math.Round(args.NewValue);
        }
        finally { _syncingGenerationControls = false; }
    }

    private void GenerationBox_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (_syncingGenerationControls || !double.IsFinite(args.NewValue)
            || TemperatureSlider is null || TopPSlider is null || MaxTokensSlider is null)
            return;
        _syncingGenerationControls = true;
        try
        {
            Slider? slider = ReferenceEquals(sender, TemperatureBox) ? TemperatureSlider
                : ReferenceEquals(sender, TopPBox) ? TopPSlider
                : ReferenceEquals(sender, MaxTokensBox) ? MaxTokensSlider : null;
            if (slider is not null)
                slider.Value = Math.Clamp(args.NewValue, slider.Minimum, slider.Maximum);
        }
        finally { _syncingGenerationControls = false; }
    }

    private void ResetGenerationSettings()
    {
        _generationOptions = GenerationOptions.Default;
        MaxTokensBox.Value = _generationOptions.MaxTokens;
        TemperatureBox.Value = _generationOptions.Temperature;
        TopPBox.Value = _generationOptions.TopP;
        // Every caller is a conversation boundary (new chat, model switch,
        // restore/import), so request-only additions reset with it.
        _systemPrompt = null;
        SystemPromptBox.Text = string.Empty;
        ClearPendingImages();
        ClearChatStats();
        GenerationSettingsInfo.Title = "Engine defaults";
        GenerationSettingsInfo.Message = "Defaults restored for this chat. Nothing was persisted.";
        GenerationSettingsInfo.Severity = InfoBarSeverity.Informational;
        UpdateGenerationSettingsInfo();
    }

    // This metadata is advisory; the engine validates each inference request.
    private bool CanUseGenerationOptions(string? modelId, GenerationOptions options, out string reason)
    {
        reason = "";
        if (modelId is null)
        {
            reason = "Choose and load a generation model in Chat first.";
            return false;
        }
        try { options.Validate(); }
        catch (ArgumentException)
        {
            reason = "Review tokens (1–4096), temperature (0–2), and top-p (0–1).";
            return false;
        }
        if (_modelPreparationAvailable && modelId is not null
            && _modelPreparations.TryGetValue(modelId, out var model))
        {
            if (model.Details?.Backend == "openvino-embeddings")
            {
                reason = "The selected model is an embedding model, not a text generator.";
                return false;
            }
            if (model.Details?.OutputTokens is int limit && limit > 0
                && options.MaxTokens > limit)
            {
                reason = "The engine reports a maximum of " + limit
                    + " output tokens for this model. Reduce the requested limit.";
                return false;
            }
        }
        return true;
    }

    private void UpdateGenerationSettingsInfo()
    {
        if (_closing.IsCancellationRequested) return;
        ChatGenerationSummary.Text = "Max " + _generationOptions.MaxTokens
            + " · temp " + _generationOptions.Temperature.ToString("0.##",
                System.Globalization.CultureInfo.InvariantCulture)
            + " · top-p " + _generationOptions.TopP.ToString("0.##",
                System.Globalization.CultureInfo.InvariantCulture)
            + (_systemPrompt is null ? "" : " · system prompt");
        string? modelId = SelectedModelId;
        if (modelId is null)
            SettingsCurrentModel.Text = "No chat model selected. Choose a generation model in Chat.";
        else if (_modelPreparationAvailable
            && _modelPreparations.TryGetValue(modelId, out var row))
            SettingsCurrentModel.Text = "Selected model: " + modelId
                + " · Runtime: " + (row.Details?.Backend ?? "Not reported")
                + " · Context: " + (row.Details?.ContextTokens?.ToString() ?? "Not reported")
                + " · Output limit: " + (row.Details?.OutputTokens?.ToString() ?? "Not reported");
        else
            SettingsCurrentModel.Text = "Selected model: " + modelId
                + ". The engine checks limits for each request.";
        bool editable = !_chatBusy && !_quitting && !_closing.IsCancellationRequested;
        bool generationModel = modelId is not null
            && SelectedChatModel?.Status == "loaded"
            && (!_modelPreparationAvailable
                || !_modelPreparations.TryGetValue(modelId, out var known)
                || known.Details?.Backend != "openvino-embeddings");
        if (!generationModel && modelId is not null
            && _modelPreparationAvailable
            && _modelPreparations.TryGetValue(modelId, out var detected)
            && detected.Details?.Backend == "openvino-embeddings")
            SettingsCurrentModel.Text += " · Embedding models cannot generate chat.";
        MaxTokensBox.IsEnabled = editable;
        TemperatureBox.IsEnabled = editable;
        TopPBox.IsEnabled = editable;
        MaxTokensSlider.IsEnabled = editable;
        TemperatureSlider.IsEnabled = editable;
        TopPSlider.IsEnabled = editable;
        SystemPromptBox.IsEnabled = editable;
        ApplyGenerationSettingsButton.IsEnabled = editable && generationModel;
        ResetGenerationSettingsButton.IsEnabled = editable;
        UpdateContextControls();
    }

    private void ApplyGenerationSettingsButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested) return;
        try
        {
            var requested = GenerationOptions.FromNumbers(
                MaxTokensBox.Value, TemperatureBox.Value, TopPBox.Value);
            if (!CanUseGenerationOptions(SelectedModelId, requested, out string reason))
                throw new ArgumentException(reason);
            string? systemPrompt = string.IsNullOrWhiteSpace(SystemPromptBox.Text)
                ? null : SystemPromptBox.Text;
            new ChatRequestContext(systemPrompt, Array.Empty<ChatImage>()).Validate();
            _generationOptions = requested;
            _systemPrompt = systemPrompt;
            GenerationSettingsInfo.Title = "Applied to current chat";
            GenerationSettingsInfo.Message = "Future requests use these values. No engine setting was changed.";
            GenerationSettingsInfo.Severity = InfoBarSeverity.Success;
        }
        catch (ArgumentException error)
        {
            GenerationSettingsInfo.Title = "Invalid or unsupported options";
            GenerationSettingsInfo.Message = error.Message;
            GenerationSettingsInfo.Severity = InfoBarSeverity.Warning;
        }
        UpdateGenerationSettingsInfo();
    }

    private void ResetGenerationSettingsButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested) return;
        ResetGenerationSettings();
    }

    private void UpdateContextControls()
    {
        bool available = !_contextOperationBusy && !_chatBusy && !_quitting
            && !_closing.IsCancellationRequested && _contextBaseline?.ModelId == SelectedModelId;
        ContextSizePicker.IsEnabled = available;
        ContextApplyButton.IsEnabled = available && ContextSizePicker.SelectedItem is ComboBoxItem
        {
            Tag: int requested
        } && requested != _contextBaseline?.Current
          && _contextBaseline?.Options.Any(option => option.Tokens == requested
              && option.Classification != "existing-unverified") == true;
        ContextResetButton.IsEnabled = available
            && _contextBaseline?.Recommended != _contextBaseline?.Current
            && _contextBaseline?.Options.Any(option =>
                option.Tokens == _contextBaseline.Recommended
                && option.Classification != "existing-unverified") == true;
    }

    private void ContextSizePicker_SelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_contextBaseline is not { } context) return;
        var choice = ContextSizePicker.SelectedItem is ComboBoxItem { Tag: int size }
            ? context.Options.FirstOrDefault(option => option.Tokens == size) : null;
        if (choice is null) return;
        ContextMemoryGuidance.Text = "Prompt budget: "
            + (choice.Tokens - context.OutputReserve).ToString("N0")
            + " · Reserved output: " + context.OutputReserve.ToString("N0")
            + " · " + choice.Classification.Replace('-', ' ')
            + "\n" + (choice.KvEstimateGib is double estimate
                ? "Illustrative FP16 KV cache: ~" + estimate.ToString("0.###")
                    + " GiB; actual CPU/GPU/NPU allocation is unmeasured."
                : "KV-cache memory unavailable: converted model architecture metadata is incomplete.")
            + "\n" + (choice.EstimatedHostRamGib is double hostRam
                ? "Estimated host-RAM planning floor: ~" + hostRam.ToString("0.##")
                    + " GiB (" + choice.FitGuidance.Replace('-', ' ') + "). Not a device measurement."
                : "Total memory estimate unavailable (" + choice.FitGuidance.Replace('-', ' ')
                    + ").")
            + "\nModel architecture limit: "
            + (context.ArchitectureLimit?.ToString("N0") ?? "Not verified")
            + ". Available RAM does not establish NPU compilation support.";
        UpdateContextControls();
    }

    private async Task RefreshContextConfigurationAsync()
    {
        if (_contextOperationBusy || _closing.IsCancellationRequested) return;
        string? modelId = SelectedModelId;
        _contextBaseline = null;
        ContextSizePicker.Items.Clear();
        UpdateContextControls();
        if (modelId is null || !_engineConnected)
        {
            ContextConfigurationInfo.Message = "Connect the engine and select a generation model.";
            return;
        }
        _contextOperationBusy = true;
        try
        {
            ContextConfigurationInfo.Title = "Checking model context";
            ContextConfigurationInfo.Message = "Reading current model and runtime constraints…";
            ContextConfigurationInfo.Severity = InfoBarSeverity.Informational;
            var context = await _backend.GetContextAsync(modelId, ApiKeyBox.Password, _closing.Token);
            if (SelectedModelId != modelId || _closing.IsCancellationRequested) return;
            _contextBaseline = context;
            foreach (var option in context.Options)
            {
                var row = new ComboBoxItem
                {
                    Tag = option.Tokens,
                    Content = option.Tokens.ToString("N0") + " · "
                        + option.Classification.Replace('-', ' '),
                };
                ContextSizePicker.Items.Add(row);
                if (option.Tokens == context.Current) ContextSizePicker.SelectedItem = row;
            }
            ContextConfigurationInfo.Title = "Current model context: " + context.Current.ToString("N0");
            ContextConfigurationInfo.Message = context.Loaded
                ? "Applying a change compiles a replacement and retains the current engine until ready."
                : "This model is unloaded. A new context will take effect on its next load.";
            ContextConfigurationInfo.Severity = InfoBarSeverity.Informational;
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            ContextConfigurationInfo.Title = "Context information unavailable";
            ContextConfigurationInfo.Message = error is EngineRequestException
                ? error.Message : "The engine could not provide verified context settings.";
            ContextConfigurationInfo.Severity = InfoBarSeverity.Warning;
        }
        finally
        {
            _contextOperationBusy = false;
            UpdateContextControls();
        }
    }

    private async Task ApplySelectedContextAsync(int tokens)
    {
        if (_contextOperationBusy || _contextBaseline is not { } baseline
            || baseline.ModelId != SelectedModelId || _chatBusy) return;
        _contextOperationBusy = true;
        UpdateContextControls();
        ContextConfigurationInfo.Title = "Applying model context";
        ContextConfigurationInfo.Message = "Compiling and verifying the replacement before saving…";
        ContextConfigurationInfo.Severity = InfoBarSeverity.Informational;
        try
        {
            var applied = await _backend.ApplyContextAsync(
                baseline, tokens, ApiKeyBox.Password, _closing.Token);
            if (SelectedModelId != baseline.ModelId) return;
            _contextBaseline = applied;
            ContextConfigurationInfo.Title = "Context saved";
            ContextConfigurationInfo.Message = "Applied " + tokens.ToString("N0")
                + " tokens. Future prompts use this budget; existing chat history was preserved.";
            ContextConfigurationInfo.Severity = InfoBarSeverity.Success;
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            ContextConfigurationInfo.Title = "Context not confirmed";
            ContextConfigurationInfo.Message = error is EngineRequestException
                ? error.Message
                : "Could not confirm whether the engine applied this request. Refresh current context before retrying.";
            ContextConfigurationInfo.Severity = InfoBarSeverity.Warning;
        }
        finally
        {
            _contextOperationBusy = false;
            await RefreshContextConfigurationAsync();
        }
    }

    private async void ContextApplyButton_Click(object sender, RoutedEventArgs args)
    {
        if (ContextSizePicker.SelectedItem is ComboBoxItem { Tag: int size })
            await ApplySelectedContextAsync(size);
    }

    private async void ContextResetButton_Click(object sender, RoutedEventArgs args)
    {
        if (_contextBaseline is { } baseline)
            await ApplySelectedContextAsync(baseline.Recommended);
    }

    private async void ContextRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshContextConfigurationAsync();
}
