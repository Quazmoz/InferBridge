using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Deterministic in-memory HTTP contracts. No Hugging Face account, DPAPI or
// actual Windows runtime is contacted by these tests.
internal static class HuggingFaceContractTests
{
    private const string ApiKey = "engine-test-key";
    private const string Token = "hf_TESTTOKEN123456";

    private static void Assert(bool value, string message)
    {
        if (!value) throw new InvalidOperationException("Hugging Face contract: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> run) where T : Exception
    {
        try { await run(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static HttpResponseMessage Json(string json, HttpStatusCode code = HttpStatusCode.OK) =>
        new(code) { Content = new StringContent(json, Encoding.UTF8, "application/json") };

    private sealed class Transport(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        public int Mutations;
        public readonly List<string> Paths = [];
        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken token)
        {
            string path = request.RequestUri!.AbsolutePath;
            Paths.Add(path);
            Assert(request.RequestUri.Host == "127.0.0.1", "non-loopback host");
            if (path is "/desktop/instance" or "/health/live")
                Assert(request.Headers.Authorization is null, "key leaked to probe");
            else
                Assert(request.Headers.Authorization?.Parameter == ApiKey,
                    "session bearer key not provided");
            if (request.Method != HttpMethod.Get) Mutations++;
            return await respond(request, token);
        }
    }

    public static async Task RunAsync(string root)
    {
        string folder = Path.Combine(root, "huggingface-contract");
        Directory.CreateDirectory(folder);
        string metadata = Path.Combine(folder, "desktop-instance.json");
        File.WriteAllText(metadata,
            """{"nonce":"hf-native-test-nonce","port":18765}""");

        static HttpResponseMessage Common(HttpRequestMessage request) =>
            request.RequestUri!.AbsolutePath switch
            {
                "/desktop/instance" => Json(
                    """{"instance_nonce":"hf-native-test-nonce","port":18765}"""),
                "/health/live" => Json("""{"status":"ok"}"""),
                "/v1/models" => Json("""{"data":[]}"""),
                _ => throw new InvalidOperationException("unexpected route"),
            };

        var accountTransport = new Transport(async (request, ct) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (path == "/v1/huggingface/status")
                return Json("""{"configured":true,"removable":true,"status":"unverified","username":null,"source":"secure_store"}""");
            if (path == "/v1/huggingface/test")
                return Json("""{"status":{"configured":true,"removable":true,"status":"connected","username":"model-owner","source":"secure_store"},"result":{"code":"hf_access_granted"}}""");
            if (path == "/v1/huggingface/token" && request.Method == HttpMethod.Post)
            {
                Assert(request.RequestUri.Query.Length == 0, "token appeared in URL");
                string serialized = await request.Content!.ReadAsStringAsync(ct);
                using var payload = JsonDocument.Parse(serialized);
                Assert(payload.RootElement.GetProperty("token").GetString() == Token,
                    "token payload was not forwarded directly to backend");
                return Json("""{"status":{"configured":true,"removable":true,"status":"connected","username":"model-owner","source":"secure_store"}}""");
            }
            if (path == "/v1/huggingface/token" && request.Method == HttpMethod.Delete)
                return Json("""{"removed":true,"status":{"configured":false,"removable":false,"status":"not_configured","username":null,"source":null}}""");
            if (path == "/v1/models/search-hf")
            {
                Assert(request.Method == HttpMethod.Get, "model search mutated state");
                Assert(request.RequestUri.Query.Contains("query=Smol%20Model", StringComparison.Ordinal),
                    "model query not URL encoded");
                return Json("""[{"id":"org/Smol-Model","suggested_local_id":"org-smol-model","backend":"openvino-genai","pipeline_tag":"text-generation","downloads":100,"likes":11}]""");
            }
            return Common(request);
        });
        using (var client = new BackendClient(accountTransport, metadata))
        {
            var before = await client.GetHuggingFaceAccountAsync(ApiKey, CancellationToken.None);
            Assert(before is { Configured: true, Removable: true, State: "unverified" },
                "stored but unverified account parsing");
            var verified = await client.TestHuggingFaceAccountAsync(ApiKey, CancellationToken.None);
            Assert(verified.State == "connected" && verified.Username == "model-owner",
                "account verification parsing");
            await RejectsAsync<HuggingFaceCredentialException>(() =>
                client.SaveHuggingFaceTokenAsync("not-a-token", ApiKey, CancellationToken.None));
            Assert((await client.SaveHuggingFaceTokenAsync(Token, ApiKey, CancellationToken.None))
                .State == "connected", "account save response");
            var results = await client.SearchHuggingFaceAsync(
                "Smol Model", "text-generation", ApiKey, CancellationToken.None);
            Assert(results.Count == 1 && results[0].Downloads == 100
                && results[0].ModelUrl == "https://huggingface.co/org/Smol-Model",
                "candidate metadata or safe link");
            await RejectsAsync<ArgumentException>(() =>
                client.SearchHuggingFaceAsync("test", "bad-task", ApiKey, CancellationToken.None));
            Assert(!(await client.RemoveHuggingFaceTokenAsync(ApiKey, CancellationToken.None))
                .Configured, "token removal response");
        }
        Assert(accountTransport.Mutations == 3,
            "unexpected or duplicated credential mutations");

        var env = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/huggingface/status"
                ? Json("""{"configured":true,"removable":false,"status":"unverified","source":"environment"}""")
                : Common(request)));
        using (var client = new BackendClient(env, metadata))
            await RejectsAsync<HuggingFaceCredentialException>(() =>
                client.RemoveHuggingFaceTokenAsync(ApiKey, CancellationToken.None));
        Assert(env.Mutations == 0, "environment token removal attempted");

        var malicious = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/huggingface/token"
                ? Json("""{"detail":"hf_TESTTOKEN123456 secret echoed"}""", HttpStatusCode.BadRequest)
                : Common(request)));
        using (var client = new BackendClient(malicious, metadata))
        {
            try
            {
                await client.SaveHuggingFaceTokenAsync(Token, ApiKey, CancellationToken.None);
                Assert(false, "token refusal was accepted");
            }
            catch (HuggingFaceCredentialException error)
            {
                Assert(!error.Message.Contains(Token, StringComparison.Ordinal),
                    "credential leaked through refused response");
            }
        }

