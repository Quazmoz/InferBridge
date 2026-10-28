using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

/// <summary>
/// "Connect a tool" on the Local API page. Values come only from the verified
/// Connection Hub metadata and the selected model. The two one-click setups
/// (Continue config.yaml, Copilot CLI user variables) always preview first,
/// write nothing without an explicit confirmation, and offer Undo.
/// </summary>
public sealed partial class MainWindow
{
    private readonly List<Button> _toolActionButtons = [];
    private readonly List<Button> _toolTestButtons = [];
    private Button? _continueUndoButton;
    private Button? _copilotUndoButton;
    private ContinueUndo? _continueUndo;
    private EnvUndo? _copilotUndo;
    private bool _toolBusy;
    private readonly IUserEnvironment _userEnvironment = new WindowsUserEnvironment();

    // MSIX virtualizes HKCU writes for packaged desktop apps, so a Store-edition
    // write to HKCU\Environment would be invisible to the user's terminals.
    private static bool UserEnvironmentWritable => !NativeDataPaths.HasPackageIdentity();

    private ToolConnectionValues? CurrentToolValues()
    {
        if (_hubMetadata is null || SelectedHubModelId is not string id) return null;
        try { return ToolConnectionValues.From(_hubMetadata, id); }
        catch (ToolSetupException) { return null; }
    }

    private void RenderToolCards()
    {
        _toolActionButtons.Clear();
        _toolTestButtons.Clear();
        _continueUndoButton = null;
        _copilotUndoButton = null;
        ToolCards.Children.Clear();
        ToolConnectionValues? values = CurrentToolValues();
        if (values is null && !_toolBusy)
        {
            ToolsInfo.Severity = InfoBarSeverity.Informational;
            ToolsInfo.Message = _hubMetadata is null
                ? "Refresh the connection to get ready-to-use settings."
                : "Select a loaded chat model above. Tool cards are ready only when the engine is up and a chat model is selected.";
        }
        else if (values is not null && !_toolBusy && ToolsInfo.Severity == InfoBarSeverity.Informational)
        {
            ToolsInfo.Message = "Ready. The settings below use " + values.ModelId + " at " + values.BaseUrl + ".";
        }
        foreach (var client in ToolSnippets.Clients)
            ToolCards.Children.Add(BuildToolCard(client, values));
        UpdateToolButtons();
    }

    private void UpdateToolButtons()
    {
        bool ready = CurrentToolValues() is not null && !_toolBusy && !_hubBusy
            && !_quitting && !_closing.IsCancellationRequested;
        foreach (var button in _toolActionButtons) button.IsEnabled = ready;
        foreach (var button in _toolTestButtons) button.IsEnabled = ready && HubTestButton.IsEnabled;
        if (_continueUndoButton is not null)
            _continueUndoButton.IsEnabled = _continueUndo is not null && !_toolBusy;
        if (_copilotUndoButton is not null)
            _copilotUndoButton.IsEnabled = _copilotUndo is not null && !_toolBusy;
    }

