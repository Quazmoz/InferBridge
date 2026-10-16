using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record ModelLibraryDetails(
    string Backend, string WeightFormat, string SourceRepository,
    string ConfiguredDevice, string RuntimeDevice, int? ContextTokens,
    int? OutputTokens, bool? SupportsVision);

internal sealed record ModelPreparationInfo(
    string ModelId, string? OperationId, string Phase,
    double? Percent, bool CanCancel, string CancelReason)
{
    public ModelLibraryDetails? Details { get; init; }
}

internal sealed partial class BackendClient
{
    private static readonly Regex PreparationOperationId = new(
        @"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.CultureInvariant);

    private static readonly string[] CancellablePhases =
    [
        "queued", "resolving", "downloading", "converting", "finalizing",
    ];

    // Optional registry metadata is advisory. Unknown or malformed fields must
    // never become unsafe text in a native UI or invalidate cancellation state.
    private static ModelLibraryDetails ParseLibraryDetails(JsonElement item)
    {
        static string Allowed(string value, string[] choices) =>
            choices.Contains(value, StringComparer.Ordinal) ? value : "Not reported";
        static string Device(string value)
        {
            static bool Physical(string token) =>
                Regex.IsMatch(token, @"^(CPU|GPU|NPU)(\.[0-9]{1,3})?$",
                    RegexOptions.CultureInvariant);
            if (value.Length is < 1 or > 64) return "Not reported";
            if (Physical(value) || value == "AUTO") return value;
            string[] expression = value.Split(':', 2);
            if (expression.Length != 2
                || expression[0] is not ("AUTO" or "MULTI" or "HETERO"))
                return "Not reported";
            string[] physical = expression[1].Split(',');
            return physical.Length is > 0 and <= 8 && physical.All(Physical)
                ? value : "Not reported";
        }
        static int? Budget(JsonElement entry, string key, int minimum, int maximum)
        {
            return entry.TryGetProperty(key, out var raw)
                && raw.ValueKind == JsonValueKind.Number
                && raw.TryGetInt32(out int size)
                && size >= minimum && size <= maximum ? size : null;
        }
        string source = ReadString(item, "source_model");
        if (source.Length > 240 ||
            !Regex.IsMatch(source,
                @"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$",
                RegexOptions.CultureInvariant)
            || Regex.IsMatch(source, @"hf_[A-Za-z0-9]{8,}",
                RegexOptions.CultureInvariant))
            source = "Not reported";
        bool? vision = null;
        if (item.TryGetProperty("supports_vision", out var flag))
        {
            if (flag.ValueKind == JsonValueKind.True) vision = true;
            if (flag.ValueKind == JsonValueKind.False) vision = false;
        }
        return new ModelLibraryDetails(
            Allowed(ReadString(item, "backend"),
                ["openvino-genai", "openvino-embeddings", "openvino-vlm"]),
            Allowed(ReadString(item, "weight_format"), ["int4", "int8", "fp16"]),
            source, Device(ReadString(item, "recommended_device")),
            Device(ReadString(item, "device")),
            Budget(item, "max_context_len", 128, 262144),
            Budget(item, "max_output_tokens", 0, 65536),
            vision);
    }

    private static ModelPreparationInfo ParsePreparation(JsonElement item)
    {
        if (item.ValueKind != JsonValueKind.Object)
            throw new JsonException("Invalid model status entry.");
        string modelId = ReadString(item, "id");
        if (modelId.Length is < 1 or > 128)
            throw new JsonException("Invalid model status ID.");
        string operationId = "";
        string phase = "idle";
        double? percent = null;
        if (item.TryGetProperty("progress", out var progress)
            && progress.ValueKind == JsonValueKind.Object)
        {
            operationId = ReadString(progress, "operation_id");
            phase = ReadString(progress, "phase");
            if (phase is not ("idle" or "queued" or "resolving" or "downloading"
                or "converting" or "finalizing" or "loading" or "ready"
                or "cancelled" or "error"))
                phase = "unknown";
            if (progress.TryGetProperty("percent", out var value)
                && value.ValueKind is not JsonValueKind.Null)
            {
                if (value.ValueKind != JsonValueKind.Number
                    || !value.TryGetDouble(out double measure)
                    || !double.IsFinite(measure) || measure is < 0 or > 100)
                    throw new JsonException("Invalid engine preparation percentage.");
                percent = measure;
            }
        }
        if (operationId.Length > 0
            && (operationId.Length > 128 || !PreparationOperationId.IsMatch(operationId)))
            throw new JsonException("Invalid engine operation identifier.");
        bool cancel = false;
        if (item.TryGetProperty("can_cancel", out var flag))
        {
            if (flag.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new JsonException("Invalid model cancellation capability.");
            cancel = flag.GetBoolean();
        }
        if (cancel && operationId.Length == 0)
            throw new JsonException("Missing engine cancellation operation ID.");
        // An engine capability can change during native compilation. Display
        // other models even if this phase is too risky to cancel.
        if (cancel && !CancellablePhases.Contains(phase, StringComparer.Ordinal))
            cancel = false;

        // Never put free-form server messages or operation IDs into the view:
        // progress details could inadvertently contain local paths or tokens.
        return new ModelPreparationInfo(modelId,
            operationId.Length > 0 ? operationId : null,
            phase, percent, cancel,
            cancel ? "" : "The engine does not offer cancellation for this stage.")
        {
            Details = ParseLibraryDetails(item),
        };
    }

    private static IReadOnlyDictionary<string, ModelPreparationInfo> ParsePreparations(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("schema_version", out var version)
            || version.ValueKind != JsonValueKind.Number
            || !version.TryGetInt32(out int schema) || schema != 1
            || !root.TryGetProperty("models", out var models)
            || models.ValueKind != JsonValueKind.Object
            || !models.TryGetProperty("available", out var rows)
            || rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() > 2000)
            throw new JsonException("Invalid model lifecycle snapshot.");

        var operations = new Dictionary<string, ModelPreparationInfo>(StringComparer.Ordinal);
        foreach (var row in rows.EnumerateArray())
        {
            var operation = ParsePreparation(row);
            if (!operations.TryAdd(operation.ModelId, operation))
                throw new JsonException("Duplicate model in lifecycle snapshot.");
        }
        return operations;
    }

    public async Task<IReadOnlyDictionary<string, ModelPreparationInfo>>
        GetModelPreparationsAsync(string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var response = await GetJsonAsync(
            origin + "/v1/models/status", apiKey, cancellationToken,
            TimeSpan.FromSeconds(12), 2 * 1024 * 1024);
        return ParsePreparations(response.RootElement);
    }

    /// <summary>Cancel only the exact still-cancellable engine operation.</summary>
    public async Task<string> CancelModelPreparationAsync(
        ModelPreparationInfo selected, string? apiKey, CancellationToken cancellationToken)
    {
        if (!selected.CanCancel || selected.OperationId is null
            || !PreparationOperationId.IsMatch(selected.OperationId)
            || !CancellablePhases.Contains(selected.Phase, StringComparer.Ordinal))
            throw new ArgumentException("This model operation is not cancellable.");

        // Fresh authoritative capability/identity check avoids acting on a stale
        // selection after polling, a restart, or another client's mutation.
        var now = await GetModelPreparationsAsync(apiKey, cancellationToken);
        if (!now.TryGetValue(selected.ModelId, out var current)
            || !current.CanCancel || current.OperationId != selected.OperationId)
            throw new EngineRequestException(
                "The model operation changed. Refresh the library before acting.");

        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var result = await PostJsonAsync(
            origin + "/v1/models/cancel",
            JsonSerializer.Serialize(new
            {
                model = selected.ModelId,
                operation_id = selected.OperationId,
            }), apiKey, cancellationToken, TimeSpan.FromSeconds(45), MaximumJsonBytes);
        if (ReadString(result.RootElement, "status") != "cancelled"
            || ReadString(result.RootElement, "operation_id") != selected.OperationId
            || !result.RootElement.TryGetProperty("model", out var model)
            || ReadString(model, "id") != selected.ModelId)
            throw new JsonException(
                "The engine did not confirm cancellation. Refresh the library.");
        return "The engine confirmed preparation cancellation for "
            + selected.ModelId + ". Review the Model library for remaining files.";
    }
}
