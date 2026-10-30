using System.Text.Json;

namespace InferBridge.Native;

internal sealed record RuntimeHealthModel(
    string ModelId, string Name, string HealthStatus, string HealthLabel,
    string Action, string ActionLabel, string Reason, bool ActionAvailable,
    bool CanLeaveUnchanged);

internal sealed record RuntimeHealthReport(
    string Application, string OpenVino, string OpenVinoGenAi, int NeedsAttention,
    bool RuntimeChangeDetected, IReadOnlyList<RuntimeHealthModel> Models)
{
    // Same eligibility the web health center uses for its batch buttons.
    public IReadOnlyList<string> BatchCandidates(string action) => Models
        .Where(model => model.Action == action && model.ActionAvailable)
        .Select(model => model.ModelId).ToArray();
}

/// <summary>
/// System Doctor remediation through the web "Runtime upgrade and model health"
/// endpoints. The engine decides which action is recommended and whether it is
/// currently available; native never offers an action the engine did not.
/// </summary>
internal sealed partial class BackendClient
{
    internal static readonly string[] RuntimeHealthActions =
        ["revalidate", "rebuild_compiled_cache", "reconvert", "leave_unchanged"];

    private static string HealthText(JsonElement element, string property, int maximum = 512)
    {
        string text = ReadString(element, property);
        return text.Length > maximum || text.Any(char.IsControl)
            ? throw new JsonException("Invalid runtime health field.") : text;
    }

    public async Task<RuntimeHealthReport> GetRuntimeHealthAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/runtime-health", apiKey,
            cancellationToken, TimeSpan.FromSeconds(30), MaximumChatBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("schema_version", out var schema) || !schema.TryGetInt32(out int version)
            || version != 1
            || !root.TryGetProperty("summary", out var summary) || summary.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("models", out var models) || models.ValueKind != JsonValueKind.Array
            || models.GetArrayLength() > 2000)
            throw new JsonException("Unsupported runtime health report.");
        root.TryGetProperty("runtime", out var runtime);
        var rows = new List<RuntimeHealthModel>();
        foreach (var model in models.EnumerateArray())
        {
            string id = HealthText(model, "model_id", 128);
            if (!LocalModelId.IsMatch(id)) throw new JsonException("Invalid runtime health model.");
            model.TryGetProperty("conversion_health", out var health);
            if (!model.TryGetProperty("recommendation", out var recommendation)
                || recommendation.ValueKind != JsonValueKind.Object
                || !recommendation.TryGetProperty("available", out var available)
                || available.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new JsonException("Invalid runtime health recommendation.");
            string action = HealthText(recommendation, "action", 32);
            if (!RuntimeHealthActions.Contains(action))
                throw new JsonException("Unknown runtime health action.");
            string reason = HealthText(recommendation, "blocked_reason");
            if (reason.Length == 0) reason = HealthText(recommendation, "reason");
            rows.Add(new RuntimeHealthModel(id, HealthText(model, "name", 160),
                HealthText(health, "status", 64), HealthText(health, "label", 120), action,
                HealthText(recommendation, "label", 120), reason, available.GetBoolean(),
                model.TryGetProperty("can_leave_unchanged", out var leave)
                    && leave.ValueKind == JsonValueKind.True));
        }
        int attention = summary.TryGetProperty("needs_attention", out var count)
            && count.TryGetInt32(out int value) && value >= 0 ? value : 0;
        return new RuntimeHealthReport(
            HealthText(runtime, "application", 64), HealthText(runtime, "openvino", 64),
            HealthText(runtime, "openvino_genai", 64), attention,
            summary.TryGetProperty("runtime_change_detected", out var changed)
                && changed.ValueKind == JsonValueKind.True, rows);
    }

    /// <summary>
    /// One maintenance action. Re-reads the report first and refuses an action the
    /// engine no longer offers for that model. Not retried after a timeout.
    /// </summary>
    public async Task<string> RunRuntimeHealthActionAsync(
        string modelId, string action, string? apiKey, CancellationToken cancellationToken)
    {
        if (!LocalModelId.IsMatch(modelId) || !RuntimeHealthActions.Contains(action))
            throw new ArgumentException("Unsupported maintenance action.");
        var current = await GetRuntimeHealthAsync(apiKey, cancellationToken);
        var row = current.Models.FirstOrDefault(model => model.ModelId == modelId);
        // Mirrors the web buttons: "No action needed" rows offer nothing, and
        // "Leave unchanged" acknowledges a warning the engine lets the user accept.
        bool offered = row is not null && row.Action != "leave_unchanged" && (action == "leave_unchanged"
            ? row.CanLeaveUnchanged
            : row.Action == action && row.ActionAvailable);
        if (!offered)
            throw new EngineRequestException("The engine no longer recommends this action. Refresh model health.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + "/v1/runtime-health/action",
            JsonSerializer.Serialize(new { action, model_id = modelId }), apiKey, cancellationToken,
            TimeSpan.FromMinutes(10), MaximumJsonBytes);
        string status = ReadString(document.RootElement, "status");
        if (status.Length is 0 or > 64)
            throw new JsonException("The engine did not confirm the maintenance action.");
        return status == "scheduled"
            ? "Reconversion queued. Progress continues in the Model library."
            : "Maintenance completed (" + status + ").";
    }

    /// <summary>Batch revalidation or compiled-cache rebuild for engine-eligible models.</summary>
    public async Task<(string Status, int Failures)> RunRuntimeHealthBatchAsync(
        string action, string? apiKey, CancellationToken cancellationToken)
    {
        if (action is not ("revalidate" or "rebuild_compiled_cache"))
            throw new ArgumentException("Unsupported batch action.");
        var current = await GetRuntimeHealthAsync(apiKey, cancellationToken);
        var models = current.BatchCandidates(action);
        if (models.Count is 0 or > 128)
            throw new EngineRequestException("No models currently need this maintenance action.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + "/v1/runtime-health/batch",
            JsonSerializer.Serialize(new { action, model_ids = models }), apiKey, cancellationToken,
            TimeSpan.FromMinutes(30), MaximumJsonBytes);
        string status = ReadString(document.RootElement, "status");
        if (status.Length is 0 or > 64)
            throw new JsonException("The engine did not confirm the maintenance batch.");
        int failures = document.RootElement.TryGetProperty("failures", out var list)
            && list.ValueKind == JsonValueKind.Array ? list.GetArrayLength() : 0;
        return (status, failures);
    }
}
