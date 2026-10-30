using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

// Projection of the existing engine-owned history. No prompt, machine fingerprint,
// private paths or error detail is ever rendered in the native results panel.
internal sealed record BenchmarkMeasurement(
    string ModelId, string SourceModel, string Precision, string RequestedDevice,
    string ActualDevice, bool Success, bool Synthetic, double? DecodeTokensPerSecond,
    double? LegacyTokensPerSecond, double? FirstTokenMilliseconds,
    double? TotalMilliseconds, double? LoadMilliseconds, double? ProcessRamMegabytes,
    int? PromptTokens, int? CompletionTokens,
    int? ContextTokens = null, int? ReservedOutputTokens = null);

internal sealed record BenchmarkRun(
    string Id, string Timestamp, string Preset, int MethodologyVersion,
    int OutputTokens, int MeasuredRuns, int Warmups, string? HardwareFingerprint,
    bool Synthetic, IReadOnlyList<BenchmarkMeasurement> Results, string? ClientRunId = null)
{
    public bool IsComparableTo(BenchmarkRun other) =>
        !ReferenceEquals(this, other)
        && Id != other.Id
        && !string.IsNullOrWhiteSpace(HardwareFingerprint)
        && HardwareFingerprint == other.HardwareFingerprint
        && Synthetic == other.Synthetic
        && MethodologyVersion == other.MethodologyVersion
        && OutputTokens == other.OutputTokens
        && MeasuredRuns == other.MeasuredRuns
        && Warmups == other.Warmups;

    public BenchmarkMeasurement? ComparableTo(BenchmarkMeasurement measurement) =>
        Results.FirstOrDefault(row => row.Success && row.Synthetic == measurement.Synthetic
            && measurement.ActualDevice.Length > 0 && row.ActualDevice.Length > 0
            && row.ModelId == measurement.ModelId
            && row.SourceModel == measurement.SourceModel
            && row.Precision == measurement.Precision
            && row.ContextTokens is not null && row.ContextTokens == measurement.ContextTokens
            && row.ReservedOutputTokens is not null
            && row.ReservedOutputTokens == measurement.ReservedOutputTokens
            && row.RequestedDevice == measurement.RequestedDevice
            && row.ActualDevice == measurement.ActualDevice);

    public string Label => Timestamp + " · " + Preset + " · "
        + Results.Count + " combinations" + (Synthetic ? " · synthetic" : "");
}

internal static class BenchmarkHistory
{
    private static string Field(JsonElement element, string name, int maximum = 160)
    {
        if (element.ValueKind != JsonValueKind.Object
            || !element.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.String) return "";
        string result = value.GetString() ?? "";
        return result.Length <= maximum ? result : result[..maximum];
    }