    private UIElement BuildToolCard(ToolClient client, ToolConnectionValues? values)
    {
        bool oneClick = client.Kind == ToolSetupKind.OneClick
            && (client.Id != "copilot-cli" || UserEnvironmentWritable);
        var panel = new StackPanel { Spacing = 10 };

        var heading = Ui.Text(client.Title, "InferBridgeSectionHeaderStyle");
        heading.Margin = new Thickness(0);
        AutomationProperties.SetHeadingLevel(heading, AutomationHeadingLevel.Level3);
        panel.Children.Add(heading);

        var pills = new WrapRow { Spacing = 8 };
        pills.Children.Add(values is null
            ? Ui.Pill("Not ready: select a loaded chat model", "warning")
            : Ui.Pill("Ready", "success"));
        pills.Children.Add(Ui.Pill(oneClick ? "One-click setup" : "Guided setup", oneClick ? "accent" : "neutral"));
        panel.Children.Add(pills);
        panel.Children.Add(Ui.Text(client.Summary, secondary: true));

        if (client.Id == "copilot-cli" && !UserEnvironmentWritable)
            panel.Children.Add(Ui.Text(
                "Microsoft Store edition: Windows keeps this app\u2019s environment-variable changes private, so they would not reach your terminals. Copy the PowerShell commands below and run them yourself.",
                secondary: true));

        if (values is not null)
        {
            if (client.Id != "generic")
            {
                var (warning, note) = ToolSnippets.AgenticNote(values.ContextTokens);
                var noteRow = new WrapRow { Spacing = 8 };
                noteRow.Children.Add(Ui.Pill(warning ? "Check model" : "Context OK", warning ? "warning" : "success"));
                panel.Children.Add(noteRow);
                panel.Children.Add(Ui.Text(note, "CaptionTextBlockStyle"));
            }

            foreach (var (label, value) in ToolSnippets.Values(client.Id, values))
                panel.Children.Add(ToolValueRow(client, label, value, values));

            var steps = ToolSnippets.Steps(client.Id, values);
            if (oneClick is false && client.Kind == ToolSetupKind.OneClick && client.Id == "copilot-cli")
                steps = ["Copy the PowerShell commands below and run them in PowerShell.",
                         "Open a new terminal and run copilot.", .. steps.Skip(2)];
            var list = new StackPanel { Spacing = 4 };
            AutomationProperties.SetName(list, client.Title + " setup steps");
            for (int i = 0; i < steps.Count; i++)
                list.Children.Add(Ui.Text((i + 1) + ". " + steps[i]));
            panel.Children.Add(list);

            string snippet = ToolSnippets.Snippet(client.Id, values);
            if (snippet.Length > 0)
                AddSnippet(panel, client, client.Id switch
                {
                    "continue" => ("config.yaml entry", "Copy YAML"),
                    "copilot-cli" => ("PowerShell commands", "Copy commands"),
                    _ => ("chatLanguageModels.json entry (Custom Endpoint)", "Copy JSON"),
                }, snippet);
            if (client.Id == "vscode-copilot")
                AddSnippet(panel, client, ("Deprecated: settings.json customOAIModels (older VS Code only)",
                    "Copy deprecated settings"), ToolSnippets.VsCodeDeprecatedCustomOaiModels(values));
        }

        var actions = new WrapRow { Spacing = 8 };
        if (oneClick)
        {
            var preview = new Button { Content = Ui.IconLabel("\uE7B3", "Preview change\u2026") };
            if (Ui.ResStyle("AccentButtonStyle") is Style accent) preview.Style = accent;
            AutomationProperties.SetName(preview, "Preview " + client.Title + " change");
            preview.Click += client.Id == "continue" ? ContinuePreview_Click : CopilotPreview_Click;
            _toolActionButtons.Add(preview);
            actions.Children.Add(preview);
            var undo = new Button { Content = Ui.IconLabel("\uE7A7", "Undo") };
            AutomationProperties.SetName(undo, "Undo " + client.Title + " change");
            undo.Click += client.Id == "continue" ? ContinueUndo_Click : CopilotUndo_Click;
            if (client.Id == "continue") _continueUndoButton = undo; else _copilotUndoButton = undo;
            actions.Children.Add(undo);
        }
        var test = new Button { Content = Ui.IconLabel("\uE9D9", "Test connection") };
        AutomationProperties.SetName(test, "Test connection for " + client.Title);
        AutomationProperties.SetHelpText(test, "Runs the engine's synthetic connection self-test for the selected model.");
        test.Click += ToolTest_Click;
        _toolTestButtons.Add(test);
        actions.Children.Add(test);
        var docs = new HyperlinkButton { Content = client.DocLabel, NavigateUri = new Uri(client.DocUrl) };
        AutomationProperties.SetName(docs, "Open " + client.DocLabel);
        actions.Children.Add(docs);
        panel.Children.Add(actions);

        var card = Ui.Card(panel);
        AutomationProperties.SetName(card, client.Title + (values is null ? ", not ready" : ", ready"));
        return card;
    }

