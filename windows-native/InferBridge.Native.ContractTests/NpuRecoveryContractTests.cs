using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Issue #65: a classified NPU compile rejection reaches the native model list
// with its error text and one-click recoveries, and each recovery sends exactly
// the engine request the web UI sends (load on GPU, or reconvert for NPU with
// channel-wise INT4). Responses are simulated; nothing is compiled or loaded.
internal static class NpuRecoveryContractTests
{
    private const int Port = 19467;
    private const string Owner = "npu-recovery-owner-123456";
    private const string Key = "npu-recovery-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native NPU recovery: " + message);
    }

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
            => respond(request, cancellationToken);
    }

    private static HttpResponseMessage Json(string body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    public static async Task RunAsync(string root)
    {
        string directory = Path.Combine(root, "npu-recovery");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Path, string Body)>();
        string actions = """["load_gpu","reconvert_npu","format_disk"]""";
        using var client = new BackendClient(new Handler(async (request, token) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (request.RequestUri.Host != "127.0.0.1" || request.RequestUri.Port != Port)
                throw new InvalidOperationException("Client left the verified loopback engine.");
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path is "/health/live" or "/health/ready") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            calls.Add((path, body));
            return path switch
            {
                "/v1/models" => Json("{\"data\":[{\"id\":\"phi-4-mini-int4\",\"name\":\"Phi-4 Mini\","
                    + "\"status\":\"error\",\"error\":\"Intel NPU compilation rejected this model\","
                    + "\"error_code\":\"npu_compile_rejected\",\"recovery_actions\":" + actions + "},"
                    + "{\"id\":\"ok\",\"name\":\"OK\",\"status\":\"ready_to_load\"}]}"),
                "/v1/models/load" => Json("""{"status":"queued"}"""),
                "/v1/models/convert" => Json("""{"status":"queued_convert"}"""),
                _ => throw new InvalidOperationException("Unexpected route " + path),
            };
        }), metadata);

        var snapshot = await client.InspectAsync(CancellationToken.None, Key);
        var failed = snapshot.Models.Single(model => model.Id == "phi-4-mini-int4");
        Check(failed is { Status: "error", ErrorCode: "npu_compile_rejected" }
            && failed.Error!.Contains("NPU compilation", StringComparison.Ordinal),
            "the classified load failure was not parsed");
        Check(failed.RecoveryActions.SequenceEqual(new[] { "load_gpu", "reconvert_npu" }),
            "unknown recovery actions were accepted or known ones dropped");
        Check(snapshot.Models.Single(model => model.Id == "ok").RecoveryActions.Count == 0,
            "a healthy model offered recovery actions");

        string gpu = await client.RecoverModelAsync(failed, "load_gpu", Key, CancellationToken.None);
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(sent.RootElement.GetProperty("device").GetString() == "GPU"
                && sent.RootElement.GetProperty("model").GetString() == "phi-4-mini-int4",
                "Load on GPU instead did not request the GPU");
        Check(gpu.Contains("GPU", StringComparison.Ordinal), "GPU confirmation is unclear");

        await client.RecoverModelAsync(failed, "reconvert_npu", Key, CancellationToken.None);
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/convert").Body))
        {
            var body = sent.RootElement;
            Check(body.GetProperty("device").GetString() == "NPU"
                && body.GetProperty("weight_format").GetString() == "int4"
                && body.GetProperty("group_size").GetInt32() == -1
                && body.GetProperty("ratio").GetDouble() == 1.0
                && body.GetProperty("sym").GetBoolean()
                && body.GetProperty("load_after").GetBoolean(),
                "Reconvert for NPU did not request symmetric channel-wise INT4");
        }

        int before = calls.Count;
        try
        {
            await client.RecoverModelAsync(failed, "format_disk", Key, CancellationToken.None);
            throw new InvalidOperationException("Native NPU recovery: an unknown action was sent");
        }
        catch (ArgumentException) { }
        Check(calls.Count == before, "an unknown recovery action reached the engine");

        actions = "[]";
        try
        {
            await client.RecoverModelAsync(failed, "load_gpu", Key, CancellationToken.None);
            throw new InvalidOperationException("Native NPU recovery: a withdrawn action was sent");
        }
        catch (InvalidOperationException error) when (error.Message.Contains("state changed",
            StringComparison.Ordinal)) { }
        Check(!calls.Skip(before).Any(call => call.Path is "/v1/models/load" or "/v1/models/convert"),
            "a recovery the engine no longer offers was sent");

        Console.WriteLine("Native NPU load-failure recovery contracts: all cases passed.");
    }
}
