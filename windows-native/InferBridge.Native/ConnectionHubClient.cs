using System.Net.Http;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record LocalApiModel(string Id, string Name, bool Busy,
    int? ContextTokens = null, int? MaxOutputTokens = null);

internal sealed record LocalApiMetadata(
    string BaseUrl, int Port, string ListenHost, bool AuthenticationRequired,
    string AuthenticationLabel, string KeyPlaceholder, string LanLabel,
    string LanDetail, IReadOnlyList<LocalApiModel> UsableModels, string SessionNonce)
{
    // JSON string literals are also valid Python string literals. In particular,
    // untrusted model IDs cannot escape the generated source string.
    public string PythonExample(string modelId) =>
        "from openai import OpenAI\n\n" +
        "client = OpenAI(base_url=" + JsonSerializer.Serialize(BaseUrl) +
        ", api_key=" + JsonSerializer.Serialize(KeyPlaceholder) + ")\n" +
        "response = client.chat.completions.create(\n" +
        "    model=" + JsonSerializer.Serialize(modelId) + ",\n" +
        "    messages=[{\"role\": \"user\", \"content\": \"Hello\"}],\n" +
        ")\nprint(response.choices[0].message.content)\n";

    public string WindowsCurlExample() =>
        "curl.exe -sS \"" + BaseUrl + "/models\"" +
        (AuthenticationRequired
            ? " -H \"Authorization: Bearer YOUR_INFERBRIDGE_API_KEY\""
            : "") + "\n";
}

internal sealed record LocalApiTest(string Id, string Label, string Status, int? DurationMs, string Detail);
internal sealed record LocalApiTestReport(string? ModelId, IReadOnlyList<LocalApiTest> Tests);

internal sealed partial class BackendClient
{
    private static readonly HashSet<string> HubTestIds =
    ["models", "non_streaming", "streaming", "cancellation", "authentication"];

    private static string HubString(JsonElement element, string property, int maximum = 256)
    {
        string value = ReadString(element, property);
        if (value.Length > maximum || value.Any(char.IsControl))
            throw new JsonException("Invalid Connection Hub metadata.");
        return value;
    }

