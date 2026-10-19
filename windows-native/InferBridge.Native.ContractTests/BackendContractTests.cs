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
            "/v1/system/status" => Json("""{"memory":{"available_gb":8,"total_gb":16},"disk":{"free_gb":23,"models_gb":4,"models_dir":"C:\\private\\local-path"},"device":{"default":"GPU","loaded":{"model-a":"GPU.0"}},"models":{"available":[{"id":"model-a","name":"Model A","status":"loaded","is_loaded":true,"is_downloaded":true},{"id":"model-b","status":"not_downloaded","is_downloaded":false}]},"events":[{"message":"private prompt: hf_secret"}]}"""),
            "/v1/desktop/operations/status" => Json("""{"application_version":"1.0.0","installation_mode":"installed","server_status":"ready","server_port":18765,"controller_available":true,"mock":true,"start_with_windows":false,"benchmark_running":false,"benchmark":{"results":[{"model_id":"model-a","device":"GPU.0"}]},"last_diagnostics_export":"previous-diagnostics.zip","active_model":{"id":"model-a","actual_device":"GPU.0"}}"""),
            "/v1/desktop/operations/diagnostics/export" => Json("""{"status":"created","filename":"inferbridge-diagnostics.zip","collection_errors":["Optional sensor unavailable"]}"""),
            "/v1/benchmarks/run" => Json("""{"results":[{"model_id":"model-a","requested_device":"GPU.0","actual_device":"GPU.0","success":true,"decode_tokens_sec":42.5,"time_to_first_token_ms":120.2,"total_latency_ms":501.3}]}"""),
            "/v1/devices" => Json("""{"default_device":"AUTO","available":["CPU","GPU.0","NPU"],"mock":false,"openvino_available":true}"""),
            "/v1/models/load" => Json("""{"status":"accepted"}"""),
            "/v1/models/convert" => Json("""{"status":"queued_convert"}"""),
            "/v1/models/delete" => Json("""{"status":"deleted"}"""),
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

        // Device discovery uses authenticated verified loopback and parses physical targets.
        var deviceTransport = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/devices")
                Check(request.Headers.Authorization?.Parameter == Key,
                    "device scan omitted session API key");
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(deviceTransport, metadata))
        {
            var devices = await client.GetDevicesAsync(Key, CancellationToken.None);
            string active = await client.GetRuntimeSummaryAsync(Key, CancellationToken.None);
            Check(active.Contains("model-a → GPU.0", StringComparison.Ordinal),
                "actual loaded-device status was not presented");
            Check(devices.Default == "AUTO" && !devices.Mock
                && devices.Available.SequenceEqual(new[] { "CPU", "GPU.0", "NPU" }),
                "physical device discovery failed");
        }

        // Native operations reuse the authenticated verified engine; export is an
        // explicit POST and never receives private tray credentials.
        var operationsTransport = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath.StartsWith("/v1/desktop/operations/",
                StringComparison.Ordinal))
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "desktop operations did not use the session API key");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "native client attempted private tray control");
                Check(request.Method == (request.RequestUri.AbsolutePath.EndsWith("/export",
                    StringComparison.Ordinal) ? HttpMethod.Post : HttpMethod.Get),
                    "desktop operations used the wrong HTTP method");
            }
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(operationsTransport, metadata))
        {
            var status = await client.GetDesktopOperationsAsync(Key, CancellationToken.None);
            Check(status.Version == "1.0.0" && status.ControllerAvailable && status.Mock
                && status.ServerPort == Port && status.LatestBenchmark == "model-a on GPU.0",
                "native operations status is incorrect");
            var exported = await client.ExportDiagnosticsAsync(Key, CancellationToken.None);
            Check(exported.Filename == "inferbridge-diagnostics.zip"
                && exported.CollectionErrors.Count == 1,
                "native diagnostics confirmation is incorrect");
        }
        Check(operationsTransport.RequestPaths.Count(path =>
                path == "/v1/desktop/operations/diagnostics/export") == 1,
            "diagnostics export was duplicated");

        var invalidExport = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/desktop/operations/diagnostics/export"
                ? Json("""{"status":"created","filename":"../unsafe.zip"}""")
                : ValidResponse(request)));
        using (var client = new BackendClient(invalidExport, metadata))
            await ThrowsAsync<JsonException>(
                () => client.ExportDiagnosticsAsync(Key, CancellationToken.None),
                "diagnostics export accepted a path instead of a filename");

        // Native System Doctor checks are read-only, authenticated and discard
        // backend fields capable of containing paths, prompts, keys or errors.
        var doctorTransport = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath is "/v1/system/status" or "/v1/devices")
            {
                Check(request.Method == HttpMethod.Get,
                    "System Doctor unexpectedly mutated engine state");
                Check(request.Headers.Authorization?.Parameter == Key,
                    "System Doctor omitted API authentication");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "System Doctor used private tray control");
            }
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(doctorTransport, metadata))
        {
            var report = await client.RunSystemDoctorAsync(Key, CancellationToken.None);
            Check(report.Errors == 0 && report.Warnings == 0
                && report.Overall == "System looks ready",
                "healthy System Doctor report misclassified runtime");
            Check(report.Devices.Contains("NPU", StringComparison.Ordinal)
                && report.ModelSummary.Contains("1/2 converted", StringComparison.Ordinal),
                "System Doctor discarded hardware or readiness metrics");
            Check(report.Checks.Any(check => check.Title == "NPU visible")
                && report.Checks.Any(check => check.Title == "Explicit runtime routing"),
                "System Doctor omitted hardware placement evidence");
            Check(!report.SupportReport.Contains("C:\\private", StringComparison.Ordinal)
                && !report.SupportReport.Contains("hf_secret", StringComparison.Ordinal)
                && !report.SupportReport.Contains("private prompt", StringComparison.Ordinal)
                && !report.SupportReport.Contains("model-a", StringComparison.Ordinal),
                "System Doctor support report disclosed private backend fields");
        }
        Check(doctorTransport.RequestPaths.Count(path => path == "/v1/system/status") == 1
            && doctorTransport.RequestPaths.Count(path => path == "/v1/devices") == 1,
            "System Doctor made unexpected requests");

        var unhealthyDoctor = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath switch
            {
                "/v1/system/status" => Json(
                    """{"memory":{"available_gb":1,"total_gb":16},"disk":{"free_gb":2},"device":{"default":"NPU","loaded":{"model-x":"AUTO:GPU,CPU"}},"models":{"available":[{"id":"private","status":"error","error":"C:\\secret\\token.txt"},{"is_downloaded":true}]}}"""),
                "/v1/devices" => Json(
                    """{"openvino_available":false,"mock":true,"available":["CPU"]}"""),
                _ => ValidResponse(request),
            }));
        using (var client = new BackendClient(unhealthyDoctor, metadata))
        {
            var report = await client.RunSystemDoctorAsync(null, CancellationToken.None);
            Check(report.Errors >= 3 && report.Warnings >= 3
                && report.Overall == "Action required",
                "System Doctor missed runtime/memory/disk/model failures");
            Check(report.Checks.Any(check => check.Title == "Fallback routing in use")
                && report.Checks.Any(check => check.Title == "NPU not detected"),
                "System Doctor missed fallback routing and absent NPU");
            Check(!report.SupportReport.Contains("secret", StringComparison.OrdinalIgnoreCase)
                && !report.SupportReport.Contains("private", StringComparison.OrdinalIgnoreCase),
                "malicious model-error content leaked into the support report");
        }
        var malformedDoctor = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/system/status"
                ? Json("""{"disk":{"free_gb":3},"device":{}}""")
                : ValidResponse(request)));
        using (var client = new BackendClient(malformedDoctor, metadata))
            await ThrowsAsync<JsonException>(
                () => client.RunSystemDoctorAsync(null, CancellationToken.None),
                "System Doctor accepted incomplete schema");

        // A quick native benchmark uses the existing authenticated API and
        // only measures the exact loaded model/device the user confirmed.
        var benchmarkTransport = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/benchmarks/run")
            {
                Check(request.Method == HttpMethod.Post,
                    "native benchmark did not use POST");
                Check(request.Headers.Authorization?.Parameter == Key,
                    "native benchmark omitted bearer authentication");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "native benchmark used private tray control");
                using var payload = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(token));
                var root = payload.RootElement;
                Check(root.GetProperty("model").GetString() == "model-a"
                    && root.GetProperty("devices")[0].GetString() == "GPU.0"
                    && root.GetProperty("runs").GetInt32() == 1
                    && root.GetProperty("max_tokens").GetInt32() == 32,
                    "native benchmark did not enforce bounded quick preset");
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(benchmarkTransport, metadata))
        {
            var state = await client.GetDesktopOperationsAsync(Key, CancellationToken.None);
            Check(state.ActiveModelId == "model-a" && state.ActualDevice == "GPU.0",
                "native benchmark preflight did not report the actual loaded target");
            string report = await client.RunQuickBenchmarkAsync(
                "model-a", "GPU.0", Key, CancellationToken.None);
            Check(report.Contains("42.5 tokens/s", StringComparison.Ordinal)
                && report.Contains("120.2 ms", StringComparison.Ordinal)
                && report.Contains("Actual: GPU.0", StringComparison.Ordinal),
                "native benchmark did not present measured metrics");
            await ThrowsAsync<InvalidOperationException>(
                () => client.RunQuickBenchmarkAsync(
                    "different-model", "GPU.0", Key, CancellationToken.None),
                "benchmark accepted a different model than the active one");
        }
        Check(benchmarkTransport.RequestPaths.Count(path => path == "/v1/benchmarks/run") == 1,
            "stale benchmark preflight still posted a generation run");

        var changedBenchmark = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/desktop/operations/status"
                ? Json("""{"application_version":"1.0","server_status":"ready","server_port":18765,"benchmark_running":false,"active_model":null}""")
                : ValidResponse(request)));
        using (var client = new BackendClient(changedBenchmark, metadata))
            await ThrowsAsync<InvalidOperationException>(
                () => client.RunQuickBenchmarkAsync(
                    "model-a", "GPU.0", Key, CancellationToken.None),
                "benchmark started after model unload");
        Check(!changedBenchmark.RequestPaths.Contains("/v1/benchmarks/run"),
            "benchmark ran without a verified loaded model");

        // History comes from the verified, authenticated engine; comparison may only
        // claim a delta for identical reproducibility and execution identities.
        const string BenchmarkHistoryPayload =
            """
            {"object":"list","data":[
              {"run_id":"current","created_at":"2026-10-19T10:00:00Z","preset":"quick",
               "methodology_version":2,"hardware_fingerprint":"local-only-fingerprint",
               "max_tokens":32,"runs_per_combo":3,"warmup_runs":1,
               "results":[{"model_id":"model-a","source_model":"source-a","weight_format":"INT4",
                 "requested_device":"AUTO","actual_device":"GPU.0","success":true,
                 "decode_tokens_sec":50,"tokens_sec":44,"time_to_first_token_ms":110,
                 "total_latency_ms":530,"prompt_tokens":12,"completion_tokens":32}]},
              {"run_id":"previous","created_at":"2026-10-18T10:00:00Z","preset":"quick",
               "methodology_version":2,"hardware_fingerprint":"local-only-fingerprint",
               "max_tokens":32,"runs_per_combo":3,"warmup_runs":1,
               "results":[{"model_id":"model-a","source_model":"source-a","weight_format":"INT4",
                 "requested_device":"AUTO","actual_device":"GPU.0","success":true,
                 "decode_tokens_sec":40,"time_to_first_token_ms":150,
                 "total_latency_ms":620}]},
              {"run_id":"different-device","methodology_version":2,
               "hardware_fingerprint":"local-only-fingerprint",
               "max_tokens":32,"runs_per_combo":3,"warmup_runs":1,
               "results":[{"model_id":"model-a","source_model":"source-a","weight_format":"INT4",
                 "requested_device":"AUTO","actual_device":"CPU","success":true,
                 "decode_tokens_sec":10}]},
              {"run_id":"automatic","automatic":true,"max_tokens":32,
               "runs_per_combo":3,"warmup_runs":1,"results":[]}
            ]}
            """;
        var historyTransport = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/benchmarks")
            {
                Check(request.Method == HttpMethod.Get
                    && request.Headers.Authorization?.Parameter == Key,
                    "native history must use authenticated GET");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "native history leaked the private tray control header");
                return Task.FromResult(Json(BenchmarkHistoryPayload));
            }
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(historyTransport, metadata))
        {
            var history = await client.GetBenchmarkHistoryAsync(Key, CancellationToken.None);
            Check(history.Count == 3, "history did not exclude automatic advisor runs");
            var current = history[0];
            var earlier = history[1];
            Check(current.IsComparableTo(earlier)
                && !current.IsComparableTo(current)
                && !current.IsComparableTo(current with { OutputTokens = 64 })
                && !current.IsComparableTo(current with { HardwareFingerprint = "other-machine" })
                && !current.IsComparableTo(current with { Synthetic = true }),
                "comparison accepted incompatible run methodology, hardware, or mock mode");
            Check(earlier.ComparableTo(current.Results[0]) is not null
                && history[2].ComparableTo(current.Results[0]) is null,
                "comparison ignored the reported actual execution device");
            Check(current.Results[0].DecodeTokensPerSecond == 50
                && current.Results[0].FirstTokenMilliseconds == 110,
                "history did not retain authoritative measurements");
            Check(BenchmarkHistory.Delta(50, 40, false).Contains("better",
                    StringComparison.Ordinal)
                && BenchmarkHistory.Delta(100, 0, false) == "No comparable measurement",
                "comparison invented deltas for invalid baseline");
        }

        using (var bad = JsonDocument.Parse("""{"data":{"runs":[]}}"""))
        {
            bool rejected = false;
            try { _ = BenchmarkHistory.Parse(bad.RootElement); }
            catch (JsonException) { rejected = true; }
            Check(rejected, "malformed benchmark history envelope was accepted");
        }
        using (var negative = JsonDocument.Parse(
            """
            {"data":[{"run_id":"bad","max_tokens":32,"runs_per_combo":3,"warmup_runs":1,
                "results":[{"model_id":"model-a","requested_device":"CPU","success":true,
                "decode_tokens_sec":-123}]}]}
            """))
        {
            var parsed = BenchmarkHistory.Parse(negative.RootElement);
            Check(parsed.Count == 1 && parsed[0].Results[0].DecodeTokensPerSecond is null,
                "invalid negative measurement was interpreted as valid performance");
        }

        // Native recovery rechecks the server-owned recovery ID and allowed
        // actions before mutation, without exposing tray-control credentials.
        const string RecoveryPayload =
            """
            {"available":true,"schema_version":1,"recovery_id":"rec-123","model_id":"model-a",
             "model_name":"Model A","failed_stage":"conversion","last_completed_stage":"download",
             "downloaded_files":"reusable","conversion_output":"incomplete",
             "recommended_action":"resume",
             "actions":{"resume":true,"retry_failed_stage":true,"restart_download":true,
                        "remove_incomplete_files":true},
             "failure_details":{"message":"Download interrupted","log_tail":["safe error"]}}
            """;
        int recoveryReads = 0;
        var recoveryTransport = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath.StartsWith("/v1/models/recovery",
                StringComparison.Ordinal))
            {
                Check(request.Headers.Authorization?.Parameter == Key,
                    "model recovery omitted session authentication");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "model recovery used private tray credential");
            }
            if (request.RequestUri.AbsolutePath == "/v1/models/recovery/model-a")
            {
                Check(request.Method == HttpMethod.Get,
                    "recovery details must be a read-only GET");
                recoveryReads++;
                return Json(RecoveryPayload);
            }
            if (request.RequestUri.AbsolutePath == "/v1/models/recovery/action")
            {
                Check(request.Method == HttpMethod.Post && recoveryReads >= 2,
                    "recovery action skipped authoritative state recheck");
                using var posted = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(token));
                var body = posted.RootElement;
                Check(body.GetProperty("model").GetString() == "model-a"
                    && body.GetProperty("recovery_id").GetString() == "rec-123"
                    && body.GetProperty("action").GetString() == "resume"
                    && body.GetProperty("device").GetString() == "CPU",
                    "recovery action sent incorrect model, lease, or device");
                return Json("""{"status":"started","action":"resume","message":"Queued"}""");
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(recoveryTransport, metadata))
        {
            var state = await client.GetModelRecoveryAsync(
                "model-a", Key, CancellationToken.None);
            Check(state.ModelId == "model-a" && state.RecoveryId == "rec-123"
                && state.RecommendedAction == "resume" && state.CanRun("restart_download")
                && state.LogTail.SequenceEqual(new[] { "safe error" }),
                "native recovery details were not parsed accurately");
            await ThrowsAsync<ArgumentException>(
                () => client.GetModelRecoveryAsync("../model-a", Key, CancellationToken.None),
                "recovery identifier accepted path traversal");
            Check((await client.ApplyModelRecoveryAsync(
                state, "resume", "CPU", Key, CancellationToken.None)).Contains("started",
                    StringComparison.OrdinalIgnoreCase),
                "recovery did not confirm actual engine status");
            await ThrowsAsync<ArgumentException>(
                () => client.ApplyModelRecoveryAsync(
                    state, "unknown", null, Key, CancellationToken.None),
                "unknown recovery action was accepted");
        }
        Check(recoveryTransport.RequestPaths.Count(p => p == "/v1/models/recovery/action") == 1,
            "invalid recovery action reached the engine");

        int changedStatePosts = 0;
        var changedRecovery = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/models/recovery/model-a")
                return Task.FromResult(Json(RecoveryPayload.Replace("rec-123", "rec-new")));
            if (request.RequestUri.AbsolutePath == "/v1/models/recovery/action")
                changedStatePosts++;
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(changedRecovery, metadata))
        {
            var oldState = new ModelRecoveryInfo("model-a", "Model A", "rec-123",
                "conversion", "download", "reusable", "incomplete", "resume", "",
                Array.Empty<string>(), new[] { "resume" });
            await ThrowsAsync<InvalidOperationException>(
                () => client.ApplyModelRecoveryAsync(
                    oldState, "resume", null, Key, CancellationToken.None),
                "stale recovery identity was allowed to mutate files");
        }
        Check(changedStatePosts == 0, "stale recovery performed a mutation");

        var malformedRecovery = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models/recovery/model-a"
                ? Json(RecoveryPayload.Replace("\"model_id\":\"model-a\"",
                    "\"model_id\":\"some-other-model\""))
                : ValidResponse(request)));
        using (var client = new BackendClient(malformedRecovery, metadata))
            await ThrowsAsync<JsonException>(
                () => client.GetModelRecoveryAsync("model-a", Key, CancellationToken.None),
                "recovery details accepted a different model identity");

        int revokedPermissionPosts = 0;
        var revokedRecovery = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/models/recovery/model-a")
                return Task.FromResult(Json(RecoveryPayload.Replace(
                    "\"resume\":true", "\"resume\":false")));
            if (request.RequestUri.AbsolutePath == "/v1/models/recovery/action")
                revokedPermissionPosts++;
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(revokedRecovery, metadata))
        {
            var previouslyAllowed = new ModelRecoveryInfo("model-a", "Model A", "rec-123",
                "conversion", "download", "reusable", "incomplete", "resume", "",
                Array.Empty<string>(), new[] { "resume" });
            await ThrowsAsync<InvalidOperationException>(
                () => client.ApplyModelRecoveryAsync(
                    previouslyAllowed, "resume", null, Key, CancellationToken.None),
                "revoked engine recovery permission was ignored");
            await ThrowsAsync<ArgumentException>(
                () => client.ApplyModelRecoveryAsync(
                    previouslyAllowed, "resume", "UNTRUSTED",
                    Key, CancellationToken.None),
                "unrecognized inference device was sent to recovery");
        }
        Check(revokedPermissionPosts == 0,
            "invalid or revoked recovery capability reached the mutation endpoint");

        // Native storage management consumes only typed, path-free inventory.
        // Every cleanup rechecks server capabilities and sends the explicit UI
        // header required by the desktop-only Python route.
        const string StoragePayload =
            """
            {
              "schema_version":1,
              "totals":{"managed_storage_bytes":100,"currently_reclaimable_bytes":60,
                        "protected_transaction_backup_bytes":30},
              "models":[{"model_id":"model-a","name":"Model A","converted_size_bytes":30,
                         "cleanup":{"action":"delete_converted_model",
                                    "available":true,"reclaimable_bytes":30,"reason":""}}],
              "source_caches":[{"source_model":"org/model-a","size_bytes":20,
                         "cleanup":{"action":"remove_huggingface_cache","model_id":"model-a",
                                    "available":true,"reclaimable_bytes":20,"reason":""}}],
              "recovery_items":[{"model_id":"model-a","name":"Model A","size_bytes":10,"protected_backup_bytes":30,
                         "cleanup":{"action":"remove_incomplete_data",
                                    "available":false,"reclaimable_bytes":10,
                                    "reason":"Model is loaded"}}],
              "compiled_cache":{"size_bytes":10,
                         "cleanup":{"action":"clear_compiled_cache",
                                    "available":true,"reclaimable_bytes":10,"reason":""}},
              "notes":{"paths_exposed":false,"transaction_backups_protected":true}
            }
            """;
        int inventoryReads = 0;
        var storageTransport = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/storage")
            {
                inventoryReads++;
                Check(request.Method == HttpMethod.Get
                    && request.Headers.Authorization?.Parameter == Key,
                    "storage inventory was not authenticated read-only");
                Check(!request.Headers.Contains("X-Desktop-Control"),
                    "storage inventory used private tray credentials");
                return Json(StoragePayload);
            }
            if (request.RequestUri.AbsolutePath == "/v1/storage/cleanup")
            {
                Check(request.Method == HttpMethod.Post && inventoryReads >= 2
                    && request.Headers.Authorization?.Parameter == Key
                    && request.Headers.TryGetValues("X-OV-LLM-UI", out var uiValues)
                    && uiValues.Single() == "1"
                    && !request.Headers.Contains("X-Desktop-Control"),
                    "cleanup bypassed local UI header, recheck or API key");
                using var posted = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(token));
                JsonElement body = posted.RootElement;
                Check(body.GetProperty("action").GetString() == "remove_huggingface_cache"
                    && body.GetProperty("model_id").GetString() == "model-a",
                    "storage cleanup targeted the wrong action or catalog model");
                return Json(
                    """{"status":"completed","action":"remove_huggingface_cache","model_id":"model-a","freed_bytes":20}""");
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(storageTransport, metadata))
        {
            StorageInventory inventory = await client.GetStorageInventoryAsync(
                Key, CancellationToken.None);
            Check(inventory.ManagedBytes == 100 && inventory.ReclaimableBytes == 60
                && inventory.ProtectedBytes == 30 && inventory.Options.Count == 4,
                "native storage inventory totals or cleanup entries were corrupted");
            Check(inventory.Options.Count(option => option.Available) == 3
                && inventory.Options.Any(option =>
                    option.Action == "remove_incomplete_data" && !option.Available),
                "engine-disabled cleanup became available in native UI");
            var cache = inventory.Options.Single(option =>
                option.Action == "remove_huggingface_cache");
            Check((await client.CleanupStorageAsync(cache, Key, CancellationToken.None))
                .Contains("confirmed cleanup", StringComparison.OrdinalIgnoreCase),
                "native cleanup accepted no authoritative confirmation");
            await ThrowsAsync<ArgumentException>(
                () => client.CleanupStorageAsync(
                    inventory.Options.Single(o => o.Action == "remove_incomplete_data"),
                    Key, CancellationToken.None),
                "disabled storage cleanup reached the engine");
            await ThrowsAsync<ArgumentException>(
                () => client.CleanupStorageAsync(
                    cache with { Action = "remove_arbitrary_path" },
                    Key, CancellationToken.None),
                "unknown cleanup action reached the engine");
            await ThrowsAsync<ArgumentException>(
                () => client.CleanupStorageAsync(
                    cache with { ModelId = "../escape" },
                    Key, CancellationToken.None),
                "arbitrary storage path reached cleanup API");
        }
        Check(storageTransport.RequestPaths.Count(p => p == "/v1/storage/cleanup") == 1,
            "invalid native cleanup caused an unexpected mutation");

        // Global compiled-cache cleanup must never acquire an arbitrary model target.
        int compiledPosts = 0;
        var compiledStorage = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/storage")
                return Json(StoragePayload);
            if (request.RequestUri.AbsolutePath == "/v1/storage/cleanup")
            {
                compiledPosts++;
                Check(request.Headers.TryGetValues("X-OV-LLM-UI", out var values)
                    && values.Single() == "1",
                    "compiled-cache cleanup omitted local UI header");
                using var post = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(token));
                Check(post.RootElement.GetProperty("action").GetString() ==
                    "clear_compiled_cache"
                    && post.RootElement.GetProperty("model_id").ValueKind == JsonValueKind.Null,
                    "global compiled-cache cleanup accepted model scope");
                return Json(
                    """{"status":"completed","action":"clear_compiled_cache","model_id":null,"freed_bytes":10}""");
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(compiledStorage, metadata))
        {
            var inventory = await client.GetStorageInventoryAsync(Key, CancellationToken.None);
            var compiled = inventory.Options.Single(o => o.Action == "clear_compiled_cache");
            Check((await client.CleanupStorageAsync(compiled, Key, CancellationToken.None))
                .Contains("confirmed cleanup", StringComparison.OrdinalIgnoreCase),
                "global compiled cache cleanup was not verified");
            await ThrowsAsync<ArgumentException>(
                () => client.CleanupStorageAsync(
                    compiled with { ModelId = "model-a" },
                    Key, CancellationToken.None),
                "compiled-cache cleanup accepted an invented model scope");
        }
        Check(compiledPosts == 1, "invalid global cleanup caused extra mutations");

        int staleStoragePosts = 0;
        var changedStorage = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/storage")
                return Task.FromResult(Json(StoragePayload.Replace(
                    "\"reclaimable_bytes\":20", "\"reclaimable_bytes\":15")));
            if (request.RequestUri.AbsolutePath == "/v1/storage/cleanup")
                staleStoragePosts++;
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(changedStorage, metadata))
        {
            var staleCleanup = new StorageCleanupOption("remove_huggingface_cache",
                "model-a", "org/model-a", "Reusable source", 20, 20, true, "");
            await ThrowsAsync<InvalidOperationException>(
                () => client.CleanupStorageAsync(staleCleanup, Key, CancellationToken.None),
                "stale storage estimate was accepted without refresh");
        }
        Check(staleStoragePosts == 0, "stale cleanup sent a destructive request");

        var unsafeStorage = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/storage"
                ? Json(StoragePayload.Replace(
                    "\"paths_exposed\":false", "\"paths_exposed\":true"))
                : ValidResponse(request)));
        using (var client = new BackendClient(unsafeStorage, metadata))
            await ThrowsAsync<JsonException>(
                () => client.GetStorageInventoryAsync(Key, CancellationToken.None),
                "inventory exposing engine paths was treated as safe");

        var missingCleanup = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/storage"
                ? Json(StoragePayload.Replace(
                    "\"action\":\"delete_converted_model\"",
                    "\"action\":\"unexpected_action\""))
                : ValidResponse(request)));
        using (var client = new BackendClient(missingCleanup, metadata))
            await ThrowsAsync<JsonException>(
                () => client.GetStorageInventoryAsync(Key, CancellationToken.None),
                "unexpected storage cleanup capability was accepted");

        // Destructive model actions are explicit, verified, and refuse stale state.
        var deleteTransport = new StubHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/models/delete")
            {
                Check(request.Method == HttpMethod.Post,
                    "model deletion was not a POST");
                Check(request.Headers.Authorization?.Parameter == Key,
                    "model deletion omitted session authentication");
                using var deletePayload = JsonDocument.Parse(
                    request.Content!.ReadAsStringAsync().GetAwaiter().GetResult());
                Check(deletePayload.RootElement.GetProperty("model").GetString() == "model-a",
                    "model deletion targeted the wrong model");
            }
            return Task.FromResult(ValidResponse(request));
        });
        using (var client = new BackendClient(deleteTransport, metadata))
        {
            var model = new ModelInfo("model-a", "Model A", "ready_to_load");
            Check(model.CanDelete, "unloaded model cannot be deleted");
            Check((await client.DeleteModelAsync(model, Key, CancellationToken.None))
                .Contains("deleted", StringComparison.Ordinal),
                "model delete did not confirm backend action");
            await ThrowsAsync<InvalidOperationException>(
                () => client.DeleteModelAsync(
                    new ModelInfo("model-a", "Model A", "loaded"), Key, CancellationToken.None),
                "loaded model deletion was accepted");
        }
        Check(deleteTransport.RequestPaths.Count(path => path == "/v1/models/delete") == 1,
            "guarded model deletion made an unexpected mutation");

        var changedModel = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/models"
                ? Json("""{"data":[{"id":"model-a","name":"Model A","status":"loaded"}]}""")
                : ValidResponse(request)));
        using (var client = new BackendClient(changedModel, metadata))
            await ThrowsAsync<InvalidOperationException>(
                () => client.DeleteModelAsync(
                    new ModelInfo("model-a", "Model A", "ready_to_load"),
                    Key, CancellationToken.None),
                "stale model selection was permitted to delete");
        Check(!changedModel.RequestPaths.Contains("/v1/models/delete"),
            "stale model state triggered deletion");

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
                Check(parsed.RootElement.GetProperty("device").GetString() == "GPU",
                    "selected GPU device was not forwarded to load");
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
            await ThrowsAsync<ArgumentException>(
                () => client.ChangeModelAsync(new ModelInfo("model-a", "Model A", "ready_to_load"),
                    ModelAction.Load, Key, CancellationToken.None, "untrusted-device"),
                "invalid inference device");
            Check(actions.RequestPaths.Count == 0,
                "invalid device triggered an HTTP request");
            await ThrowsAsync<InvalidOperationException>(
                () => client.ChangeModelAsync(
                    new ModelInfo("not-in-catalog", "Missing", "ready_to_load"),
                    ModelAction.Load, Key, CancellationToken.None),
                "stale model selection");
            Check(!actions.RequestPaths.Contains("/v1/models/load"),
                "stale model selection triggered mutation");
            string result = await client.ChangeModelAsync(
                new ModelInfo("model-a", "Model A", "ready_to_load"),
                ModelAction.Load, Key, CancellationToken.None, "GPU");
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
                if (model == "model-b")
                    Check(parsed.RootElement.GetProperty("device").GetString() == "NPU",
                        "selected NPU device was not forwarded to conversion");
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
                ModelAction.Download, Key, CancellationToken.None, "NPU");
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
                Check(root.GetProperty("max_tokens").GetInt32() == 512
                    && root.GetProperty("temperature").GetDouble() == 0.7
                    && root.GetProperty("top_p").GetDouble() == 1,
                    "non-stream chat defaults diverged from backend");
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

        // SSE uses the existing authenticated chat route. Only a completed [DONE]
        // response becomes conversation history; partial output is display-only.
        static string Sse(string text, string? finish = null) =>
            "data: " + JsonSerializer.Serialize(new
            {
                choices = new[] { new { index = 0, delta = new { content = text },
                    finish_reason = finish } },
            }) + "\n\n";
        static HttpResponseMessage Events(string content) => new(HttpStatusCode.OK)
        {
            Content = new StringContent(content, Encoding.UTF8, "text/event-stream"),
        };

        string longPiece = new('x', 4200); // Event crosses the HTTP read buffer.
        bool sawCustomOptions = false;
        var streaming = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/chat/completions")
            {
                Check(request.Method == HttpMethod.Post,
                    "native streamed chat did not use POST");
                Check(request.Headers.Authorization?.Parameter == Key,
                    "native streamed chat omitted the session bearer key");
                Check(request.Headers.Accept.Any(value => value.MediaType == "text/event-stream"),
                    "native streamed chat did not request SSE");
                using var parsed = JsonDocument.Parse(
                    await request.Content!.ReadAsStringAsync(token));
                Check(parsed.RootElement.GetProperty("stream").GetBoolean(),
                    "native chat request did not enable streaming");
                Check(parsed.RootElement.GetProperty("model").GetString() == "model-a",
                    "streamed chat changed the selected model");
                var payload = parsed.RootElement;
                int tokens = payload.GetProperty("max_tokens").GetInt32();
                double temperature = payload.GetProperty("temperature").GetDouble();
                double topP = payload.GetProperty("top_p").GetDouble();
                if (tokens == 384)
                {
                    Check(temperature == 0.2 && topP == 0.85,
                        "custom SSE generation parameters were not serialized");
                    sawCustomOptions = true;
                }
                else
                    Check(tokens == 512 && temperature == 0.7 && topP == 1,
                        "streaming chat defaults diverged from backend");
                return Events("data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"},\"finish_reason\":null}]}\n\n"
                    + Sse(longPiece + "✨") + Sse(" hello") + Sse("", "stop")
                    + "data: {\"choices\":[]}\n\n"
                    + "data: [DONE]\n\n");
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(streaming, metadata))
        {
            var previews = new List<string>();
            string response = await client.StreamChatAsync(
                "model-a", new[] { new ChatTurn("user", "hello") },
                Key, previews.Add, CancellationToken.None);
            Check(response == longPiece + "✨ hello",
                "SSE parser lost an event or UTF-8 data across a buffer boundary");
            Check(previews.Count == 2 && previews[0] == longPiece + "✨"
                && previews[1] == response, "SSE progress callbacks are incorrect");
            string configured = await client.StreamChatAsync(
                "model-a", new[] { new ChatTurn("user", "custom") },
                Key, _ => { }, CancellationToken.None,
                GenerationOptions.FromNumbers(384, 0.2, 0.85));
            Check(configured == response && sawCustomOptions,
                "custom inference settings did not reach authenticated SSE endpoint");
            foreach (var invalid in new[]
            {
                new GenerationOptions(0, 0.7, 1),
                new GenerationOptions(4097, 0.7, 1),
                new GenerationOptions(64, double.NaN, 1),
                new GenerationOptions(64, double.PositiveInfinity, 1),
                new GenerationOptions(64, 2.1, 1),
                new GenerationOptions(64, 0.7, -0.1),
            })
                await ThrowsAsync<ArgumentException>(
                    () => client.StreamChatAsync("model-a",
                        new[] { new ChatTurn("user", "invalid") },
                        Key, _ => { }, CancellationToken.None, invalid),
                    "invalid generation settings reached the network");
            bool fractionRejected = false;
            try { _ = GenerationOptions.FromNumbers(12.5, 0.7, 1); }
            catch (ArgumentException) { fractionRejected = true; }
            Check(fractionRejected, "fractional output token limit was accepted");
            await ThrowsAsync<ArgumentException>(
                () => client.StreamChatAsync("model-a",
                    new[] { new ChatTurn("system", "override") },
                    Key, _ => { }, CancellationToken.None),
                "SSE accepted an invalid chat role");
        }

        foreach (string invalidStream in new[]
        {
            Sse("partial"), // Missing both finish and [DONE].
            Sse("partial") + "data: [DONE]\n\n", // Missing finish.
            "data: {malformed}\n\n" + "data: [DONE]\n\n",
            Sse("[error: generation failed; see server logs for the request ID]")
                + Sse("", "stop") + "data: [DONE]\n\n",
        })
        {
            var invalid = new StubHandler((request, _) =>
                Task.FromResult(request.RequestUri!.AbsolutePath == "/v1/chat/completions"
                    ? Events(invalidStream) : ValidResponse(request)));
            using var client = new BackendClient(invalid, metadata);
            await ThrowsAsync<Exception>(
                () => client.StreamChatAsync("model-a",
                    new[] { new ChatTurn("user", "hello") },
                    null, _ => { }, CancellationToken.None),
                "truncated, malformed or failed stream was treated as complete");
        }

        // The stream is bounded even if a malicious or broken server never
        // emits a line terminator, and cancellation stops a pending request.
        var hugeEvent = new StubHandler((request, _) => Task.FromResult(
            request.RequestUri!.AbsolutePath == "/v1/chat/completions"
                ? Events(new string('q', 65_537)) : ValidResponse(request)));
        using (var client = new BackendClient(hugeEvent, metadata))
            await ThrowsAsync<IOException>(
                () => client.StreamChatAsync("model-a",
                    new[] { new ChatTurn("user", "hello") },
                    null, _ => { }, CancellationToken.None),
                "oversized SSE event was accepted");
        var streamStarted = new TaskCompletionSource<bool>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var blockingStream = new StubHandler(async (request, token) =>
        {
            if (request.RequestUri!.AbsolutePath == "/v1/chat/completions")
            {
                streamStarted.TrySetResult(true);
                await Task.Delay(Timeout.InfiniteTimeSpan, token);
            }
            return ValidResponse(request);
        });
        using (var client = new BackendClient(blockingStream, metadata))
        using (var stop = new CancellationTokenSource())
        {
            Task<string> waiting = client.StreamChatAsync("model-a",
                new[] { new ChatTurn("user", "hello") },
                null, _ => { }, stop.Token);
            await streamStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            stop.Cancel();
            await ThrowsAsync<OperationCanceledException>(
                async () => { _ = await waiting; },
                "cancellation did not stop the active SSE request");
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

        foreach (string state in new[] { "loaded", "ready_to_load", "queued", "loading" })
            Check(new ModelInfo("chat", "Chat", state).AvailableInChat,
                "downloaded/loading model missing from chat picker");
        foreach (string state in new[] { "not_downloaded", "converting", "error", "unknown" })
            Check(!new ModelInfo("chat", "Chat", state).AvailableInChat,
                "unavailable model offered in chat picker");

        string lockFile = Path.Combine(directory, "desktop-instance.lock");
        string commandFile = Path.Combine(directory, "tray-command.json");
        var quitTransport = new StubHandler((request, _) => Task.FromResult(ValidResponse(request)));
        using (var client = new BackendClient(quitTransport, metadata))
        {
            // A stopped server still has a tray controller. Quit via the local
            // command protocol and wait for ownership to be released.
            using (var owner = new FileStream(lockFile, FileMode.OpenOrCreate,
                FileAccess.ReadWrite, FileShare.ReadWrite))
            {
                Task quit = client.QuitApplicationAsync(CancellationToken.None);
                for (int retry = 0; retry < 100 && !File.Exists(commandFile); retry++)
                    await Task.Delay(20);
                Check(File.Exists(commandFile), "tray quit command was not written");
                using (var command = JsonDocument.Parse(File.ReadAllText(commandFile)))
                {
                    Check(command.RootElement.GetProperty("command").GetString() == "quit",
                        "quit used an unexpected tray command");
                    Check(command.RootElement.EnumerateObject().Count() == 2,
                        "quit command leaked credentials or session metadata");
                }
                Check(!quit.IsCompleted, "quit reported success while the tray still owned its lock");
                owner.Dispose();
                await quit.WaitAsync(TimeSpan.FromSeconds(5));
            }
            Check(quitTransport.RequestPaths.Count == 0,
                "tray quit contacted an HTTP control endpoint");
            Check(!Directory.EnumerateFiles(directory, "tray-command-*.tmp").Any(),
                "tray quit left a temporary command behind");
            File.Delete(commandFile);
            Task startingQuit = client.QuitApplicationAsync(CancellationToken.None, waitForStartup: true);
            await Task.Delay(40);
            Check(!startingQuit.IsCompleted, "quit left a pending engine launch behind");
            using (var startingOwner = new FileStream(lockFile, FileMode.OpenOrCreate,
                FileAccess.ReadWrite, FileShare.ReadWrite))
            {
                for (int retry = 0; retry < 100 && !File.Exists(commandFile); retry++)
                    await Task.Delay(20);
                Check(File.Exists(commandFile), "quit missed a newly started tray controller");
                startingOwner.Dispose();
                await startingQuit.WaitAsync(TimeSpan.FromSeconds(5));
            }
            File.Delete(commandFile);
            using (var cancelled = new CancellationTokenSource())
            {
                cancelled.Cancel();
                await ThrowsAsync<OperationCanceledException>(
                    () => client.QuitApplicationAsync(cancelled.Token), "cancelled quit still acted");
            }
            Check(!File.Exists(commandFile), "cancelled quit wrote a command");

            // Do not claim to shut down a standalone server we do not own.
            WriteMetadata(metadata);
            await ThrowsAsync<InvalidOperationException>(
                () => client.QuitApplicationAsync(CancellationToken.None),
                "quit accepted a live engine without its tray controller");
            Check(!File.Exists(commandFile), "standalone engine received a tray command");
            File.Delete(metadata);
            await client.QuitApplicationAsync(CancellationToken.None);
            Check(!File.Exists(commandFile), "already stopped application received a stale quit command");
        }
        Console.WriteLine("Native tray quit and chat model state contracts: all cases passed.");
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
