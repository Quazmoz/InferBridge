using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record BenchmarkModelChoice(
    string Id, string Name, string Status, string Detail, bool Prepared, bool Loaded);

internal sealed record BenchmarkCatalog(
    string SessionId, bool Synthetic, IReadOnlyList<BenchmarkModelChoice> Models,
    IReadOnlyList<string> Devices);

internal sealed record BenchmarkPlan(
    IReadOnlyList<string> Models, IReadOnlyList<string> Devices,
    string Preset, int Runs, int Warmups, int MaxTokens)
{
    public int Combinations => Models.Count * Devices.Count;
    public int TotalSamples => Combinations * (Runs + Warmups);
}

internal sealed record BenchmarkActivity(string Message, int Completed, int Total);
internal sealed record BenchmarkCompletion(string RunId, bool Synthetic, int Successes, int Failures);

// UI-local guard: a timed-out POST must not be transparently retried.
internal sealed class BenchmarkAttemptGate
{
    public bool Active { get; private set; }
    public bool OutcomeUnknown { get; private set; }

    public bool TryBegin()
    {
        if (Active || OutcomeUnknown) return false;
        Active = true;
        return true;
    }

    public void Finish(bool confirmed)
    {
        Active = false;
        if (!confirmed) OutcomeUnknown = true;
    }
}

internal static class BenchmarkLab
{
    // The server permits 32 combinations, but native defaults deliberately impose
    // a smaller ceiling to keep expensive local matrix runs manageable.
    public const int MaximumCombinations = 8;

    public static BenchmarkCatalog ReadCatalog(JsonElement root, string sessionId)
    {
        if (sessionId.Length is < 8 or > 512
            || root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("models", out var modelRoot)
            || !modelRoot.TryGetProperty("available", out var models)
            || models.ValueKind != JsonValueKind.Array
            || modelRoot.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("device", out var deviceRoot)
            || deviceRoot.ValueKind != JsonValueKind.Object
            || !deviceRoot.TryGetProperty("available", out var devices)
            || devices.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid benchmark engine inventory.");
        bool mock = deviceRoot.TryGetProperty("mock", out var mockValue)
            && mockValue.ValueKind == JsonValueKind.True;
        var choices = new List<BenchmarkModelChoice>();
        foreach (var item in models.EnumerateArray())
        {
            if (choices.Count >= 200) break;
            if (item.ValueKind != JsonValueKind.Object) continue;
            string id = Text(item, "id", 128);
            string backend = Text(item, "backend", 64);
            if (id.Length == 0 || backend is not ("openvino-genai" or "openvino-vlm"))
                continue; // Embeddings and unknown generation capabilities are not benchmarks.
            string status = Text(item, "status", 64);
            bool loaded = Bool(item, "is_loaded");
            bool prepared = mock || loaded || Bool(item, "is_downloaded");
            string name = Text(item, "name", 180);
            string format = Text(item, "weight_format", 32);
            choices.Add(new BenchmarkModelChoice(id, name.Length > 0 ? name : id,
                status, format.Length > 0 ? format.ToUpperInvariant() : backend,
                prepared, loaded));
        }
        var targets = new List<string>();
        foreach (var item in devices.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String) continue;
            string reported = (item.GetString() ?? "").ToUpperInvariant();
            string direct = reported.Split('.')[0];
            if ((direct is "CPU" or "GPU" or "NPU")
                && Regex.IsMatch(reported, @"^(CPU|GPU|NPU)(\.[0-9]{1,3})?$")
                && !targets.Contains(direct))
                targets.Add(direct);
        }
        if (mock && targets.Count == 0) targets.Add("CPU");
        // AUTO is a routing request, not a claim about the actual execution device.
        if (targets.Count > 0) targets.Add("AUTO");
        return new BenchmarkCatalog(sessionId, mock, choices, targets);
    }

