using System.Text.Json;

namespace InferBridge.Native;

internal sealed record ContextOption(int Tokens, string Classification, double? KvEstimateGib,
    double? EstimatedHostRamGib, string FitGuidance);

/// <summary>One KV-cache precision choice; explicit values are unavailable on NPU loads.</summary>
internal sealed record KvPrecisionOption(string Value, string Label, bool Available);

/// <summary>
/// Per-model context facts. KvPrecision is "auto" (OpenVINO's own choice) or
/// f16/u8/u4, sent as KV_CACHE_PRECISION to CPU/GPU only. KvApplies is false
/// when the model is loaded on the NPU, which manages its own KV cache.
/// </summary>
internal sealed record ContextConfiguration(
    string ModelId, int Current, int Recommended, int PromptBudget, int OutputReserve,
    int? ArchitectureLimit, bool Loaded, string? Device,
    IReadOnlyList<ContextOption> Options,
    string KvPrecision = "auto", bool KvApplies = true,
    IReadOnlyList<KvPrecisionOption>? KvOptions = null,
    string Guidance = "", string KvNote = "");

/// <summary>Context facts for one registered model, loaded or not (model lists).</summary>
internal sealed record ContextSummary(
    string ModelId, string Name, bool Configurable, int? Current, int? Recommended,
    int? ArchitectureLimit, bool Loaded, bool Trial);

internal sealed partial class BackendClient
{
    private static ContextConfiguration ParseContext(JsonElement root, string modelId)
    {
        if (root.ValueKind != JsonValueKind.Object
            || ReadString(root, "model_id") != modelId)
            throw new JsonException("Context response does not match the selected model.");
        static int Budget(JsonElement element, string key, int low, int high)
        {
            if (!element.TryGetProperty(key, out var value)
                || value.ValueKind != JsonValueKind.Number
                || !value.TryGetInt32(out int size) || size < low || size > high)
                throw new JsonException("Invalid context budget response.");
            return size;
        }
        int current = Budget(root, "configured_context_tokens", 128, 262144);
        int recommended = Budget(root, "recommended_context_tokens", 128, 262144);
        int prompt = Budget(root, "prompt_budget_tokens", 1, 262144);
        int output = Budget(root, "reserved_output_tokens", 1, 65536);
        if (prompt + output != current)
            throw new JsonException("Context and output budgets disagree.");
        int? architecture = null;
        if (root.TryGetProperty("model_architecture_limit_tokens", out var maximum)
            && maximum.ValueKind == JsonValueKind.Number)
            architecture = maximum.TryGetInt32(out int limit) && limit is >= 128 and <= 262144
                ? limit : throw new JsonException("Invalid model architecture limit.");
        if (!root.TryGetProperty("options", out var options)
            || options.ValueKind != JsonValueKind.Array || options.GetArrayLength() > 32)
            throw new JsonException("Invalid context options.");
        var choices = new List<ContextOption>();
        foreach (var option in options.EnumerateArray())
        {
            int tokens = Budget(option, "tokens", 128, 262144);
            string classification = ReadString(option, "classification");
            if (classification is not ("recommended" or "lower-memory"
                or "eligible-unverified" or "existing-unverified"))
                throw new JsonException("Unknown context recommendation classification.");
            double? estimate = null;
            if (option.TryGetProperty("fp16_kv_cache_gib_estimate", out var kv)
                && kv.ValueKind == JsonValueKind.Number)
                estimate = kv.TryGetDouble(out double size) && double.IsFinite(size) && size >= 0
                    ? size : throw new JsonException("Invalid KV-cache estimate.");
            double? hostRam = null;
            if (option.TryGetProperty("estimated_host_ram_gib", out var host)
                && host.ValueKind == JsonValueKind.Number)
                hostRam = host.TryGetDouble(out double hostSize) && double.IsFinite(hostSize)
                    && hostSize >= 0 ? hostSize : throw new JsonException("Invalid host memory estimate.");
            string fit = ReadString(option, "fit_guidance");
            if (fit is not ("unverified-memory" or "memory-constrained" or "estimated-headroom"))
                throw new JsonException("Unknown memory guidance classification.");
            if (!choices.Any(item => item.Tokens == tokens))
                choices.Add(new ContextOption(tokens, classification, estimate, hostRam, fit));
        }
        if (!choices.Any(item => item.Tokens == current))
            throw new JsonException("Current context is not among the supported options.");
        bool loaded = root.TryGetProperty("loaded", out var flag)
            && flag.ValueKind == JsonValueKind.True;
        string device = ReadString(root, "active_device");
        // KV precision is optional so older engines (no field) still parse as Auto.
        string kvPrecision = ReadString(root, "kv_cache_precision");
        if (kvPrecision.Length == 0) kvPrecision = "auto";
        if (!ContextEditor.KvPrecisions.Contains(kvPrecision))
            throw new JsonException("Unknown KV cache precision.");
        bool kvApplies = !(root.TryGetProperty("kv_cache_precision_applies", out var applies)
            && applies.ValueKind == JsonValueKind.False);
        List<KvPrecisionOption>? kvOptions = null;
        if (root.TryGetProperty("kv_cache_precision_options", out var kvRows)
            && kvRows.ValueKind == JsonValueKind.Array && kvRows.GetArrayLength() <= 8)
        {
            kvOptions = new List<KvPrecisionOption>();
            foreach (var row in kvRows.EnumerateArray())
            {
                string value = ReadString(row, "value");
                if (!ContextEditor.KvPrecisions.Contains(value)
                    || kvOptions.Any(item => item.Value == value)) continue;
                string label = ReadString(row, "label");
                kvOptions.Add(new KvPrecisionOption(value,
                    label.Length is > 0 and <= 80 ? label : ContextEditor.KvLabel(value),
                    row.TryGetProperty("available", out var ok) && ok.ValueKind == JsonValueKind.True));
            }
        }
        static string Text(JsonElement element, string key)
        {
            string value = ReadString(element, key);
            return value.Length <= 1024 ? value : "";
        }
        return new ContextConfiguration(modelId, current, recommended, prompt, output,
            architecture, loaded, device.Length <= 64 ? device : null, choices,
            kvPrecision, kvApplies, kvOptions, Text(root, "context_guidance"),
            Text(root, "kv_cache_precision_note"));
    }

