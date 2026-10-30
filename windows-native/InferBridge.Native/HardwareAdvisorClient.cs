using System.Globalization;
using System.Text.Json;

namespace InferBridge.Native;

/// <summary>One profile's recommendation ("Best model for this PC"), as the web advisor shows it.</summary>
internal sealed record AdvisorRecommendation(
    string Profile, string ModelId, string ModelName, string Device, string Precision,
    int ContextLength, int OutputTokens, string Compatibility, double FitScore, string Reason,
    IReadOnlyList<string> Warnings, double? MeasuredTokensPerSecond, double? MeasuredFirstTokenMs,
    bool MeasuredSynthetic)
{
    public IReadOnlyList<string> Pills =>
    [
        Device,
        Precision.ToUpperInvariant(),
        ContextLength.ToString("N0", CultureInfo.InvariantCulture) + " context",
        OutputTokens.ToString("N0", CultureInfo.InvariantCulture) + " output",
        "Fit " + Math.Round(FitScore).ToString(CultureInfo.InvariantCulture) + "/100",
    ];
}

/// <summary>One row of the advisor's model compatibility table (generation models only).</summary>
internal sealed record AdvisorModel(
    string Id, string Name, string Compatibility, double FitScore, string RecommendedDevice,
    double? RuntimeMemoryGb, IReadOnlyList<string> Warnings);

