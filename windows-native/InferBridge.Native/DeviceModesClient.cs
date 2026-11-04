using System.Text.Json;

namespace InferBridge.Native;

/// <summary>One backend's verdict for a builder option, as the engine classified it.</summary>
internal sealed record ModeSupport(string Support, string Reason)
{
    public bool Unsupported => Support == "unsupported";
}

/// <summary>
/// One of the combined-device builder's modes for an ordered member list, read from
/// <c>GET /v1/devices/modes?members=…</c>. Every rule (and every explanation) comes
/// from the engine's <c>app/device_modes.py</c>; nothing here re-derives support.
/// </summary>
internal sealed record CombinedModeOption(
    string Id, string Label, string Summary, string Device,
    string? PerformanceHint, string? ModelDistribution, bool Experimental,
    IReadOnlyDictionary<string, ModeSupport> Backends)
{
    /// <summary>
    /// The strictest verdict over the given backend kinds. A chat model whose vision
    /// capability is unknown is judged as both "chat" and "vlm".
    /// </summary>
    public ModeSupport SupportFor(IEnumerable<string> kinds)
    {
        ModeSupport? worst = null;
        foreach (string kind in kinds)
        {
            ModeSupport current = Backends.TryGetValue(kind, out var found) ? found
                : new ModeSupport("unsupported", "The engine did not classify this mode for this model.");
            if (worst is null || CombinedModes.Rank(current.Support) > CombinedModes.Rank(worst.Support))
                worst = current;
        }
        return worst ?? new ModeSupport("unsupported", "No model selected.");
    }

    public CombinedTarget ToTarget() => new(Device, PerformanceHint, ModelDistribution, Label);
}

internal sealed record CombinedModeBuilder(
    IReadOnlyList<string> Choices, IReadOnlyList<string> Members,
    IReadOnlyList<CombinedModeOption> Options);

/// <summary>A builder choice the load and device-change flows send to the engine.</summary>
internal sealed record CombinedTarget(
    string Device, string? PerformanceHint, string? ModelDistribution, string Label)
{
    public string PickerLabel => Label + " · " + Device + " · experimental";
}

internal static class CombinedModes
{
    public static readonly string[] Bases = { "GPU", "CPU", "NPU" };
    public static readonly string[] PerformanceHints = { "LATENCY", "THROUGHPUT", "CUMULATIVE_THROUGHPUT" };
    public const string PipelineParallel = "pipeline_parallel";
    private static readonly string[] Kinds = { "chat", "vlm", "embedding", "rerank" };
    private static readonly string[] ModeIds = { "auto", "auto_cumulative", "hetero", "hetero_pipeline" };

    public static int Rank(string support) => support switch
    {
        "supported" => 0,
        "experimental" => 1,
        _ => 2,
    };

    /// <summary>The backend kinds a model is judged as (vision unknown → chat and vlm).</summary>
    public static IReadOnlyList<string> KindsFor(string role, bool? supportsVision) => role switch
    {
        "embedding" => new[] { "embedding" },
        "rerank" => new[] { "rerank" },
        _ => supportsVision switch
        {
            true => new[] { "vlm" },
            false => new[] { "chat" },
            null => new[] { "chat", "vlm" },
        },
    };

    /// <summary>Moves one member up (-1) or down (+1); out-of-range moves are no-ops.</summary>
    public static IReadOnlyList<string> Move(IReadOnlyList<string> members, int index, int delta)
    {
        var copy = members.ToList();
        int target = index + delta;
        if (index < 0 || index >= copy.Count || target < 0 || target >= copy.Count || delta is not (-1 or 1))
            return copy;
        (copy[index], copy[target]) = (copy[target], copy[index]);
        return copy;
    }

