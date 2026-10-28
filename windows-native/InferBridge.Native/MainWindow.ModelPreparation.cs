using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private IReadOnlyDictionary<string, ModelPreparationInfo> _modelPreparations =
        new Dictionary<string, ModelPreparationInfo>(StringComparer.Ordinal);
    private bool _modelPreparationRefreshing;
    private bool _modelPreparationAvailable;
    private long _modelPreparationEpoch;

    private void ResetModelPreparations()
    {
        // Invalidate an in-flight response from a previous engine/API-key session.
        _modelPreparationEpoch++;
        _modelPreparations = new Dictionary<string, ModelPreparationInfo>(StringComparer.Ordinal);
        _modelPreparationAvailable = false;
        UpdateModelPreparationUi();
    }

    private void UpdateModelPreparationUi()
    {
        ModelInfo? model = SelectedLibraryModel;
        _modelPreparations.TryGetValue(model?.Id ?? "", out var operation);
        bool hasProgress = _modelPreparationAvailable && operation is not null;
        string label = model is null ? "Select a model to inspect preparation."
            : !_modelPreparationAvailable
                ? "Live preparation status unavailable. Refresh the Model library."
                : operation is null
                    ? "No preparation state reported by the engine."
                    : operation.OperationId is null
                        ? "No active preparation. Model status: " + model.StatusLabel
                        : "Stage: " + operation.Phase.Replace('_', ' ')
                            + (operation.Percent is double amount
                                ? " · " + amount.ToString("0.#",
                                    System.Globalization.CultureInfo.InvariantCulture) + "%"
                                : " · Percentage not reported")
                            + (operation.CanCancel
                                ? " · Cancellation available"
                                : " · Cancellation unavailable at this stage");
        if (ModelPreparationSummary.Text != label)
            ModelPreparationSummary.Text = label;
        string details = "";
        if (hasProgress && operation!.Details is ModelLibraryDetails info)
        {
            static string Budget(int? value) =>
                value?.ToString("N0",
                    System.Globalization.CultureInfo.InvariantCulture)
                    ?? "Not reported";
            details = "Backend: " + info.Backend
                + " · Weight format: " + info.WeightFormat
                + "\nHugging Face source: " + info.SourceRepository
                + "\nConfigured device preference: " + info.ConfiguredDevice
                + " · Active runtime device: " + info.RuntimeDevice
                + "\nContext limit: " + Budget(info.ContextTokens)
                + " · Output token limit: " + Budget(info.OutputTokens)
                + "\nVision capability: " + (info.SupportsVision is true
                    ? "Reported" : info.SupportsVision is false
                    ? "Not reported as supported" : "Unknown")
                + "\nDevice preferences and model metadata do not certify local hardware compatibility.";
        }
        if (ModelDetailsSummary.Text != details)
            ModelDetailsSummary.Text = details;

        bool showPercent = hasProgress
            && operation!.OperationId is not null
            && operation.Percent is not null;
        ModelPreparationPercent.Visibility =
            showPercent ? Visibility.Visible : Visibility.Collapsed;
        if (showPercent)
            ModelPreparationPercent.Value = operation!.Percent!.Value;
        CancelPreparationButton.IsEnabled = hasProgress
            && operation!.CanCancel && !_modelPreparationRefreshing
            && !_isManaging && !RefreshLocksControls && !_chatBusy && !_quitting
            && !_closing.IsCancellationRequested && _engineConnected
            && _rejectedApiKey is null;
        UpdateModelRowStates();
    }

    private async Task RefreshModelPreparationsAsync()
    {
        if (_modelPreparationRefreshing || _quitting || _closing.IsCancellationRequested
            || !_engineConnected || _rejectedApiKey is not null)
            return;
        _modelPreparationRefreshing = true;
        long epoch = _modelPreparationEpoch;
        string requestedKey = ApiKeyBox.Password;
        UpdateModelPreparationUi();
        try
        {
            var current = await _backend.GetModelPreparationsAsync(
                requestedKey, _closing.Token);
            if (_closing.IsCancellationRequested || epoch != _modelPreparationEpoch
                || !_engineConnected || _rejectedApiKey is not null
                || ApiKeyBox.Password != requestedKey)
                return;
            _modelPreparations = current;
            _modelPreparationAvailable = true;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            // Fail closed without letting a failed old request erase a newer session.
            if (epoch != _modelPreparationEpoch) return;
            _modelPreparations = new Dictionary<string, ModelPreparationInfo>(
                StringComparer.Ordinal);
            _modelPreparationAvailable = false;
        }
        finally
        {
            _modelPreparationRefreshing = false;
            if (!_closing.IsCancellationRequested)
            {
                UpdateModelPreparationUi();
                UpdateGenerationSettingsInfo();
            }
        }
    }

    private async void CancelPreparationButton_Click(object sender, RoutedEventArgs args)
    {
        ModelInfo? model = SelectedLibraryModel;
        if (model is null || !_modelPreparationAvailable || _isManaging
            || _modelPreparationRefreshing || _isRefreshing || _chatBusy || _quitting || !_engineConnected
            || _rejectedApiKey is not null || _closing.IsCancellationRequested
            || !_modelPreparations.TryGetValue(model.Id, out var chosen)
            || !chosen.CanCancel) return;

        _isManaging = true;
        UpdateChatButtons();
        try
        {
            var review = new ContentDialog
            {
                XamlRoot = ((FrameworkElement)Content).XamlRoot,
                Title = "Cancel model preparation?",
                Content = "Request cancellation of the current preparation for "
                    + model.Name + "? The engine may refuse if OpenVINO native loading "
                    + "has begun or the operation has already changed. Partial files "
                    + "may remain available for later recovery.",
                PrimaryButtonText = "Request cancellation",
                CloseButtonText = "Keep running",
                DefaultButton = ContentDialogButton.Close,
            };
            if (await review.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            ShowModelsInfo("Checking the current operation before cancellation…",
                InfoBarSeverity.Informational);
            string message = await _backend.CancelModelPreparationAsync(
                chosen, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            ShowModelsInfo(message, InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowModelsInfo(SafeError(error)
                + " Refresh the Model library before trying again. "
                + "A timed-out request may already have succeeded.",
                InfoBarSeverity.Warning);
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
}
