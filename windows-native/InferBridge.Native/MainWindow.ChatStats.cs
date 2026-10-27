using System.Globalization;

namespace InferBridge.Native;

// Live stats strip and context gauge for the last completed reply. Timing is
// measured by this client around the stream; token counts come only from the
// engine's usage event. Nothing is estimated from text length.
public sealed partial class MainWindow
{
    private int? ChatContextWindow(string? modelId)
    {
        if (modelId is null) return null;
        if (_contextBaseline is { } context && context.ModelId == modelId) return context.Current;
        return _modelPreparationAvailable && _modelPreparations.TryGetValue(modelId, out var row)
            ? row.Details?.ContextTokens : null;
    }

    private void ClearChatStats()
    {
        ChatStatsStrip.Children.Clear();
        ChatContextMeter.SetValue(null, "Send a message to measure context use.");
    }

    private void UpdateChatStats(string modelId, double firstTokenMs, double totalMs, ChatUsage? usage)
    {
        static string Number(double value) => value.ToString("N0", CultureInfo.InvariantCulture);
        ChatStatsStrip.Children.Clear();
        double? first = firstTokenMs >= 0 ? firstTokenMs : null;
        ChatStatsStrip.Children.Add(Ui.Pill("First token " + ChartMath.Format(first, " ms", "N0"), "accent"));
        double? rate = ChartMath.DecodeTokensPerSecond(usage?.CompletionTokens, totalMs, first);
        ChatStatsStrip.Children.Add(Ui.Pill(rate is double r
            ? "Decode " + r.ToString("0.0", CultureInfo.InvariantCulture) + " tok/s"
            : "Decode speed not reported", rate is null ? "neutral" : "accent"));
        ChatStatsStrip.Children.Add(Ui.Pill("Total " + ChartMath.Format(totalMs / 1000.0, " s", "0.0"), "neutral"));
        ChatStatsStrip.Children.Add(Ui.Pill(usage is null ? "Tokens not reported"
            : Number(usage.CompletionTokens) + " tokens out · " + Number(usage.PromptTokens) + " in", "neutral"));
        int? window = ChatContextWindow(modelId);
        double? used = usage is null ? null : usage.PromptTokens + usage.CompletionTokens;
        double? fraction = ChartMath.Fraction(used, window);
        ChatContextMeter.SetValue(fraction, used is double u && window is int w
            ? Number(u) + " of " + Number(w) + " tokens"
            : used is double only ? Number(only) + " tokens; context window not reported"
            : "Token usage not reported by the engine");
        if (fraction is double f)
            ChatStatsStrip.Children.Add(Ui.Pill("Context " + Math.Round(f * 100) + "%",
                f >= 0.9 ? "warning" : "neutral"));
    }
}