    /// <summary>Strict parse of the endpoint's builder fields; unknown values are dropped.</summary>
    public static CombinedModeBuilder Parse(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
            throw new JsonException("Invalid combined-device modes.");
        var choices = ReadBases(root, "builder_members");
        if (!root.TryGetProperty("builder", out var builder) || builder.ValueKind == JsonValueKind.Null)
            return new CombinedModeBuilder(choices, Array.Empty<string>(), Array.Empty<CombinedModeOption>());
        if (builder.ValueKind != JsonValueKind.Object)
            throw new JsonException("Invalid combined-device builder.");
        var members = ReadBases(builder, "members");
        if (members.Count is < 2 or > 3)
            throw new JsonException("Invalid combined-device members.");
        var options = new List<CombinedModeOption>();
        if (builder.TryGetProperty("options", out var rows) && rows.ValueKind == JsonValueKind.Array)
        {
            foreach (var row in rows.EnumerateArray())
            {
                if (options.Count >= ModeIds.Length || row.ValueKind != JsonValueKind.Object) continue;
                string id = Text(row, "id", 32);
                string device = Text(row, "device", 64);
                string? hint = Optional(row, "performance_hint", 32);
                string? distribution = Optional(row, "model_distribution", 32);
                if (!ModeIds.Contains(id) || options.Any(option => option.Id == id)
                    || !BackendClient.IsSupportedInferenceDevice(device) || !device.Contains(':')
                    || (hint is not null && (!PerformanceHints.Contains(hint) || !device.StartsWith("AUTO:", StringComparison.Ordinal)))
                    || (distribution is not null && (distribution != PipelineParallel || !device.StartsWith("HETERO:", StringComparison.Ordinal))))
                    continue;
                var backends = new Dictionary<string, ModeSupport>(StringComparer.Ordinal);
                if (row.TryGetProperty("backends", out var verdicts) && verdicts.ValueKind == JsonValueKind.Object)
                    foreach (string kind in Kinds)
                        if (verdicts.TryGetProperty(kind, out var verdict) && verdict.ValueKind == JsonValueKind.Object)
                        {
                            string support = Text(verdict, "support", 16);
                            if (support is "supported" or "experimental" or "unsupported")
                                backends[kind] = new ModeSupport(support, Text(verdict, "reason", 400));
                        }
                string label = Text(row, "label", 80);
                options.Add(new CombinedModeOption(id, label.Length > 0 ? label : id,
                    Text(row, "summary", 400), device, hint, distribution,
                    !row.TryGetProperty("experimental", out var flag) || flag.ValueKind != JsonValueKind.False,
                    backends));
            }
        }
        return new CombinedModeBuilder(choices, members, options);
    }

    private static List<string> ReadBases(JsonElement owner, string name)
    {
        var bases = new List<string>();
        if (owner.TryGetProperty(name, out var list) && list.ValueKind == JsonValueKind.Array)
            foreach (var item in list.EnumerateArray())
            {
                string value = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : "";
                if (Bases.Contains(value) && !bases.Contains(value)) bases.Add(value);
                if (bases.Count >= 3) break;
            }
        return bases;
    }

    private static string Text(JsonElement owner, string name, int maximum)
    {
        string value = owner.TryGetProperty(name, out var found) && found.ValueKind == JsonValueKind.String
            ? (found.GetString() ?? "").Trim() : "";
        return value.Length <= maximum ? value : value[..maximum] + "…";
    }

    private static string? Optional(JsonElement owner, string name, int maximum)
    {
        string value = Text(owner, name, maximum);
        return value.Length == 0 ? null : value;
    }
}

internal sealed partial class BackendClient
{
    /// <summary>
    /// The engine's builder evaluation for an ordered member list (or its default list
    /// when <paramref name="members"/> is null).
    /// </summary>
    public async Task<CombinedModeBuilder> GetCombinedModesAsync(
        IReadOnlyList<string>? members, string? apiKey, CancellationToken cancellationToken)
    {
        if (members is not null && (members.Count is < 2 or > 3
            || members.Any(member => !CombinedModes.Bases.Contains(member))
            || members.Distinct(StringComparer.Ordinal).Count() != members.Count))
            throw new ArgumentException("Choose two or three different devices.", nameof(members));
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        string query = members is null ? "" : "?members=" + string.Join(",", members);
        using var document = await GetJsonAsync(origin + "/v1/devices/modes" + query, apiKey,
            cancellationToken, TimeSpan.FromSeconds(8));
        return CombinedModes.Parse(document.RootElement);
    }
}
