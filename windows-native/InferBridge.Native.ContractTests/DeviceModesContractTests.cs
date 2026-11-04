using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Combined-device builder: the native client consumes GET /v1/devices/modes
// (fixture generated from app/device_modes.py), never re-deriving support rules,
// and sends performance_hint / model_distribution only with the matching device.
// The Benchmark Lab "Compare combined modes" preset matches the web preset.
internal static class DeviceModesContractTests
{
    private const int Port = 19473;
    private const string Owner = "device-modes-owner-123456";
    private const string Key = "device-modes-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native combined devices: " + message);
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
        throw new InvalidOperationException("Native combined devices: " + message);
    }

    public static async Task RunAsync(string root)
    {
        string fixture = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures",
            "device-modes-builder.json"));
        using (var document = JsonDocument.Parse(fixture))
        {
            var builder = CombinedModes.Parse(document.RootElement);
            Check(builder.Choices.SequenceEqual(new[] { "GPU", "CPU", "NPU" })
                && builder.Members.SequenceEqual(new[] { "GPU", "CPU" }),
                "the engine's members and default order were not read");
            Check(builder.Options.Select(o => o.Id).SequenceEqual(
                new[] { "auto", "auto_cumulative", "hetero", "hetero_pipeline" }),
                "the four builder modes were not read in order");
            var cumulative = builder.Options.Single(o => o.Id == "auto_cumulative");
            Check(cumulative is { Device: "AUTO:GPU,CPU", PerformanceHint: "CUMULATIVE_THROUGHPUT", Experimental: true }
                && cumulative.Summary.Length > 0, "the cumulative AUTO toggle lost its hint or explanation");
            var pipeline = builder.Options.Single(o => o.Id == "hetero_pipeline");
            Check(pipeline.ModelDistribution == "pipeline_parallel"
                && pipeline.SupportFor(new[] { "chat" }).Support == "experimental"
                && pipeline.SupportFor(new[] { "embedding" }).Unsupported
                && pipeline.SupportFor(new[] { "embedding" }).Reason.Contains("chat", StringComparison.Ordinal),
                "pipeline-parallel support did not come from the engine verdicts");
            Check(builder.Options.Single(o => o.Id == "auto").SupportFor(new[] { "chat", "vlm" }).Support == "supported",
                "AUTO priority should be supported for chat and vision");
            var target = cumulative.ToTarget();
            Check(target == new CombinedTarget("AUTO:GPU,CPU", "CUMULATIVE_THROUGHPUT", null, "AUTO cumulative throughput")
                && target.PickerLabel.Contains("experimental", StringComparison.Ordinal),
                "builder targets must compare by value and be labelled experimental");
        }

        // Strictness: a hostile or newer engine cannot smuggle options the load route rejects.
        using (var hostile = JsonDocument.Parse("""
            {"builder_members":["GPU","CPU","TPU","GPU"],"builder":{"members":["GPU","CPU"],"options":[
              {"id":"auto","device":"AUTO:GPU,CPU","performance_hint":"FAST","backends":{}},
              {"id":"hetero","device":"HETERO:GPU,CPU","performance_hint":"LATENCY","backends":{}},
              {"id":"hetero_pipeline","device":"AUTO:GPU,CPU","model_distribution":"pipeline_parallel","backends":{}},
              {"id":"format_disk","device":"AUTO:GPU,CPU","backends":{}},
              {"id":"auto_cumulative","label":"Cumulative","device":"AUTO:GPU,CPU","performance_hint":"CUMULATIVE_THROUGHPUT",
               "backends":{"chat":{"support":"maybe","reason":"?"},"vlm":{"support":"unsupported","reason":"No."}}}]}}
            """))
        {
            var builder = CombinedModes.Parse(hostile.RootElement);
            Check(builder.Choices.SequenceEqual(new[] { "GPU", "CPU" }), "unknown or repeated devices were accepted");
            Check(builder.Options.Select(o => o.Id).SequenceEqual(new[] { "auto_cumulative" }),
                "an option with an invalid hint, distribution or id was accepted");
            var only = builder.Options[0];
            Check(only.SupportFor(new[] { "chat" }).Unsupported
                && only.SupportFor(new[] { "chat", "vlm" }).Unsupported
                && only.SupportFor(new[] { "vlm" }).Reason == "No.",
                "an unknown or missing verdict must count as unsupported (strictest wins)");
        }
        using (var empty = JsonDocument.Parse("""{"builder_members":["CPU"],"builder":null}"""))
            Check(CombinedModes.Parse(empty.RootElement).Options.Count == 0, "a one-device PC offered modes");

        Check(CombinedModes.Move(new[] { "GPU", "CPU", "NPU" }, 2, -1).SequenceEqual(new[] { "GPU", "NPU", "CPU" })
            && CombinedModes.Move(new[] { "GPU", "CPU" }, 0, -1).SequenceEqual(new[] { "GPU", "CPU" })
            && CombinedModes.Move(new[] { "GPU", "CPU" }, 1, 1).SequenceEqual(new[] { "GPU", "CPU" })
            && CombinedModes.Move(new[] { "GPU", "CPU" }, 0, 2).SequenceEqual(new[] { "GPU", "CPU" }),
            "Move up / Move down must swap neighbours only");
        Check(CombinedModes.KindsFor("chat", null).SequenceEqual(new[] { "chat", "vlm" })
            && CombinedModes.KindsFor("chat", true).SequenceEqual(new[] { "vlm" })
            && CombinedModes.KindsFor("rerank", null).SequenceEqual(new[] { "rerank" }),
            "model kinds for support lookups changed");

        string directory = Path.Combine(root, "device-modes");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Path, string Query, string Body)>();
        using var client = new BackendClient(new Handler(async (request, token) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path is "/health/live" or "/health/ready") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            calls.Add((path, request.RequestUri.Query, body));
            return path switch
            {
                "/v1/devices/modes" => Json(fixture),
                "/v1/models" => Json("""{"data":[{"id":"chat","status":"ready_to_load"},{"id":"live","status":"loaded"}]}"""),
                "/v1/models/load" => Json("""{"status":"queued"}"""),
                _ => throw new InvalidOperationException("Unexpected route " + path),
            };
        }), metadata);

        await client.GetCombinedModesAsync(new[] { "GPU", "CPU" }, Key, CancellationToken.None);
        Check(calls.Last().Query == "?members=GPU,CPU", "the ordered member list was not sent");
        await client.GetCombinedModesAsync(null, Key, CancellationToken.None);
        Check(calls.Last().Query == "", "the default builder request added members");
        int before = calls.Count;
        foreach (var bad in new[] { new[] { "GPU" }, new[] { "GPU", "GPU" }, new[] { "GPU", "TPU" } })
            await RejectsAsync<ArgumentException>(() => client.GetCombinedModesAsync(bad, Key, CancellationToken.None),
                "invalid members reached the engine");
        Check(calls.Count == before, "an invalid member list was sent");

        var chat = new ModelInfo("chat", "Chat", "ready_to_load");
        await client.ChangeModelAsync(chat, ModelAction.Load, Key, CancellationToken.None, "AUTO:GPU,CPU",
            performanceHint: "CUMULATIVE_THROUGHPUT");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(sent.RootElement.GetProperty("device").GetString() == "AUTO:GPU,CPU"
                && sent.RootElement.GetProperty("performance_hint").GetString() == "CUMULATIVE_THROUGHPUT"
                && !sent.RootElement.TryGetProperty("model_distribution", out _),
                "cumulative AUTO was not sent as performance_hint");
        var live = new ModelInfo("live", "Live", "loaded");
        await client.ChangeModelAsync(live, ModelAction.SwitchDevice, Key, CancellationToken.None, "HETERO:GPU,CPU",
            modelDistribution: "pipeline_parallel");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(sent.RootElement.GetProperty("model_distribution").GetString() == "pipeline_parallel"
                && !sent.RootElement.TryGetProperty("performance_hint", out _),
                "a device change did not carry pipeline-parallel");
        await client.ChangeModelAsync(chat, ModelAction.Load, Key, CancellationToken.None, "AUTO:GPU,CPU",
            contextTokens: 4096, allowResourceRisk: true, performanceHint: "LATENCY");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(sent.RootElement.GetProperty("context_tokens").GetInt32() == 4096
                && sent.RootElement.GetProperty("allow_resource_risk").GetBoolean()
                && sent.RootElement.GetProperty("performance_hint").GetString() == "LATENCY",
                "Load anyway / load with context dropped the builder option");
        await client.ChangeModelAsync(chat, ModelAction.Load, Key, CancellationToken.None, "GPU");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(!sent.RootElement.TryGetProperty("performance_hint", out _)
                && !sent.RootElement.TryGetProperty("model_distribution", out _),
                "a plain device load sent builder options");
        before = calls.Count;
        await RejectsAsync<ArgumentException>(() => client.ChangeModelAsync(chat, ModelAction.Load, Key,
            CancellationToken.None, "HETERO:GPU,CPU", performanceHint: "LATENCY"), "a hint on HETERO was accepted");
        await RejectsAsync<ArgumentException>(() => client.ChangeModelAsync(chat, ModelAction.Load, Key,
            CancellationToken.None, "AUTO:GPU,CPU", modelDistribution: "pipeline_parallel"), "pipeline-parallel on AUTO was accepted");
        await RejectsAsync<ArgumentException>(() => client.ChangeModelAsync(chat, ModelAction.Load, Key,
            CancellationToken.None, "AUTO:GPU,CPU", performanceHint: "TURBO"), "an unknown hint was accepted");
        await RejectsAsync<ArgumentException>(() => client.ChangeModelAsync(live, ModelAction.Unload, Key,
            CancellationToken.None, performanceHint: "LATENCY"), "an unload accepted device options");
        Check(calls.Count == before, "a refused device option reached the engine");

        // Benchmark Lab "Compare combined modes" = the web preset.
        using (var status = JsonDocument.Parse("""
            {"models":{"available":[{"id":"gen","name":"Gen","backend":"openvino-genai","status":"loaded","is_loaded":true,"is_downloaded":true}]},
             "device":{"available":["CPU","GPU.0","NPU"],"mock":false}}
            """))
        {
            var catalog = BenchmarkLab.ReadCatalog(status.RootElement, "device-modes-session");
            var preset = BenchmarkLab.CombinedModesPreset(catalog);
            Check(preset.SequenceEqual(new[] { "CPU", "GPU", "NPU", "AUTO", "HETERO:GPU,CPU", "MULTI:GPU,CPU" }),
                "the combined-modes preset differs from the web preset");
            var plan = BenchmarkLab.CreatePlan(catalog, new[] { "gen" }, preset, "quick");
            Check(plan.Combinations == 6 && plan.Runs == 3 && plan.MaxTokens == 32,
                "the preset is not one model × six targets on Quick");
        }
        using (var cpuOnly = JsonDocument.Parse("""
            {"models":{"available":[{"id":"gen","name":"Gen","backend":"openvino-genai","status":"loaded","is_loaded":true,"is_downloaded":true}]},
             "device":{"available":["CPU"],"mock":false}}
            """))
        {
            var catalog = BenchmarkLab.ReadCatalog(cpuOnly.RootElement, "device-modes-session");
            Check(BenchmarkLab.CombinedModesPreset(catalog).SequenceEqual(new[] { "CPU", "AUTO" }),
                "combined targets were offered without both a GPU and a CPU");
            try
            {
                BenchmarkLab.CreatePlan(catalog, new[] { "gen" }, new[] { "HETERO:GPU,CPU" }, "quick");
                throw new InvalidOperationException("Native combined devices: an undetected combined target was planned");
            }
            catch (ArgumentException) { }
        }

        // VRAM-aware advisor suggestion: only the engine's exact shape is shown.
        using (var status = JsonDocument.Parse("""
            {"models":{"available":[
              {"id":"big","advisor":{"device_suggestion":{"device":"HETERO:GPU,CPU","model_distribution":"pipeline_parallel","reason":"Fits GPU plus RAM."}}},
              {"id":"odd","advisor":{"device_suggestion":{"device":"HETERO:NPU,CPU","model_distribution":"pipeline_parallel"}}},
              {"id":"none","advisor":{"device_suggestion":null}}]}}
            """))
        {
            var hints = DeviceSplitSuggestion.Parse(status.RootElement);
            Check(hints.Count == 1 && hints["big"].Reason == "Fits GPU plus RAM.",
                "an unexpected split suggestion was accepted or the real one dropped");
        }

        Console.WriteLine("Native combined-device builder contracts: all cases passed.");
    }
}
