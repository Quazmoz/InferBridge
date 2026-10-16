using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record HuggingFaceAccount(
    bool Configured, bool Removable, string State, string Username, string Source);

internal sealed record HuggingFaceModel(
    string Id, string Backend, string Task, long Downloads, long Likes)
{
    // Search metadata is not hardware compatibility or gated-license evidence.
    public string ModelUrl => "https://huggingface.co/" + Id;
}

internal sealed class HuggingFaceCredentialException(string message) : Exception(message);

internal sealed partial class BackendClient
{
    private static readonly Regex HuggingFaceId = new(
        @"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$",
        RegexOptions.CultureInvariant);

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
            string backend = ReadString(entry, "backend");
            if (backend is not ("openvino-genai" or "openvino-embeddings"
                or "openvino-vlm"))
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
            results.Add(new HuggingFaceModel(id, backend, kind,
                Count(entry, "downloads"), Count(entry, "likes")));
        }
        return results;
    }
}
