using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record HuggingFaceAccount(
    bool Configured, bool Removable, string State, string Username, string Source);

internal sealed record HuggingFaceModel(
    string Id, string SuggestedLocalId, string Backend, string Task, long Downloads, long Likes)
{
    // Search metadata is not hardware compatibility or gated-license evidence.
    public string ModelUrl => "https://huggingface.co/" + Id;
}

internal sealed record HuggingFaceDownloadRequest(
    string LocalId, string Name, string SourceModel, string Backend,
    string WeightFormat, string Device, int ContextTokens,
    int OutputTokens, bool LoadAfter)
{
    // Web "Granular Quantization Options (INT4)": sent only for int4, as in the web form.
    public int? GroupSize { get; init; }
    public double? Ratio { get; init; }
    public bool? Symmetric { get; init; }
    public string? Description { get; init; }

    // All-null keeps the engine's conversion defaults; otherwise all three are set.
    public bool HasValidQuantization =>
        (GroupSize is null && Ratio is null && Symmetric is null)
        || (WeightFormat == "int4" && GroupSize is 128 or -1
            && Ratio is double ratio && double.IsFinite(ratio) && ratio is >= 0 and <= 1
            && Symmetric is not null);
}

internal sealed class HuggingFaceCredentialException(string message) : Exception(message);

/// <summary>
/// The engine's structured Hugging Face refusal (409 <c>{"detail": {"code": "hf_…"}}</c>)
/// from the conversion preflight, presented like the browser access dialog
/// (<c>issuePresentation</c> in app/huggingface_access_ui.py).
/// </summary>
internal sealed record HuggingFaceAccessIssue(string Code, string Message, string SourceModel, string AccessType)
{
    // Built from the validated repository ID, never from an engine-supplied URL.
    public string ModelUrl => SourceModel.Length > 0 ? "https://huggingface.co/" + SourceModel : "";

    public (string Title, string Guidance, bool Configure, bool OpenPage) Presentation => Code switch
    {
        "hf_approval_required" => ("Publisher approval required",
            "InferBridge cannot accept the publisher agreement for you. Open the model page, complete approval, then check access again.",
            true, ModelUrl.Length > 0),
        "hf_token_missing" or "hf_token_invalid" => ("Hugging Face access required",
            "Configure a valid read token. For gated models, also complete the publisher approval on the model page.",
            true, ModelUrl.Length > 0),
        "hf_model_not_found" => ("Model could not be found",
            "Review the repository ID and confirm the model contains a config.json file that Optimum can convert.",
            false, ModelUrl.Length > 0),
        "hf_rate_limited" or "hf_network_error" => ("Hugging Face check unavailable",
            "The conversion was not queued. Check the connection and try the access check again.",
            false, false),
        _ => ("Hugging Face access check failed",
            "Review the model page or token, then check access again.",
            true, ModelUrl.Length > 0),
    };
}

internal sealed partial class BackendClient
{
    private static readonly Regex HuggingFaceId = new(
        @"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$",
        RegexOptions.CultureInvariant);

    private static readonly Regex HuggingFaceAccessCode = new(
        @"^hf_[a-z_]{1,60}$", RegexOptions.CultureInvariant);

    /// <summary>Validated access issue from a refusal detail object, or null for any other refusal.</summary>
    private static HuggingFaceAccessIssue? ReadHuggingFaceAccess(JsonElement detail)
    {
        string code = ReadString(detail, "code");
        if (!HuggingFaceAccessCode.IsMatch(code) || code == "hf_access_granted") return null;
        string message = SafeHubLabel(ReadString(detail, "message"), 400);
        string source = ReadString(detail, "source_model");
        string access = ReadString(detail, "access_type");
        return new HuggingFaceAccessIssue(code,
            message.Length > 0 ? message : "Hugging Face access must be checked before this model can be downloaded.",
            HuggingFaceId.IsMatch(source) ? source : "",
            access is "public" or "gated" ? access : "unknown");
    }

