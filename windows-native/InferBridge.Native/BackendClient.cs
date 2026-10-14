using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record ModelInfo(string Id, string Name, string Status)
{
    public bool CanLoad => Status == "ready_to_load";
    public bool CanUnload => Status == "loaded";
}

internal sealed record ChatTurn(string Role, string Content);

internal sealed record BackendSnapshot(
    bool Connected,
    string Message,
    string Origin,
    IReadOnlyList<ModelInfo> Models,
    bool RequiresAuthentication)
{
    public static BackendSnapshot Offline(string message) =>
        new(false, message, string.Empty, Array.Empty<ModelInfo>(), false);
}

/// <summary>
/// Session-only client for the authoritative loopback engine. No proxy, redirects,
/// alternate hostname, persistent key, or private tray-control credential.
/// </summary>
internal sealed class BackendClient : IDisposable
{
    private const int MaximumJsonBytes = 256 * 1024;
    private const int MaximumChatBytes = 1024 * 1024;
    private const int MaximumMetadataBytes = 16 * 1024;
    private readonly HttpClient _http;
    private readonly string? _metadataFile;

    public BackendClient() : this(new SocketsHttpHandler
    {
        AllowAutoRedirect = false,
        UseProxy = false,
    }, null)
    {
    }

    // The same production request pipeline is exercised with an in-memory
    // response handler and disposable metadata file in native contract tests.
    internal BackendClient(HttpMessageHandler handler, string? metadataFile)
    {
        _http = new HttpClient(handler, disposeHandler: true)
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };
        if (metadataFile is not null)
        {
            _metadataFile = metadataFile;
            return;
        }
        try
        {
            _metadataFile = NativeDataPaths.ResolveMetadataFile();
        }
        catch (Exception error) when (error is ArgumentException or IOException
            or NotSupportedException)
        {
            // A malformed environment override must not crash the entire WinUI
            // window or silently attach to a different engine's data directory.
            _metadataFile = null;
        }
    }

    public async Task<BackendSnapshot> InspectAsync(
        CancellationToken cancellationToken, string? apiKey = null)
    {
        if (_metadataFile is null)
            return BackendSnapshot.Offline(
                "Invalid native data directory. Check INFERBRIDGE_NATIVE_DATA_DIR and OV_LLM_DATA_DIR.");
        if (!File.Exists(_metadataFile))
            return BackendSnapshot.Offline("No local engine session found. Start the engine.");

        try
        {
            byte[] metadataBytes;
            using (var stream = new FileStream(
                _metadataFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite,
                bufferSize: 4096, FileOptions.Asynchronous | FileOptions.SequentialScan))
            {
                if (stream.Length is <= 0 or > MaximumMetadataBytes)
                    return BackendSnapshot.Offline("Invalid engine session metadata.");
                metadataBytes = new byte[checked((int)stream.Length)];
                await stream.ReadExactlyAsync(metadataBytes, cancellationToken);
            }

            using var metadata = JsonDocument.Parse(metadataBytes, new JsonDocumentOptions
            {
                MaxDepth = 8,
            });
            var root = metadata.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("port", out var portElement)
                || portElement.ValueKind != JsonValueKind.Number
                || !portElement.TryGetInt32(out int port)
                || port is < 1 or > 65535)
                return BackendSnapshot.Offline("Invalid local engine port.");

            string nonce = ReadString(root, "nonce");
            if (nonce.Length is < 8 or > 512)
                return BackendSnapshot.Offline("Invalid engine session identity.");

            string origin = "http://127.0.0.1:" + port;
            using var identity = await GetJsonAsync(
                origin + "/desktop/instance", null, cancellationToken, TimeSpan.FromSeconds(4));
            if (ReadString(identity.RootElement, "instance_nonce") != nonce
                || !identity.RootElement.TryGetProperty("port", out var claimedPort)
                || claimedPort.ValueKind != JsonValueKind.Number
                || !claimedPort.TryGetInt32(out int reportedPort)
                || reportedPort != port)
                return BackendSnapshot.Offline("The local engine changed ownership. Reconnect.");

            using var health = await GetJsonAsync(
                origin + "/health/live", null, cancellationToken, TimeSpan.FromSeconds(4));
            if (ReadString(health.RootElement, "status") != "ok")
                return BackendSnapshot.Offline("The local engine is not live.");

            try
            {
                using var modelsPayload = await GetJsonAsync(
                    origin + "/v1/models", apiKey, cancellationToken, TimeSpan.FromSeconds(4));
                if (modelsPayload.RootElement.ValueKind != JsonValueKind.Object
                    || !modelsPayload.RootElement.TryGetProperty("data", out var data)
                    || data.ValueKind != JsonValueKind.Array)
                    throw new JsonException("Invalid model inventory schema.");

                var models = new List<ModelInfo>();
                foreach (var item in data.EnumerateArray())
                {
                    if (models.Count >= 2000) break;
                    string id = ReadString(item, "id");
                    if (id.Length is < 1 or > 512) continue;
                    string name = ReadString(item, "name");
                    string status = ReadString(item, "status");
                    models.Add(new ModelInfo(id,
                        string.IsNullOrWhiteSpace(name) ? id : name,
                        status.Length is > 0 and <= 64 ? status : "unknown"));
                }

                return new BackendSnapshot(
                    true, "Connected to the local InferBridge engine.", origin,
                    models, false);
            }
            catch (UnauthorizedAccessException)
            {
                return new BackendSnapshot(
                    true, "Engine verified. Enter the configured API key to view models and chat.",
                    origin, Array.Empty<ModelInfo>(), true);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception error) when (error is IOException
            or UnauthorizedAccessException
            or JsonException
            or HttpRequestException
            or TaskCanceledException
            or ArgumentException)
        {
            return BackendSnapshot.Offline(
                "Could not verify the local engine. Check the tray and refresh.");
        }
    }

    public async Task<string> CompleteChatAsync(
        string modelId, IReadOnlyList<ChatTurn> turns, string? apiKey,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(modelId) || modelId.Length > 512
            || turns.Count is < 1 or > 20
            || turns.Any(t => (t.Role != "user" && t.Role != "assistant")
                || t.Content.Length is < 1 or > 8000)
            || turns.Sum(t => t.Content.Length) > 32000)
            throw new ArgumentException("Chat context is empty or exceeds the native client limit.");

        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        var messages = turns.Select(t => new { role = t.Role, content = t.Content }).ToArray();
        var body = JsonSerializer.Serialize(new
        {
            model = modelId,
            messages,
            stream = false,
            max_tokens = 512,
        });
        using var document = await PostJsonAsync(
            origin + "/v1/chat/completions", body, apiKey,
            cancellationToken, TimeSpan.FromSeconds(120), MaximumChatBytes);

        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0
            || !choices[0].TryGetProperty("message", out var message)
            || message.ValueKind != JsonValueKind.Object
            || !message.TryGetProperty("content", out var content)
            || content.ValueKind != JsonValueKind.String)
            throw new JsonException("The engine returned an invalid chat response.");

        string answer = content.GetString() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(answer))
            throw new JsonException("The engine returned an empty answer.");
        return answer;
    }

    public async Task<string> ChangeModelAsync(
        ModelInfo model, bool load, string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        // Reconcile before mutation; do not act on a stale UI selection.
        using var modelsPayload = await GetJsonAsync(
            origin + "/v1/models", apiKey, cancellationToken, TimeSpan.FromSeconds(4));
        if (!modelsPayload.RootElement.TryGetProperty("data", out var items)
            || items.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid current model inventory.");

        bool permitted = items.EnumerateArray().Any(item =>
            ReadString(item, "id") == model.Id
            && ReadString(item, "status") == (load ? "ready_to_load" : "loaded"));
        if (!permitted)
            throw new InvalidOperationException(
                "The model state changed. Refresh the library before trying again.");

        string path = load ? "/v1/models/load" : "/v1/models/unload";
        using var document = await PostJsonAsync(
            origin + path, JsonSerializer.Serialize(new { model = model.Id }),
            apiKey, cancellationToken, TimeSpan.FromSeconds(20), MaximumJsonBytes);
        string status = ReadString(document.RootElement, "status");
        if (status.Length == 0)
            throw new JsonException("The engine did not confirm the model operation.");
        return load
            ? "Load requested. Refresh to check actual model progress."
            : "Model unload accepted. Refresh to confirm its state.";
    }

    private async Task<string> RequireVerifiedOriginAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        var status = await InspectAsync(cancellationToken, apiKey);
        if (!status.Connected)
            throw new IOException(status.Message);
        if (status.RequiresAuthentication)
            throw new UnauthorizedAccessException("The engine requires a valid API key.");
        return status.Origin;
    }

    private async Task<JsonDocument> GetJsonAsync(
        string url, string? apiKey, CancellationToken cancellationToken, TimeSpan deadline)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        SetAuth(request, apiKey);
        return await RequestJsonAsync(request, cancellationToken, deadline, MaximumJsonBytes);
    }

    private async Task<JsonDocument> PostJsonAsync(
        string url, string body, string? apiKey, CancellationToken cancellationToken,
        TimeSpan deadline, int maximumBytes)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, url);
        request.Content = new StringContent(body, Encoding.UTF8, "application/json");
        SetAuth(request, apiKey);
        return await RequestJsonAsync(request, cancellationToken, deadline, maximumBytes);
    }

    private static void SetAuth(HttpRequestMessage request, string? apiKey)
    {
        if (!string.IsNullOrWhiteSpace(apiKey))
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
    }

    private async Task<JsonDocument> RequestJsonAsync(
        HttpRequestMessage request, CancellationToken cancellationToken,
        TimeSpan deadline, int maximumBytes)
    {
        using var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        limit.CancelAfter(deadline);
        using var response = await _http.SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead, limit.Token);
        if (response.StatusCode == HttpStatusCode.Unauthorized
            || response.StatusCode == HttpStatusCode.Forbidden)
            throw new UnauthorizedAccessException("The engine rejected the API key.");
        if (response.StatusCode == HttpStatusCode.Conflict)
            throw new InvalidOperationException("The engine is busy or the model state changed.");
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength is > 0
            && response.Content.Headers.ContentLength > maximumBytes)
            throw new IOException("The engine response is too large.");

        await using var source = await response.Content.ReadAsStreamAsync(limit.Token);
        using var buffer = new MemoryStream();
        byte[] chunk = new byte[8192];
        while (true)
        {
            int count = await source.ReadAsync(chunk, limit.Token);
            if (count == 0) break;
            if (buffer.Length + count > maximumBytes)
                throw new IOException("The engine response is too large.");
            buffer.Write(chunk, 0, count);
        }
        return JsonDocument.Parse(buffer.ToArray(), new JsonDocumentOptions { MaxDepth = 24 });
    }

    private static string ReadString(JsonElement element, string property)
    {
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? string.Empty
            : string.Empty;
    }

    public void Dispose() => _http.Dispose();
}
