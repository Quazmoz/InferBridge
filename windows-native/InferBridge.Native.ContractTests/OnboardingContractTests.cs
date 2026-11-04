using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Native first-run wizard: the browser's seven steps over the same /v1/onboarding
// endpoints, strict parsing, the browser's prepare body (never trust_remote_code),
// engine-owned completion, and a one-time auto-open flag in native preferences.
internal static class OnboardingContractTests
{
    private const int Port = 19481;
    private const string Owner = "onboarding-wizard-owner-1234";
    private const string Key = "onboarding-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native onboarding wizard: " + message);
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

    private static async Task RejectsAsync<T>(Func<Task> action, string message) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Native onboarding wizard: " + message);
    }

    private const string Recommendation = """
        {"schema_version":1,"profile":"balanced","model_id":"qwen2.5-1.5b-int4","model_name":"Qwen2.5 1.5B",
         "description":"Small chat model.","requested_device":"GPU","expected_actual_device":"GPU","precision":"INT4",
         "download_size_gb":1.2,"converted_size_gb":0.9,"runtime_memory_gb":2.5,"first_load_seconds":40,
         "required_free_disk_gb":3.1,"context_length":4096,"output_tokens":512,"compatibility":"compatible",
         "fit_score":88.5,"reason":"Fits this GPU.","warnings":[{"code":"w","severity":"blocking","message":"Low disk."}],
         "trust_remote_code":false}
        """;

    public static async Task RunAsync(string root)
    {
        Check(Onboarding.Steps.SequenceEqual(new[] { "Welcome", "System scan", "NPU readiness", "Recommended model",
            "Prepare model", "Your local benchmark", "Ready" }), "the step labels differ from the browser wizard");

        string directory = Path.Combine(root, "onboarding");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Method, string Path, string Body)>();
        string recommendation = Recommendation;
        string npu = """{"state":"usable","usable":true,"title":"NPU ready","explanation":"Driver found.","available_devices":["NPU"],"fallback_device":"CPU","driver_version":"32.0.100","support_url":"https://www.intel.com/content/www/us/en/download/794734/intel-npu-driver-windows.html"}""";
        int polls = 0;
        using var client = new BackendClient(new Handler(async (request, token) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path is "/health/live" or "/health/ready") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            calls.Add((request.Method.Method, request.RequestUri.PathAndQuery, body));
            return path switch
            {
                "/v1/models" => Json("""{"data":[]}"""),
                "/v1/onboarding/status" => Json("""{"completed":false,"restart_requested":true,"recovery_message":"State was reset."}"""),
                "/v1/onboarding/system-scan" => Json("""
                    {"generated_at":"t","fingerprint":"f","mock":false,"hardware":{},"warnings":["Low RAM"],
                     "items":[{"key":"ram","label":"Memory","status":"warning","value":15.6,"detail":"16 GB recommended"},
                              {"key":"npu","label":"NPU","status":"ready","value":true},{"key":"x","label":"","status":"ready"}]}
                    """),
                "/v1/onboarding/npu-readiness" => Json(npu),
                "/v1/onboarding/recommendation" => Json(recommendation),
                "/v1/onboarding/prepare" => Json("""{"job_id":"onboard-0123456789abcdef","model_id":"qwen2.5-1.5b-int4","requested_device":"CPU","stage":"downloading","stage_label":"Downloading model files","status":"running","determinate":true,"percent":140,"message":"Downloading","can_cancel":true,"stages":[{"stage":"downloading","label":"Downloading model files","status":"active"}]}""", HttpStatusCode.Accepted),
                "/v1/onboarding/preparation/onboard-0123456789abcdef" => Json(++polls == 1
                    ? """{"job_id":"onboard-0123456789abcdef","model_id":"m","requested_device":"CPU","stage":"benchmarking","stage_label":"Running a short benchmark","status":"running","message":"Benchmarking","stages":[]}"""
                    : """{"job_id":"onboard-0123456789abcdef","model_id":"m","requested_device":"CPU","actual_device":"CPU","stage":"ready","stage_label":"Ready","status":"ready","message":"Ready","stages":[],"benchmark":{"model_id":"qwen2.5-1.5b-int4","requested_device":"CPU","actual_device":"CPU","load_time_ms":1200.5,"time_to_first_token_ms":95,"tokens_sec":21.456,"completion_tokens":64,"success":true,"mock":true}}"""),
                "/v1/onboarding/preparation/onboard-0123456789abcdef/cancel" => Json("""{"job_id":"onboard-0123456789abcdef","status":"cancelling","message":"Cancellation requested."}"""),
                "/v1/onboarding/complete" or "/v1/onboarding/connection" => Json("""
                    {"chat_url":"http://127.0.0.1:8000","base_url":"http://127.0.0.1:8000/v1","health_url":"http://127.0.0.1:8000/health/ready",
                     "active_model_id":"qwen2.5-1.5b-int4","actual_device":"CPU","api_key_state":"configured","api_key_placeholder":"<configured-local-api-key>",
                     "openai_python":"from openai import OpenAI","environment_variables":"OPENAI_BASE_URL=http://127.0.0.1:8000/v1",
                     "open_webui":{"base_url":"http://127.0.0.1:8000/v1","api_key":"<configured-local-api-key>","note":"Add a connection."},
                     "n8n":{"base_url":"http://127.0.0.1:8000/v1","api_key":"<configured-local-api-key>","note":"Use an OpenAI credential."}}
                    """),
                "/v1/onboarding/restart" => Json("""{"completed":false,"message":"First-run setup will be shown again."}"""),
                _ => throw new InvalidOperationException("Unexpected route " + path),
            };
        }), metadata);
        CancellationToken none = CancellationToken.None;

        OnboardingStatus status = await client.GetOnboardingStatusAsync(Key, none);
        Check(status is { NeedsSetup: true, OpenerLabel: "Onboarding needs attention", RecoveryMessage: "State was reset." },
            "a restart request was not reported like the browser opener");
        Check(new OnboardingStatus(true, false, true, "").OpenerLabel == "Rescan recommended"
            && new OnboardingStatus(true, false, false, "") is { NeedsSetup: false, OpenerLabel: "Setup and onboarding" },
            "completed setup labels differ from the browser opener");

        OnboardingScan scan = await client.GetOnboardingScanAsync(true, Key, none);
        Check(calls.Last().Path == "/v1/onboarding/system-scan?refresh=true", "Rescan did not ask for a fresh scan");
        Check(scan.Warnings.SequenceEqual(new[] { "Low RAM" }) && scan.Items.Count == 2
            && scan.Items[0] == new OnboardingFact("Memory", "15.6", "16 GB recommended", "warning")
            && scan.Items[1].Value == "Yes", "scan items were not parsed like the browser list");

        OnboardingNpu readiness = await client.GetNpuReadinessAsync(false, Key, none);
        Check(readiness is { Title: "NPU ready", Fallback: "CPU", Driver: "32.0.100" }
            && readiness.SupportUrl.StartsWith("https://www.intel.com/", StringComparison.Ordinal),
            "NPU readiness lost the official Intel support link or details");
        npu = """{"title":"x","explanation":"y","support_url":"https://evil.example/driver"}""";
        Check((await client.GetNpuReadinessAsync(false, Key, none)) is { SupportUrl: "", Fallback: "None reported", Driver: "Unknown" },
            "a non-Intel support link would be opened");

        OnboardingRecommendation rec = await client.GetOnboardingRecommendationAsync(false, Key, none);
        Check(rec is { ModelId: "qwen2.5-1.5b-int4", RequestedDevice: "GPU", TrustRemoteCode: false }
            && rec.Stats.Select(s => s.Label).SequenceEqual(new[] { "Requested device", "Expected actual device", "Download",
                "Converted", "Runtime memory", "First load", "Context / output", "Required free disk" })
            && rec.Stats[2].Value == "1.20 GB" && rec.Stats[5].Value == "40s" && rec.Stats[6].Value == "4096 / 512"
            && rec.Warnings.Single() == new OnboardingWarning(true, "Low disk.")
            && rec.Advanced.Last().Value == "Disabled", "the recommendation differs from the browser step");

        OnboardingJob job = await client.StartOnboardingPreparationAsync(rec, "CPU", "  ", true, true, false, Key, none);
        using (var sent = JsonDocument.Parse(calls.Last().Body))
        {
            var body = sent.RootElement;
            Check(calls.Last().Method == "POST" && body.GetProperty("model_id").GetString() == "qwen2.5-1.5b-int4"
                && body.GetProperty("device").GetString() == "CPU"
                && body.GetProperty("model_storage_location").ValueKind == JsonValueKind.Null
                && body.GetProperty("confirm_license").GetBoolean() && body.GetProperty("confirm_disk_requirement").GetBoolean()
                && !body.GetProperty("acknowledge_warnings").GetBoolean()
                && !body.GetProperty("trust_remote_code").GetBoolean()
                && body.EnumerateObject().Count() == 7, "the prepare body differs from the browser form");
        }
        Check(job is { JobId: "onboard-0123456789abcdef", Status: "running", Determinate: true, Percent: 100, CanCancel: true }
            && job.Stages.Single().Status == "active", "preparation progress was not parsed and clamped");

        job = await client.GetOnboardingJobAsync(job.JobId, Key, none);
        Check(job is { Stage: "benchmarking", Determinate: false, Percent: null }, "the benchmark step was not reported");
        job = await client.GetOnboardingJobAsync(job.JobId, Key, none);
        Check(job.Status == "ready" && job.Benchmark.Select(f => f.Label).SequenceEqual(new[] { "Model", "Actual device",
                "Load or compilation time", "Time to first token", "Generation throughput", "Completion tokens",
                "Benchmark status", "Evidence" })
            && job.Benchmark[4].Value == "21.46 tokens/s" && job.Benchmark[6].Value == "Passed"
            && job.Benchmark[7].Value == "Deterministic mock", "benchmark verification differs from the browser Ready step");
        await client.CancelOnboardingJobAsync(job.JobId, Key, none);
        Check(calls.Last().Path == "/v1/onboarding/preparation/onboard-0123456789abcdef/cancel", "Cancel used another route");

        OnboardingConnection connection = await client.CompleteOnboardingAsync(job.JobId, Key, none);
        using (var sent = JsonDocument.Parse(calls.Last().Body))
            Check(sent.RootElement.GetProperty("job_id").GetString() == job.JobId, "complete did not send the job");
        Check(connection is { ActiveModelId: "qwen2.5-1.5b-int4", ActualDevice: "CPU" }
            && connection.Snippets.Select(s => s.Label).SequenceEqual(new[] { "OpenAI Python client", "Environment variables",
                "Open WebUI", "n8n" })
            && connection.Snippets[2].Value == "Base URL: http://127.0.0.1:8000/v1\nAPI key: <configured-local-api-key>\nAdd a connection.",
            "connection details differ from the browser Ready step");
        Check((await client.GetOnboardingConnectionAsync(Key, none)).Endpoints[2].Value == "http://127.0.0.1:8000/health/ready",
            "reopening a completed setup did not read the connection");
        await client.RestartOnboardingAsync(Key, none);
        Check(calls.Last() is { Method: "POST", Path: "/v1/onboarding/restart" }, "Restart onboarding used another route");

        // Nothing invalid reaches the engine, and native never enables reviewed repository code.
        int before = calls.Count;
        await RejectsAsync<EngineRequestException>(() => client.StartOnboardingPreparationAsync(rec with { TrustRemoteCode = true },
            "CPU", null, true, true, true, Key, none), "a remote-code model was prepared natively");
        await RejectsAsync<ArgumentException>(() => client.StartOnboardingPreparationAsync(rec, "CPU; rm", null, true, true, true,
            Key, none), "an invalid device was sent");
        await RejectsAsync<ArgumentException>(() => client.GetOnboardingJobAsync("../../v1/models", Key, none),
            "a path-like job identifier was requested");
        Check(calls.Count == before, "an invalid onboarding request reached the engine");
        recommendation = Recommendation.Replace("qwen2.5-1.5b-int4", "../evil");
        await RejectsAsync<JsonException>(() => client.GetOnboardingRecommendationAsync(false, Key, none),
            "an invalid recommended model id was accepted");

        // The one-time auto-open flag round-trips and defaults to off for older files.
        string preferences = Path.Combine(directory, "native-preferences.json");
        File.WriteAllText(preferences, """{"Version":1,"Theme":"dark"}""");
        Check(!NativePreferences.Load(preferences).OnboardingAutoOpened, "older preferences auto-opened as already shown");
        (NativePreferences.Load(preferences) with { OnboardingAutoOpened = true }).Save(preferences);
        Check(NativePreferences.Load(preferences) is { OnboardingAutoOpened: true, Theme: "dark" },
            "the auto-open flag was not saved with the other preferences");

        Console.WriteLine("Native onboarding wizard contracts: all cases passed.");
    }
}