    /// <summary>
    /// "Check access again" (<c>POST /v1/huggingface/preflight</c>). A catalog model is
    /// checked by its ID so the engine uses its own access metadata, as the browser does;
    /// a custom download is checked by repository. Throws <see cref="EngineRequestException"/>
    /// with the new issue while access is still not ready.
    /// </summary>
    public async Task CheckHuggingFaceAccessAsync(
        HuggingFaceAccessIssue issue, string? catalogModelId, string? apiKey,
        CancellationToken cancellationToken)
    {
        if (catalogModelId is null && issue.SourceModel.Length == 0)
            throw new EngineRequestException("A Hugging Face source model is required.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        string body = catalogModelId is not null
            ? JsonSerializer.Serialize(new { model_id = catalogModelId })
            : JsonSerializer.Serialize(new { source_model = issue.SourceModel, access_type = issue.AccessType });
        using var result = await PostJsonAsync(origin + "/v1/huggingface/preflight",
            body, apiKey, cancellationToken, TimeSpan.FromSeconds(30), 16384);
        if (ReadString(result.RootElement, "code") != "hf_access_granted")
            throw new JsonException("Hugging Face access was not confirmed.");
    }

    private static string SafeHubLabel(string text, int limit = 120) =>
        new(text.Where(c => !char.IsControl(c)).Take(limit).ToArray());

    private static bool RequireHubBoolean(JsonElement root, string property)
    {
        if (!root.TryGetProperty(property, out var value)
            || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new JsonException("Invalid Hugging Face account response.");
        return value.GetBoolean();
    }

    private static HuggingFaceAccount ParseHubStatus(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
            throw new JsonException("Invalid Hugging Face account response.");
        bool configured = RequireHubBoolean(root, "configured");
        bool removable = RequireHubBoolean(root, "removable");
        string state = ReadString(root, "status");
        if (state is not ("connected" or "not_configured" or "unverified"
            or "network_error" or "rate_limited" or "invalid_token"))
            throw new JsonException("Invalid Hugging Face account state.");
        if (!configured && (removable || state != "not_configured"))
            throw new JsonException("Inconsistent Hugging Face account state.");
        string source = ReadString(root, "source");
        if (source is not ("" or "secure_store" or "environment"))
            throw new JsonException("Invalid Hugging Face credential source.");
        if (removable && source != "secure_store")
            throw new JsonException("Inconsistent Hugging Face credential ownership.");
        return new HuggingFaceAccount(configured, removable, state,
            SafeHubLabel(ReadString(root, "username")),
            source);
    }

    public async Task<HuggingFaceAccount> GetHuggingFaceAccountAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var result = await GetJsonAsync(origin + "/v1/huggingface/status",
            apiKey, cancellationToken, TimeSpan.FromSeconds(12), 16384);
        return ParseHubStatus(result.RootElement);
    }

    public async Task<HuggingFaceAccount> TestHuggingFaceAccountAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var result = await PostJsonAsync(origin + "/v1/huggingface/test",
            "{}", apiKey, cancellationToken, TimeSpan.FromSeconds(25), 16384);
        if (ReadString(result.RootElement.GetProperty("result"), "code")
            != "hf_access_granted")
            throw new JsonException("Hugging Face verification was not confirmed.");
        return ParseHubStatus(result.RootElement.GetProperty("status"));
    }

