using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// One shared per-model context editor. Model cards, the load step, the chat
// pane and the Settings list all open the same dialog, which reads the model's
// own config.json limits from the engine and applies ContextEditor's rules.
// The model never needs to be loaded: unloaded models keep the value for their
// next load; loaded models can apply it now (engine rebuild, chat kept).
// Background list refreshes only repaint text; they never disable controls.
// While a value is applied, the dialog stays open with a ProgressRing and every
// surface showing that model's context (card, chat bar, chat pane, Settings)
// reads "Applying 16K context… rebuilding engine" until the engine confirms.
public sealed partial class MainWindow
{
    private readonly Dictionary<string, ContextSummary> _contextSummaries = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (TextBlock Line, Button Edit, ProgressRing Busy)> _modelContextLines =
        new(StringComparer.Ordinal);
    // This window's own apply in flight (user-initiated); polls never set or clear it.
    private (string ModelId, int Tokens, bool Loaded, string? Kv)? _contextApplying;
    private bool _contextSummariesLoading;
    private DateTime _contextSummariesAt = DateTime.MinValue;
    private string _contextListSignature = "";

    private ContextSummary? ContextSummaryFor(string? modelId) =>
        modelId is not null && _contextSummaries.TryGetValue(modelId, out var row) ? row : null;

    /// <summary>
    /// The busy line for a model with an apply in flight: this window's own, else one
    /// the engine reports as changing (for example started from the browser). Null when idle.
    /// </summary>
    private string? ContextBusyText(string? modelId)
    {
        if (modelId is null) return null;
        if (_contextApplying is { } local && local.ModelId == modelId)
            return ContextEditor.ApplyingText(local.Tokens, local.Loaded, local.Kv);
        return ContextSummaryFor(modelId) is { Changing: true } row
            ? ContextEditor.ApplyingText(null, row.Loaded) : null;
    }

    /// <summary>True while the selected, loaded chat model's engine is rebuilt for a new context.</summary>
    private bool ChatModelRebuilding => SelectedChatModel is { Status: "loaded" } model
        && ((_contextApplying is { Loaded: true } local && local.ModelId == model.Id)
            || ContextSummaryFor(model.Id) is { Changing: true, Loaded: true });

    private string ContextLineText(ContextSummary? summary) => ContextBusyText(summary?.ModelId) ?? summary switch
    {
        null => ContextEditor.ChipText(null),
        { Configurable: false } => "Context: not configurable (embedding model)",
        { Trial: true } => ContextEditor.ChipText(summary.Current) + " · trial",
        _ => ContextEditor.ChipText(summary.Current),
    };

    // Called when Settings, Models or Chat is shown, and by Refresh.
    private async Task RefreshContextConfigurationAsync() => await RefreshContextSummariesAsync(force: true);

    private async Task RefreshContextSummariesAsync(bool force = false)
    {
        if (_contextSummariesLoading || _closing.IsCancellationRequested || !_engineConnected
            || _rejectedApiKey is not null) return;
        if (!force && DateTime.UtcNow - _contextSummariesAt < TimeSpan.FromSeconds(10)) return;
        _contextSummariesLoading = true;
        try
        {
            var rows = await _backend.GetContextSummariesAsync(ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _contextSummaries.Clear();
            foreach (var row in rows) _contextSummaries[row.ModelId] = row;
            _contextSummariesAt = DateTime.UtcNow;
            if (!_contextOperationBusy && ContextConfigurationInfo.Severity != InfoBarSeverity.Success)
            {
                ContextConfigurationInfo.Title = "Context configuration";
                ContextConfigurationInfo.Message = rows.Count == 0
                    ? "No models are registered yet."
                    : "Select Edit beside a model to change its context window.";
                ContextConfigurationInfo.Severity = InfoBarSeverity.Informational;
            }
        }
        catch (Exception) when (_closing.IsCancellationRequested) { return; }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            // Advisory list: keep the last known values and say so only in Settings.
            if (!_contextOperationBusy)
            {
                ContextConfigurationInfo.Title = "Context list unavailable";
                ContextConfigurationInfo.Message = error is EngineRequestException
                    ? error.Message : "The engine could not list model context windows. Select Refresh to try again.";
                ContextConfigurationInfo.Severity = InfoBarSeverity.Warning;
            }
        }
        finally
        {
            _contextSummariesLoading = false;
        }
        ApplyContextSummaries();
    }

