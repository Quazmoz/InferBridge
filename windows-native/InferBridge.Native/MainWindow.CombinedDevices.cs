using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Combined-device builder: pick members from detected devices, order them with
// Move up / Move down (keyboard reachable, no drag), choose AUTO priority, AUTO
// cumulative throughput, HETERO or HETERO pipeline-parallel. Support levels and
// explanations come only from GET /v1/devices/modes (app/device_modes.py).
public sealed partial class MainWindow
{
    private CombinedTarget? _combinedTarget;
    private IReadOnlyDictionary<string, DeviceSplitSuggestion> _deviceSplitSuggestions =
        new Dictionary<string, DeviceSplitSuggestion>();

    /// <summary>Refreshes the advisor's VRAM-aware suggestions; shown, never selected.</summary>
    private async Task RefreshDeviceSplitSuggestionsAsync()
    {
        try
        {
            _deviceSplitSuggestions = await _backend.GetDeviceSplitSuggestionsAsync(
                ApiKeyBox.Password, _closing.Token);
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            _deviceSplitSuggestions = new Dictionary<string, DeviceSplitSuggestion>();
        }
        catch (Exception) { return; }
        if (!_closing.IsCancellationRequested) UpdateChatDeviceSummary();
    }

    private DeviceSplitSuggestion? SplitSuggestionFor(ModelInfo? model) =>
        model is not null && _deviceSplitSuggestions.TryGetValue(model.Id, out var hint) ? hint : null;
    private bool _combinedBuilderOpen;

    private CombinedTarget? SelectedLibraryTarget =>
        (LibraryDevicePicker.SelectedItem as ComboBoxItem)?.Tag as CombinedTarget;

    private CombinedTarget? SelectedChatTarget =>
        (ChatDevicePicker.SelectedItem as ComboBoxItem)?.Tag as CombinedTarget;

    private static string? DeviceTag(object? item) => (item as ComboBoxItem)?.Tag switch
    {
        CombinedTarget target => target.Device,
        string { Length: > 0 } device => device,
        _ => null,
    };

    /// <summary>The builder's options apply only when the request still targets its device.</summary>
    private static (string? Hint, string? Distribution) TargetOptions(CombinedTarget? target, string? device) =>
        target is not null && device == target.Device
            ? (target.PerformanceHint, target.ModelDistribution) : (null, null);

    private bool CombineDevicesReady => _devicesAvailable && _engineConnected && !_combinedBuilderOpen
        && !_chatBusy && !_isManaging && !RefreshLocksControls && !_quitting
        && !_closing.IsCancellationRequested;

    private async void CombineDevicesButton_Click(object sender, RoutedEventArgs args)
    {
        bool fromChat = ReferenceEquals(sender, ChatCombineDevicesButton);
        await ShowCombinedDeviceBuilderAsync(fromChat ? SelectedChatModel : SelectedLibraryModel, fromChat);
    }

