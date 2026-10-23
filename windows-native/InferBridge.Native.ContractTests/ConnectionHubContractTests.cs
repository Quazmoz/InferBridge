using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

internal static class ConnectionHubContractTests
{
    private const int Port = 19437;
    private const string Owner = "hub-session-owner-123456";
    private const string Key = "in-memory-only-test-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native Connection Hub: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static HttpResponseMessage Json(string body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private const string Models =
        """{"data":[{"id":"model-a","name":"A","status":"loaded"}]}""";

    private static string Metadata(bool required = true, bool modelLoaded = true,
        int port = Port, string? baseUrl = null) => JsonSerializer.Serialize(new
        {
            runtime_state = "available",
            base_url = baseUrl ?? "http://127.0.0.1:" + Port + "/v1",
            listen_host = "0.0.0.0",
            port,
            authentication = new
            {
                required,
                enabled = required,
                label = required ? "Authentication required" : "Authentication disabled",
                api_key_placeholder = required ? "YOUR_INFERBRIDGE_API_KEY" : "not-required",
            },
            lan = new
            {
                label = "LAN access enabled",
                detail = "Firewall rules still determine reachability.",
            },
            usable_model_ids = modelLoaded ? new[] { "model-a" } : Array.Empty<string>(),
            models = new[]
            {
                new { id = "model-a", name = "A", loaded = modelLoaded,
                    generation_capable = true, busy = false },
            },
        });

