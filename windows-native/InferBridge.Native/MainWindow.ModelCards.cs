using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Model library rows as cards: status/device/format/vision pills and per-row
// commands. Row commands select the row, then call the same handlers as the
// page buttons, so every guard and confirmation dialog is shared. Row state is
// refreshed by setting properties, never by replacing list items.
public sealed partial class MainWindow
{
    private readonly List<(Button Button, ModelInfo Model, string Action)> _modelRowButtons = new();
    private readonly Dictionary<string, (StackPanel Pills, string Signature)> _modelRowDetails =
        new(StringComparer.Ordinal);

    private static string ModelRowLabel(ModelInfo model, bool recommended) =>
        (recommended ? "Recommended  ·  " : string.Empty)
        + model.Name + "  ·  " + model.StatusLabel
        + (model.Name.Contains(model.Id, StringComparison.Ordinal) ? string.Empty : "  (" + model.Id + ")");

    private static string StatusKind(ModelInfo model) => model.Status switch
    {
        "loaded" => "success",
        "ready_to_load" => "accent",
        "error" or "cancelled" => "error",
        "queued" or "loading" or "queued_convert" or "converting" => "warning",
        _ => "neutral",
    };

    private UIElement BuildModelCard(ModelInfo model, bool recommended)
    {
        var card = new StackPanel { Spacing = 8, Padding = new Thickness(4, 10, 4, 10) };
        var title = new Grid { ColumnSpacing = 12 };
        title.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        title.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var glyph = new Border
        {
            Width = 40, Height = 40, CornerRadius = new CornerRadius(8),
            Background = Ui.ResBrush("SubtleFillColorSecondaryBrush"),
            Child = Ui.Icon(model.CanUnload ? "\uE768" : "\uE8F1", 18),
            VerticalAlignment = VerticalAlignment.Top,
        };
        ((FontIcon)glyph.Child).Foreground = Ui.ResBrush(model.CanUnload
            ? "InferBridgeBrandBrush" : "TextFillColorSecondaryBrush");
        ((FontIcon)glyph.Child).HorizontalAlignment = HorizontalAlignment.Center;
        title.Children.Add(glyph);
        var names = new StackPanel { Spacing = 2 };
        names.Children.Add(Ui.Text(model.Name, "BodyStrongTextBlockStyle"));
        if (!model.Name.Contains(model.Id, StringComparison.Ordinal))
            names.Children.Add(Ui.Text(model.Id, "CaptionTextBlockStyle", secondary: true));
        var pills = new WrapRow { Spacing = 6, Margin = new Thickness(0, 4, 0, 0) };
        pills.Children.Add(Ui.Pill(model.StatusLabel, StatusKind(model)));
        if (recommended) pills.Children.Add(Ui.Pill("Recommended for this PC", "accent"));
        if (model.CanUnload)
            pills.Children.Add(Ui.Pill("Running on " + (model.Device is { Length: > 0 } device
                ? device : "device not reported"), "neutral"));
        var details = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        pills.Children.Add(details);
        names.Children.Add(pills);
        Grid.SetColumn(names, 1);
        title.Children.Add(names);
        card.Children.Add(title);
        _modelRowDetails[model.Id] = (details, "");

        var actions = new WrapRow { Spacing = 8, Margin = new Thickness(52, 0, 0, 0) };
        void Add(string action, string glyph, string label, bool show, bool accent = false)
        {
            if (!show) return;
            var button = new Button
            {
                Content = Ui.IconLabel(glyph, label), Tag = (model.Id, action), MinHeight = 32,
                Style = accent ? Ui.ResStyle("AccentButtonStyle") : null,
            };
            AutomationProperties.SetName(button, label + " " + model.Name);
            button.Click += ModelRowAction_Click;
            actions.Children.Add(button);
            _modelRowButtons.Add((button, model, action));
        }
        Add("load", "\uE768", "Load", model.CanLoad, accent: true);
        Add("download", "\uE896", "Download and load", model.CanDownload, accent: true);
        Add("unload", "\uE71A", "Unload", model.CanUnload);
        Add("revalidate", "\uE9D9", "Revalidate", true);
        Add("delete", "\uE74D", "Delete files", model.CanDelete);
        card.Children.Add(actions);
        return card;
    }