    /// <summary>Repaints every context line from the cache. Text and visibility only.</summary>
    private void ApplyContextSummaries()
    {
        if (_closing.IsCancellationRequested) return;
        foreach (var (modelId, entry) in _modelContextLines)
            entry.Edit.Visibility = ContextSummaryFor(modelId) is { Configurable: false }
                ? Visibility.Collapsed : Visibility.Visible;
        foreach (var (button, model, action) in _modelRowButtons)
            if (action == "loadctx")
                button.Visibility = model.CanLoad && ContextSummaryFor(model.Id) is not { Configurable: false }
                    ? Visibility.Visible : Visibility.Collapsed;
        UpdateContextControls();
    }

    // Settings: every registered model with its context and an Edit button. Rebuilt
    // only when a value actually changes, so focus is not lost on each refresh.
    private void RebuildContextModelsList()
    {
        string signature = string.Join("|", _contextSummaries.Values.Select(row =>
            row.ModelId + ":" + row.Current + ":" + row.Loaded + ":" + row.Trial + ":" + row.Configurable
            + ":" + ContextBusyText(row.ModelId)));
        if (signature == _contextListSignature) return;
        _contextListSignature = signature;
        ContextModelsPanel.Children.Clear();
        foreach (var row in _contextSummaries.Values.OrderBy(row => row.Name, StringComparer.CurrentCultureIgnoreCase))
        {
            var grid = new Grid { ColumnSpacing = 12 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var names = new StackPanel { Spacing = 2 };
            names.Children.Add(Ui.Text(row.Name, "BodyStrongTextBlockStyle"));
            names.Children.Add(Ui.Text(ContextLineText(row)
                + (row.Loaded ? " · loaded" : "")
                + (row.ArchitectureLimit is int limit ? " · model max " + ContextEditor.Format(limit) : ""),
                "CaptionTextBlockStyle", secondary: true));
            grid.Children.Add(names);
            if (row.Configurable)
            {
                var edit = new Button { Content = "Edit…", Tag = row.ModelId, VerticalAlignment = VerticalAlignment.Center };
                AutomationProperties.SetName(edit, "Edit context window for " + row.Name);
                edit.Click += ContextListEdit_Click;
                Grid.SetColumn(edit, 1);
                grid.Children.Add(edit);
            }
            AutomationProperties.SetName(grid, row.Name + ", " + ContextLineText(row));
            ContextModelsPanel.Children.Add(grid);
        }
        if (_contextSummaries.Count == 0)
            ContextModelsPanel.Children.Add(Ui.Text("No models to show yet.", "CaptionTextBlockStyle", secondary: true));
    }

    /// <summary>
    /// Enablement for context buttons; depends only on user-initiated busy states.
    /// Also repaints every context line and busy ring (text and visibility only).
    /// </summary>
    private void UpdateContextControls()
    {
        bool ready = _engineConnected && !_contextOperationBusy && !_quitting
            && !_closing.IsCancellationRequested;
        RebuildContextModelsList();
        foreach (var (modelId, entry) in _modelContextLines)
        {
            string line = ContextLineText(ContextSummaryFor(modelId));
            if (entry.Line.Text != line) entry.Line.Text = line;
            ShowBusy(entry.Busy, ContextBusyText(modelId) is not null);
            entry.Edit.IsEnabled = ready;
        }
        foreach (var child in ContextModelsPanel.Children.OfType<Grid>())
            foreach (var button in child.Children.OfType<Button>())
                button.IsEnabled = ready;

        ModelInfo? model = SelectedChatModel;
        ContextSummary? summary = ContextSummaryFor(model?.Id);
        string? busy = ContextBusyText(model?.Id);
        string text = model is null ? "Context: choose a model" : busy ?? ContextLineText(summary);
        if (ChatContextSummary.Text != text) ChatContextSummary.Text = text;
        if (ChatPaneContextText.Text != text) ChatPaneContextText.Text = text;
        ShowBusy(ChatContextBusyRing, busy is not null);
        ShowBusy(ChatPaneContextBusyRing, busy is not null);
        UpdateChatRebuildState();
        bool configurable = summary is not { Configurable: false };
        // Unloaded: choose the context, then load. Loaded: change it (apply now).
        ChatLoadWithContextButton.Content = model?.CanLoad == true ? "Load with context…" : "Context…";
        ChatLoadWithContextButton.IsEnabled = ready && configurable && !_isManaging && !_chatBusy
            && model is { Status: "loaded" or "ready_to_load" };
        ChatPaneContextButton.IsEnabled = ready && configurable && !_isManaging
            && model is { Status: "loaded" or "ready_to_load" };
    }

    private static void ShowBusy(ProgressRing ring, bool busy)
    {
        if (ring.IsActive != busy) ring.IsActive = busy;
        var visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (ring.Visibility != visibility) ring.Visibility = visibility;
    }

    /// <summary>Composer note and ring while the selected model rebuilds; Send waits for it.</summary>
    private void UpdateChatRebuildState()
    {
        bool rebuilding = ChatModelRebuilding;
        string note = rebuilding ? ContextEditor.SendPausedText(
            _contextApplying is { } local && local.ModelId == SelectedChatModel?.Id ? (int?)local.Tokens : null) : "";
        if (ChatRebuildText.Text != note) ChatRebuildText.Text = note;
        var visibility = rebuilding ? Visibility.Visible : Visibility.Collapsed;
        if (ChatRebuildText.Visibility != visibility) ChatRebuildText.Visibility = visibility;
        ChatProgress.IsActive = _chatBusy || rebuilding;
    }

    /// <summary>Narrator: speak a context-apply transition once (start, success, failure).</summary>
    private static void AnnounceContext(UIElement element, string text)
    {
        if (text.Length == 0) return;
        (FrameworkElementAutomationPeer.FromElement(element)
            ?? FrameworkElementAutomationPeer.CreatePeerForElement(element))
            ?.RaiseNotificationEvent(AutomationNotificationKind.ActionCompleted,
                AutomationNotificationProcessing.ImportantMostRecent, text, "InferBridge.ContextApply");
    }

    private void ReportContextToSettings(string title, string message, InfoBarSeverity severity)
    {
        ContextConfigurationInfo.Title = title;
        ContextConfigurationInfo.Message = message;
        ContextConfigurationInfo.Severity = severity;
    }

    private void ReportContextToModels(string title, string message, InfoBarSeverity severity) =>
        ShowModelsInfo(title + ". " + message, severity);

    private void ReportContextToChat(string title, string message, InfoBarSeverity severity)
    {
        GenerationSettingsInfo.Title = title;
        GenerationSettingsInfo.Message = message;
        GenerationSettingsInfo.Severity = severity;
    }

    /// <summary>The note appended to the chat "Loading model" status: which context it uses.</summary>
    private string LoadContextNote(string modelId, ModelAction action, int? chosen)
    {
        if (action != ModelAction.Load) return "";
        int? tokens = chosen ?? ContextSummaryFor(modelId)?.Current;
        return tokens is int value
            ? " Context: " + ContextEditor.Format(value) + " tokens"
                + (chosen is null ? " (saved for this model; change it with Context…)." : ".")
            : "";
    }

    /// <summary>
    /// Opens the shared editor for any registered model. Edit saves (next load) or
    /// applies now (loaded); LoadWith loads an unloaded model with the chosen value.
    /// </summary>
    private async Task OpenContextEditorAsync(string modelId, ContextEditorMode mode,
        Action<string, string, InfoBarSeverity> report, bool fromChat = false)
    {
        if (_contextOperationBusy || _quitting || _closing.IsCancellationRequested) return;
        if (!_engineConnected)
        {
            report("Engine not connected", "Start or connect the local engine, then try again.",
                InfoBarSeverity.Warning);
            return;
        }
        if (ContextSummaryFor(modelId) is { Configurable: false })
        {
            report("Context not configurable", "Embedding models do not use a chat context window.",
                InfoBarSeverity.Informational);
            return;
        }
        ContextConfiguration context;
        _contextOperationBusy = true;
        UpdateContextControls();
        try
        {
            context = await _backend.GetContextAsync(modelId, ApiKeyBox.Password, _closing.Token);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { return; }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            report("Context information unavailable", error is EngineRequestException
                ? error.Message : "The engine could not read this model's context limits. Check the model files, then try again.",
                InfoBarSeverity.Warning);
            return;
        }
        finally
        {
            _contextOperationBusy = false;
            UpdateContextControls();
        }
        _contextBaseline = context;
        // A model loaded since the button was shown is edited (apply now), not loaded.
        if (mode == ContextEditorMode.LoadWith && context.Loaded) mode = ContextEditorMode.Edit;
        string name = _shownModels.FirstOrDefault(model => model.Id == modelId)?.Name ?? modelId;
        // Edit applies inside the dialog (it stays open with progress until the engine
        // confirms); LoadWith closes it and hands the value to the normal load flow.
        var chosen = await ShowContextEditorDialogAsync(context, mode, name,
            mode == ContextEditorMode.LoadWith ? null : (size, precision, local) => SaveContextAsync(context, size,
                (title, message, severity) => { report(title, message, severity); local(title, message, severity); },
                precision));
        if (chosen is not { } picked || _closing.IsCancellationRequested) return;
        var (tokens, kv) = picked;
        if (mode == ContextEditorMode.LoadWith)
        {
            ModelInfo? model = _shownModels.FirstOrDefault(row => row.Id == modelId);
            if (model is not { CanLoad: true })
            {
                report("Model state changed", "Refresh the library, then try again.", InfoBarSeverity.Warning);
                return;
            }
            if (kv != context.KvPrecision)
            {
                // Persist the KV precision (and context) first; the load then uses both.
                await SaveContextAsync(context, tokens, report, kv);
                if (_contextBaseline is not { } saved || saved.KvPrecision != kv
                    || saved.Current != tokens) return;
                await ManageModelAsync(ModelAction.Load, model, fromChat, null);
                return;
            }
            await ManageModelAsync(ModelAction.Load, model, fromChat,
                tokens == context.Current ? null : tokens);
        }
    }

    /// <summary>
    /// The shared editor dialog. With <paramref name="apply"/> (Edit), the primary
    /// button applies in place: controls lock, a ProgressRing and status text show
    /// until the engine confirms, then a brief success state closes the dialog; a
    /// failure restores the controls and keeps the friendly error. Without it
    /// (LoadWith) the dialog closes and returns the choice.
    /// </summary>
    private async Task<(int Tokens, string Kv)?> ShowContextEditorDialogAsync(ContextConfiguration context,
        ContextEditorMode mode, string modelName,
        Func<int, string, Action<string, string, InfoBarSeverity>, Task<bool>>? apply = null)
    {
        var presets = ContextEditor.Presets(context);
        var panel = new StackPanel { Spacing = 12, MinWidth = 340, MaxWidth = 520 };
        panel.Children.Add(Ui.Text(ContextEditor.Facts(context), "BodyTextBlockStyle"));
        var picker = new ComboBox
        {
            Header = "Preset", PlaceholderText = "Custom value", HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(picker, "Context window preset for " + modelName);
        foreach (var preset in presets)
            picker.Items.Add(new ComboBoxItem { Content = preset.Label, Tag = preset.Tokens });
        panel.Children.Add(picker);
        var box = new NumberBox
        {
            Header = "Context window (tokens)",
            Value = context.Current,
            Minimum = ContextEditor.Minimum,
            Maximum = ContextEditor.Maximum,
            SmallChange = 1024,
            LargeChange = 8192,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
            ValidationMode = NumberBoxValidationMode.Disabled,
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(box, "Custom context window in tokens for " + modelName);
        panel.Children.Add(box);
        // KV-cache precision: Auto keeps OpenVINO's choice; explicit values reach CPU/GPU only.
        var kvPicker = new ComboBox { Header = "KV cache precision", HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(kvPicker, "KV cache precision for " + modelName);
        foreach (var option in ContextEditor.KvChoices(context))
            kvPicker.Items.Add(new ComboBoxItem
            {
                Content = option.Available ? option.Label : option.Label + " (unavailable on NPU)",
                Tag = option.Value, IsEnabled = option.Available,
            });
        kvPicker.SelectedItem = kvPicker.Items.OfType<ComboBoxItem>()
            .FirstOrDefault(item => item.Tag is string value && value == context.KvPrecision);
        panel.Children.Add(kvPicker);
        if (context.KvNote.Length > 0)
            panel.Children.Add(Ui.Text(context.KvNote, "CaptionTextBlockStyle", secondary: true));
        if (context.Guidance.Length > 0)
            panel.Children.Add(Ui.Text(context.Guidance, "CaptionTextBlockStyle", secondary: true));
        string SelectedKv() => kvPicker.SelectedItem is ComboBoxItem { Tag: string value }
            ? value : context.KvPrecision;
        var status = new InfoBar { IsOpen = true, IsClosable = false };
        AutomationProperties.SetLiveSetting(status, AutomationLiveSetting.Polite);
        panel.Children.Add(status);
        // In-progress row: shown from the moment Apply/Save is pressed until the engine answers.
        var busyRing = new ProgressRing
        {
            IsActive = false, Width = 24, Height = 24, VerticalAlignment = VerticalAlignment.Center,
            Foreground = Ui.ResBrush("InferBridgeBrandBrush"),
        };
        AutomationProperties.SetName(busyRing, "Applying context");
        var busyText = Ui.Text(" ", "BodyStrongTextBlockStyle");
        AutomationProperties.SetLiveSetting(busyText, AutomationLiveSetting.Polite);
        var busyLines = new StackPanel { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        busyLines.Children.Add(busyText);
        busyLines.Children.Add(Ui.Text(context.Loaded
            ? "This can take a while for large models. Your chat history is kept."
            : "Used next time this model loads.", "CaptionTextBlockStyle", secondary: true));
        var busyRow = new Grid { ColumnSpacing = 12, Visibility = Visibility.Collapsed };
        busyRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        busyRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        busyRow.Children.Add(busyRing);
        Grid.SetColumn(busyLines, 1);
        busyRow.Children.Add(busyLines);
        panel.Children.Add(busyRow);
        panel.Children.Add(Ui.Text(ContextEditor.WhenItApplies(mode, context.Loaded),
            "CaptionTextBlockStyle", secondary: true));

        var dialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = (mode == ContextEditorMode.LoadWith ? "Load " : "Context window · ") + modelName,
            Content = new ScrollViewer
            {
                Content = panel,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            },
            PrimaryButtonText = ContextEditor.PrimaryAction(mode, context.Loaded),
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
        };

        bool syncing = false;
        void Refresh()
        {
            var choice = ContextEditor.Validate(context, box.Value, SelectedKv());
            status.Title = !choice.Valid ? "Check the value"
                : choice.Trial ? "Trial value" : "Ready";
            status.Message = choice.Message;
            status.Severity = !choice.Valid ? InfoBarSeverity.Error
                : choice.Trial ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
            dialog.IsPrimaryButtonEnabled = ContextEditor.CanCommit(context, choice, mode, SelectedKv());
            if (syncing) return;
            syncing = true;
            try
            {
                picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(item =>
                    item.Tag is int size && choice.Valid && size == choice.Tokens);
            }
            finally { syncing = false; }
        }
        picker.SelectionChanged += (_, _) =>
        {
            if (syncing || picker.SelectedItem is not ComboBoxItem { Tag: int size }) return;
            syncing = true;
            try { box.Value = size; }
            finally { syncing = false; }
            Refresh();
        };
        box.ValueChanged += (_, _) => Refresh();
        kvPicker.SelectionChanged += (_, _) => Refresh();

        bool applying = false;
        (int Tokens, string Kv)? applied = null;
        string primaryText = dialog.PrimaryButtonText;
        void SetApplying(bool on, string text)
        {
            applying = on;
            picker.IsEnabled = box.IsEnabled = kvPicker.IsEnabled = !on;
            dialog.IsPrimaryButtonEnabled = !on;
            dialog.PrimaryButtonText = on ? (context.Loaded ? "Applying…" : "Saving…") : primaryText;
            // No Cancel while the engine rebuilds: the request cannot be withdrawn halfway.
            dialog.CloseButtonText = on ? "" : "Cancel";
            busyText.Text = on ? text : " ";
            busyRing.IsActive = on;
            busyRow.Visibility = on ? Visibility.Visible : Visibility.Collapsed;
        }
        // Results (success or friendly error) land in the dialog's own status bar too.
        void Local(string title, string message, InfoBarSeverity severity)
        {
            if (severity == InfoBarSeverity.Informational) return;
            status.Title = title;
            status.Message = message;
            status.Severity = severity;
        }
        dialog.Closing += (_, args) =>
        {
            if (applying) args.Cancel = true;
        };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            string kv = SelectedKv();
            var choice = ContextEditor.Validate(context, box.Value, kv);
            if (!ContextEditor.CanCommit(context, choice, mode, kv))
            {
                args.Cancel = true;
                return;
            }
            if (apply is null) return; // LoadWith: close; the caller runs the load with progress.
            args.Cancel = true; // Stay open with progress until the engine answers.
            if (applying) return;
            string? kvChange = kv == context.KvPrecision ? null : kv;
            string busy = ContextEditor.ApplyingText(choice.Tokens, context.Loaded, kvChange);
            SetApplying(true, busy);
            AnnounceContext(busyRow, busy);
            bool ok = await apply(choice.Tokens, kv, Local);
            if (_closing.IsCancellationRequested) return;
            if (ok)
            {
                applied = (choice.Tokens, kv);
                busyRing.IsActive = false;
                busyRow.Visibility = Visibility.Collapsed;
                dialog.PrimaryButtonText = "Done";
                AnnounceContext(status, status.Title + ". " + status.Message);
                await Task.Delay(TimeSpan.FromMilliseconds(1400));
                applying = false;
                dialog.Hide();
                return;
            }
            // Failure: the engine kept the old value. Unlock and keep the friendly error visible.
            SetApplying(false, "");
            dialog.IsPrimaryButtonEnabled = ContextEditor.CanCommit(context,
                ContextEditor.Validate(context, box.Value, SelectedKv()), mode, SelectedKv());
            AnnounceContext(status, status.Title + ". " + status.Message);
        };
        Refresh();

        var result = await dialog.ShowAsync();
        if (apply is not null) return applied;
        if (result != ContentDialogResult.Primary) return null;
        var final = ContextEditor.Validate(context, box.Value, SelectedKv());
        return final.Valid ? (final.Tokens, SelectedKv()) : null;
    }

    /// <summary>Saves or applies a value; true only when the engine confirmed it.</summary>
    private async Task<bool> SaveContextAsync(ContextConfiguration baseline, int tokens,
        Action<string, string, InfoBarSeverity> report, string? kv = null)
    {
        if (_contextOperationBusy || _closing.IsCancellationRequested) return false;
        if (baseline.Loaded && _chatBusy)
        {
            report("Reply in progress", "Wait for the reply to finish, then apply the new context.",
                InfoBarSeverity.Warning);
            return false;
        }
        string? kvChange = kv is null || kv == baseline.KvPrecision ? null : kv;
        bool confirmed = false;
        _contextOperationBusy = true;
        // Every surface showing this model reads "Applying…" and chat sends wait.
        _contextApplying = (baseline.ModelId, tokens, baseline.Loaded, kvChange);
        UpdateContextControls();
        UpdateChatButtons();
        report(baseline.Loaded ? "Applying context" : "Saving context", baseline.Loaded
            ? "Rebuilding the engine with " + ContextEditor.Format(tokens)
                + " tokens. Chat sends wait until it is ready; your chat history is kept."
            : "Saving " + ContextEditor.Format(tokens) + " tokens for this model…",
            InfoBarSeverity.Informational);
        try
        {
            var applied = await _backend.SetContextAsync(baseline, tokens, ApiKeyBox.Password,
                _closing.Token, kv == baseline.KvPrecision ? null : kv);
            _contextBaseline = applied;
            confirmed = true;
            report(ContextEditor.AppliedText(tokens, kvChange), ContextEditor.SavedMessage(tokens, applied.Loaded),
                InfoBarSeverity.Success);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (ArgumentException error)
        {
            report("Check the value", error.Message, InfoBarSeverity.Warning);
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            report("Context not confirmed", error is EngineRequestException
                ? error.Message
                : "Could not confirm whether the engine saved this value. Select Refresh before retrying.",
                InfoBarSeverity.Warning);
        }
        finally
        {
            _contextOperationBusy = false;
            _contextApplying = null;
            if (!_closing.IsCancellationRequested)
            {
                await RefreshContextSummariesAsync(force: true);
                UpdateContextControls();
                UpdateChatButtons();
            }
        }
        return confirmed;
    }

    private async void ContextListEdit_Click(object sender, RoutedEventArgs args)
    {
        if (sender is Button { Tag: string modelId })
            await OpenContextEditorAsync(modelId, ContextEditorMode.Edit, ReportContextToSettings);
    }

    private async void ContextRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshContextSummariesAsync(force: true);

    private async void ChatLoadWithContextButton_Click(object sender, RoutedEventArgs args)
    {
        if (SelectedChatModel is not { } model) return;
        await OpenContextEditorAsync(model.Id,
            model.CanLoad ? ContextEditorMode.LoadWith : ContextEditorMode.Edit,
            ReportContextToChat, fromChat: true);
    }

    private async void ChatPaneContextButton_Click(object sender, RoutedEventArgs args)
    {
        if (SelectedChatModel is not { } model) return;
        await OpenContextEditorAsync(model.Id,
            model.CanLoad ? ContextEditorMode.LoadWith : ContextEditorMode.Edit,
            ReportContextToChat, fromChat: true);
    }
}
