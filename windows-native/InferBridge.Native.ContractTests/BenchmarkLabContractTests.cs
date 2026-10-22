using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

internal static class BenchmarkLabContractTests
{
    private const string Session = "engine-session-native-benchmark-123";
    private const string Key = "benchmark-test-key";
    private const int Port = 18765;
    private const string Status = """
        {"models":{"available":[
            {"id":"gen-a","name":"Generation A","backend":"openvino-genai",
             "status":"loaded","is_loaded":true,"is_downloaded":true,"weight_format":"int4"},
            {"id":"gen-b","name":"Generation B","backend":"openvino-genai",
             "status":"ready_to_load","is_loaded":false,"is_downloaded":true},
            {"id":"unprepared","name":"Needs conversion","backend":"openvino-genai",
             "status":"not_downloaded","is_downloaded":false},
            {"id":"embed","name":"Embeddings","backend":"openvino-embeddings",
             "status":"loaded","is_loaded":true,"is_downloaded":true}]},
          "device":{"available":["CPU","GPU.0"],"mock":false},"events":[]}
        """;

    private static void Check(bool success, string detail)
    {
        if (!success) throw new InvalidOperationException("Benchmark Lab contract: " + detail);
    }

    private static void Throws<T>(Action action, string detail) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name + ": " + detail);
    }

    private static async Task ThrowsAsync<T>(Func<Task> action, string detail)
        where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name + ": " + detail);
    }

    public static async Task RunAsync(string root)
    {
        using var document = JsonDocument.Parse(Status);
        var catalog = BenchmarkLab.ReadCatalog(document.RootElement, Session);
        Check(catalog.Models.Count == 3 && catalog.Models.All(m => m.Id != "embed"),
            "embedding-only models must never be listed for generation benchmarking");
        Check(catalog.Models.Single(m => m.Id == "gen-a").Loaded
            && catalog.Models.Single(m => m.Id == "gen-b").Prepared
            && !catalog.Models.Single(m => m.Id == "unprepared").Prepared,
            "loaded / prepared / unavailable generation states were not distinguished");
        Check(catalog.Devices.SequenceEqual(new[] { "CPU", "GPU", "AUTO" }),
            "unsupported device choices were invented");
        var plan = BenchmarkLab.CreatePlan(catalog, new[] { "gen-a", "gen-b" },
            new[] { "CPU", "GPU" }, "standard");
        Check(plan.Combinations == 4 && plan.Runs == 5 && plan.Warmups == 1
            && plan.MaxTokens == 64 && plan.TotalSamples == 24,
            "multi-model/device workload or Standard preset is incorrect");
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(catalog,
            new[] { "embed" }, new[] { "CPU" }, "quick"), "embedding accepted");
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(catalog,
            new[] { "unprepared" }, new[] { "CPU" }, "quick"), "unprepared accepted");
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(catalog,
            new[] { "gen-a" }, new[] { "NPU" }, "quick"), "unreported NPU accepted");
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(catalog,
            new[] { "gen-a" }, new[] { "CPU" }, "unsafe-custom"), "unknown preset accepted");
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(catalog,
            new[] { "gen-a", "gen-b" }, new[] { "CPU", "GPU", "AUTO", "CPU", "NPU" },
            "thorough"), "unsupported device accepted");

        var expanded = catalog with
        {
            Models = Enumerable.Range(0, 9).Select(index =>
                new BenchmarkModelChoice("gen-" + index, "Generation " + index,
                    "ready_to_load", "INT4", true, false)).ToArray(),
            Devices = new[] { "CPU" },
        };
        Throws<ArgumentException>(() => BenchmarkLab.CreatePlan(expanded,
            expanded.Models.Select(m => m.Id), new[] { "CPU" }, "thorough"),
            "native maximum combination budget was bypassed");
        using var invalidInventory = JsonDocument.Parse(
            """{"models":{"available":"not-array"},"device":{"available":["CPU"]}}""");
        Throws<JsonException>(() => BenchmarkLab.ReadCatalog(
            invalidInventory.RootElement, Session),
            "malformed catalog was accepted");

        var gate = new BenchmarkAttemptGate();
        Check(gate.TryBegin() && !gate.TryBegin(), "duplicate Start acquired a second lease");
        gate.Finish(confirmed: false);
        Check(!gate.TryBegin() && gate.OutcomeUnknown,
            "timeout-after-acceptance allowed silent resubmission");
        var newWindow = new BenchmarkAttemptGate();
        Check(newWindow.TryBegin(), "a new window cannot begin its own explicit attempt");
        newWindow.Finish(confirmed: true);

        var time = DateTimeOffset.FromUnixTimeSeconds(200);
        using var activity = JsonDocument.Parse("""
            {"events":[{"timestamp":199,"message":"Benchmark Lab · combination 4/4 · old · CPU · complete"},
                       {"timestamp":200,"message":"Benchmark Lab · combination 2/4 · Generation B · GPU · complete"},
                       {"timestamp":200,"message":"Benchmark Lab · combination 1/4 · Generation A · CPU · preparing"}]}
            """);
        var progress = BenchmarkLab.ReadActivity(activity.RootElement, time, 4);
        Check(progress?.Completed == 2 && progress.Total == 4,
            "stale or out-of-order events rolled benchmark progress backward");
        Check(BenchmarkLab.ReadActivity(activity.RootElement, time.AddSeconds(2), 4) is null,
            "old events were attributed to a new benchmark");
        using var noEvents = JsonDocument.Parse("""{"events":[]}""");
        Check(BenchmarkLab.ReadActivity(noEvents.RootElement, time, 4) is null,
            "missing progress became fabricated completion");
        using var malformedEvents = JsonDocument.Parse("""{"events":"broken"}""");
        Throws<JsonException>(() => BenchmarkLab.ReadActivity(
            malformedEvents.RootElement, time, 4), "malformed activity accepted");

        using var completed = JsonDocument.Parse("""
            {"run_id":"bench-1","synthetic":true,"results":[
             {"model_id":"gen-a","requested_device":"CPU","success":true},
             {"model_id":"gen-a","requested_device":"GPU","success":false},
             {"model_id":"gen-b","requested_device":"CPU","success":true},
             {"model_id":"gen-b","requested_device":"GPU","success":true}]}
            """);
        var completion = BenchmarkLab.ReadCompletion(completed.RootElement, plan);
        Check(completion.Synthetic && completion.Successes == 3 && completion.Failures == 1,
            "synthetic hardware evidence or failed rows mislabeled");
        using var missing = JsonDocument.Parse("""
            {"run_id":"bench-1","results":[
             {"model_id":"gen-a","requested_device":"CPU","success":true}]}
            """);
        Throws<JsonException>(() => BenchmarkLab.ReadCompletion(missing.RootElement, plan),
            "malformed completion became a successful measured run");
        using var duplicated = JsonDocument.Parse("""
            {"run_id":"bench-1","synthetic":false,"results":[
             {"model_id":"gen-a","requested_device":"CPU","success":true},
             {"model_id":"gen-a","requested_device":"CPU","success":true},
             {"model_id":"gen-b","requested_device":"CPU","success":true},
             {"model_id":"gen-b","requested_device":"GPU","success":true}]}
            """);
        Throws<JsonException>(() => BenchmarkLab.ReadCompletion(duplicated.RootElement, plan),
            "duplicate results accepted as a complete matrix");

        string directory = Path.Combine(root, "benchmark");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Session, port = Port }));
        int posts = 0;
        bool switchedSession = false;
        using (var client = new BackendClient(new FakeHandler(async (request, _) =>
        {
            string endpoint = request.RequestUri!.AbsolutePath;
            if (endpoint == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new
                {
                    instance_nonce = switchedSession ? "new-session-identity" : Session,
                    port = Port,
                }));
            if (endpoint == "/health/live") return Json("""{"status":"ok"}""");
            if (endpoint == "/v1/models") return Json("""{"data":[]}""");
            if (endpoint == "/v1/system/status")
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "system status omitted session-only API authentication");
                return Json(Status);
            }
            if (endpoint == "/v1/benchmarks/run")
            {
                posts++;
                Check(request.Method == HttpMethod.Post
                    && request.Headers.Authorization?.Parameter == Key,
                    "matrix dispatch did not use authenticated POST");
                using var payload = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
                var body = payload.RootElement;
                Check(body.GetProperty("models").EnumerateArray().Count() == 2
                    && body.GetProperty("devices").EnumerateArray().Count() == 2
                    && body.GetProperty("runs").GetInt32() == 5
                    && body.GetProperty("max_tokens").GetInt32() == 64,
                    "matrix POST did not carry the validated workload");
                return Json(completed.RootElement.GetRawText());
            }
            throw new InvalidOperationException("Unexpected route: " + endpoint);
        }), metadata))
        {
            var refreshed = await client.GetBenchmarkCatalogAsync(Key, CancellationToken.None);
            Check(refreshed.SessionId == Session && refreshed.Models.Count == 3,
                "real client could not read verified benchmark capability inventory");
            var measured = await client.RunBenchmarkLabAsync(
                plan, Session, Key, CancellationToken.None);
            Check(measured.RunId == "bench-1" && measured.Synthetic,
                "real HTTP client lost the backend measurement label");
            switchedSession = true;
            await ThrowsAsync<IOException>(() =>
                client.RunBenchmarkLabAsync(plan, Session, Key, CancellationToken.None),
                "engine restart did not stop stale matrix dispatch");
        }
        Check(posts == 1, "stale or duplicate requests dispatched a second benchmark");
        Console.WriteLine("Native Benchmark Lab contracts: all cases passed.");
    }

    private static HttpResponseMessage Json(string body) =>
        new(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private sealed class FakeHandler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken token)
        {
            if (request.RequestUri?.Host != "127.0.0.1" || request.RequestUri.Port != Port)
                throw new InvalidOperationException("Benchmark escaped verified loopback.");
            return respond(request, token);
        }
    }
}