    // Called from UpdateModelButtons: cheap property updates only.
    private void UpdateModelRowStates()
    {
        bool idle = !_isManaging && !_chatBusy && !RefreshLocksControls && !_quitting;
        foreach (var (button, model, action) in _modelRowButtons)
        {
            if (action == "revalidate")
            {
                // Offered only when the engine's health center recommends it and allows it now.
                RuntimeHealthModel? health = _modelHealth?.Models.FirstOrDefault(row => row.ModelId == model.Id);
                bool offered = health is { Action: "revalidate", ActionAvailable: true };
                button.Visibility = offered ? Visibility.Visible : Visibility.Collapsed;
                button.IsEnabled = offered && MaintenanceReady && !_modelHealthBusy && idle;
                continue;
            }
            button.IsEnabled = idle && model.Permits(action switch
            {
                "load" => ModelAction.Load,
                "unload" => ModelAction.Unload,
                "download" => ModelAction.Download,
                _ => ModelAction.Load,
            }) && (action != "delete" || model.CanDelete);
        }
        foreach (var (modelId, entry) in _modelRowDetails.ToArray())
        {
            if (!_modelPreparationAvailable || !_modelPreparations.TryGetValue(modelId, out var row)
                || row.Details is not ModelLibraryDetails info) continue;
            var pills = new List<(string, string)>();
            if (info.WeightFormat is { Length: > 0 } format && format != "Not reported")
                pills.Add((format.ToUpperInvariant(), "neutral"));
            if (info.Backend is { Length: > 0 } backend && backend != "Not reported")
                pills.Add((backend, "neutral"));
            if (info.ContextTokens is int context)
                pills.Add((context.ToString("N0", System.Globalization.CultureInfo.InvariantCulture)
                    + " ctx", "neutral"));
            if (info.SupportsVision is true) pills.Add(("Vision", "accent"));
            if (info.ConfiguredDevice is { Length: > 0 } preferred && preferred != "Not reported")
                pills.Add(("Prefers " + preferred, "neutral"));
            string signature = string.Join("|", pills.Select(pill => pill.Item1));
            if (signature == entry.Signature) continue;
            entry.Pills.Children.Clear();
            foreach (var (text, kind) in pills) entry.Pills.Children.Add(Ui.Pill(text, kind));
            _modelRowDetails[modelId] = (entry.Pills, signature);
        }
    }

    private void ModelRowAction_Click(object sender, RoutedEventArgs args)
    {
        if (sender is not Button { Tag: ValueTuple<string, string> target }) return;
        (string modelId, string action) = target;
        ListViewItem? item = ModelsList.Items.OfType<ListViewItem>()
            .FirstOrDefault(candidate => (candidate.Tag as ModelInfo)?.Id == modelId);
        if (item is null) return;
        ModelsList.SelectedItem = item;
        switch (action)
        {
            case "load": LoadButton_Click(sender, args); break;
            case "unload": UnloadButton_Click(sender, args); break;
            case "download": DownloadButton_Click(sender, args); break;
            case "delete": DeleteButton_Click(sender, args); break;
            case "revalidate": ModelHealthAction_Click(sender, args); break;
        }
    }

    // Local, non-destructive filter: hides rows without replacing list items.
    private void ModelsFilterBox_TextChanged(object sender, TextChangedEventArgs args)
    {
        string query = ModelsFilterBox.Text.Trim();
        foreach (ListViewItem item in ModelsList.Items.OfType<ListViewItem>())
        {
            bool match = query.Length == 0 || item.Tag is ModelInfo model
                && (model.Name.Contains(query, StringComparison.OrdinalIgnoreCase)
                    || model.Id.Contains(query, StringComparison.OrdinalIgnoreCase)
                    || model.StatusLabel.Contains(query, StringComparison.OrdinalIgnoreCase));
            item.Visibility = match ? Visibility.Visible : Visibility.Collapsed;
        }
    }
}
