using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace InferBridge.Native;

/// <summary>
/// Theme-resource helpers for elements created in code. Every color comes from
/// a Fluent or InferBridge theme resource, so light, dark and high-contrast
/// themes stay correct; charts re-render on ActualThemeChanged.
/// </summary>
internal static class Ui
{
    // The indexer (unlike TryGetValue) also searches merged and theme dictionaries.
    private static object? Lookup(string key)
    {
        try { return Application.Current.Resources[key]; }
        catch (Exception) { return null; }
    }

    public static Brush ResBrush(string key) =>
        Lookup(key) as Brush ?? (Brush)Application.Current.Resources["TextFillColorPrimaryBrush"];

    public static Style? ResStyle(string key) => Lookup(key) as Style;

    /// <summary>
    /// Wrapping text for code-built cards. Style and Foreground are only assigned
    /// when asked for: assigning null clears the inherited theme brush, which is
    /// what left System Doctor check titles and details invisible.
    /// </summary>
    public static TextBlock Text(string text, string? style = null, bool secondary = false)
    {
        var block = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap };
        if (style is not null && ResStyle(style) is Style resolved) block.Style = resolved;
        if (secondary) block.Foreground = ResBrush("TextFillColorSecondaryBrush");
        return block;
    }

    /// <summary>A rounded status pill: colored dot plus readable primary text.</summary>
    public static Border Pill(string text, string kind = "neutral")
    {
        string tone = kind switch
        {
            "success" => "Success",
            "warning" => "Caution",
            "error" => "Critical",
            "accent" => "Attention",
            _ => "Neutral",
        };
        // A Grid (not a horizontal StackPanel) gives the label a finite width, so a
        // long model/device name wraps to two lines and then ellipsizes instead of
        // being clipped at the rail edge. The tooltip always carries the full text.
        var row = new Grid { ColumnSpacing = 6 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.Children.Add(new Ellipse
        {
            Width = 8, Height = 8, VerticalAlignment = VerticalAlignment.Center,
            Fill = ResBrush(kind == "accent" ? "InferBridgeBrandBrush" : "SystemFillColor" + tone + "Brush"),
        });
        var label = Text(text, "CaptionTextBlockStyle");
        label.VerticalAlignment = VerticalAlignment.Center;
        label.MaxLines = 2;
        label.TextTrimming = TextTrimming.CharacterEllipsis;
        Grid.SetColumn(label, 1);
        row.Children.Add(label);
        var pill = new Border
        {
            Child = row,
            Padding = new Thickness(10, 4, 10, 4),
            CornerRadius = new CornerRadius(12),
            BorderThickness = new Thickness(1),
            BorderBrush = ResBrush("CardStrokeColorDefaultBrush"),
            Background = ResBrush("SystemFillColor" + tone + "BackgroundBrush"),
        };
        AutomationProperties.SetName(pill, text);
        ToolTipService.SetToolTip(pill, text);
        return pill;
    }

    public static FontIcon Icon(string glyph, double size = 16) => new()
    {
        Glyph = glyph,
        FontSize = size,
        FontFamily = (FontFamily)Application.Current.Resources["SymbolThemeFontFamily"],
    };

    /// <summary>An icon + label button content, used for per-row commands.</summary>
    public static StackPanel IconLabel(string glyph, string label)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(Icon(glyph, 14));
        row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
        return row;
    }

    public static Border Card(UIElement child) => new()
    {
        Child = child,
        Padding = new Thickness(16),
        CornerRadius = new CornerRadius(8),
        BorderThickness = new Thickness(1),
        BorderBrush = ResBrush("CardStrokeColorDefaultBrush"),
        Background = ResBrush("CardBackgroundFillColorDefaultBrush"),
    };
}

/// <summary>Base for charts: keyboard-focusable, named for Narrator, theme-aware.</summary>
public abstract partial class ChartBase : UserControl
{
    protected ChartBase()
    {
        IsTabStop = true;
        UseSystemFocusVisuals = true;
        ActualThemeChanged += (_, _) => Render();
        Loaded += (_, _) => Render();
    }

    public string Title { get; set; } = "";

    public string Unit { get; set; } = "";

    protected void Announce(string name, string help)
    {
        AutomationProperties.SetName(this, name);
        AutomationProperties.SetHelpText(this, help);
        ToolTipService.SetToolTip(this, name);
    }

    protected abstract void Render();
}

/// <summary>
/// Horizontal bar chart: one labelled row per item, the bar scaled to the
/// largest real reading. Two rows per item (label/value, then bar) so it
/// never truncates at narrow widths or 150% text scaling.
/// </summary>
public sealed partial class BarChart : ChartBase
{
    private IReadOnlyList<ChartBar> _bars = Array.Empty<ChartBar>();

    public string Format { get; set; } = "0.#";

    public bool LowerIsBetter { get; set; }

    public string EmptyText { get; set; } = "No measurements yet.";

    internal void SetData(IEnumerable<(string Label, double? Value)> data)
    {
        _bars = ChartMath.Bars(data, Unit, Format);
        Render();
    }

