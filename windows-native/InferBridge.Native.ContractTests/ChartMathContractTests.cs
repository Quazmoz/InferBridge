namespace InferBridge.Native;

// Native charts must scale only real readings, never draw missing data as zero,
// and expose every plotted number as text for Narrator.
internal static class ChartMathContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native chart math: " + message);
    }

    private static bool Near(double a, double b) => Math.Abs(a - b) < 1e-9;

    public static void Run()
    {
        var bars = ChartMath.Bars(new (string, double?)[]
        {
            ("NPU", 20), ("GPU", 40), ("CPU", 10),
        }, " tok/s");
        Check(bars.Count == 3, "bar count changed");
        Check(Near(bars[1].Fraction, 1) && Near(bars[0].Fraction, 0.5) && Near(bars[2].Fraction, 0.25),
            "bars are not scaled to the series maximum");
        Check(bars[0].ValueText == "20 tok/s", "value label lost its unit");
        Check(bars[0].Label == "NPU", "label changed");

        var invalid = ChartMath.Bars(new (string, double?)[]
        {
            ("missing", null), ("nan", double.NaN), ("inf", double.PositiveInfinity),
            ("negative", -3), ("real", 5),
        }, " ms");
        foreach (ChartBar bar in invalid.Take(4))
        {
            Check(bar.Value is null && bar.Fraction == 0, bar.Label + " was drawn as a measurement");
            Check(bar.ValueText == ChartMath.NotReported, bar.Label + " was not labelled Not reported");
        }
        Check(Near(invalid[4].Fraction, 1), "the only real reading should fill the bar");

        var zeros = ChartMath.Bars(new (string, double?)[] { ("a", 0), ("b", 0) }, "");
        Check(zeros.All(bar => bar.Fraction == 0 && bar.ValueText == "0"), "all-zero series must not divide by zero");
        Check(ChartMath.Bars(Array.Empty<(string, double?)>(), "").Count == 0, "empty series produced bars");
        Check(ChartMath.Bars(new (string, double?)[] { (" ", 1) }, "")[0].Label == "Unnamed", "blank label not replaced");

        Check(ChartMath.Describe("Decode speed", bars) == "Decode speed: NPU 20 tok/s; GPU 40 tok/s; CPU 10 tok/s",
            "accessible description lost a number");
        Check(ChartMath.Describe("Empty", Array.Empty<ChartBar>()) == "Empty: no data", "empty description");

        var line = ChartMath.Sparkline(new double?[] { 10, null, 30, double.NaN, 20 }, 100, 50, 0);
        Check(line.Count == 3, "sparkline drew invalid readings");
        Check(Near(line[0].X, 0) && Near(line[0].Y, 50), "oldest minimum must be bottom-left");
        Check(Near(line[1].X, 50) && Near(line[1].Y, 0), "maximum must be at the top, keeping its index");
        Check(Near(line[2].X, 100) && Near(line[2].Y, 25), "latest reading misplaced");
        var flat = ChartMath.Sparkline(new double?[] { 7, 7 }, 80, 40, 4);
        Check(flat.All(point => Near(point.Y, 20)), "a flat series must sit on the midline");
        var single = ChartMath.Sparkline(new double?[] { 3 }, 80, 40, 4);
        Check(single.Count == 1 && Near(single[0].X, 40), "a single reading must be centered");
        Check(ChartMath.Sparkline(new double?[] { null, double.NaN }, 80, 40).Count == 0, "no-data sparkline drew points");
        Check(ChartMath.Sparkline(new double?[] { 1, 2 }, 0, 40).Count == 0, "zero-size canvas drew points");
        Check(ChartMath.Sparkline(new double?[] { 1, 2 }, double.NaN, 40).Count == 0, "NaN canvas drew points");
        Check(ChartMath.DescribeTrend("TTFT", new double?[] { 30, null, 10, 20 }, " ms")
            == "TTFT: 3 readings, oldest 30 ms, latest 20 ms, lowest 10 ms, highest 30 ms", "trend description");
        Check(ChartMath.DescribeTrend("TTFT", new double?[] { null }, " ms") == "TTFT: no data", "empty trend");

        Check(ChartMath.Fraction(2, 8) is double quarter && Near(quarter, 0.25), "gauge fraction");
        Check(ChartMath.Fraction(12, 8) is double full && Near(full, 1), "gauge must clamp at full");
        Check(ChartMath.Fraction(null, 8) is null && ChartMath.Fraction(1, 0) is null
            && ChartMath.Fraction(double.NaN, 8) is null && ChartMath.Fraction(1, -1) is null,
            "unknown gauge readings must stay unknown");

        Check(ChartMath.DecodeTokensPerSecond(33, 2100, 500) is double rate && Near(rate, 20),
            "decode rate must match the engine benchmark formula");
        Check(ChartMath.DecodeTokensPerSecond(1, 2000, 500) is null, "one token has no decode rate");
        Check(ChartMath.DecodeTokensPerSecond(null, 2000, 500) is null, "missing usage invented a rate");
        Check(ChartMath.DecodeTokensPerSecond(10, 500, 500) is null, "zero decode time invented a rate");
        Check(ChartMath.DecodeTokensPerSecond(10, double.NaN, 1) is null, "NaN latency invented a rate");

        Check(ChartMath.Columns(500) == 1 && ChartMath.Columns(639.9) == 1, "narrow breakpoint");
        Check(ChartMath.Columns(640) == 2 && ChartMath.Columns(1007) == 2, "medium breakpoint");
        Check(ChartMath.Columns(1008) == 3 && ChartMath.Columns(1400, 2) == 2, "wide breakpoint / maximum");
        Check(ChartMath.Columns(double.PositiveInfinity) == 1 && ChartMath.Columns(2000, 0) == 1,
            "unbounded width or invalid maximum");

        var counts = ChartMath.LevelCounts(new[] { "pass", "PASS", "warning", "error", "info", "other" });
        Check(counts.Select(item => item.Label).SequenceEqual(new[] { "Passed", "Warnings", "Errors", "Info" }),
            "doctor summary order");
        Check(counts.Select(item => item.Value).SequenceEqual(new double?[] { 2, 1, 1, 1 }), "doctor summary counts");
        Check(ChartMath.Format(1234.56, " MB") == "1234.6 MB" && ChartMath.Format(null, " MB") == "Not reported",
            "invariant number formatting");
        Console.WriteLine("Native chart math contract: all cases passed.");
    }
}