    public static BenchmarkPlan CreatePlan(
        BenchmarkCatalog catalog, IEnumerable<string> models,
        IEnumerable<string> devices, string preset)
    {
        (int runs, int warmups, int tokens) = preset switch
        {
            "quick" => (3, 1, 32),
            "standard" => (5, 1, 64),
            "thorough" => (8, 2, 128),
            _ => throw new ArgumentException("Unknown benchmark preset."),
        };
        var modelIds = models.Distinct(StringComparer.Ordinal).ToArray();
        var deviceIds = devices.Distinct(StringComparer.Ordinal).ToArray();
        if (modelIds.Length == 0 || deviceIds.Length == 0)
            throw new ArgumentException("Select at least one prepared model and device.");
        if (modelIds.Length > 16 || deviceIds.Length > 16
            || (long)modelIds.Length * deviceIds.Length > MaximumCombinations)
            throw new ArgumentException("Select at most 8 model/device combinations.");
        if (modelIds.Any(id => !catalog.Models.Any(m => m.Id == id && m.Prepared))
            || deviceIds.Any(id => !catalog.Devices.Contains(id)))
            throw new ArgumentException("Model preparation or device availability changed. Refresh.");
        return new BenchmarkPlan(modelIds, deviceIds, preset, runs, warmups, tokens);
    }

    public static BenchmarkActivity? ReadActivity(
        JsonElement root, DateTimeOffset startedAt, int expectedCombinations)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("events", out var events)
            || events.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid engine activity events.");
        BenchmarkActivity? latest = null;
        foreach (var item in events.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object
                || !item.TryGetProperty("timestamp", out var timestamp)
                || timestamp.ValueKind != JsonValueKind.Number
                || !timestamp.TryGetInt64(out long seconds)
                || seconds < startedAt.ToUnixTimeSeconds()) continue;
            string message = Text(item, "message", 240);
            Match match = Regex.Match(message, @"^Benchmark Lab · combination (\d+)/(\d+) · ");
            if (!match.Success
                || !int.TryParse(match.Groups[1].Value, out int index)
                || !int.TryParse(match.Groups[2].Value, out int total)
                || total != expectedCombinations || index < 1 || index > total)
                continue;
            bool finished = message.EndsWith("· complete", StringComparison.Ordinal)
                || message.EndsWith("· failed", StringComparison.Ordinal);
            int completed = finished ? index : index - 1;
            // Engine event ordering is authoritative; never roll progress backwards.
            if (latest is null || completed >= latest.Completed)
                latest = new BenchmarkActivity(message, completed, total);
        }
        return latest;
    }

    public static BenchmarkCompletion ReadCompletion(JsonElement root, BenchmarkPlan plan)
    {
        string id = Text(root, "run_id", 128);
        if (root.ValueKind != JsonValueKind.Object
            || id.Length == 0
            || !root.TryGetProperty("synthetic", out var synthetic)
            || synthetic.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
            || !root.TryGetProperty("results", out var results)
            || results.ValueKind != JsonValueKind.Array
            || results.GetArrayLength() != plan.Combinations)
            throw new JsonException("The engine did not confirm the benchmark matrix.");
        if (root.TryGetProperty("mock", out var mock)
            && (mock.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
                || (mock.ValueKind == JsonValueKind.True
                    && synthetic.ValueKind != JsonValueKind.True)))
            throw new JsonException("Mock benchmark cannot be labeled as real.");
        var expected = new HashSet<string>(plan.Models.SelectMany(m =>
            plan.Devices.Select(d => m + "\u001F" + d)), StringComparer.Ordinal);
        int successes = 0, failures = 0;
        foreach (var row in results.EnumerateArray())
        {
            if (row.ValueKind != JsonValueKind.Object
                || !row.TryGetProperty("success", out var success)
                || success.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
                || !expected.Remove(Text(row, "model_id", 128) + "\u001F"
                    + Text(row, "requested_device", 64)))
                throw new JsonException("Unexpected or duplicate benchmark result.");
            if (success.ValueKind == JsonValueKind.True) successes++;
            else failures++;
        }
        if (expected.Count != 0)
            throw new JsonException("Missing benchmark combinations.");
        return new BenchmarkCompletion(id, synthetic.ValueKind == JsonValueKind.True,
            successes, failures);
    }

    private static bool Bool(JsonElement value, string field) =>
        value.TryGetProperty(field, out var child) && child.ValueKind == JsonValueKind.True;

    private static string Text(JsonElement value, string field, int limit)
    {
        if (value.ValueKind != JsonValueKind.Object
            || !value.TryGetProperty(field, out var child)
            || child.ValueKind != JsonValueKind.String) return "";
        string text = child.GetString() ?? "";
        return text.Length <= limit ? text : "";
    }
}