    private UIElement ToolValueRow(ToolClient client, string label, string value, ToolConnectionValues values)
    {
        var row = new Grid { ColumnSpacing = 8 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var text = new StackPanel { Spacing = 2 };
        text.Children.Add(Ui.Text(label, "CaptionTextBlockStyle", secondary: true));
        var shown = Ui.Text(value, "BodyStrongTextBlockStyle");
        shown.FontFamily = new FontFamily("Cascadia Mono, Consolas");
        shown.IsTextSelectionEnabled = true;
        AutomationProperties.SetName(shown, client.Title + " " + label + ": " + value);
        text.Children.Add(shown);
        row.Children.Add(text);
        var copy = new Button { Content = Ui.IconLabel("\uE8C8", "Copy"), VerticalAlignment = VerticalAlignment.Center };
        AutomationProperties.SetName(copy, "Copy " + label + " for " + client.Title);
        copy.Click += (_, _) =>
        {
            // A key the user typed this session may go to the clipboard; nothing is stored.
            bool sessionKey = label == "API key" && values.AuthenticationRequired
                && !string.IsNullOrWhiteSpace(ApiKeyBox.Password);
            CopyToolText(sessionKey ? ApiKeyBox.Password : value,
                sessionKey ? "Your session API key" : label,
                sessionKey ? " It was not saved anywhere." : "");
        };
        Grid.SetColumn(copy, 1);
        row.Children.Add(copy);
        return row;
    }

    private void AddSnippet(StackPanel panel, ToolClient client, (string Title, string Button) label, string snippet)
    {
        string title = label.Title;
        panel.Children.Add(Ui.Text(title, "BodyStrongTextBlockStyle"));
        var box = new TextBox
        {
            Text = snippet,
            IsReadOnly = true,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.NoWrap,
            MaxHeight = 220,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
        };
        ScrollViewer.SetHorizontalScrollBarVisibility(box, ScrollBarVisibility.Auto);
        AutomationProperties.SetName(box, client.Title + " " + title);
        panel.Children.Add(box);
        var copy = new Button { Content = Ui.IconLabel("\uE8C8", label.Button) };
        AutomationProperties.SetName(copy, "Copy " + title + " for " + client.Title);
        copy.Click += (_, _) => CopyToolText(snippet, title, "");
        panel.Children.Add(copy);
    }

    private void CopyToolText(string value, string description, string suffix)
    {
        if (string.IsNullOrEmpty(value)) return;
        try
        {
            var data = new DataPackage();
            data.SetText(value);
            Clipboard.SetContent(data);
            ToolsInfo.Severity = InfoBarSeverity.Success;
            ToolsInfo.Message = description + " copied." + suffix;
        }
        catch (Exception)
        {
            ToolsInfo.Severity = InfoBarSeverity.Warning;
            ToolsInfo.Message = "Clipboard unavailable. Select the text and copy it manually.";
        }
    }

    private void ShowToolError(Exception error, string target)
    {
        ToolsInfo.Severity = InfoBarSeverity.Error;
        ToolsInfo.Message = error switch
        {
            ToolSetupException => error.Message,
            UnauthorizedAccessException => "Windows denied access to " + target + ". Check its permissions or close apps that lock it, then try again. Nothing else was changed.",
            System.Security.SecurityException => "Windows denied access to " + target + ". Nothing was changed.",
            IOException => "Could not read or write " + target + " (" + error.Message + "). Close apps that use it and try again.",
            _ => "Tool setup failed unexpectedly. Nothing further was changed.",
        };
    }

    private async Task<bool> ConfirmToolChangeAsync(string title, string body, string primary)
    {
        var text = new TextBlock
        {
            Text = body,
            TextWrapping = TextWrapping.Wrap,
            IsTextSelectionEnabled = true,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
        };
        AutomationProperties.SetName(text, "Change preview");
        var dialog = new ContentDialog
        {
            Title = title,
            Content = new ScrollViewer { Content = text, MaxHeight = 420 },
            PrimaryButtonText = primary,
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary && !_closing.IsCancellationRequested;
    }

    private async Task RunToolOperationAsync(Func<Task> operation, string target)
    {
        if (_toolBusy || _quitting || _closing.IsCancellationRequested) return;
        _toolBusy = true;
        UpdateToolButtons();
        try { await operation(); }
        catch (Exception error) when (!_closing.IsCancellationRequested) { ShowToolError(error, target); }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        finally
        {
            _toolBusy = false;
            if (!_closing.IsCancellationRequested) UpdateToolButtons();
        }
    }

    private async void ContinuePreview_Click(object sender, RoutedEventArgs args)
    {
        if (CurrentToolValues() is not { } values) return;
        var file = new ContinueConfigFile(ContinueConfigFile.DefaultPath);
        await RunToolOperationAsync(async () =>
        {
            ContinuePlan plan = file.Plan(values);
            if (plan.Unchanged)
            {
                ToolsInfo.Severity = InfoBarSeverity.Success;
                ToolsInfo.Message = "Continue is already set up for " + values.ModelId + ". Nothing to change.";
                return;
            }
            string body = plan.Preview + "\n\n" + (plan.Existed
                ? "Your other models and settings are kept. A backup is saved next to config.yaml first."
                : "Continue's config.yaml does not exist yet, so InferBridge will create it.");
            if (!await ConfirmToolChangeAsync("Write Continue config.yaml?", body, "Write config.yaml")) return;
            _continueUndo = file.Apply(plan);
            ToolsInfo.Severity = InfoBarSeverity.Success;
            ToolsInfo.Message = "Continue updated. Choose \u201cInferBridge (local)\u201d in Continue's model picker."
                + (_continueUndo.BackupPath is null ? "" : " Backup: " + _continueUndo.BackupPath)
                + (values.AuthenticationRequired ? " Replace YOUR_INFERBRIDGE_API_KEY in config.yaml with your key." : "");
        }, ContinueConfigFile.DefaultPath);
    }

    private async void ContinueUndo_Click(object sender, RoutedEventArgs args)
    {
        if (_continueUndo is not { } undo) return;
        var file = new ContinueConfigFile(undo.Path);
        await RunToolOperationAsync(async () =>
        {
            if (!await ConfirmToolChangeAsync("Undo the Continue change?",
                undo.BackupPath is null
                    ? "Remove " + undo.Path + ", which InferBridge created?"
                    : "Restore " + undo.Path + " from " + undo.BackupPath + "?", "Undo")) return;
            file.Undo(undo);
            _continueUndo = null;
            ToolsInfo.Severity = InfoBarSeverity.Success;
            ToolsInfo.Message = "Continue config.yaml restored.";
        }, undo.Path);
    }

    private async void CopilotPreview_Click(object sender, RoutedEventArgs args)
    {
        if (CurrentToolValues() is not { } values || !UserEnvironmentWritable) return;
        await RunToolOperationAsync(async () =>
        {
            var changes = CopilotCliEnvironment.Preview(_userEnvironment, values);
            string body = "Set these Windows user environment variables:\n\n"
                + string.Join("\n", changes.Select(c => c.Name + "\n  before: " + (c.Before ?? "(not set)") + "\n  after:  " + c.After))
                + "\n\nNo API key is stored. Open a new terminal afterwards.";
            if (!await ConfirmToolChangeAsync("Set Copilot CLI variables?", body, "Set variables")) return;
            _copilotUndo = await Task.Run(() => CopilotCliEnvironment.Apply(_userEnvironment, values));
            ToolsInfo.Severity = InfoBarSeverity.Success;
            ToolsInfo.Message = "Copilot CLI variables set. Open a new terminal and run copilot."
                + (values.AuthenticationRequired ? " Set $env:COPILOT_PROVIDER_API_KEY in that terminal." : "");
        }, "your Windows user environment variables");
    }

    private async void CopilotUndo_Click(object sender, RoutedEventArgs args)
    {
        if (_copilotUndo is not { } undo) return;
        await RunToolOperationAsync(async () =>
        {
            if (!await ConfirmToolChangeAsync("Undo the Copilot CLI variables?",
                "Put back the previous values of:\n" + string.Join("\n", undo.Changes.Select(c => c.Name)), "Undo")) return;
            var skipped = await Task.Run(() => CopilotCliEnvironment.Undo(_userEnvironment, undo));
            _copilotUndo = null;
            ToolsInfo.Severity = skipped.Count == 0 ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            ToolsInfo.Message = skipped.Count == 0
                ? "Previous Copilot CLI variables restored. Open a new terminal."
                : "Restored, except " + string.Join(", ", skipped) + ", which changed after InferBridge set them and were left alone.";
        }, "your Windows user environment variables");
    }

    private async void ToolTest_Click(object sender, RoutedEventArgs args)
    {
        if (CurrentToolValues() is null) return;
        ToolsInfo.Severity = InfoBarSeverity.Informational;
        ToolsInfo.Message = "Running the engine's connection self-test\u2026";
        string? summary = await RunHubSelfTestAsync();
        if (_closing.IsCancellationRequested) return;
        ToolsInfo.Severity = summary is null ? InfoBarSeverity.Warning
            : summary.Contains(" 0 failed", StringComparison.Ordinal) ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
        ToolsInfo.Message = summary is null
            ? "The self-test did not complete. See Connection self-test above."
            : "Test connection: " + summary + " Details are under Connection self-test above.";
    }
}
