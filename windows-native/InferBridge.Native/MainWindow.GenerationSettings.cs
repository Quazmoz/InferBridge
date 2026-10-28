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
        // Saved defaults (Save as my defaults) or the engine defaults, like the
        // browser's persisted chat settings.
        _generationOptions = _preferences.DefaultGeneration;
        MaxTokensBox.Value = _generationOptions.MaxTokens;
        TemperatureBox.Value = _generationOptions.Temperature;
        TopPBox.Value = _generationOptions.TopP;
        // Every caller is a conversation boundary (new chat, model switch,
        // restore/import), so request-only additions reset with it.
        _systemPrompt = _preferences.DefaultSystemPrompt;
        SystemPromptBox.Text = _systemPrompt ?? string.Empty;
        ClearPendingImages();
        ClearChatStats();
        bool custom = _preferences.DefaultGeneration != GenerationOptions.Default
            || _preferences.DefaultSystemPrompt is not null;
        GenerationSettingsInfo.Title = custom ? "Your defaults" : "Engine defaults";
        GenerationSettingsInfo.Message = custom
            ? "Your saved defaults are restored for this chat."
            : "Engine defaults restored for this chat. Use Save as my defaults to keep your own.";
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
        SaveGenerationDefaultsButton.IsEnabled = editable && _preferencesPath is not null;
        ClearGenerationDefaultsButton.IsEnabled = editable && _preferencesPath is not null;
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

    // Per-model context editing lives in MainWindow.ContextEditor.cs: one shared
    // editor serves model cards, the load step, this pane and Settings.
}