        var missing = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/huggingface/status"
                ? Json("""{"configured":false,"removable":false,"status":"not_configured","source":null}""")
                : Common(request)));
        using (var client = new BackendClient(missing, metadata))
            Assert(!(await client.GetHuggingFaceAccountAsync(ApiKey, CancellationToken.None))
                .Configured, "nullable source of empty account");

        foreach (string bad in new[]
        {
            """{"configured":true,"removable":true,"status":"connected","source":"environment"}""",
            """{"configured":true,"removable":true,"status":"bogus","source":"secure_store"}""",
            """{"configured":"yes","removable":false,"status":"connected","source":"environment"}""",
        })
        {
            var broken = new Transport((request, _) => Task.FromResult(
                request.RequestUri!.AbsolutePath == "/v1/huggingface/status"
                    ? Json(bad) : Common(request)));
            using var client = new BackendClient(broken, metadata);
            await RejectsAsync<JsonException>(() =>
                client.GetHuggingFaceAccountAsync(ApiKey, CancellationToken.None));
        }

        // Search candidates must include the backend's own filesystem-safe ID,
        // rather than letting the frontend guess an ID for a missing field.
        var missingSuggestedId = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/search-hf"
                ? Json("""[{"id":"org/model","backend":"openvino-genai","pipeline_tag":"text-generation","downloads":1,"likes":0}]""")
                : Common(request)));
        using (var client = new BackendClient(missingSuggestedId, metadata))
            await RejectsAsync<JsonException>(() =>
                client.SearchHuggingFaceAsync(
                    "model", "text-generation", ApiKey, CancellationToken.None));

        var oversized = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/search-hf"
                ? Json(new string('x', 270000)) : Common(request)));
        using (var client = new BackendClient(oversized, metadata))
            await RejectsAsync<IOException>(() =>
                client.SearchHuggingFaceAsync("test", "embeddings", ApiKey, CancellationToken.None));

        var unauthorized = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/huggingface/status"
                ? Json("""{"detail":"invalid key"}""", HttpStatusCode.Unauthorized)
                : Common(request)));
        using (var client = new BackendClient(unauthorized, metadata))
            await RejectsAsync<UnauthorizedAccessException>(() =>
                client.GetHuggingFaceAccountAsync(ApiKey, CancellationToken.None));

        using (var cancelled = new CancellationTokenSource())
        {
            cancelled.Cancel();
            var handler = new Transport((request, _) => Task.FromResult(Common(request)));
            using var client = new BackendClient(handler, metadata);
            await RejectsAsync<OperationCanceledException>(() =>
                client.SearchHuggingFaceAsync("test", "text-generation", ApiKey, cancelled.Token));
        }

        Console.WriteLine("Native Hugging Face HTTP contracts: all cases passed.");
    }
}