    public async Task<HuggingFaceAccount> SaveHuggingFaceTokenAsync(
        string token, string? apiKey, CancellationToken cancellationToken)
    {
        if (token.Length is < 11 or > 503
            || !Regex.IsMatch(token, @"^hf_[A-Za-z0-9]{8,500}$",
                RegexOptions.CultureInvariant))
            throw new HuggingFaceCredentialException(
                "Enter a valid Hugging Face user access token beginning with hf_.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        try
        {
            // The secret is sent only to the verified local engine. Never log or
            // include this request body in any error, status, or history surface.
            using var result = await PostJsonAsync(origin + "/v1/huggingface/token",
                JsonSerializer.Serialize(new { token }), apiKey, cancellationToken,
                TimeSpan.FromSeconds(25), 16384);
            HuggingFaceAccount account =
                ParseHubStatus(result.RootElement.GetProperty("status"));
            if (!account.Configured || account.State != "connected")
                throw new JsonException("Token persistence was not confirmed.");
            return account;
        }
        catch (EngineRequestException)
        {
            // Provider errors must never be used to echo secret-containing input.
            throw new HuggingFaceCredentialException(
                "Token verification or secure storage failed. Check read permission and retry.");
        }
    }

    public async Task<HuggingFaceAccount> RemoveHuggingFaceTokenAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        // A previous status may be stale; environment-managed tokens cannot
        // be removed through the API.
        var status = await GetHuggingFaceAccountAsync(apiKey, cancellationToken);
        if (!status.Removable)
            throw new HuggingFaceCredentialException(
                "This token is not removable. Check the engine's HF_TOKEN environment setting.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var request = new HttpRequestMessage(HttpMethod.Delete,
            origin + "/v1/huggingface/token");
        SetAuth(request, apiKey);
        using var result = await RequestJsonAsync(request, cancellationToken,
            TimeSpan.FromSeconds(20), 16384);
        if (!RequireHubBoolean(result.RootElement, "removed"))
            throw new JsonException("Credential removal was not confirmed.");
        return ParseHubStatus(result.RootElement.GetProperty("status"));
    }