internal sealed partial class BackendClient
{
    public async Task<BenchmarkCatalog> GetBenchmarkCatalogAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var identity = await GetJsonAsync(origin + "/desktop/instance", null,
            cancellationToken, TimeSpan.FromSeconds(4));
        string session = identity.RootElement.GetProperty("instance_nonce").GetString() ?? "";
        using var status = await GetJsonAsync(origin + "/v1/system/status", apiKey,
            cancellationToken, TimeSpan.FromSeconds(12), MaximumChatBytes);
        return BenchmarkLab.ReadCatalog(status.RootElement, session);
    }

    public async Task<BenchmarkActivity?> GetBenchmarkActivityAsync(
        string sessionId, DateTimeOffset startedAt, int combinations,
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var identity = await GetJsonAsync(origin + "/desktop/instance", null,
            cancellationToken, TimeSpan.FromSeconds(4));
        if (identity.RootElement.GetProperty("instance_nonce").GetString() != sessionId)
            throw new InvalidOperationException("Engine restarted; benchmark progress is no longer attributable.");
        using var status = await GetJsonAsync(origin + "/v1/system/status", apiKey,
            cancellationToken, TimeSpan.FromSeconds(12), MaximumChatBytes);
        return BenchmarkLab.ReadActivity(status.RootElement, startedAt, combinations);
    }

    public async Task<BenchmarkCompletion> RunBenchmarkLabAsync(
        BenchmarkPlan plan, string sessionId, string? apiKey,
        CancellationToken cancellationToken)
    {
        // Refresh preparation, capabilities and instance identity before dispatch.
        BenchmarkCatalog fresh = await GetBenchmarkCatalogAsync(apiKey, cancellationToken);
        if (fresh.SessionId != sessionId)
            throw new InvalidOperationException("Engine restarted. Refresh benchmark choices.");
        BenchmarkPlan validated = BenchmarkLab.CreatePlan(
            fresh, plan.Models, plan.Devices, plan.Preset);
        if (validated.Runs != plan.Runs || validated.Warmups != plan.Warmups
            || validated.MaxTokens != plan.MaxTokens
            || validated.Models.Count != plan.Models.Count
            || validated.Devices.Count != plan.Devices.Count)
            throw new ArgumentException("Benchmark workload differs from its validated preset.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var dispatchIdentity = await GetJsonAsync(origin + "/desktop/instance",
            null, cancellationToken, TimeSpan.FromSeconds(4));
        if (dispatchIdentity.RootElement.GetProperty("instance_nonce").GetString() != sessionId)
            throw new InvalidOperationException("Engine restarted before benchmark dispatch.");
        using var result = await PostJsonAsync(origin + "/v1/benchmarks/run",
            JsonSerializer.Serialize(new
            {
                models = validated.Models, devices = validated.Devices,
                runs = validated.Runs, max_tokens = validated.MaxTokens,
            }), apiKey, cancellationToken, TimeSpan.FromMinutes(45), MaximumChatBytes);
        return BenchmarkLab.ReadCompletion(result.RootElement, validated);
    }
}