    private const string Results = """
        {"model_id":"model-a","tests":[
          {"id":"models","label":"Model listing","status":"passed","duration_ms":15,"detail":"Found models"},
          {"id":"non_streaming","label":"Non-streaming generation","status":"passed","duration_ms":38,"detail":"OK"},
          {"id":"streaming","label":"Streaming generation","status":"passed","duration_ms":20,"detail":"OK"},
          {"id":"cancellation","label":"Cancellation","status":"skipped","duration_ms":null,"detail":"Worker busy"},
          {"id":"authentication","label":"Authentication","status":"passed","duration_ms":9,"detail":"Verified"}
        ]}
        """;

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
            => respond(request, cancellationToken);
    }

    public static async Task RunAsync(string root)
    {
        string directory = Path.Combine(root, "hub");
        Directory.CreateDirectory(directory);
        string metadataFile = Path.Combine(directory, "desktop-instance.json");
        string owner = Owner;
        void WriteMetadata() => File.WriteAllText(metadataFile,
            JsonSerializer.Serialize(new { nonce = owner, port = Port }));
        WriteMetadata();
        bool required = true;
        bool modelLoaded = true;
        int returnedPort = Port;
        string? badBaseUrl = null;
        int postCount = 0;
        int metadataCount = 0;
        bool timeoutAfterPost = false;

        var handler = new Handler(async (request, token) =>
        {
            Check(request.RequestUri?.Host == "127.0.0.1" && request.RequestUri.Port == Port,
                "client left the verified dynamic loopback port");
            string path = request.RequestUri!.AbsolutePath;
            if (path is "/desktop/instance" or "/health/live")
                Check(request.Headers.Authorization is null, "identity probe leaked a key");
            else if (path is "/v1/models" or "/internal/connection-hub/self-test")
                Check(!required || request.Headers.Authorization?.Parameter == Key,
                    "protected call omitted session credential");
            if (path.StartsWith("/internal/connection-hub", StringComparison.Ordinal))
                Check(request.Headers.TryGetValues("X-OV-LLM-UI", out var values)
                    && values.Single() == "1", "native API request omitted local UI marker");
            return path switch
            {
                "/desktop/instance" => Json(JsonSerializer.Serialize(new
                {
                    instance_nonce = owner, port = Port,
                })),
                "/health/live" => Json("""{"status":"ok"}"""),
                "/v1/models" => required && request.Headers.Authorization?.Parameter != Key
                    ? Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized)
                    : Json(Models),
                "/internal/connection-hub" => ObserveMetadata(),
                "/internal/connection-hub/self-test" => await ObserveSelfTest(request, token),
                _ => throw new InvalidOperationException("Unexpected hub route: " + path),
            };
        });

        HttpResponseMessage ObserveMetadata()
        {
            metadataCount++;
            return Json(Metadata(required, modelLoaded, returnedPort, badBaseUrl));
        }

        async Task<HttpResponseMessage> ObserveSelfTest(HttpRequestMessage request, CancellationToken token)
        {
            postCount++;
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            Check(body.RootElement.GetProperty("model_id").GetString() == "model-a",
                "self-test did not use the explicitly selected loaded model");
            if (timeoutAfterPost) throw new TaskCanceledException("Synthetic lost response");
            return Json(Results);
        }

        using var client = new BackendClient(handler, metadataFile);
        LocalApiMetadata hub = await client.GetLocalApiMetadataAsync(Key, CancellationToken.None);
        Check(hub.Port == Port && hub.BaseUrl == "http://127.0.0.1:" + Port + "/v1",
            "dynamic port or API path was guessed");
        Check(hub.AuthenticationRequired && hub.UsableModels.Single().Id == "model-a",
            "metadata omitted required authentication or the usable model");
        Check(!hub.PythonExample("model-a").Contains(Key, StringComparison.Ordinal)
            && !hub.WindowsCurlExample().Contains(Key, StringComparison.Ordinal)
            && hub.WindowsCurlExample().Contains("YOUR_INFERBRIDGE_API_KEY"),
            "copyable examples contained a configured API key");
        string adversarialId = "model-\"injection";
        Check(hub.PythonExample(adversarialId).Contains(
            JsonSerializer.Serialize(adversarialId), StringComparison.Ordinal),
            "untrusted model ID was not encoded as a safe string literal");
        var report = await client.RunLocalApiSelfTestAsync(
            hub, "model-a", Key, CancellationToken.None);
        Check(report.Tests.Count == 5 && report.Tests.Count(t => t.Status == "passed") == 4
            && report.Tests.Single(t => t.Id == "cancellation").Status == "skipped"
            && postCount == 1, "self-test statuses were collapsed or retried");

        // Authentication-disabled mode must not invent a secret-bearing header.
        required = false;
        hub = await client.GetLocalApiMetadataAsync(null, CancellationToken.None);
        Check(!hub.AuthenticationRequired && hub.KeyPlaceholder == "not-required"
            && !hub.WindowsCurlExample().Contains("Authorization", StringComparison.Ordinal),
            "unauthenticated client example still requests a credential");

        // No loaded generation models: still expose real metadata without inventing a model.
        modelLoaded = false;
        hub = await client.GetLocalApiMetadataAsync(null, CancellationToken.None);
        Check(hub.UsableModels.Count == 0, "unavailable model advertised as usable");
        int baselinePosts = postCount;
        await RejectsAsync<InvalidOperationException>(() => client.RunLocalApiSelfTestAsync(
            hub, "model-a", null, CancellationToken.None));
        Check(postCount == baselinePosts, "stale model selection reached generation");

        modelLoaded = true;
        returnedPort = Port + 1;
        await RejectsAsync<JsonException>(() => client.GetLocalApiMetadataAsync(
            null, CancellationToken.None));
        returnedPort = Port;
        badBaseUrl = "http://evil.invalid:9000/v1";
        await RejectsAsync<JsonException>(() => client.GetLocalApiMetadataAsync(
            null, CancellationToken.None));
        badBaseUrl = null;

        hub = await client.GetLocalApiMetadataAsync(null, CancellationToken.None);
        owner = "replacement-hub-session-456789";
        WriteMetadata();
        await RejectsAsync<IOException>(() => client.RunLocalApiSelfTestAsync(
            hub, "model-a", null, CancellationToken.None));
        Check(postCount == baselinePosts, "stale engine session reached generation");

        hub = await client.GetLocalApiMetadataAsync(null, CancellationToken.None);
        timeoutAfterPost = true;
        await RejectsAsync<TaskCanceledException>(() => client.RunLocalApiSelfTestAsync(
            hub, "model-a", null, CancellationToken.None));
        Check(postCount == baselinePosts + 1 && metadataCount > 5,
            "uncertain self-test was retried or model metadata was not reconciled");
        Console.WriteLine("Native Connection Hub contract: all cases passed.");
    }
}
