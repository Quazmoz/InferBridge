using System.Globalization;

namespace InferBridge.Native;

/// <summary>One bar of a horizontal bar chart, already scaled to the series maximum.</summary>
internal sealed record ChartBar(string Label, double? Value, double Fraction, string ValueText);

/// <summary>
/// Pure layout math for the in-house WinUI charts. No WinUI types, so the
/// contract tests can prove scaling, empty/NaN handling and accessible text.
/// Missing, non-finite or negative readings are never drawn as zero: they keep
/// a null value, a zero-length bar and the text "Not reported".
/// </summary>
internal static class ChartMath
{
    public const string NotReported = "Not reported";

    public static double? Clean(double? value) =>
        value is double v && double.IsFinite(v) && v >= 0 ? v : null;

    public static string Format(double? value, string unit, string format = "0.#")
    {
        double? clean = Clean(value);
        return clean is double v
            ? v.ToString(format, CultureInfo.InvariantCulture) + unit
            : NotReported;
    }

    public static IReadOnlyList<ChartBar> Bars(
        IEnumerable<(string Label, double? Value)> data, string unit, string format = "0.#")
    {
        var rows = data.Select(item => (Label: string.IsNullOrWhiteSpace(item.Label) ? "Unnamed" : item.Label,
            Value: Clean(item.Value))).ToList();
        double max = rows.Select(row => row.Value ?? 0).DefaultIfEmpty(0).Max();
        return rows.Select(row => new ChartBar(row.Label, row.Value,
            row.Value is double v && max > 0 ? Math.Clamp(v / max, 0, 1) : 0,
            Format(row.Value, unit, format))).ToArray();
    }

    /// <summary>Polyline points inside a width × height box; invalid readings are skipped.</summary>
    public static IReadOnlyList<(double X, double Y)> Sparkline(
        IEnumerable<double?> values, double width, double height, double padding = 2)
    {
        var all = values.Select(Clean).ToList();
        if (!double.IsFinite(width) || !double.IsFinite(height) || width <= 0 || height <= 0)
            return Array.Empty<(double, double)>();
        var valid = all.Select((value, index) => (value, index))
            .Where(item => item.value is not null).ToList();
        if (valid.Count == 0) return Array.Empty<(double, double)>();
        double min = valid.Min(item => item.value!.Value);
        double max = valid.Max(item => item.value!.Value);
        double innerW = Math.Max(0, width - 2 * padding);
        double innerH = Math.Max(0, height - 2 * padding);
        double span = all.Count > 1 ? all.Count - 1 : 1;
        return valid.Select(item =>
        {
            double x = all.Count == 1 ? width / 2 : padding + innerW * item.index / span;
            double y = max > min
                ? padding + innerH * (1 - (item.value!.Value - min) / (max - min))
                : height / 2; // A flat series sits on the midline.
            return (x, y);
        }).ToArray();
    }

    /// <summary>Used/total as 0–1, or null when either reading is unavailable.</summary>
    public static double? Fraction(double? used, double? total)
    {
        if (Clean(used) is not double u || Clean(total) is not double t || t <= 0) return null;
        return Math.Clamp(u / t, 0, 1);
    }

    /// <summary>Decode throughput, matching the engine benchmark: (tokens − 1) / (total − TTFT).</summary>
    public static double? DecodeTokensPerSecond(int? completionTokens, double? totalMs, double? firstTokenMs)
    {
        if (completionTokens is not int tokens || tokens < 2
            || Clean(totalMs) is not double total || Clean(firstTokenMs) is not double first
            || total <= first) return null;
        return (tokens - 1) / ((total - first) / 1000.0);
    }

    /// <summary>Card columns for the measured content width (Fluent breakpoints 640 / 1008).</summary>
    public static int Columns(double width, int maximum = 3)
    {
        int columns = !double.IsFinite(width) || width < 640 ? 1 : width < 1008 ? 2 : 3;
        return Math.Clamp(columns, 1, Math.Max(1, maximum));
    }

    /// <summary>Narrator text with every number the chart shows.</summary>
    public static string Describe(string title, IReadOnlyList<ChartBar> bars) =>
        bars.Count == 0
            ? title + ": no data"
            : title + ": " + string.Join("; ", bars.Select(bar => bar.Label + " " + bar.ValueText));

    public static string DescribeTrend(string title, IReadOnlyList<double?> values, string unit)
    {
        var valid = values.Select(Clean).Where(value => value is not null).Select(value => value!.Value).ToList();
        if (valid.Count == 0) return title + ": no data";
        return title + ": " + valid.Count + " readings, oldest " + Format(valid[0], unit)
            + ", latest " + Format(valid[^1], unit) + ", lowest " + Format(valid.Min(), unit)
            + ", highest " + Format(valid.Max(), unit);
    }

    /// <summary>Counts System Doctor check levels in a fixed order for the summary chart.</summary>
    public static IReadOnlyList<(string Label, double? Value)> LevelCounts(IEnumerable<string> levels)
    {
        var list = levels.Select(level => (level ?? "").ToLowerInvariant()).ToList();
        return new (string, double?)[]
        {
            ("Passed", list.Count(level => level == "pass")),
            ("Warnings", list.Count(level => level == "warning")),
            ("Errors", list.Count(level => level == "error")),
            ("Info", list.Count(level => level == "info")),
        };
    }
}
