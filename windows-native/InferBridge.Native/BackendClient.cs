using System.Net;
using System.Net.Http;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record ModelInfo(string Id, string Name, string Status);

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
/// Read-only client for the authoritative local engine. Never probes arbitrary hosts,
/// follows redirects, or treats a listener at a reused port as the original server.
/// </summary>
internal sealed class BackendClient : IDisposable
{
    private const int MaximumJsonBytes = 256 * 1024;
    private const int MaximumMetadataBytes = 16 * 1024;
    private readonly HttpClient _http = new(new SocketsHttpHandler
    {
        AllowAutoRedirect = false,
        UseProxy = false,
    })
    {
        Timeout = TimeSpan.FromSeconds(4),
    };

    private readonly string _metadataFile;

    public BackendClient()
    {
        // Use the same non-virtualized Store root as app/paths.py. An
        // explicit development override avoids coupling this client to dev builds.
        string root = Environment.GetEnvironmentVariable("INFERBRIDGE_NATIVE_DATA_DIR")
            ?? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "InferBridgeStoreData");
        _metadataFile = Path.Combine(Path.GetFullPath(root), "desktop-instance.json");
    }

    public async Task<BackendSnapshot> InspectAsync(CancellationToken cancellationToken)
    {
        if (!File.Exists(_metadataFile))
            return BackendSnapshot.Offline(
                "No local engine session was found. Start the engine to connect.");

        try
        {
            byte[] metadataBytes;
            using (var stream = new FileStream(
                _metadataFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite,
                bufferSize: 4096, FileOptions.Asynchronous | FileOptions.SequentialScan))
            {
                if (stream.Length is <= 0 or > MaximumMetadataBytes)
                    return BackendSnapshot.Offline("Invalid local engine session metadata.");

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
                || !portElement.TryGetInt32(out int port)
                || port is < 1 or > 65535)
                return BackendSnapshot.Offline("Invalid local engine port.");

            string nonce = ReadString(root, "nonce");
            if (nonce.Length is < 8 or > 512)
                return BackendSnapshot.Offline("Invalid local engine session identity.");

            string origin = $"http://127.0.0.1:{port}";
            using var identity = await GetJsonAsync(origin + "/desktop/instance", cancellationToken);
            if (ReadString(identity.RootElement, "instance_nonce") != nonce
                || !identity.RootElement.TryGetProperty("port", out var claimedPort)
                || !claimedPort.TryGetInt32(out int reportedPort)
                || reportedPort != port)
                return BackendSnapshot.Offline(
                    "The local engine changed ownership. Reopen InferBridge.");

            using var health = await GetJsonAsync(origin + "/health/live", cancellationToken);
            if (ReadString(health.RootElement, "status") != "ok")
                return BackendSnapshot.Offline("The local engine has not reached liveness.");

            try
            {
                using var modelsPayload = await GetJsonAsync(origin + "/v1/models", cancellationToken);
                var models = new List<ModelInfo>();
                if (modelsPayload.RootElement.TryGetProperty("data", out var data)
                    && data.ValueKind == JsonValueKind.Array)
                {
                    foreach (var item in data.EnumerateArray())
                    {
                        if (models.Count >= 2000) break;
                        string id = ReadString(item, "id");
                        if (id.Length is < 1 or > 512) continue;
                        string name = ReadString(item, "name");
                        string status = ReadString(item, "status");
                        models.Add(new ModelInfo(
                            id, string.IsNullOrWhiteSpace(name) ? id : name,
                            string.IsNullOrWhiteSpace(status) ? "unknown" : status));
                    }
                }

                return new BackendSnapshot(
                    true, "Connected to the local InferBridge engine.", origin,
                    models, false);
            }
            catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.Unauthorized)
            {
                return new BackendSnapshot(
                    true, "Engine connected; model list requires API authentication.",
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
                "Could not verify the local engine. Check the tray or start it again.");
        }
    }

    private async Task<JsonDocument> GetJsonAsync(string url, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync(
            url, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength is > MaximumJsonBytes)
            throw new IOException("Response exceeds local metadata limit.");

        await using var source = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var buffer = new MemoryStream();
        byte[] chunk = new byte[8192];
        while (true)
        {
            int count = await source.ReadAsync(chunk, cancellationToken);
            if (count == 0) break;
            if (buffer.Length + count > MaximumJsonBytes)
                throw new IOException("Response exceeds local metadata limit.");
            buffer.Write(chunk, 0, count);
        }

        return JsonDocument.Parse(buffer.ToArray(), new JsonDocumentOptions { MaxDepth = 20 });
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