    protected override void Render()
    {
        var root = new StackPanel { Spacing = 10 };
        if (Title.Length > 0)
            root.Children.Add(Ui.Text(Title, "BodyStrongTextBlockStyle"));
        if (_bars.Count == 0)
            root.Children.Add(Ui.Text(EmptyText, "CaptionTextBlockStyle", secondary: true));
        foreach (ChartBar bar in _bars)
        {
            var item = new StackPanel { Spacing = 4 };
            var header = new Grid { ColumnSpacing = 12 };
            header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            header.Children.Add(Ui.Text(bar.Label, "CaptionTextBlockStyle"));
            var value = Ui.Text(bar.ValueText, "CaptionTextBlockStyle", secondary: bar.Value is null);
            value.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
            Grid.SetColumn(value, 1);
            header.Children.Add(value);
            item.Children.Add(header);
            var track = new Grid { Height = 8, CornerRadius = new CornerRadius(4),
                Background = Ui.ResBrush("ControlStrongFillColorDisabledBrush") };
            track.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(bar.Fraction, GridUnitType.Star) });
            track.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1 - bar.Fraction, GridUnitType.Star) });
            if (bar.Fraction > 0)
                track.Children.Add(new Rectangle { Fill = Ui.ResBrush("InferBridgeBrandBrush"), RadiusX = 4, RadiusY = 4 });
            item.Children.Add(track);
            root.Children.Add(item);
        }
        Content = root;
        Announce(ChartMath.Describe(Title, _bars),
            LowerIsBetter ? "Bar chart. Shorter bars are better." : "Bar chart. Longer bars are better.");
    }
}

/// <summary>Trend line drawn with a Polyline over real readings only.</summary>
public sealed partial class Sparkline : ChartBase
{
    private IReadOnlyList<double?> _values = Array.Empty<double?>();
    private readonly Canvas _canvas = new() { Height = 56 };
    private readonly TextBlock _title = Ui.Text("", "BodyStrongTextBlockStyle");
    private readonly TextBlock _caption = Ui.Text("", "CaptionTextBlockStyle", secondary: true);

    public Sparkline()
    {
        var root = new StackPanel { Spacing = 6 };
        root.Children.Add(_title);
        root.Children.Add(_canvas);
        root.Children.Add(_caption);
        Content = root;
        _canvas.SizeChanged += (_, _) => Render();
    }

    internal void SetData(IEnumerable<double?> values)
    {
        _values = values.ToArray();
        Render();
    }

    protected override void Render()
    {
        _canvas.Children.Clear();
        var points = ChartMath.Sparkline(_values, _canvas.ActualWidth, _canvas.ActualHeight, 4);
        string description = ChartMath.DescribeTrend(Title, _values, Unit);
        _title.Text = Title;
        _title.Visibility = Title.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        _caption.Text = points.Count == 0 ? "Not enough measurements for a trend." : description;
        if (points.Count > 0)
        {
            var line = new Polyline
            {
                Stroke = Ui.ResBrush("InferBridgeBrandBrush"),
                StrokeThickness = 2,
                StrokeLineJoin = PenLineJoin.Round,
            };
            foreach (var (x, y) in points)
                line.Points.Add(new Windows.Foundation.Point(x, y));
            _canvas.Children.Add(line);
            var (lx, ly) = points[^1];
            var dot = new Ellipse { Width = 8, Height = 8, Fill = Ui.ResBrush("InferBridgeBrandBrush") };
            Canvas.SetLeft(dot, lx - 4);
            Canvas.SetTop(dot, ly - 4);
            _canvas.Children.Add(dot);
        }
        Announce(description, "Trend line, oldest on the left.");
    }
}

/// <summary>Determinate ring gauge (Fluent ProgressRing) with a centered reading.</summary>
public sealed partial class RingMeter : ChartBase
{
    private double? _fraction;
    private string _detail = ChartMath.NotReported;

    internal void SetValue(double? fraction, string detail)
    {
        _fraction = fraction is double f && double.IsFinite(f) ? Math.Clamp(f, 0, 1) : null;
        _detail = string.IsNullOrWhiteSpace(detail) ? ChartMath.NotReported : detail;
        Render();
    }

    protected override void Render()
    {
        var root = new StackPanel { Spacing = 8, HorizontalAlignment = HorizontalAlignment.Center };
        var ring = new Grid { Width = 96, Height = 96, HorizontalAlignment = HorizontalAlignment.Center };
        ring.Children.Add(new ProgressRing
        {
            IsIndeterminate = false, IsActive = true, Width = 96, Height = 96,
            Minimum = 0, Maximum = 100, Value = (_fraction ?? 0) * 100,
            Foreground = Ui.ResBrush("InferBridgeBrandBrush"),
            Background = Ui.ResBrush("ControlStrongFillColorDisabledBrush"),
        });
        var percent = Ui.Text(_fraction is double f ? Math.Round(f * 100) + "%" : "—", "SubtitleTextBlockStyle");
        percent.HorizontalAlignment = HorizontalAlignment.Center;
        percent.VerticalAlignment = VerticalAlignment.Center;
        ring.Children.Add(percent);
        root.Children.Add(ring);
        var title = Ui.Text(Title, "BodyStrongTextBlockStyle");
        title.HorizontalAlignment = HorizontalAlignment.Center;
        title.TextAlignment = TextAlignment.Center;
        root.Children.Add(title);
        var detail = Ui.Text(_detail, "CaptionTextBlockStyle", secondary: true);
        detail.HorizontalAlignment = HorizontalAlignment.Center;
        detail.TextAlignment = TextAlignment.Center;
        root.Children.Add(detail);
        Content = root;
        Announce(Title + ": " + (_fraction is double g ? Math.Round(g * 100) + " percent, " : "") + _detail,
            "Gauge.");
    }
}
