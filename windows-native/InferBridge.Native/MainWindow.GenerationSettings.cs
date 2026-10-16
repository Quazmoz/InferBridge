using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private GenerationOptions _generationOptions = GenerationOptions.Default;

    private void OpenGenerationSettingsButton_Click(object sender, RoutedEventArgs args) =>
        MainNavigation.SelectedItem = SettingsNavigationItem;

    private void ResetGenerationSettings()
    {
        _generationOptions = GenerationOptions.Default;
        MaxTokensBox.Value = _generationOptions.MaxTokens;
        TemperatureBox.Value = _generationOptions.Temperature;
        TopPBox.Value = _generationOptions.TopP;
        GenerationSettingsInfo.Title = "Engine defaults";
        GenerationSettingsInfo.Message = "Defaults restored for this chat. Nothing was persisted.";
        GenerationSettingsInfo.Severity = InfoBarSeverity.Informational;
        UpdateGenerationSettingsInfo();
    }

    // This metadata is advisory; the engine validates each inference request.
    private bool CanUseGenerationOptions(string? modelId, GenerationOptions options, out string reason)
    {
        reason = "";
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
                System.Globalization.CultureInfo.InvariantCulture);
        string? modelId = SelectedModelId;
        if (modelId is null)
            SettingsCurrentModel.Text = "No chat model selected. Choose a generation model in Chat.";
        else if (_modelPreparationAvailable
            && _modelPreparations.TryGetValue(modelId, out var row))
            SettingsCurrentModel.Text = "Selected model: " + modelId
                + " · Backend: " + (row.Details?.Backend ?? "Not reported")
                + " · Context: " + (row.Details?.ContextTokens?.ToString() ?? "Not reported")
                + " · Output limit: " + (row.Details?.OutputTokens?.ToString() ?? "Not reported");
        else
            SettingsCurrentModel.Text = "Selected model: " + modelId
                + ". Engine limits not reported; the backend validates each request.";
        bool editable = !_chatBusy && !_quitting && !_closing.IsCancellationRequested;
        MaxTokensBox.IsEnabled = editable;
        TemperatureBox.IsEnabled = editable;
        TopPBox.IsEnabled = editable;
        ApplyGenerationSettingsButton.IsEnabled = editable;
        ResetGenerationSettingsButton.IsEnabled = editable;
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
            _generationOptions = requested;
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
}
