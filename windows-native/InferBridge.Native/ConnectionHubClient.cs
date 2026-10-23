using System.Net.Http;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record LocalApiModel(string Id, string Name, bool Busy);

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
            available.Add(new LocalApiModel(id, HubString(model, "name", 256),
                HubBoolean(model, "busy")));
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
            throw new UnauthorizedAccessException("Enter your API key on Engine before testing.");
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