    private async Task ShowCombinedDeviceBuilderAsync(ModelInfo? model, bool fromChat)
    {
        if (!CombineDevicesReady) return;
        _combinedBuilderOpen = true;
        UpdateChatButtons();
        try
        {
            IReadOnlyList<string> kinds = CombinedModes.KindsFor(model?.Role ?? "chat", null);
            if (model is { IsChatModel: true })
            {
                try
                {
                    kinds = CombinedModes.KindsFor("chat", await _backend.GetModelSupportsVisionAsync(
                        model.Id, ApiKeyBox.Password, _closing.Token));
                }
                catch (Exception) when (!_closing.IsCancellationRequested) { } // judged as chat and vision
            }
            CombinedModeBuilder initial = await _backend.GetCombinedModesAsync(
                null, ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            if (initial.Choices.Count < 2)
            {
                ShowModelsInfo("Combining devices needs at least two detected devices (CPU, GPU or NPU).",
                    InfoBarSeverity.Informational);
                return;
            }
            CombinedTarget? chosen = await RunCombinedBuilderDialogAsync(initial, kinds, model);
            if (chosen is null || _closing.IsCancellationRequested) return;
            _combinedTarget = chosen;
            if (_shownDevices is { } shown) ApplyDeviceOptions(shown);
            ComboBox picker = fromChat ? ChatDevicePicker : LibraryDevicePicker;
            picker.SelectedItem = picker.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(item => Equals(item.Tag, chosen)) ?? picker.SelectedItem;
            string message = chosen.PickerLabel + " is the next load target. Select "
                + (fromChat ? "Load / switch" : "Load") + " to use it; the engine reports where the model actually runs.";
            if (fromChat) ShowChatInfo("Combined device target", message, InfoBarSeverity.Informational);
            else ShowModelsInfo(message, InfoBarSeverity.Informational);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            ShowModelsInfo("Combined devices unavailable: " + SafeError(error), InfoBarSeverity.Warning);
        }
        finally
        {
            _combinedBuilderOpen = false;
            if (!_closing.IsCancellationRequested) UpdateChatButtons();
        }
    }

    private async Task<CombinedTarget?> RunCombinedBuilderDialogAsync(
        CombinedModeBuilder initial, IReadOnlyList<string> kinds, ModelInfo? model)
    {
        // Display order covers every detected device; only checked ones are members.
        var order = initial.Members.Concat(initial.Choices.Where(c => !initial.Members.Contains(c))).ToList();
        var included = new HashSet<string>(initial.Members, StringComparer.Ordinal);
        CombinedModeBuilder? current = initial;
        string? selectedId = _combinedTarget is { } previous
            ? initial.Options.FirstOrDefault(o => o.Device == previous.Device
                && o.PerformanceHint == previous.PerformanceHint
                && o.ModelDistribution == previous.ModelDistribution)?.Id
            : null;
        int version = 0;

        var membersPanel = new StackPanel { Spacing = 4 };
        var modesPanel = new StackPanel { Spacing = 8 };
        var status = Ui.Text("", "CaptionTextBlockStyle", secondary: true);
        AutomationProperties.SetLiveSetting(status, AutomationLiveSetting.Polite);
        AutomationProperties.SetName(status, "Combined device target");
        var body = new StackPanel { Spacing = 12 };
        body.Children.Add(Ui.Text("Pick the devices to combine and their order. AUTO tries the first device first; "
            + "HETERO gives the first device the layers it supports. The engine marks each mode for "
            + (model is null ? "this kind of model" : model.Name) + ".", secondary: true));
        if (SplitSuggestionFor(model) is { } split)
            body.Children.Add(Ui.Text("Suggested for " + model!.Name + ": HETERO pipeline-parallel on GPU, CPU. "
                + split.Reason));
        body.Children.Add(Ui.Text("Devices, in order", "BodyStrongTextBlockStyle"));
        body.Children.Add(membersPanel);
        body.Children.Add(Ui.Text("Mode", "BodyStrongTextBlockStyle"));
        body.Children.Add(modesPanel);
        body.Children.Add(status);
        body.Children.Add(Ui.Text("Every combined mode is experimental and none is benchmarked on Intel Core Ultra "
            + "hardware yet. Compare them in Benchmark Lab before relying on one.", secondary: true));

        var dialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = "Combine devices (experimental)",
            Content = new ScrollViewer
            {
                Content = body,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            },
            PrimaryButtonText = "Use this target",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            IsPrimaryButtonEnabled = false,
        };

        CombinedModeOption? Selected() => current?.Options.FirstOrDefault(o => o.Id == selectedId
            && !o.SupportFor(kinds).Unsupported);

        void UpdateStatus(string? text = null)
        {
            CombinedModeOption? option = Selected();
            dialog.IsPrimaryButtonEnabled = option is not null;
            status.Text = text ?? (option is null ? "Choose an available mode."
                : "Next load target: " + option.Device
                    + (option.PerformanceHint is { } hint ? " with " + hint : "")
                    + (option.ModelDistribution is not null ? " with pipeline-parallel" : "") + ".");
            // Narrator announces the polite live region when its text changes.
            (FrameworkElementAutomationPeer.FromElement(status)
                ?? FrameworkElementAutomationPeer.CreatePeerForElement(status))
                ?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
        }

        void RenderModes()
        {
            modesPanel.Children.Clear();
            if (current is null) return;
            foreach (CombinedModeOption option in current.Options)
            {
                ModeSupport support = option.SupportFor(kinds);
                var header = new Grid { ColumnSpacing = 8 };
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.Children.Add(Ui.Text(option.Label + " · " + option.Device, "BodyStrongTextBlockStyle"));
                if (option.Experimental)
                {
                    var badge = Ui.Pill("Experimental", "warning");
                    Grid.SetColumn(badge, 1);
                    header.Children.Add(badge);
                }
                string verdict = support.Support switch
                {
                    "unsupported" => "Not available: ",
                    "experimental" => "Experimental: ",
                    _ => "Supported: ",
                } + support.Reason;
                var content = new StackPanel { Spacing = 2 };
                content.Children.Add(header);
                content.Children.Add(Ui.Text(option.Summary, "CaptionTextBlockStyle", secondary: true));
                content.Children.Add(Ui.Text(verdict, "CaptionTextBlockStyle"));
                var radio = new RadioButton
                {
                    GroupName = "CombinedDeviceMode",
                    Content = content,
                    Tag = option,
                    IsEnabled = !support.Unsupported,
                    IsChecked = option.Id == selectedId && !support.Unsupported,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Stretch,
                };
                AutomationProperties.SetName(radio, option.Label + ", " + option.Device
                    + (option.Experimental ? ", experimental. " : ". ") + option.Summary + " " + verdict);
                radio.Checked += (_, _) =>
                {
                    selectedId = option.Id;
                    UpdateStatus();
                };
                modesPanel.Children.Add(radio);
            }
            UpdateStatus();
        }

        async Task ReloadAsync()
        {
            int mine = ++version;
            var members = order.Where(included.Contains).ToList();
            current = null;
            RenderModes();
            if (members.Count < 2)
            {
                UpdateStatus("Choose at least two devices to combine.");
                return;
            }
            UpdateStatus("Checking these devices with the engine…");
            try
            {
                CombinedModeBuilder fresh = await _backend.GetCombinedModesAsync(
                    members, ApiKeyBox.Password, _closing.Token);
                if (mine != version || _closing.IsCancellationRequested) return;
                current = fresh;
                RenderModes();
            }
            catch (Exception error) when (!_closing.IsCancellationRequested)
            {
                if (mine == version) UpdateStatus("The engine could not check these devices: " + SafeError(error));
            }
        }

        void RenderMembers(string? focusBase = null, int focusDelta = 0)
        {
            membersPanel.Children.Clear();
            int position = 0;
            for (int index = 0; index < order.Count; index++)
            {
                string device = order[index];
                int at = index;
                bool member = included.Contains(device);
                if (member) position++;
                var row = new Grid { ColumnSpacing = 4 };
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                var check = new CheckBox
                {
                    Content = new TextBlock
                    {
                        Text = member ? position + ". " + device : device + " (not used)",
                        TextWrapping = TextWrapping.Wrap,
                    },
                    IsChecked = member,
                };
                AutomationProperties.SetName(check, "Use " + device
                    + (member ? ", position " + position : ""));
                check.Click += async (_, _) =>
                {
                    if (check.IsChecked == true) included.Add(device);
                    else included.Remove(device);
                    RenderMembers(device);
                    await ReloadAsync();
                };
                row.Children.Add(check);
                Button Mover(string glyph, int delta, string verb)
                {
                    var button = new Button
                    {
                        Content = Ui.Icon(glyph, 14),
                        IsEnabled = delta < 0 ? at > 0 : at < order.Count - 1,
                        MinWidth = 32,
                        MinHeight = 32,
                    };
                    AutomationProperties.SetName(button, "Move " + device + " " + verb);
                    ToolTipService.SetToolTip(button, "Move " + device + " " + verb);
                    button.Click += async (_, _) =>
                    {
                        var moved = CombinedModes.Move(order, at, delta);
                        order.Clear();
                        order.AddRange(moved);
                        RenderMembers(device, delta);
                        if (included.Contains(device)) await ReloadAsync();
                    };
                    Grid.SetColumn(button, delta < 0 ? 1 : 2);
                    row.Children.Add(button);
                    return button;
                }
                Button up = Mover("\uE70E", -1, "up");
                Button down = Mover("\uE70D", 1, "down");
                membersPanel.Children.Add(row);
                if (device == focusBase)
                {
                    // Keep keyboard focus on the moved device so repeated presses keep moving it.
                    Control target = focusDelta < 0 && up.IsEnabled ? up
                        : focusDelta > 0 && down.IsEnabled ? down : check;
                    DispatcherQueue.TryEnqueue(() => target.Focus(FocusState.Keyboard));
                }
            }
        }

        RenderMembers();
        RenderModes();
        ContentDialogResult result = await dialog.ShowAsync();
        version++; // ignore any response that lands after the dialog closed
        return result == ContentDialogResult.Primary ? Selected()?.ToTarget() : null;
    }
}
