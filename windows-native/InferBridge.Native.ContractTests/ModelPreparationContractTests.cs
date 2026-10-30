using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Simulated engine HTTP contracts. No actual model, HF account or Windows hardware.
internal static class ModelPreparationContractTests
{
    private const string Key = "local-test-key";
    private const string Ready = """
        {"schema_version":1,"models":{"available":[{"id":"model-a",
        "status":"converting","can_cancel":true,"cancel_mode":"conversion",
        "backend":"openvino-genai","source_model":"org/safe-model",
        "weight_format":"int4","recommended_device":"AUTO:GPU,CPU",
        "device":"GPU.0","max_context_len":4096,"max_output_tokens":512,
        "supports_vision":false,
        "progress":{"phase":"downloading","operation_id":"op:77","revision":2,"percent":45.5}},
        {"id":"model-b","status":"loading","can_cancel":false,
        "progress":{"phase":"loading","operation_id":"op:88","percent":null}}]}}
        """;

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Model preparation: " + message);
    }

    private static async Task Throws<T>(Func<Task> operation) where T : Exception
    {
        try { await operation(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected exception: " + typeof(T).Name);
    }

    private static HttpResponseMessage Json(
        string body, HttpStatusCode code = HttpStatusCode.OK) =>
        new(code) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private sealed class Transport(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> responder)
        : HttpMessageHandler
    {
        public int Cancels;
        public int StatusReads;

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken token)
        {
            Check(request.RequestUri!.Host == "127.0.0.1", "request left loopback");
            if (request.RequestUri.AbsolutePath is "/desktop/instance" or "/health/live")
                Check(request.Headers.Authorization is null, "probe exposed key");
            else
                Check(request.Headers.Authorization?.Parameter == Key,
                    "engine API key omitted");
            if (request.RequestUri.AbsolutePath == "/v1/models/cancel") Cancels++;
            if (request.RequestUri.AbsolutePath == "/v1/models/status") StatusReads++;
            return await responder(request, token);
        }
    }

    private static HttpResponseMessage Standard(
        HttpRequestMessage request, string status = Ready) =>
        request.RequestUri!.AbsolutePath switch
        {
            "/desktop/instance" => Json(
                """{"instance_nonce":"native-progress-nonce","port":18765}"""),
            "/health/live" => Json("""{"status":"ok"}"""),
            "/v1/models" => Json(
                """{"data":[{"id":"model-a","name":"A","status":"converting"}]}"""),
            "/v1/models/status" => Json(status),
            _ => throw new InvalidOperationException("Unexpected route: "
                + request.RequestUri.AbsolutePath),
        };

    public static async Task RunAsync(string root)
    {
        string folder = Path.Combine(root, "native-preparation");
        Directory.CreateDirectory(folder);
        string metadata = Path.Combine(folder, "desktop-instance.json");
        File.WriteAllText(metadata, """{"nonce":"native-progress-nonce","port":18765}""");

        var success = new Transport(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath != "/v1/models/cancel")
                return Standard(request);
            Check(request.Method == HttpMethod.Post, "cancel must be a POST");
            Check(request.RequestUri.Query.Length == 0, "operation ID leaked into URL");
            using var body = JsonDocument.Parse(
                await request.Content!.ReadAsStringAsync(token));
            Check(body.RootElement.GetProperty("model").GetString() == "model-a"
                && body.RootElement.GetProperty("operation_id").GetString() == "op:77",
                "engine operation identity changed");
            return Json(
                """{"status":"cancelled","operation_id":"op:77","model":{"id":"model-a"}}""");
        });
        using (var client = new BackendClient(success, metadata))
        {
            var items = await client.GetModelPreparationsAsync(Key, CancellationToken.None);
            Check(items.Count == 2
                && items["model-a"] is
                    { CanCancel: true, Phase: "downloading", Percent: 45.5 }
                && items["model-b"] is { CanCancel: false, Percent: null },
                "progress contract not parsed accurately");
            Check(items["model-a"].Details is
                {
                    Backend: "openvino-genai",
                    SourceRepository: "org/safe-model",
                    WeightFormat: "int4",
                    ConfiguredDevice: "AUTO:GPU,CPU",
                    RuntimeDevice: "GPU.0",
                    ContextTokens: 4096,
                    OutputTokens: 512,
                    SupportsVision: false,
                }, "engine model configuration and runtime metadata not parsed");
            var message = await client.CancelModelPreparationAsync(
                items["model-a"], Key, CancellationToken.None);
            Check(message.Contains("confirmed", StringComparison.Ordinal),
                "cancel response not confirmed");
            await Throws<ArgumentException>(() =>
                client.CancelModelPreparationAsync(items["model-b"], Key,
                    CancellationToken.None));
        }
        Check(success.Cancels == 1 && success.StatusReads >= 2,
            "duplicate mutation or missed preflight");

        var stale = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("op:77", "op:99", StringComparison.Ordinal))));
        using (var client = new BackendClient(stale, metadata))
            await Throws<EngineRequestException>(() =>
                client.CancelModelPreparationAsync(
                    new ModelPreparationInfo(
                        "model-a", "op:77", "downloading", 40, true, ""),
                    Key, CancellationToken.None));
        Check(stale.Cancels == 0, "stale operation ID triggered cancellation");

        var revoked = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("\"can_cancel\":true", "\"can_cancel\":false",
                StringComparison.Ordinal))));
        using (var client = new BackendClient(revoked, metadata))
            await Throws<EngineRequestException>(() =>
                client.CancelModelPreparationAsync(
                    new ModelPreparationInfo(
                        "model-a", "op:77", "downloading", 40, true, ""),
                    Key, CancellationToken.None));
        Check(revoked.Cancels == 0, "revoked capability triggered cancellation");

        var invalid = new[]
        {
            Ready.Replace("\"schema_version\":1", "\"schema_version\":2",
                StringComparison.Ordinal),
            Ready.Replace("\"percent\":45.5", "\"percent\":101",
                StringComparison.Ordinal),
            Ready.Replace("\"op:77\"", "\"../../../secret\"",
                StringComparison.Ordinal),
            Ready.Replace("\"can_cancel\":true", "\"can_cancel\":\"true\"",
                StringComparison.Ordinal),
            Ready.Replace("\"model-b\"", "\"model-a\"",
                StringComparison.Ordinal),
        };
        foreach (string malformed in invalid)
        {
            var fake = new Transport(
                (request, _) => Task.FromResult(Standard(request, malformed)));
            using var client = new BackendClient(fake, metadata);
            await Throws<JsonException>(() =>
                client.GetModelPreparationsAsync(Key, CancellationToken.None));
            Check(fake.Cancels == 0, "malformed progress caused mutation");
        }

        // Even if an active conversion advertises cancellable capability while
        // native loading has begun, the UI must fail closed for that row.
        var compiling = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("\"phase\":\"downloading\"", "\"phase\":\"loading\"",
                StringComparison.Ordinal))));
        using (var client = new BackendClient(compiling, metadata))
            Check(!(await client.GetModelPreparationsAsync(Key, CancellationToken.None))
                ["model-a"].CanCancel,
                "native compilation appeared cancellable");

        // Unknown stage is a safe fixed label and cannot leak arbitrary text.
        var strange = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("\"downloading\"", "\"hf_SECRET_EXAMPLE\"",
                StringComparison.Ordinal).Replace(
                "\"can_cancel\":true", "\"can_cancel\":false",
                StringComparison.Ordinal))));
        using (var client = new BackendClient(strange, metadata))
            Check((await client.GetModelPreparationsAsync(Key, CancellationToken.None))
                ["model-a"].Phase == "unknown", "untrusted stage shown in UI");

        // Optional metadata is always advisory: malformed values cannot turn
        // a valid cancellation snapshot into unsafe user-visible text or a
        // disabled read-only model library.
        var untrusted = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("\"source_model\":\"org/safe-model\"",
                  "\"source_model\":\"C:\\\\Users\\\\secrets\\\\hf_token\"",
                  StringComparison.Ordinal)
                .Replace("\"recommended_device\":\"AUTO:GPU,CPU\"",
                  "\"recommended_device\":\"GPU/unsafe\"",
                  StringComparison.Ordinal)
                .Replace("\"backend\":\"openvino-genai\"",
                  "\"backend\":\"<script>\"",
                  StringComparison.Ordinal)
                .Replace("\"max_context_len\":4096",
                  "\"max_context_len\":-1",
                  StringComparison.Ordinal)
                .Replace("\"device\":\"GPU.0\"",
                  "\"device\":\"./secret\"",
                  StringComparison.Ordinal))));
        using (var client = new BackendClient(untrusted, metadata))
        {
            var model = (await client.GetModelPreparationsAsync(
                Key, CancellationToken.None))["model-a"];
            Check(model.CanCancel && model.Details is
                {
                    Backend: "Not reported",
                    SourceRepository: "Not reported",
                    ConfiguredDevice: "Not reported",
                    RuntimeDevice: "Not reported",
                    ContextTokens: null,
                }, "untrusted metadata reached the native model details");
        }

        var tokenShaped = new Transport((request, _) => Task.FromResult(Standard(request,
            Ready.Replace("\"source_model\":\"org/safe-model\"",
                  "\"source_model\":\"org/hf_1234567890123456\"",
                  StringComparison.Ordinal))));
        using (var client = new BackendClient(tokenShaped, metadata))
        {
            var details = (await client.GetModelPreparationsAsync(
                Key, CancellationToken.None))["model-a"].Details;
            Check(details?.SourceRepository == "Not reported",
                "token-shaped source repository reached the native UI");
        }

        var refused = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/cancel"
                ? Json("""{"detail":{"code":"native_load_in_progress","message":"The model cannot be interrupted."}}""",
                    HttpStatusCode.Conflict)
                : Standard(request)));
        using (var client = new BackendClient(refused, metadata))
            await Throws<EngineRequestException>(() =>
                client.CancelModelPreparationAsync(
                    new ModelPreparationInfo("model-a","op:77","downloading",40,true,""),
                    Key, CancellationToken.None));
        Check(refused.Cancels == 1, "conflict automatically retried");

        // Cancellation might already have succeeded when the HTTP response failed.
        var uncertain = new Transport((request, _) =>
            request.RequestUri!.AbsolutePath == "/v1/models/cancel"
                ? Task.FromException<HttpResponseMessage>(
                    new TaskCanceledException("disconnect after mutation"))
                : Task.FromResult(Standard(request)));
        using (var client = new BackendClient(uncertain, metadata))
            await Throws<TaskCanceledException>(() =>
                client.CancelModelPreparationAsync(
                    new ModelPreparationInfo("model-a","op:77","downloading",40,true,""),
                    Key, CancellationToken.None));
        Check(uncertain.Cancels == 1, "ambiguous cancellation replayed");

        var wrongAck = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/cancel"
                ? Json("""{"status":"cancelled","operation_id":"op:99","model":{"id":"model-a"}}""")
                : Standard(request)));
        using (var client = new BackendClient(wrongAck, metadata))
            await Throws<JsonException>(() =>
                client.CancelModelPreparationAsync(
                    new ModelPreparationInfo("model-a","op:77","downloading",40,true,""),
                    Key, CancellationToken.None));
        Check(wrongAck.Cancels == 1, "acknowledgement mismatch was retried");

        var unauthorized = new Transport((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/status"
                ? Json("""{"detail":"Invalid API key"}""", HttpStatusCode.Unauthorized)
                : Standard(request)));
        using (var client = new BackendClient(unauthorized, metadata))
            await Throws<UnauthorizedAccessException>(() =>
                client.GetModelPreparationsAsync(Key, CancellationToken.None));
        Check(unauthorized.Cancels == 0, "unauthenticated cancel");

        Console.WriteLine("Native model preparation contracts: all cases passed.");
    }
}
