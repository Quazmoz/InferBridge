using System.Text.Json;

namespace InferBridge.Native;

internal sealed record ContextOption(int Tokens, string Classification, double? KvEstimateGib,
    double? EstimatedHostRamGib, string FitGuidance);

internal sealed record ContextConfiguration(
    string ModelId, int Current, int Recommended, int PromptBudget, int OutputReserve,
    int? ArchitectureLimit, bool Loaded, string? Device,
    IReadOnlyList<ContextOption> Options);

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
        return new ContextConfiguration(modelId, current, recommended, prompt, output,
            architecture, loaded, device.Length <= 64 ? device : null, choices);
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
}
