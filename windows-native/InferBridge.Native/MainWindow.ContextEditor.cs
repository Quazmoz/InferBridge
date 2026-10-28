using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// One shared per-model context editor. Model cards, the load step, the chat
// pane and the Settings list all open the same dialog, which reads the model's
// own config.json limits from the engine and applies ContextEditor's rules.
// The model never needs to be loaded: unloaded models keep the value for their
// next load; loaded models can apply it now (engine rebuild, chat kept).
// Background list refreshes only repaint text; they never disable controls.
public sealed partial class MainWindow
{
    private readonly Dictionary<string, ContextSummary> _contextSummaries = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (TextBlock Line, Button Edit)> _modelContextLines =
        new(StringComparer.Ordinal);
    private bool _contextSummariesLoading;
    private DateTime _contextSummariesAt = DateTime.MinValue;
    private string _contextListSignature = "";

    private ContextSummary? ContextSummaryFor(string? modelId) =>
        modelId is not null && _contextSummaries.TryGetValue(modelId, out var row) ? row : null;

    private static string ContextLineText(ContextSummary? summary) => summary switch
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
        {
            var summary = ContextSummaryFor(modelId);
            string text = ContextLineText(summary);
            if (entry.Line.Text != text) entry.Line.Text = text;
            entry.Edit.Visibility = summary is { Configurable: false }
                ? Visibility.Collapsed : Visibility.Visible;
        }
        foreach (var (button, model, action) in _modelRowButtons)
            if (action == "loadctx")
                button.Visibility = model.CanLoad && ContextSummaryFor(model.Id) is not { Configurable: false }
                    ? Visibility.Visible : Visibility.Collapsed;
        RebuildContextModelsList();
        UpdateContextControls();
    }

    // Settings: every registered model with its context and an Edit button. Rebuilt
    // only when a value actually changes, so focus is not lost on each refresh.
    private void RebuildContextModelsList()
    {
        string signature = string.Join("|", _contextSummaries.Values.Select(row =>
            row.ModelId + ":" + row.Current + ":" + row.Loaded + ":" + row.Trial + ":" + row.Configurable));
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

    /// <summary>Enablement for context buttons; depends only on user-initiated busy states.</summary>
    private void UpdateContextControls()
    {
        bool ready = _engineConnected && !_contextOperationBusy && !_quitting
            && !_closing.IsCancellationRequested;
        foreach (var (_, entry) in _modelContextLines)
            entry.Edit.IsEnabled = ready;
        foreach (var child in ContextModelsPanel.Children.OfType<Grid>())
            foreach (var button in child.Children.OfType<Button>())
                button.IsEnabled = ready;

        ModelInfo? model = SelectedChatModel;
        ContextSummary? summary = ContextSummaryFor(model?.Id);
        string text = model is null ? "Context: choose a model" : ContextLineText(summary);
        if (ChatContextSummary.Text != text) ChatContextSummary.Text = text;
        if (ChatPaneContextText.Text != text) ChatPaneContextText.Text = text;
        bool configurable = summary is not { Configurable: false };
        // Unloaded: choose the context, then load. Loaded: change it (apply now).
        ChatLoadWithContextButton.Content = model?.CanLoad == true ? "Load with context…" : "Context…";
        ChatLoadWithContextButton.IsEnabled = ready && configurable && !_isManaging && !_chatBusy
            && model is { Status: "loaded" or "ready_to_load" };
        ChatPaneContextButton.IsEnabled = ready && configurable && !_isManaging
            && model is { Status: "loaded" or "ready_to_load" };
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
        int? chosen = await ShowContextEditorDialogAsync(context, mode, name);
        if (chosen is not int tokens || _closing.IsCancellationRequested) return;
        if (mode == ContextEditorMode.LoadWith)
        {
            ModelInfo? model = _shownModels.FirstOrDefault(row => row.Id == modelId);
            if (model is not { CanLoad: true })
            {
                report("Model state changed", "Refresh the library, then try again.", InfoBarSeverity.Warning);
                return;
            }
            await ManageModelAsync(ModelAction.Load, model, fromChat,
                tokens == context.Current ? null : tokens);
            return;
        }
        await SaveContextAsync(context, tokens, report);
    }

    private async Task<int?> ShowContextEditorDialogAsync(ContextConfiguration context,
        ContextEditorMode mode, string modelName)
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
        var status = new InfoBar { IsOpen = true, IsClosable = false };
        AutomationProperties.SetLiveSetting(status, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        panel.Children.Add(status);
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
            var choice = ContextEditor.Validate(context, box.Value);
            status.Title = !choice.Valid ? "Check the value"
                : choice.Trial ? "Trial value" : "Ready";
            status.Message = choice.Message;
            status.Severity = !choice.Valid ? InfoBarSeverity.Error
                : choice.Trial ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
            dialog.IsPrimaryButtonEnabled = ContextEditor.CanCommit(context, choice, mode);
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
        dialog.PrimaryButtonClick += (_, args) =>
        {
            if (!ContextEditor.CanCommit(context, ContextEditor.Validate(context, box.Value), mode))
                args.Cancel = true;
        };
        Refresh();

        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return null;
        var final = ContextEditor.Validate(context, box.Value);
        return final.Valid ? final.Tokens : null;
    }

    private async Task SaveContextAsync(ContextConfiguration baseline, int tokens,
        Action<string, string, InfoBarSeverity> report)
    {
        if (_contextOperationBusy || _closing.IsCancellationRequested) return;
        if (baseline.Loaded && _chatBusy)
        {
            report("Reply in progress", "Wait for the reply to finish, then apply the new context.",
                InfoBarSeverity.Warning);
            return;
        }
        _contextOperationBusy = true;
        UpdateContextControls();
        report(baseline.Loaded ? "Applying context" : "Saving context", baseline.Loaded
            ? "Rebuilding the engine with " + ContextEditor.Format(tokens)
                + " tokens. The current engine keeps answering until the replacement is ready."
            : "Saving " + ContextEditor.Format(tokens) + " tokens for this model…",
            InfoBarSeverity.Informational);
        try
        {
            var applied = await _backend.SetContextAsync(baseline, tokens, ApiKeyBox.Password, _closing.Token);
            _contextBaseline = applied;
            report("Context saved", ContextEditor.SavedMessage(tokens, applied.Loaded), InfoBarSeverity.Success);
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
            if (!_closing.IsCancellationRequested)
            {
                await RefreshContextSummariesAsync(force: true);
                UpdateContextControls();
            }
        }
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
