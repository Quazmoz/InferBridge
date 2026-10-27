using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace InferBridge.Native;

/// <summary>
/// Responsive card grid: 1 column below 640 px of available width, 2 below
/// 1008 px, otherwise up to <see cref="MaxColumns"/>. It measures the space the
/// page actually has (the NavigationView pane can take 320 px), which window
/// AdaptiveTriggers cannot see. Collapsed children take no cell.
/// </summary>
public sealed partial class CardGrid : Panel
{
    public static readonly DependencyProperty SpacingProperty = DependencyProperty.Register(
        nameof(Spacing), typeof(double), typeof(CardGrid), new PropertyMetadata(16.0, Changed));

    public static readonly DependencyProperty MaxColumnsProperty = DependencyProperty.Register(
        nameof(MaxColumns), typeof(int), typeof(CardGrid), new PropertyMetadata(3, Changed));

    public double Spacing
    {
        get => (double)GetValue(SpacingProperty);
        set => SetValue(SpacingProperty, value);
    }

    public int MaxColumns
    {
        get => (int)GetValue(MaxColumnsProperty);
        set => SetValue(MaxColumnsProperty, value);
    }

    private static void Changed(DependencyObject owner, DependencyPropertyChangedEventArgs args) =>
        ((CardGrid)owner).InvalidateMeasure();

    private List<UIElement> Visible() =>
        Children.Where(child => child.Visibility == Visibility.Visible).ToList();

    private (int Columns, double Width) Cells(double available)
    {
        int columns = ChartMath.Columns(available, MaxColumns);
        double width = double.IsFinite(available)
            ? Math.Max(0, (available - (columns - 1) * Spacing) / columns) : double.PositiveInfinity;
        return (columns, width);
    }

    protected override Size MeasureOverride(Size availableSize)
    {
        var items = Visible();
        var (columns, cell) = Cells(availableSize.Width);
        double height = 0, widest = 0;
        for (int start = 0; start < items.Count; start += columns)
        {
            double row = 0;
            for (int i = start; i < Math.Min(start + columns, items.Count); i++)
            {
                items[i].Measure(new Size(cell, double.PositiveInfinity));
                row = Math.Max(row, items[i].DesiredSize.Height);
                widest = Math.Max(widest, items[i].DesiredSize.Width);
            }
            height += row + (start > 0 ? Spacing : 0);
        }
        foreach (UIElement hidden in Children.Where(child => child.Visibility != Visibility.Visible))
            hidden.Measure(new Size(0, 0));
        double width = double.IsFinite(availableSize.Width) ? availableSize.Width : widest;
        return new Size(width, height);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        var items = Visible();
        var (columns, cell) = Cells(finalSize.Width);
        double y = 0;
        for (int start = 0; start < items.Count; start += columns)
        {
            int end = Math.Min(start + columns, items.Count);
            double row = 0;
            for (int i = start; i < end; i++) row = Math.Max(row, items[i].DesiredSize.Height);
            for (int i = start; i < end; i++)
                items[i].Arrange(new Rect((i - start) * (cell + Spacing), y, cell, row));
            y += row + Spacing;
        }
        return finalSize;
    }
}

/// <summary>Flow layout for command buttons and status pills; wraps instead of clipping.</summary>
public sealed partial class WrapRow : Panel
{
    public static readonly DependencyProperty SpacingProperty = DependencyProperty.Register(
        nameof(Spacing), typeof(double), typeof(WrapRow),
        new PropertyMetadata(8.0, (owner, _) => ((WrapRow)owner).InvalidateMeasure()));

    public double Spacing
    {
        get => (double)GetValue(SpacingProperty);
        set => SetValue(SpacingProperty, value);
    }

    private Size Flow(double width, bool arrange)
    {
        double x = 0, y = 0, line = 0, widest = 0;
        foreach (UIElement child in Children)
        {
            if (child.Visibility != Visibility.Visible)
            {
                if (!arrange) child.Measure(new Size(0, 0));
                continue;
            }
            if (!arrange) child.Measure(new Size(width, double.PositiveInfinity));
            Size size = child.DesiredSize;
            if (x > 0 && x + size.Width > width)
            {
                x = 0;
                y += line + Spacing;
                line = 0;
            }
            if (arrange) child.Arrange(new Rect(x, y, Math.Min(size.Width, width), size.Height));
            x += size.Width + Spacing;
            widest = Math.Max(widest, x - Spacing);
            line = Math.Max(line, size.Height);
        }
        return new Size(double.IsFinite(width) ? Math.Min(widest, width) : widest, y + line);
    }

    protected override Size MeasureOverride(Size availableSize) => Flow(availableSize.Width, false);

    protected override Size ArrangeOverride(Size finalSize)
    {
        Flow(finalSize.Width, true);
        return finalSize;
    }
}