    // Older engines omit token limits; tool setup then reports them as unknown.
    private static int? HubOptionalTokens(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null)
            return null;
        if (!value.TryGetInt32(out int tokens) || tokens is < 1 or > 10_000_000)
            throw new JsonException("Invalid Connection Hub token limit.");
        return tokens;
    }

    private static bool HubBoolean(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value)
            || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new JsonException("Invalid Connection Hub state.");
        return value.GetBoolean();
    }

    public async Task<LocalApiMetadata> GetLocalApiMetadataAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        var session = await InspectAsync(cancellationToken, apiKey);
        if (!session.Connected)
            throw new IOException(session.Message);
        using var request = new HttpRequestMessage(
            HttpMethod.Get, session.Origin + "/internal/connection-hub");
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        using var document = await RequestJsonAsync(
            request, cancellationToken, TimeSpan.FromSeconds(8), MaximumJsonBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("port", out var portElement)
            || !portElement.TryGetInt32(out int port)
            || port is < 1 or > 65535
            || port != new Uri(session.Origin).Port)
            throw new JsonException("Connection Hub returned a different port.");

        string baseUrl = HubString(root, "base_url", 128);
        if (baseUrl != session.Origin + "/v1")
            throw new JsonException("Connection Hub returned an unverified Base URL.");
        var auth = root.GetProperty("authentication");
        var lan = root.GetProperty("lan");
        bool required = HubBoolean(auth, "required");
        string placeholder = HubString(auth, "api_key_placeholder", 64);
        // Never use arbitrary server text or a configured secret in snippets.
        if (placeholder != (required ? "YOUR_INFERBRIDGE_API_KEY" : "not-required"))
            throw new JsonException("Unexpected Connection Hub key placeholder.");
        var usableIds = root.GetProperty("usable_model_ids");
        var models = root.GetProperty("models");
        if (usableIds.ValueKind != JsonValueKind.Array || models.ValueKind != JsonValueKind.Array
            || usableIds.GetArrayLength() > 2000 || models.GetArrayLength() > 2000)
            throw new JsonException("Invalid Connection Hub model inventory.");
        var usable = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in usableIds.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String)
                throw new JsonException("Invalid usable model ID.");
            string id = item.GetString() ?? "";
            if (id.Length is < 1 or > 128 || id.Any(char.IsControl) || !usable.Add(id))
                throw new JsonException("Invalid usable model ID.");
        }
        var available = new List<LocalApiModel>();
        foreach (var model in models.EnumerateArray())
        {
            if (model.ValueKind != JsonValueKind.Object) throw new JsonException("Invalid model.");
            string id = HubString(model, "id", 128);
            if (!usable.Contains(id)) continue;
            if (!HubBoolean(model, "loaded") || !HubBoolean(model, "generation_capable"))
                throw new JsonException("Unloaded model reported as usable.");
            int? context = HubOptionalTokens(model, "context_tokens");
            int? output = HubOptionalTokens(model, "max_output_tokens");
            if (output is int o && (context is not int c || o >= c)) output = null;
            available.Add(new LocalApiModel(id, HubString(model, "name", 256),
                HubBoolean(model, "busy"), context, output));
        }
        if (available.Count != usable.Count)
            throw new JsonException("Connection Hub model inventory is inconsistent.");

        var after = await InspectAsync(cancellationToken, apiKey);
        if (!after.Connected || after.SessionNonce != session.SessionNonce
            || after.Origin != session.Origin)
            throw new IOException("The local engine restarted. Refresh connection details.");
        return new LocalApiMetadata(baseUrl, port, HubString(root, "listen_host", 128),
            required, HubString(auth, "label", 128), placeholder,
            HubString(lan, "label", 128), HubString(lan, "detail", 512),
            available, session.SessionNonce);
    }

    public async Task<LocalApiTestReport> RunLocalApiSelfTestAsync(
        LocalApiMetadata metadata, string? selectedModel, string? apiKey,
        CancellationToken cancellationToken)
    {
        var fresh = await GetLocalApiMetadataAsync(apiKey, cancellationToken);
        if (fresh.SessionNonce != metadata.SessionNonce)
            throw new IOException("The engine session changed. Refresh before testing.");
        if (fresh.AuthenticationRequired && string.IsNullOrWhiteSpace(apiKey))
            throw new UnauthorizedAccessException("Enter your API key on Engine & devices before testing.");
        if (selectedModel is not null && !fresh.UsableModels.Any(m => m.Id == selectedModel))
            throw new InvalidOperationException("The selected model is no longer loaded. Refresh the Hub.");
        // This calls the existing backend-owned synthetic test coordinator once.
        // A timeout/cancel is uncertain, so the native UI must not auto-retry it.
        using var request = new HttpRequestMessage(
            HttpMethod.Post, fresh.BaseUrl[..^3] + "/internal/connection-hub/self-test");
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        request.Content = new StringContent(
            JsonSerializer.Serialize(new { model_id = selectedModel }),
            System.Text.Encoding.UTF8, "application/json");
        using var document = await RequestJsonAsync(
            request, cancellationToken, TimeSpan.FromSeconds(150), MaximumJsonBytes);
        var root = document.RootElement;
        string? reportedModel = root.GetProperty("model_id").ValueKind == JsonValueKind.Null
            ? null : HubString(root, "model_id", 128);
        if (selectedModel != reportedModel && selectedModel is not null)
            throw new JsonException("Self-test returned a different model.");
        var results = root.GetProperty("tests");
        if (results.ValueKind != JsonValueKind.Array || results.GetArrayLength() != HubTestIds.Count)
            throw new JsonException("The Connection Hub returned incomplete test results.");
        var found = new HashSet<string>(StringComparer.Ordinal);
        var tests = new List<LocalApiTest>();
        foreach (var item in results.EnumerateArray())
        {
            string id = HubString(item, "id", 32);
            string status = HubString(item, "status", 16);
            if (!HubTestIds.Contains(id) || !found.Add(id)
                || status is not ("passed" or "failed" or "skipped"))
                throw new JsonException("Invalid Connection Hub test result.");
            int? duration = null;
            if (item.TryGetProperty("duration_ms", out var elapsed)
                && elapsed.ValueKind != JsonValueKind.Null)
            {
                if (!elapsed.TryGetInt32(out int ms) || ms is < 0 or > 180000)
                    throw new JsonException("Invalid self-test duration.");
                duration = ms;
            }
            tests.Add(new LocalApiTest(id, HubString(item, "label", 80), status,
                duration, HubString(item, "detail", 512)));
        }
        var after = await InspectAsync(cancellationToken, apiKey);
        if (!after.Connected || after.SessionNonce != fresh.SessionNonce)
            throw new IOException("Engine changed during self-test. Results are stale.");
        return new LocalApiTestReport(reportedModel, tests);
    }
}