    private static int? Number(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object
            || !element.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.Number
            || !value.TryGetInt32(out int result)) return null;
        return result;
    }

    private static double? Metric(JsonElement element, string name)
    {
        if (element.ValueKind != JsonValueKind.Object
            || !element.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.Number
            || !value.TryGetDouble(out double result)
            || !double.IsFinite(result) || result < 0) return null;
        return result;
    }

    private static bool Boolean(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.True;

    private static string? Fingerprint(JsonElement item)
    {
        // Fingerprints are comparison-only opaque identities, never UI labels.
        string value = Field(item, "hardware_fingerprint", 256);
        return value.Length > 0 ? value : null;
    }

    public static IReadOnlyList<BenchmarkRun> Parse(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("data", out var data)
            || data.ValueKind != JsonValueKind.Array)
            throw new JsonException("Unexpected benchmark history response.");
        var results = new List<BenchmarkRun>();
        var seenIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in data.EnumerateArray())
        {
            if (results.Count >= 100) break; // server keeps at most 100 runs.
            if (item.ValueKind != JsonValueKind.Object || Boolean(item, "automatic")) continue;
            string id = Field(item, "run_id", 128);
            if (id.Length == 0 || !seenIds.Add(id)
                || !item.TryGetProperty("results", out var rows)
                || rows.ValueKind != JsonValueKind.Array) continue;
            int tokens = Number(item, "max_tokens") ?? 0;
            int runs = Number(item, "runs_per_combo") ?? 0;
            int warmups = Number(item, "warmup_runs") ?? 0;
            if (tokens is < 1 or > 4096 || runs is < 1 or > 10 || warmups is < 0 or > 10)
                continue;
            var measurements = new List<BenchmarkMeasurement>();
            foreach (var row in rows.EnumerateArray())
            {
                if (measurements.Count >= 64) break; // bounded native presentation
                if (row.ValueKind != JsonValueKind.Object
                    || !row.TryGetProperty("success", out var success)
                    || success.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    continue;
                string model = Field(row, "model_id");
                string requested = Field(row, "requested_device", 128);
                if (model.Length == 0 || requested.Length == 0) continue;
                int? promptTokens = Number(row, "prompt_tokens");
                int? completionTokens = Number(row, "completion_tokens");
                measurements.Add(new BenchmarkMeasurement(
                    model, Field(row, "source_model"), Field(row, "weight_format"),
                    requested, Field(row, "actual_device", 128),
                    success.ValueKind == JsonValueKind.True,
                    Boolean(row, "synthetic") || Boolean(item, "synthetic") || Boolean(item, "mock"),
                    Metric(row, "decode_tokens_sec"), Metric(row, "tokens_sec"),
                    Metric(row, "time_to_first_token_ms"), Metric(row, "total_latency_ms"),
                    Metric(row, "load_time_ms"), Metric(row, "peak_process_ram_mb"),
                    promptTokens >= 0 ? promptTokens : null,
                    completionTokens >= 0 ? completionTokens : null,
                    Number(row, "configured_context_tokens") is int context && context is >= 128 and <= 262144
                        ? context : null,
                    Number(row, "reserved_output_tokens") is int reserve && reserve is >= 0 and <= 65536
                        ? reserve : null));
            }
            string clientRunId = Field(item, "client_run_id", 64);
            if (!Regex.IsMatch(clientRunId, @"^[0-9a-f]{32}$")) clientRunId = "";
            results.Add(new BenchmarkRun(id, Field(item, "created_at", 64),
                Field(item, "preset", 40), Number(item, "methodology_version") ?? 1,
                tokens, runs, warmups, Fingerprint(item),
                Boolean(item, "synthetic") || Boolean(item, "mock")
                    || measurements.Any(row => row.Synthetic), measurements,
                clientRunId.Length == 32 ? clientRunId : null));
        }
        return results;
    }

    // --- Chart series: only engine-measured values; failures and gaps stay null ---

    public static string ResultLabel(BenchmarkMeasurement row) =>
        row.ModelId + (row.Precision.Length > 0 ? " " + row.Precision : "") + " · "
        + (row.ActualDevice.Length > 0 ? row.ActualDevice : row.RequestedDevice + " (actual not reported)");

    /// <summary>Decode tok/s per result; the older inclusive metric is used only when labelled.</summary>
    public static IReadOnlyList<(string Label, double? Value)> SpeedSeries(BenchmarkRun run) =>
        run.Results.Select(row => (
            ResultLabel(row) + (row.Success && row.DecodeTokensPerSecond is null
                && row.LegacyTokensPerSecond is not null ? " (legacy overall)" : ""),
            row.Success ? row.DecodeTokensPerSecond ?? row.LegacyTokensPerSecond : null)).ToArray();

    public static IReadOnlyList<(string Label, double? Value)> FirstTokenSeries(BenchmarkRun run) =>
        run.Results.Select(row => (ResultLabel(row), row.Success ? row.FirstTokenMilliseconds : null)).ToArray();

    /// <summary>
    /// Best decode tok/s per saved run, oldest first, for runs of the same
    /// real/synthetic mode with a parseable timestamp. Runs without a decode
    /// measurement stay null rather than borrowing the legacy metric.
    /// </summary>
    public static IReadOnlyList<double?> DecodeTrend(IEnumerable<BenchmarkRun> history, bool synthetic) =>
        history.Where(run => run.Synthetic == synthetic)
            .Select(run => (Run: run, Ok: DateTimeOffset.TryParse(run.Timestamp, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal, out DateTimeOffset at), At: at))
            .Where(item => item.Ok)
            .OrderBy(item => item.At)
            .Select(item => item.Run.Results
                .Where(row => row.Success && row.DecodeTokensPerSecond is double value
                    && double.IsFinite(value) && value > 0)
                .Select(row => row.DecodeTokensPerSecond)
                .DefaultIfEmpty(null).Max())
            .ToArray();

    public static string FormatMetric(double? value, string suffix)
        => value is { } measured
            ? measured.ToString("0.##", CultureInfo.InvariantCulture) + suffix
            : "Unavailable";

    public static string Delta(double? current, double? previous, bool lowerIsBetter)
    {
        if (current is not { } now || previous is not { } prior
            || !double.IsFinite(now) || !double.IsFinite(prior) || prior <= 0)
            return "No comparable measurement";
        double change = (now - prior) / prior * 100;
        if (!double.IsFinite(change)) return "No comparable measurement";
        if (Math.Abs(change) < 0.5) return "Essentially unchanged";
        bool better = lowerIsBetter ? change < 0 : change > 0;
        return (change > 0 ? "+" : "") + change.ToString("0", CultureInfo.InvariantCulture)
            + "% (" + (better ? "better" : "worse") + ")";
    }
}

internal sealed partial class BackendClient
{
    public async Task<IReadOnlyList<BenchmarkRun>> GetBenchmarkHistoryAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(
            origin + "/v1/benchmarks", apiKey, cancellationToken,
            TimeSpan.FromSeconds(20), maximumBytes: 4 * 1024 * 1024);
        return BenchmarkHistory.Parse(document.RootElement);
    }
}
