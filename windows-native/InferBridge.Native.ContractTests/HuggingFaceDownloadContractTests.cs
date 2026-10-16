using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Production client, simulated HTTP transport. No model files or HF provider
// account are touched. Actual conversion/hardware are separate release gates.
internal static class HuggingFaceDownloadContractTests
{
    private const string ApiKey = "engine-test-key";

    private static void Require(bool condition, string reason)
    {
        if (!condition)
            throw new InvalidOperationException("HF download contract: " + reason);
    }

    private static async Task ExpectAsync<T>(Func<Task> operation)
        where T : Exception
    {
        try { await operation(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static HttpResponseMessage Json(
        string body, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private sealed class Handler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> dispatch)
        : HttpMessageHandler
    {
        public int Posts;
        public int DeviceChecks;
        public int CatalogChecks;

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken token)
        {
            Require(request.RequestUri!.Host == "127.0.0.1", "non-loopback request");
            if (request.RequestUri.AbsolutePath is "/health/live" or "/desktop/instance")
                Require(request.Headers.Authorization is null, "key sent to probe");
            else
                Require(request.Headers.Authorization?.Parameter == ApiKey,
                    "missing API authentication");
            if (request.RequestUri.AbsolutePath == "/v1/models/download-custom")
                Posts++;
            if (request.RequestUri.AbsolutePath == "/v1/devices")
                DeviceChecks++;
            if (request.RequestUri.AbsolutePath == "/v1/models")
                CatalogChecks++;
            return await dispatch(request, token);
        }
    }

    public static async Task RunAsync(string root)
    {
        string directory = Path.Combine(root, "hf-download");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, """{"nonce":"hf-download-nonce","port":18765}""");
        static HttpResponseMessage Base(
            HttpRequestMessage request, string catalog = """{"data":[]}""",
            string devices = """{"available":["CPU","GPU.0"],"default_device":"CPU","mock":false}""")
            => request.RequestUri!.AbsolutePath switch
            {
                "/desktop/instance" => Json(
                    """{"instance_nonce":"hf-download-nonce","port":18765}"""),
                "/health/live" => Json("""{"status":"ok"}"""),
                "/v1/models" => Json(catalog),
                "/v1/devices" => Json(devices),
                _ => throw new InvalidOperationException("Unexpected HTTP path"),
            };

        var requested = new HuggingFaceDownloadRequest(
            "org-safe-model", "Safe model", "org/safe-model", "openvino-genai",
            "int4", "CPU", 2048, 512, true);

        var successTransport = new Handler(async (request, ct) =>
        {
            if (request.RequestUri!.AbsolutePath != "/v1/models/download-custom")
                return Base(request);
            Require(request.Method == HttpMethod.Post, "download did not POST");
            Require(request.RequestUri.Query.Length == 0, "parameters leaked in URL");
            using var json = JsonDocument.Parse(
                await request.Content!.ReadAsStringAsync(ct));
            var body = json.RootElement;
            Require(body.GetProperty("model_id").GetString() == requested.LocalId
                && body.GetProperty("source_model").GetString() == requested.SourceModel
                && body.GetProperty("recommended_device").GetString() == "CPU"
                && body.GetProperty("weight_format").GetString() == "int4"
                && body.GetProperty("max_context_len").GetInt32() == 2048
                && body.GetProperty("max_output_tokens").GetInt32() == 512
                && body.GetProperty("load_after").GetBoolean()
                && !body.GetProperty("trust_remote_code").GetBoolean(),
                "request differs from the reviewed download contract");
            return Json(
                """{"status":"queued_convert","model":{"id":"org-safe-model","source_model":"org/safe-model","backend":"openvino-genai"}}""");
        });
        using (var client = new BackendClient(successTransport, metadata))
        {
            var message = await client.QueueHuggingFaceModelAsync(
                requested, ApiKey, CancellationToken.None);
            Require(message.Contains("Engine accepted", StringComparison.Ordinal),
                "accepted job was not reported as queued");
            await ExpectAsync<ArgumentException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested with { LocalId = "../escape" }, ApiKey, CancellationToken.None));
            await ExpectAsync<ArgumentException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested with { Device = "TPU" }, ApiKey, CancellationToken.None));
            await ExpectAsync<ArgumentException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested with { ContextTokens = 512, OutputTokens = 512 },
                    ApiKey, CancellationToken.None));
            await ExpectAsync<ArgumentException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested with { Backend = "openvino-embeddings" },
                    ApiKey, CancellationToken.None));
        }
        Require(successTransport.Posts == 1 && successTransport.DeviceChecks == 1
            && successTransport.CatalogChecks > 0,
            "invalid calls posted or checks were skipped");

        var absentDevice = new Handler((request, _) => Task.FromResult(Base(request)));
        using (var client = new BackendClient(absentDevice, metadata))
            await ExpectAsync<InvalidOperationException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested with { Device = "NPU" }, ApiKey, CancellationToken.None));
        Require(absentDevice.Posts == 0 && absentDevice.CatalogChecks == 1,
            "unsupported hardware unexpectedly mutated state");

        var staleCatalog = new Handler((request, _) => Task.FromResult(
            Base(request, catalog:
                """{"data":[{"id":"org-safe-model","status":"queued_convert"}]}""")));
        using (var client = new BackendClient(staleCatalog, metadata))
            await ExpectAsync<InvalidOperationException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested, ApiKey, CancellationToken.None));
        Require(staleCatalog.Posts == 0, "duplicate model ID caused a second POST");

        var gated = new Handler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/download-custom"
                ? Json("""{"detail":{"code":"hf_approval_required","message":"Accept the license on the publisher page."}}""",
                    HttpStatusCode.Conflict)
                : Base(request)));
        using (var client = new BackendClient(gated, metadata))
            await ExpectAsync<EngineRequestException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested, ApiKey, CancellationToken.None));
        Require(gated.Posts == 1, "gated request was replayed");

        // The first POST could have registered the model before the response
        // disconnected. Caller must surface uncertainty, not repeat the POST.
        var timedOut = new Handler((request, _) =>
            request.RequestUri!.AbsolutePath == "/v1/models/download-custom"
                ? Task.FromException<HttpResponseMessage>(new TaskCanceledException("uncertain"))
                : Task.FromResult(Base(request)));
        using (var client = new BackendClient(timedOut, metadata))
            await ExpectAsync<TaskCanceledException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested, ApiKey, CancellationToken.None));
        Require(timedOut.Posts == 1, "timeout-after-success was retried");

        var malformed = new Handler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/download-custom"
                ? Json("""{"status":"queued_convert","model":{"id":"wrong-id"}}""")
                : Base(request)));
        using (var client = new BackendClient(malformed, metadata))
            await ExpectAsync<JsonException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested, ApiKey, CancellationToken.None));
        Require(malformed.Posts == 1, "malformed response was retried");

        var auth = new Handler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models"
                ? Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized)
                : Base(request)));
        using (var client = new BackendClient(auth, metadata))
            await ExpectAsync<UnauthorizedAccessException>(() =>
                client.QueueHuggingFaceModelAsync(
                    requested, ApiKey, CancellationToken.None));
        Require(auth.Posts == 0, "unauthorized request attempted mutation");

        Console.WriteLine("Native Hugging Face download contracts: all cases passed.");
    }
}