    public async Task<ContextConfiguration> GetContextAsync(
        string modelId, string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/models/context?model="
            + Uri.EscapeDataString(modelId), apiKey, cancellationToken, TimeSpan.FromSeconds(12));
        return ParseContext(document.RootElement, modelId);
    }

    public async Task<ContextConfiguration> ApplyContextAsync(
        ContextConfiguration baseline, int requested, string? apiKey,
        CancellationToken cancellationToken)
    {
        if (!baseline.Options.Any(option => option.Tokens == requested
            && option.Classification != "existing-unverified"))
            throw new ArgumentException("Select an available context configuration.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + "/v1/models/context",
            JsonSerializer.Serialize(new
            {
                model = baseline.ModelId, context_tokens = requested,
                expected_context_tokens = baseline.Current,
            }), apiKey, cancellationToken, TimeSpan.FromMinutes(20), MaximumJsonBytes);
        if (ReadString(document.RootElement, "status") != "applied"
            || !document.RootElement.TryGetProperty("context", out var context))
            throw new JsonException("The engine did not confirm context persistence.");
        var applied = ParseContext(context, baseline.ModelId);
        if (applied.Current != requested)
            throw new JsonException("The engine reported a different applied context.");
        return applied;
    }

    /// <summary>
    /// Saves any value the shared editor accepts (preset or custom). The engine
    /// re-validates; for an unloaded model this only persists the value, for a
    /// loaded one it rebuilds the engine and keeps chat history.
    /// </summary>
    public async Task<ContextConfiguration> SetContextAsync(
        ContextConfiguration baseline, int requested, string? apiKey,
        CancellationToken cancellationToken, string? kvPrecision = null)
    {
        var choice = ContextEditor.Validate(baseline, requested, kvPrecision);
        if (!choice.Valid) throw new ArgumentException(choice.Message);
        if (kvPrecision is not null && !ContextEditor.KvPrecisions.Contains(kvPrecision))
            throw new ArgumentException("Choose Auto, FP16, INT8 or INT4 KV cache precision.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        string body = kvPrecision is null
            ? JsonSerializer.Serialize(new
            {
                model = baseline.ModelId, context_tokens = requested,
                expected_context_tokens = baseline.Current,
            })
            : JsonSerializer.Serialize(new
            {
                model = baseline.ModelId, context_tokens = requested,
                expected_context_tokens = baseline.Current, kv_cache_precision = kvPrecision,
            });
        using var document = await PostJsonAsync(origin + "/v1/models/context",
            body, apiKey, cancellationToken, TimeSpan.FromMinutes(20), MaximumJsonBytes);
        if (ReadString(document.RootElement, "status") != "applied"
            || !document.RootElement.TryGetProperty("context", out var context))
            throw new JsonException("The engine did not confirm context persistence.");
        var applied = ParseContext(context, baseline.ModelId);
        if (applied.Current != requested)
            throw new JsonException("The engine reported a different applied context.");
        if (kvPrecision is not null && applied.KvPrecision != kvPrecision)
            throw new JsonException("The engine reported a different KV cache precision.");
        return applied;
    }

    /// <summary>Context for every registered model, without loading any of them.</summary>
    public async Task<IReadOnlyList<ContextSummary>> GetContextSummariesAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/models/contexts",
            apiKey, cancellationToken, TimeSpan.FromSeconds(12));
        if (!document.RootElement.TryGetProperty("data", out var rows)
            || rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() > 512)
            throw new JsonException("Invalid model context list.");
        static int? Tokens(JsonElement row, string key)
        {
            if (!row.TryGetProperty(key, out var value) || value.ValueKind == JsonValueKind.Null)
                return null;
            return value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int size)
                && size is >= 1 and <= 262144
                ? size : throw new JsonException("Invalid context size.");
        }
        var result = new List<ContextSummary>();
        foreach (var row in rows.EnumerateArray())
        {
            string id = ReadString(row, "model_id");
            if (id.Length is 0 or > 128 || result.Any(item => item.ModelId == id)) continue;
            bool configurable = row.TryGetProperty("configurable", out var flag)
                && flag.ValueKind == JsonValueKind.True;
            int? current = Tokens(row, "configured_context_tokens");
            if (configurable && current is null)
                throw new JsonException("A configurable model did not report its context.");
            result.Add(new ContextSummary(id, ReadString(row, "name") is { Length: > 0 and <= 256 } name
                    ? name : id,
                configurable, current, Tokens(row, "recommended_context_tokens"),
                Tokens(row, "model_architecture_limit_tokens"),
                row.TryGetProperty("loaded", out var loaded) && loaded.ValueKind == JsonValueKind.True,
                row.TryGetProperty("trial", out var trial) && trial.ValueKind == JsonValueKind.True));
        }
        return result;
    }
}
