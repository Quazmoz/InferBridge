using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Resident model slots on the Model library page: one chat, one embedding and one
// reranker model can stay loaded together. Each slot shows its model, engine-reported
// device and memory estimate, plus a per-slot Unload. Background polls only update text
// and never disable these controls (PickerRefresh rules); user actions do.
public sealed partial class MainWindow
{
    private IReadOnlyList<ModelSlot> _residentSlots = Array.Empty<ModelSlot>();
    private string _residentSlotsSignature = "";
    private readonly List<(Button Button, ModelSlot Slot)> _slotUnloadButtons = new();
    private bool _unloadingSlot;

    private void ApplyResidentSlots(IReadOnlyList<ModelSlot> slots)
    {
        _residentSlots = slots;
        string signature = string.Join("|", slots.Select(slot => slot.Summary + ":" + slot.CanUnload));
        if (signature == _residentSlotsSignature)
        {
            UpdateResidentSlotButtons();
            return;
        }
        _residentSlotsSignature = signature;
        ResidentSlotsRow.Children.Clear();
        _slotUnloadButtons.Clear();
        bool any = slots.Any(slot => slot.Resident);
        ResidentSlotsPanel.Visibility = any ? Visibility.Visible : Visibility.Collapsed;
        foreach (ModelSlot slot in slots)
        {
            var group = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            AutomationProperties.SetName(group, slot.Summary);
            group.Children.Add(Ui.Pill(slot.Summary, slot.Resident ? "accent" : "neutral"));
            if (slot.Resident)
            {
                var unload = new Button
                {
                    Content = Ui.IconLabel("\uE71A", "Unload"), MinHeight = 32, Tag = slot.Role,
                };
                AutomationProperties.SetName(unload,
                    "Unload " + slot.Name + " from the " + ModelSlot.Label(slot.Role).ToLowerInvariant() + " slot");
                ToolTipService.SetToolTip(unload, "Free this slot. Models in the other slots stay loaded.");
                unload.Click += SlotUnload_Click;
                group.Children.Add(unload);
                _slotUnloadButtons.Add((unload, slot));
            }
            ResidentSlotsRow.Children.Add(group);
        }
        UpdateResidentSlotButtons();
    }

    private void UpdateResidentSlotButtons()
    {
        bool idle = !_isManaging && !_chatBusy && !RefreshLocksControls && !_quitting && !_unloadingSlot;
        foreach (var (button, slot) in _slotUnloadButtons)
            button.IsEnabled = idle && slot.CanUnload;
    }

    private async void SlotUnload_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: string role } || _unloadingSlot || _isManaging || _chatBusy) return;
        _unloadingSlot = true;
        UpdateResidentSlotButtons();
        try
        {
            ShowModelsInfo(await _backend.UnloadSlotAsync(role, ApiKeyBox.Password, _closing.Token),
                InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { return; }
        catch (Exception error)
        {
            ShowModelsInfo(SafeError(error), InfoBarSeverity.Error);
        }
        finally
        {
            _unloadingSlot = false;
        }
        if (!_closing.IsCancellationRequested) await RefreshAsync();
    }

    /// <summary>After a combined-memory / NPU refusal, ask before loading anyway.</summary>
    private async Task OfferLoadAnywayAsync(ModelInfo model, string? device, int? contextTokens, string reason,
        CombinedTarget? combinedTarget = null)
    {
        (string? hint, string? distribution) = TargetOptions(combinedTarget, device);
        var body = Ui.Text(reason + "\n\nLoading anyway may slow this PC or fail to compile.");
        body.TextWrapping = TextWrapping.Wrap;
        var dialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = "Not enough room for " + model.Name,
            Content = body,
            PrimaryButtonText = "Load anyway",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || _closing.IsCancellationRequested)
        {
            ShowModelsInfo(reason, InfoBarSeverity.Warning);
            return;
        }
        try
        {
            ShowModelsInfo(await _backend.ChangeModelAsync(model, ModelAction.Load, ApiKeyBox.Password,
                _closing.Token, device, contextTokens, allowResourceRisk: true,
                performanceHint: hint, modelDistribution: distribution), InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            ShowModelsInfo(SafeError(error), InfoBarSeverity.Error);
        }
    }
}