/// <summary>
/// The engine's conservative hardware advisor (<c>metrics.advisor</c> of
/// <c>GET /v1/system/telemetry</c>), the data behind the web "Best model for this
/// PC" panel. Estimates stay estimates until a benchmark on this PC exists.
/// </summary>
internal sealed record HardwareAdvisor(
    IReadOnlyList<(string Id, string Label)> Profiles, string DefaultProfile,
    IReadOnlyDictionary<string, AdvisorRecommendation?> Recommendations,
    IReadOnlyList<AdvisorModel> Models, string Notice)
{
    private static readonly System.Text.RegularExpressions.Regex Slug =
        new("^[a-z][a-z0-9-]{0,31}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant);

    public static HardwareAdvisor Parse(JsonElement telemetry)
    {
        if (telemetry.ValueKind != JsonValueKind.Object
            || !telemetry.TryGetProperty("metrics", out var metrics) || metrics.ValueKind != JsonValueKind.Object
            || !metrics.TryGetProperty("advisor", out var advisor) || advisor.ValueKind != JsonValueKind.Object
            || !advisor.TryGetProperty("profile_order", out var order) || order.ValueKind != JsonValueKind.Array
            || !advisor.TryGetProperty("profiles", out var profiles) || profiles.ValueKind != JsonValueKind.Object)
            throw new JsonException("The engine did not report hardware advisor data.");
        advisor.TryGetProperty("profile_labels", out var labels);
        var ids = new List<(string, string)>();
        var recommendations = new Dictionary<string, AdvisorRecommendation?>(StringComparer.Ordinal);
        foreach (var item in order.EnumerateArray())
        {
            if (ids.Count >= 8 || item.ValueKind != JsonValueKind.String) continue;
            string id = item.GetString() ?? "";
            if (!Slug.IsMatch(id) || recommendations.ContainsKey(id)) continue;
            string label = Text(labels, id, 40);
            ids.Add((id, label.Length > 0 ? label : id));
            recommendations[id] = profiles.TryGetProperty(id, out var rec) && rec.ValueKind == JsonValueKind.Object
                ? ParseRecommendation(id, rec) : null;
        }
        if (ids.Count == 0) throw new JsonException("The hardware advisor reported no profiles.");
        string fallback = Text(advisor, "default_profile", 32);
        var models = new List<AdvisorModel>();
        if (advisor.TryGetProperty("models", out var rows) && rows.ValueKind == JsonValueKind.Array)
            foreach (var row in rows.EnumerateArray())
            {
                if (models.Count >= 200 || row.ValueKind != JsonValueKind.Object) continue;
                if (Text(row, "backend", 64).Contains("embedding", StringComparison.OrdinalIgnoreCase)) continue;
                string id = Text(row, "id", 128);
                if (id.Length == 0) continue;
                string name = Text(row, "name", 120);
                models.Add(new AdvisorModel(id, name.Length > 0 ? name : id,
                    Compatibility(Text(row, "compatibility", 24)), Number(row, "fit_score", 100) ?? 0,
                    Text(row, "recommended_device", 64), Number(row, "runtime_memory_gb", 4096),
                    Warnings(row)));
            }
        return new HardwareAdvisor(ids,
            ids.Any(p => p.Item1 == fallback) ? fallback : ids[0].Item1,
            recommendations,
            models.OrderByDescending(m => m.FitScore).ThenBy(m => m.Name, StringComparer.OrdinalIgnoreCase).ToList(),
            Text(advisor, "estimates_notice", 300));
    }

    private static AdvisorRecommendation? ParseRecommendation(string profile, JsonElement rec)
    {
        string modelId = Text(rec, "model_id", 128);
        if (modelId.Length == 0) return null;
        double? speed = null, first = null;
        bool synthetic = false;
        if (rec.TryGetProperty("benchmark", out var bench) && bench.ValueKind == JsonValueKind.Object
            && !(bench.TryGetProperty("success", out var ok) && ok.ValueKind == JsonValueKind.False))
        {
            speed = Number(bench, "decode_tokens_sec", 100000) ?? Number(bench, "tokens_sec", 100000);
            first = Number(bench, "time_to_first_token_ms", 3_600_000);
            synthetic = bench.TryGetProperty("synthetic", out var mock) && mock.ValueKind == JsonValueKind.True;
        }
        string name = Text(rec, "model_name", 120);
        string precision = Text(rec, "precision", 16);
        return new AdvisorRecommendation(profile, modelId, name.Length > 0 ? name : modelId,
            Text(rec, "device", 64) is { Length: > 0 } device ? device : "AUTO",
            precision.Length > 0 ? precision : "unknown",
            (int)(Number(rec, "context_length", 10_000_000) ?? 0), (int)(Number(rec, "output_tokens", 1_000_000) ?? 0),
            Compatibility(Text(rec, "compatibility", 24)), Number(rec, "fit_score", 100) ?? 0,
            Text(rec, "reason", 400), Warnings(rec), speed, first, synthetic);
    }

    private static string Compatibility(string value) =>
        value is "compatible" or "caution" or "blocked" ? value : "unknown";

    private static IReadOnlyList<string> Warnings(JsonElement owner)
    {
        var warnings = new List<string>();
        if (owner.TryGetProperty("warnings", out var items) && items.ValueKind == JsonValueKind.Array)
            foreach (var item in items.EnumerateArray())
            {
                if (warnings.Count >= 3) break;
                string message = Text(item, "message", 240);
                if (message.Length > 0) warnings.Add(message);
            }
        return warnings;
    }

    private static double? Number(JsonElement owner, string name, double maximum) =>
        owner.ValueKind == JsonValueKind.Object && owner.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out double number)
            && double.IsFinite(number) && number >= 0 && number <= maximum ? number : null;

    private static string Text(JsonElement owner, string name, int maximum)
    {
        if (owner.ValueKind != JsonValueKind.Object || !owner.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.String) return "";
        string text = string.Join(' ', (value.GetString() ?? "").Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        text = new string(text.Where(ch => !char.IsControl(ch)).ToArray());
        return text.Length > maximum ? text[..maximum] + "…" : text;
    }
}

internal sealed partial class BackendClient
{
    /// <summary>Read-only advisor snapshot; the engine computes it, the app only displays it.</summary>
    public async Task<HardwareAdvisor> GetHardwareAdvisorAsync(string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/system/telemetry", apiKey,
            cancellationToken, TimeSpan.FromSeconds(20), MaximumChatBytes);
        return HardwareAdvisor.Parse(document.RootElement);
    }
}