    public async Task<IReadOnlyList<HuggingFaceModel>> SearchHuggingFaceAsync(
        string query, string task, string? apiKey, CancellationToken cancellationToken)
    {
        query = query.Trim();
        if (query.Length is < 1 or > 200
            || task is not ("text-generation" or "embeddings" or "image-text-to-text"))
            throw new ArgumentException("Enter a valid search term and task.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        string url = origin + "/v1/models/search-hf?query="
            + Uri.EscapeDataString(query) + "&task=" + Uri.EscapeDataString(task)
            + "&limit=20";
        using var response = await GetJsonAsync(url, apiKey,
            cancellationToken, TimeSpan.FromSeconds(20));
        if (response.RootElement.ValueKind != JsonValueKind.Array
            || response.RootElement.GetArrayLength() > 20)
            throw new JsonException("Invalid Hugging Face search response.");
        var results = new List<HuggingFaceModel>();
        foreach (JsonElement entry in response.RootElement.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object)
                throw new JsonException("Invalid Hugging Face model entry.");
            string id = ReadString(entry, "id");
            if (id.Length > 240 || !HuggingFaceId.IsMatch(id))
                continue;
            string suggested = ReadString(entry, "suggested_local_id");
            if (suggested.Length is < 1 or > 128
                || !LocalModelId.IsMatch(suggested))
                throw new JsonException("Invalid local model ID suggestion.");
            string backend = ReadString(entry, "backend");
            if (backend is not ("openvino-genai" or "openvino-embeddings"
                or "openvino-vlm" or "openvino-rerank"))
                throw new JsonException("Unexpected model backend.");
            string kind = SafeHubLabel(ReadString(entry, "pipeline_tag"), 80);
            static long Count(JsonElement row, string field)
            {
                if (!row.TryGetProperty(field, out var value)
                    || value.ValueKind != JsonValueKind.Number
                    || !value.TryGetInt64(out long count) || count < 0)
                    throw new JsonException("Invalid Hugging Face model count.");
                return count;
            }
            results.Add(new HuggingFaceModel(id, suggested, backend, kind,
                Count(entry, "downloads"), Count(entry, "likes")));
        }
        return results;
    }
    private static readonly Regex LocalModelId = new(
        @"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        RegexOptions.CultureInvariant);

    /// <summary>
    /// Register a remote discovery candidate using the existing conversion queue.
    /// Recheck device and catalog immediately before mutation. Do not retry after
    /// a timeout: the engine might have registered and queued successfully.
    /// </summary>
    public async Task<string> QueueHuggingFaceModelAsync(
        HuggingFaceDownloadRequest selection, string? apiKey,
        CancellationToken cancellationToken)
    {
        if (!LocalModelId.IsMatch(selection.LocalId)
            || selection.LocalId.Length > 128
            || selection.Name.Length is < 1 or > 160
            || string.IsNullOrWhiteSpace(selection.Name)
            || selection.Name.Any(char.IsControl)
            || selection.SourceModel.Length > 240
            || !HuggingFaceId.IsMatch(selection.SourceModel)
            || selection.Backend is not ("openvino-genai" or "openvino-embeddings"
                or "openvino-vlm" or "openvino-rerank")
            || selection.WeightFormat is not ("int4" or "int8" or "fp16")
            || selection.Device is not ("CPU" or "GPU" or "NPU" or "AUTO")
            || !selection.HasValidQuantization
            || selection.Description is { } description
                && (description.Length > 500 || description.Any(char.IsControl))
            || selection.ContextTokens is < 128 or > 262144
            || (selection.Backend is "openvino-embeddings" or "openvino-rerank"
                ? selection.OutputTokens != 0
                : selection.OutputTokens < 1
                    || selection.OutputTokens >= selection.ContextTokens))
            throw new ArgumentException("Review the model ID, device and conversion settings.");

        DeviceInventory devices = await GetDevicesAsync(apiKey, cancellationToken);
        if (selection.Device != "AUTO" && !devices.Available.Any(found =>
            found == selection.Device
            || found.StartsWith(selection.Device + ".", StringComparison.Ordinal)))
            throw new EngineRequestException(
                "The selected device is no longer available. Scan hardware again.");

        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var catalog = await GetJsonAsync(origin + "/v1/models",
            apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        if (catalog.RootElement.ValueKind != JsonValueKind.Object
            || !catalog.RootElement.TryGetProperty("data", out var entries)
            || entries.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid current model catalog.");

        // Existing IDs are never overwritten or silently requeued. After a
        // timeout, refresh the Model library rather than replaying the POST.
        if (entries.EnumerateArray().Any(entry =>
            ReadString(entry, "id") == selection.LocalId))
            throw new EngineRequestException(
                "This local model ID is already registered. Use the Model library.");

        using var response = await PostJsonAsync(
            origin + "/v1/models/download-custom",
            JsonSerializer.Serialize(new
            {
                model_id = selection.LocalId,
                name = selection.Name,
                source_model = selection.SourceModel,
                backend = selection.Backend,
                weight_format = selection.WeightFormat,
                group_size = selection.GroupSize,
                ratio = selection.Ratio,
                sym = selection.Symmetric,
                recommended_device = selection.Device,
                max_context_len = selection.ContextTokens,
                max_output_tokens = selection.OutputTokens,
                load_after = selection.LoadAfter,
                trust_remote_code = false,
                description = string.IsNullOrWhiteSpace(selection.Description)
                    ? "Hugging Face model registered from native InferBridge."
                    : selection.Description.Trim(),
            }), apiKey, cancellationToken, TimeSpan.FromSeconds(65), MaximumJsonBytes);

        JsonElement result = response.RootElement;
        if (result.ValueKind != JsonValueKind.Object
            || !result.TryGetProperty("model", out var model)
            || ReadString(model, "id") != selection.LocalId
            || ReadString(model, "source_model") != selection.SourceModel
            || ReadString(model, "backend") != selection.Backend
            || ReadString(result, "status") is not ("queued_convert" or "converting"
                or "queued" or "loading" or "loaded" or "ready_to_load"))
            throw new JsonException(
                "The engine did not confirm model registration. Refresh the library.");

        return "Engine accepted " + selection.LocalId
            + " for download/conversion"
            + (selection.LoadAfter ? " and loading." : ".")
            + " View actual progress in the Model library.";
    }

}