internal sealed record NetworkSettingsStatus(
    string ActiveBindHost, bool LanSettingEnabled, bool LanActive, string? LanBlockedReason,
    string LocalEndpoint, IReadOnlyList<string> LanEndpoints, bool ApiKeyConfigured,
    string? ApiKeySource, string CorsOrigins, bool HostEnvironmentOverride,
    bool CorsEnvironmentOverride, bool ApiKeyEnvironmentOverride, bool WildcardCors,
    bool RestartRequired, IReadOnlyList<string> Warnings);

/// <summary>The web Local API network form, field for field.</summary>
internal sealed record NetworkSettingsUpdate(
    bool AllowLan, string CorsOrigins, string? ApiKey, bool GenerateApiKey,
    bool RemoveStoredApiKey, bool AcknowledgeWildcardCors)
{
    // Client-side copy of the engine's own rules; the engine re-validates and
    // remains authoritative (LAN requires a key, '*' requires a key and consent).
    public void Validate()
    {
        if (CorsOrigins is null || CorsOrigins.Length > 2048 || CorsOrigins.Any(char.IsControl))
            throw new ArgumentException("Browser origins must be readable text up to 2,048 characters.");
        if (ApiKey is { } key && (key.Length is < 1 or > 512 || key.Any(ch => char.IsControl(ch) || char.IsWhiteSpace(ch))))
            throw new ArgumentException("API keys must be 1–512 characters without spaces.");
        if (ApiKey is not null && GenerateApiKey)
            throw new ArgumentException("Enter an API key or generate one, not both.");
        if (RemoveStoredApiKey && (ApiKey is not null || GenerateApiKey))
            throw new ArgumentException("Remove the stored API key or replace it, not both at once.");
        if (CorsOrigins.Trim() == "*" && !AcknowledgeWildcardCors)
            throw new ArgumentException("Confirm the wildcard CORS warning before applying '*'.");
    }
}

internal sealed record NetworkSettingsResult(
    NetworkSettingsStatus Status, string? GeneratedApiKey, string Message);

