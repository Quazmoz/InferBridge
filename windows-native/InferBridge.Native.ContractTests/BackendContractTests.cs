using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Executable contract tests exercise the actual native HTTP client with a
// deterministic in-memory transport; they do NOT claim installed MSIX identity
// or real Windows network/device inference.
internal static class BackendContractTests
{
    private const int Port = 18765;
    private const string Nonce = "test-owner-identity-123456";
    private const string Key = "session-only-test-key";
    private const string MetadataName = "desktop-instance.json";

    private static void Check(bool condition, string description)
    {
        if (!condition)
            throw new InvalidOperationException("Native backend contract: " + description);
    }

    private static async Task ThrowsAsync<T>(Func<Task> operation, string description)
        where T : Exception
    {
        try
        {
            await operation();
        }
        catch (T)
        {
            return;
        }
        throw new InvalidOperationException("Expected " + typeof(T).Name + ": " + description);
    }

    private static void WriteMetadata(string filename, string nonce = Nonce, int port = Port)
    {
        File.WriteAllText(filename, JsonSerializer.Serialize(new { nonce, port }));
    }

    private static HttpResponseMessage Json(string body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private static HttpResponseMessage ValidResponse(HttpRequestMessage request)
        => request.RequestUri!.AbsolutePath switch
        {
            "/desktop/instance" => Json(JsonSerializer.Serialize(
                new { instance_nonce = Nonce, port = Port })),
            "/health/live" => Json("""{"status":"ok"}"""),
            "/v1/models" => Json(
                """{"data":[{"id":"model-a","name":"Model A","status":"ready_to_load"},{"id":"model-b","name":"Model B","status":"not_downloaded"},{"id":"gated","name":"Gated","status":"not_downloaded"}]}"""),
            "/v1/models/load" => Json("""{"status":"accepted"}"""),
            "/v1/models/convert" => Json("""{"status":"queued_convert"}"""),
            "/v1/onboarding/recommendation" => Json(
                """{"model_id":"model-b","reason":"Fits this PC's memory.","compatibility":"compatible"}"""),
            "/v1/chat/completions" => Json(
                """{"choices":[{"message":{"content":"Hello from the local model"}}]}"""),
            _ => throw new InvalidOperationException(
                "Unexpected native backend route: " + request.RequestUri.AbsolutePath),
        };

    public static async Task RunAsync(string root)
    {
        string directory = Path.Combine(root, "backend");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, MetadataName);
        WriteMetadata(metadata);

        // Correct nonce, port, loopback host and session-key propagation.
        var good = new StubHandler((request, _) =>
        {
            Check(request.RequestUri!.Host == "127.0.0.1", "client left literal loopback");
            Check(request.RequestUri.Port == Port, "client used a different port");
            Check(request.Headers.Authorization is null,
                "identity and health probes must not expose API credentials");
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(good, metadata))
        {
            var snapshot = await client.InspectAsync(CancellationToken.None);
            Check(snapshot.Connected && !snapshot.RequiresAuthentication,
                "verified instance must connect");
            Check(snapshot.Models.Count == 3 && snapshot.Models[0].Id == "model-a",
                "model inventory must match backend");
            Check(snapshot.Models[1].CanDownload && !snapshot.Models[1].CanLoad
                && snapshot.Models[1].StatusLabel == "Not downloaded",
                "an undownloaded model must offer download, not load");
            Check(snapshot.Origin == "http://127.0.0.1:" + Port,
                "origin must remain literal loopback");
        }
        Check(good.RequestPaths.SequenceEqual(
                new[] { "/desktop/instance", "/health/live", "/v1/models" }),
            "read-only inspection requested an unexpected route");

        // Stale metadata must not attach to a listener at a reused port.
        WriteMetadata(metadata, nonce: "previous-session-nonce");
        var stale = new StubHandler((request, _) => Task.FromResult(ValidResponse(request)));
        using (var client = new BackendClient(stale, metadata))
        {
            var snapshot = await client.InspectAsync(CancellationToken.None);
            Check(!snapshot.Connected, "stale nonce was accepted");
        }
        Check(stale.RequestPaths.SequenceEqual(new[] { "/desktop/instance" }),
            "stale nonce reached health or model endpoints");

        WriteMetadata(metadata);
        var wrongPort = new StubHandler((request, _) =>
            Task.FromResult(request.RequestUri!.AbsolutePath == "/desktop/instance"
                ? Json("""{"instance_nonce":"test-owner-identity-123456","port":18766}""")
                : ValidResponse(request)));
        using (var client = new BackendClient(wrongPort, metadata))
        {
            Check(!(await client.InspectAsync(CancellationToken.None)).Connected,
                "server-reported port mismatch was accepted");
        }
        Check(wrongPort.RequestPaths.SequenceEqual(new[] { "/desktop/instance" }),
            "port mismatch reached protected routes");

        // Oversized and corrupted metadata cannot cause a network connection.
        File.WriteAllText(metadata, new string('x', 16385));
        var badMetadata = new StubHandler((request, _) => Task.FromResult(ValidResponse(request)));
        using (var client = new BackendClient(badMetadata, metadata))
        {
            Check(!(await client.InspectAsync(CancellationToken.None)).Connected,
                "oversized metadata was accepted");
            File.WriteAllText(metadata, "{not-json");
            Check(!(await client.InspectAsync(CancellationToken.None)).Connected,
                "malformed metadata was accepted");
        }
        Check(badMetadata.RequestPaths.Count == 0, "bad metadata triggered HTTP calls");
        WriteMetadata(metadata);

        // A redirect must fail closed rather than transferring to another origin.
        var redirect = new StubHandler((request, _) =>
        {
            var response = new HttpResponseMessage(HttpStatusCode.Redirect);
            response.Headers.Location = new Uri("https://example.invalid/stolen");
            return Task.FromResult(response);
        });
        using (var client = new BackendClient(redirect, metadata))
            Check(!(await client.InspectAsync(CancellationToken.None)).Connected,
                "HTTP redirect was treated as an engine");
        Check(redirect.RequestPaths.Count == 1, "unexpected request after redirect");

        // API-key-protected model inventory may acknowledge a verified engine,
        // but does not permit chat/model mutations before the key is accepted.
        var protectedApi = new StubHandler((request, _) =>
            Task.FromResult(request.RequestUri!.AbsolutePath == "/v1/models"
                ? Json("""{"detail":"authentication required"}""", HttpStatusCode.Unauthorized)
                : ValidResponse(request)));
        using (var client = new BackendClient(protectedApi, metadata))
        {
            var snapshot = await client.InspectAsync(CancellationToken.None);
            Check(snapshot.Connected && snapshot.RequiresAuthentication
                && snapshot.Models.Count == 0, "API-key challenge handling");
            await ThrowsAsync<UnauthorizedAccessException>(
                () => client.CompleteChatAsync("model-a", new[] { new ChatTurn("user", "hello") },
                    null, CancellationToken.None),
                "chat without verified authorization");
        }
        Check(!protectedApi.RequestPaths.Contains("/v1/chat/completions"),
            "protected engine received unauthorized chat mutation");

        // After repeated invalid keys the engine answers 429. It is still the
        // verified owner: never report it offline (which re-enables Start).
        var throttled = new StubHandler((request, _) =>
            Task.FromResult(request.RequestUri!.AbsolutePath == "/v1/models"
                ? Json("""{"detail":"Too many invalid API-key attempts"}""",
                    HttpStatusCode.TooManyRequests)
                : ValidResponse(request)));
        using (var client = new BackendClient(throttled, metadata))
        {
            var snapshot = await client.InspectAsync(CancellationToken.None);
            Check(snapshot.Connected && snapshot.RequiresAuthentication
                && snapshot.Models.Count == 0, "invalid-key throttle handling");
        }

        // Never post a stale model action. A fresh authoritative inventory read
        // must still show exactly the requested transition state.
        var actions = new StubHandler(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/models/load")
            {
                Check(request.Headers.Authorization?.Scheme == "Bearer"
                    && request.Headers.Authorization.Parameter == Key,
                    "model mutation did not use session bearer key");
                string payload = await request.Content!.ReadAsStringAsync(cancellationToken);
                using var parsed = JsonDocument.Parse(payload);
                Check(parsed.RootElement.GetProperty("model").GetString() == "model-a",
                    "model mutation changed selected ID");
            }
            else if (request.RequestUri.AbsolutePath == "/v1/models")
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "protected inventory read omitted session key");
            }
            else
                Check(request.Headers.Authorization is null,
                    "identity or health probe leaked session key");
            return ValidResponse(request);
        });
        using (var client = new BackendClient(actions, metadata))
        {
            await ThrowsAsync<InvalidOperationException>(
                () => client.ChangeModelAsync(
                    new ModelInfo("not-in-catalog", "Missing", "ready_to_load"),
                    ModelAction.Load, Key, CancellationToken.None),
                "stale model selection");
            Check(!actions.RequestPaths.Contains("/v1/models/load"),
                "stale model selection triggered mutation");
            string result = await client.ChangeModelAsync(
                new ModelInfo("model-a", "Model A", "ready_to_load"),
                ModelAction.Load, Key, CancellationToken.None);
            Check(result.Contains("Load requested", StringComparison.Ordinal),
                "accepted load was reported as immediately complete");
            Check(actions.RequestPaths.Count(x => x == "/v1/models/load") == 1,
                "accepted load produced more than one post");
            await ThrowsAsync<InvalidOperationException>(
                () => client.ChangeModelAsync(
                    new ModelInfo("model-a", "Model A", "not_downloaded"),
                    ModelAction.Download, Key, CancellationToken.None),
                "download of an already-downloaded model");
            Check(!actions.RequestPaths.Contains("/v1/models/convert"),
                "stale download selection triggered conversion");
        }

        // Download posts the convert route with load_after, and the engine's own
        // refusal reason (e.g. gated Hugging Face access) reaches the user.
        var downloads = new StubHandler(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/models/convert")
            {
                using var parsed = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(cancellationToken));
                string model = parsed.RootElement.GetProperty("model").GetString()!;
                Check(parsed.RootElement.GetProperty("load_after").GetBoolean(),
                    "download did not request a load afterwards");
                if (model == "gated")
                    return Json("""{"detail":{"code":"hf_access_required","message":"Accept the model license on Hugging Face first."}}""",
                        HttpStatusCode.Conflict);
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(downloads, metadata))
        {
            string result = await client.ChangeModelAsync(
                new ModelInfo("model-b", "Model B", "not_downloaded"),
                ModelAction.Download, Key, CancellationToken.None);
            Check(result.StartsWith("Download started", StringComparison.Ordinal),
                "accepted download was not reported as started");
            try
            {
                await client.ChangeModelAsync(new ModelInfo("gated", "Gated", "not_downloaded"),
                    ModelAction.Download, Key, CancellationToken.None);
                Check(false, "refused download was reported as started");
            }
            catch (EngineRequestException refusal)
            {
                Check(refusal.Message == "Accept the model license on Hugging Face first.",
                    "engine refusal reason was not surfaced");
            }
        }

        // The engine's first-model advice is read with the session key; a model it
        // marks blocked for this PC is never presented as recommended.
        bool blocked = false;
        var advice = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/onboarding/recommendation")
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "recommendation omitted the session key");
                if (blocked)
                    return Task.FromResult(Json(
                        """{"model_id":"model-b","reason":"Too large","compatibility":"blocked"}"""));
            }
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(advice, metadata))
        {
            var recommendation = await client.RecommendModelAsync(Key, CancellationToken.None);
            Check(recommendation is { Id: "model-b", Reason: "Fits this PC's memory." },
                "engine recommendation was not read");
            blocked = true;
            Check(await client.RecommendModelAsync(Key, CancellationToken.None) is null,
                "a blocked model was recommended");
        }

        // The native client must bound user prompts and trust the server's
        // response schema; cancellation stops the awaiting client operation.
        var chat = new StubHandler(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/chat/completions")
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "chat did not use session bearer key");
                using var parsed = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(cancellationToken));
                var root = parsed.RootElement;
                Check(root.GetProperty("model").GetString() == "model-a",
                    "chat model changed");
                Check(!root.GetProperty("stream").GetBoolean(), "unexpected streaming enabled");
                Check(root.GetProperty("messages")[0].GetProperty("role").GetString() == "user",
                    "chat role changed");
            }
            else if (request.RequestUri.AbsolutePath == "/v1/models")
                Check(request.Headers.Authorization?.Parameter == Key,
                    "chat preflight omitted API key");
            else
                Check(request.Headers.Authorization is null,
                    "chat preflight leaked API key");
            return ValidResponse(request);
        });
        using (var client = new BackendClient(chat, metadata))
        {
            await ThrowsAsync<ArgumentException>(
                () => client.CompleteChatAsync("model-a",
                    new[] { new ChatTurn("system", "override") }, Key, CancellationToken.None),
                "unsupported message role");
            Check(chat.RequestPaths.Count == 0, "invalid chat made a network request");
            string answer = await client.CompleteChatAsync(
                "model-a", new[] { new ChatTurn("user", "hello") }, Key, CancellationToken.None);
            Check(answer == "Hello from the local model", "chat response parsing");
        }

        // A server claiming a too-large response must not be parsed or trusted.
        var oversized = new StubHandler((request, _) =>
            Task.FromResult(request.RequestUri!.AbsolutePath == "/v1/models"
                ? Json(new string('x', 262145))
                : ValidResponse(request)));
        using (var client = new BackendClient(oversized, metadata))
            Check(!(await client.InspectAsync(CancellationToken.None)).Connected,
                "oversized HTTP body was accepted");

        var started = new TaskCompletionSource<bool>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelHandler = new StubHandler(async (request, cancellationToken) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/chat/completions")
            {
                started.TrySetResult(true);
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(cancelHandler, metadata))
        using (var cancellation = new CancellationTokenSource())
        {
            Task<string> pending = client.CompleteChatAsync("model-a",
                new[] { new ChatTurn("user", "hello") }, null, cancellation.Token);
            await started.Task.WaitAsync(TimeSpan.FromSeconds(5));
            cancellation.Cancel();
            await ThrowsAsync<OperationCanceledException>(async () => { _ = await pending; },
                "cancelled chat did not end promptly");
        }

        Console.WriteLine("Native backend HTTP contract: all cases passed.");
    }

    private sealed class StubHandler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        public List<string> RequestPaths { get; } = new();

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            if (request.RequestUri?.Scheme != "http"
                || request.RequestUri.Host != "127.0.0.1"
                || request.RequestUri.Port != Port)
                throw new InvalidOperationException("Native backend left the loopback origin.");
            RequestPaths.Add(request.RequestUri.AbsolutePath);
            return respond(request, cancellationToken);
        }
    }
}