internal sealed partial class BackendClient
{
    private static string? HubOptionalString(JsonElement element, string property, int maximum)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null)
            return null;
        if (value.ValueKind != JsonValueKind.String)
            throw new JsonException("Invalid network settings field.");
        string text = value.GetString() ?? "";
        if (text.Length > maximum || text.Any(char.IsControl))
            throw new JsonException("Invalid network settings field.");
        return text;
    }

    private static IReadOnlyList<string> HubStringList(JsonElement element, string property, int count, int maximum)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind != JsonValueKind.Array
            || value.GetArrayLength() > count)
            throw new JsonException("Invalid network settings list.");
        var items = new List<string>();
        foreach (var item in value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String) throw new JsonException("Invalid network settings list.");
            string text = item.GetString() ?? "";
            if (text.Length > maximum || text.Any(char.IsControl))
                throw new JsonException("Invalid network settings list.");
            items.Add(text);
        }
        return items;
    }

    private static NetworkSettingsStatus ParseNetworkStatus(JsonElement root, string origin)
    {
        if (root.ValueKind != JsonValueKind.Object) throw new JsonException("Invalid network settings.");
        string local = HubString(root, "local_endpoint", 128);
        // The engine reports its own loopback endpoint; it must be this verified session.
        if (local != origin + "/v1") throw new JsonException("Network settings describe a different engine.");
        var lan = HubStringList(root, "lan_endpoints", 16, 128);
        if (lan.Any(url => !url.StartsWith("http://", StringComparison.Ordinal)))
            throw new JsonException("Invalid LAN endpoint.");
        string? source = HubOptionalString(root, "api_key_source", 32);
        if (source is not (null or "secure_store" or "environment" or "settings"))
            throw new JsonException("Invalid API key source.");
        return new NetworkSettingsStatus(
            HubString(root, "active_bind_host", 64), HubBoolean(root, "lan_setting_enabled"),
            HubBoolean(root, "lan_active"), HubOptionalString(root, "lan_blocked_reason", 512),
            local, lan, HubBoolean(root, "api_key_configured"), source,
            HubString(root, "cors_origins", 2048), HubBoolean(root, "host_environment_override"),
            HubBoolean(root, "cors_environment_override"), HubBoolean(root, "api_key_environment_override"),
            HubBoolean(root, "wildcard_cors"), HubBoolean(root, "restart_required"),
            HubStringList(root, "warnings", 16, 512));
    }

    /// <summary>
    /// Read the same loopback-only, local-UI-marked endpoint the web network
    /// panel uses. An engine that requires authentication must first accept the
    /// session key, so native never reaches listener controls the web would not.
    /// </summary>
    public async Task<NetworkSettingsStatus> GetNetworkSettingsAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var request = new HttpRequestMessage(HttpMethod.Get, origin + "/internal/desktop-network");
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        using var document = await RequestJsonAsync(request, cancellationToken, TimeSpan.FromSeconds(8), MaximumJsonBytes);
        return ParseNetworkStatus(document.RootElement, origin);
    }

    /// <summary>
    /// Save listener settings through the existing protected endpoint. A lost
    /// response is uncertain (the engine may have saved and rotated a key), so the
    /// caller must refresh instead of replaying the POST.
    /// </summary>
    public async Task<NetworkSettingsResult> UpdateNetworkSettingsAsync(
        NetworkSettingsUpdate update, string? apiKey, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(update);
        update.Validate();
        var session = await InspectAsync(cancellationToken, apiKey);
        if (!session.Connected) throw new IOException(session.Message);
        if (session.RequiresAuthentication)
            throw new UnauthorizedAccessException("The engine requires a valid API key.");
        using var request = new HttpRequestMessage(HttpMethod.Post, session.Origin + "/internal/desktop-network");
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        request.Content = new StringContent(JsonSerializer.Serialize(new
        {
            allow_lan = update.AllowLan,
            cors_origins = update.CorsOrigins.Trim(),
            api_key = update.ApiKey,
            generate_api_key = update.GenerateApiKey,
            remove_stored_api_key = update.RemoveStoredApiKey,
            acknowledge_wildcard_cors = update.AcknowledgeWildcardCors,
        }), System.Text.Encoding.UTF8, "application/json");
        using var document = await RequestJsonAsync(request, cancellationToken, TimeSpan.FromSeconds(20), MaximumJsonBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("status", out var status))
            throw new JsonException("The engine did not confirm the network settings.");
        string? generated = HubOptionalString(root, "generated_api_key", 512);
        if (update.GenerateApiKey != (generated is not null)
            || generated is { Length: < 16 } || generated?.Any(char.IsWhiteSpace) == true)
            throw new JsonException("The engine returned an unexpected API key result.");
        var parsed = ParseNetworkStatus(status, session.Origin);
        var after = await InspectAsync(cancellationToken, apiKey);
        if (after.SessionNonce != session.SessionNonce)
            throw new IOException("The engine restarted while saving. Refresh network settings.");
        return new NetworkSettingsResult(parsed, generated, HubString(root, "message", 300));
    }

    /// <summary>Same protected restart the web Operations panel requests.</summary>
    public async Task<string> RestartEngineAsync(string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + "/v1/desktop/operations/restart-server", "{}",
            apiKey, cancellationToken, TimeSpan.FromSeconds(15), MaximumJsonBytes);
        if (ReadString(document.RootElement, "status") != "restarting")
            throw new JsonException("The engine did not confirm the restart request.");
        string message = ReadString(document.RootElement, "message");
        return message.Length is > 0 and <= 300 && !message.Any(char.IsControl)
            ? message : "The tray controller will restart the local engine.";
    }
}
