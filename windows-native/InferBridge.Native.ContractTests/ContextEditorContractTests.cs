using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// The shared per-model context editor: presets capped at the model maximum,
// custom-value validation, the trial flag, apply-now vs next-load messaging,
// and the HTTP calls the model cards, load step, chat pane and Settings share.
// Responses are simulated; no engine is rebuilt and no inference runs.
internal static class ContextEditorContractTests
{
    private const int Port = 19466;
    private const string Owner = "context-editor-owner-123456";
    private const string Key = "context-editor-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native context editor: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action, string message) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Native context editor: " + message);
    }

    private static ContextConfiguration Config(int current = 8192, int recommended = 8192,
        int? limit = 32768, int output = 512, bool loaded = false, bool kv = true,
        string precision = "auto", bool kvApplies = true) =>
        new("model-a", current, recommended, current - output, output, limit, loaded,
            loaded ? "CPU" : null, new[]
            {
                new ContextOption(current, "recommended", kv ? current * 0.000002 : null,
                    null, "unverified-memory"),
            }, precision, kvApplies);

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
        // --- Presets: common sizes, capped at the model maximum -------------------
        var unloaded = Config();
        var presets = ContextEditor.Presets(unloaded);
        Check(presets.Select(row => row.Tokens).SequenceEqual(new[] { 2048, 4096, 8192, 16384, 32768 }),
            "presets were not capped at the architecture maximum");
        Check(presets.Single(row => row.Tokens == 8192) is { Current: true, Recommended: true, Trial: false },
            "current/recommended preset was mislabeled");
        Check(presets.Where(row => row.Tokens > 8192).All(row => row.Trial)
            && presets.Where(row => row.Tokens <= 8192).All(row => !row.Trial),
            "values above the recommendation were not marked trial");
        Check(presets.Single(row => row.Tokens == 32768).Label
                == "32K · 32,768 tokens (max for this model, trial)",
            "preset label is not readable");
        Check(presets.Count(row => row.ModelMax) == 1 && presets.Last().ModelMax,
            "the model maximum was not offered as one preset");
        var qwen3 = ContextEditor.Presets(Config(current: 4096, recommended: 4096, limit: 40960));
        Check(qwen3.Last() is { Tokens: 40960, ModelMax: true, Trial: true }
            && qwen3.Select(row => row.Tokens).SequenceEqual(new[] { 2048, 4096, 8192, 16384, 32768, 40960 }),
            "a non-standard model maximum was not offered as max for this model");
        var huge = ContextEditor.Presets(Config(limit: 262144));
        Check(huge.Last() is { Tokens: 262144, ModelMax: true }
            && huge.Where(row => !row.ModelMax).Last().Tokens == 131072,
            "standard presets exceeded the largest common size or omitted the model maximum");
        Check(ContextEditor.Presets(Config(current: 4096, recommended: 4096, limit: null))
                .Select(row => row.Tokens).SequenceEqual(new[] { 2048, 4096 })
            && !ContextEditor.Presets(Config(current: 4096, recommended: 4096, limit: null))
                .Any(row => row.ModelMax),
            "without verified metadata the presets exceeded the recommendation");
        Check(ContextEditor.Presets(Config(current: 8192, output: 4096)).First().Tokens == 8192,
            "presets ignored the output reserve");
        Check(ContextEditor.UpperBound(unloaded) == 32768 && ContextEditor.LowerBound(unloaded) == 577,
            "bounds disagree with the engine's validation rules");

        // --- Custom values ----------------------------------------------------------
        var custom = ContextEditor.Validate(unloaded, 20000);
        Check(custom is { Valid: true, Tokens: 20000, Trial: true }, "a valid custom value was refused");
        Check(custom.Message.Contains("19,488 prompt tokens", StringComparison.Ordinal)
            && custom.Message.Contains("Trial", StringComparison.Ordinal)
            && custom.KvEstimateGib is double kv && Math.Abs(kv - 0.04) < 0.0005,
            "custom value guidance or KV-cache estimate is wrong");
        Check(ContextEditor.Validate(unloaded, 6000) is { Valid: true, Trial: false },
            "a value below the recommendation was flagged as a trial");
        foreach (double? bad in new double?[] { null, double.NaN, 4096.5, 100, 300000 })
            Check(!ContextEditor.Validate(unloaded, bad).Valid
                && ContextEditor.Validate(unloaded, bad).Message.StartsWith("Enter a whole number",
                    StringComparison.Ordinal),
                "an invalid number was accepted: " + bad);
        Check(ContextEditor.Validate(unloaded, 512).Message.Contains("Leave room for the prompt",
            StringComparison.Ordinal), "too-small context did not explain the output reserve");
        Check(ContextEditor.Validate(unloaded, 65536).Message == "This model supports at most 32,768 tokens.",
            "over-limit message is not friendly");
        Check(ContextEditor.Validate(Config(current: 4096, recommended: 4096, limit: null), 8192)
                .Message.Contains("Without verified model metadata", StringComparison.Ordinal),
            "missing metadata allowed a larger context");
        Check(ContextEditor.Validate(Config(kv: false), 4096).KvEstimateGib is null
            && ContextEditor.Validate(Config(kv: false), 4096).Message.Contains("unavailable",
                StringComparison.Ordinal), "missing KV metadata invented an estimate");

        // --- KV-cache precision (CPU/GPU only) -----------------------------------------
        Check(ContextEditor.KvPrecisions.SequenceEqual(new[] { "auto", "f16", "u8", "u4" }),
            "KV precision choices changed");
        var choices = ContextEditor.KvChoices(unloaded);
        Check(choices.Count == 4 && choices.All(row => row.Available)
            && choices.Single(row => row.Value == "u4").Label.Contains("INT4", StringComparison.Ordinal),
            "KV precision choices were not all offered for CPU/GPU");
        var npu = Config(loaded: true, kvApplies: false);
        Check(ContextEditor.KvChoices(npu).Where(row => row.Value != "auto").All(row => !row.Available)
            && ContextEditor.KvChoices(npu).Single(row => row.Value == "auto").Available,
            "explicit KV precision was offered on the NPU");
        double fp16 = ContextEditor.EstimateKvGib(unloaded, 32768, "f16")!.Value;
        Check(Math.Abs(fp16 - 0.066) < 0.001
            && Math.Abs(ContextEditor.EstimateKvGib(unloaded, 32768, "u8")!.Value - fp16 * 1.125 / 2) < 0.001
            && Math.Abs(ContextEditor.EstimateKvGib(unloaded, 32768, "u4")!.Value - fp16 * 0.625 / 2) < 0.001
            && ContextEditor.EstimateKvGib(unloaded, 32768, "auto") == fp16,
            "KV estimate did not scale with precision");
        Check(ContextEditor.EstimateKvGib(npu, 32768, "u4") == fp16,
            "an NPU estimate used a KV precision the NPU never receives");
        Check(ContextEditor.Validate(unloaded, 16384, "u4").Message.Contains("INT4 estimate", StringComparison.Ordinal)
            && ContextEditor.Validate(unloaded, 16384).Message.Contains("Auto, FP16 upper bound estimate",
                StringComparison.Ordinal),
            "KV estimate label does not name the precision");
        Check(ContextEditor.CanCommit(unloaded, ContextEditor.Validate(unloaded, 8192, "u8"),
                ContextEditorMode.Edit, "u8")
            && !ContextEditor.CanCommit(unloaded, ContextEditor.Validate(unloaded, 8192, "auto"),
                ContextEditorMode.Edit, "auto"),
            "a precision-only change could not be committed");

        // --- Apply-now vs next-load messaging ---------------------------------------
        Check(ContextEditor.PrimaryAction(ContextEditorMode.Edit, loaded: true)
                == "Apply now (reloads engine, chat kept)"
            && ContextEditor.PrimaryAction(ContextEditorMode.Edit, loaded: false) == "Save for next load"
            && ContextEditor.PrimaryAction(ContextEditorMode.LoadWith, loaded: false) == "Load with this context",
            "primary action text changed");
        Check(ContextEditor.WhenItApplies(ContextEditorMode.Edit, false) == "Used next time this model loads."
            && ContextEditor.WhenItApplies(ContextEditorMode.Edit, true).Contains("chat history is kept",
                StringComparison.Ordinal), "when-it-applies messaging changed");
        Check(ContextEditor.SavedMessage(4096, applied: false).EndsWith("Used next time this model loads.",
                StringComparison.Ordinal)
            && ContextEditor.SavedMessage(4096, applied: true).Contains("rebuilt", StringComparison.Ordinal),
            "saved confirmation does not say when the value applies");
        Check(!ContextEditor.CanCommit(unloaded, ContextEditor.Validate(unloaded, 8192), ContextEditorMode.Edit)
            && ContextEditor.CanCommit(unloaded, ContextEditor.Validate(unloaded, 8192), ContextEditorMode.LoadWith)
            && ContextEditor.CanCommit(unloaded, ContextEditor.Validate(unloaded, 4096), ContextEditorMode.Edit),
            "commit gating is wrong");
        Check(ContextEditor.ChipText(8192) == "Context: 8,192 tokens"
            && ContextEditor.ChipText(null) == "Context: not reported", "model card chip text changed");
        Check(ContextEditor.Facts(unloaded) == "Current 8,192 · Recommended 8,192 · Model maximum 32,768",
            "facts line changed");

        // --- HTTP: list, custom set, load-with-context ------------------------------
        string directory = Path.Combine(root, "context-editor");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Method, string Path, string Body)>();
        string appliedKv = "auto";
        string ContextBody(int current) =>
            "{\"model_id\":\"model-a\",\"configured_context_tokens\":" + current
            + ",\"recommended_context_tokens\":8192,\"prompt_budget_tokens\":" + (current - 512)
            + ",\"reserved_output_tokens\":512,\"model_architecture_limit_tokens\":32768,\"loaded\":false,"
            + "\"options\":[{\"tokens\":" + current + ",\"classification\":\"recommended\","
            + "\"fp16_kv_cache_gib_estimate\":0.016,\"fit_guidance\":\"unverified-memory\"}],"
            + "\"kv_cache_precision\":\"" + appliedKv + "\",\"kv_cache_precision_applies\":true,"
            + "\"kv_cache_precision_options\":[{\"value\":\"auto\",\"label\":\"Auto (OpenVINO default)\",\"available\":true},"
            + "{\"value\":\"u4\",\"label\":\"INT4 (u4)\",\"available\":true}],"
            + "\"context_guidance\":\"NPU-safe default\",\"kv_cache_precision_note\":\"CPU and GPU\"}";
        using var client = new BackendClient(new Handler(async (request, token) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (request.RequestUri.Host != "127.0.0.1" || request.RequestUri.Port != Port)
                throw new InvalidOperationException("Client left the verified loopback engine.");
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path == "/health/live") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            calls.Add((request.Method.Method, path, body));
            return path switch
            {
                "/v1/models" => Json("""{"data":[{"id":"model-a","name":"A","status":"ready_to_load"}]}"""),
                "/v1/models/contexts" => Json("""{"object":"list","data":[{"model_id":"model-a","name":"A","configurable":true,"configured_context_tokens":8192,"recommended_context_tokens":8192,"model_architecture_limit_tokens":32768,"loaded":false,"trial":false},{"model_id":"embed","name":"E","configurable":false,"configured_context_tokens":null,"loaded":false}]}"""),
                "/v1/models/context" => Json("{\"status\":\"applied\",\"context\":" + ContextBody(20000) + "}"),
                "/v1/models/load" => Json("""{"status":"queued"}"""),
                _ => throw new InvalidOperationException("Unexpected route " + path),
            };
        }), metadata);

        var summaries = await client.GetContextSummariesAsync(Key, CancellationToken.None);
        Check(summaries.Count == 2 && summaries[0] is { ModelId: "model-a", Current: 8192, Configurable: true,
                ArchitectureLimit: 32768, Loaded: false }
            && summaries[1] is { Configurable: false, Current: null },
            "the all-models context list was parsed incorrectly");

        var applied = await client.SetContextAsync(unloaded, 20000, Key, CancellationToken.None);
        Check(applied.Current == 20000, "a custom value was not confirmed by the engine");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/context").Body))
            Check(sent.RootElement.GetProperty("context_tokens").GetInt32() == 20000
                && sent.RootElement.GetProperty("expected_context_tokens").GetInt32() == 8192,
                "custom set omitted the expected-current guard");
        Check(applied.KvPrecision == "auto" && applied.Guidance == "NPU-safe default"
            && applied.KvOptions is { Count: 2 },
            "KV precision facts were not parsed from the engine");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/context").Body))
            Check(!sent.RootElement.TryGetProperty("kv_cache_precision", out _),
                "a context-only change started sending a KV precision");
        appliedKv = "u4";
        var withKv = await client.SetContextAsync(unloaded, 20000, Key, CancellationToken.None, "u4");
        Check(withKv.KvPrecision == "u4", "KV precision was not confirmed by the engine");
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/context").Body))
            Check(sent.RootElement.GetProperty("kv_cache_precision").GetString() == "u4",
                "the chosen KV precision was not sent");
        appliedKv = "auto";
        await RejectsAsync<System.Text.Json.JsonException>(
            () => client.SetContextAsync(unloaded, 20000, Key, CancellationToken.None, "u8"),
            "a different engine-reported KV precision was accepted");
        int before = calls.Count;
        await RejectsAsync<ArgumentException>(
            () => client.SetContextAsync(unloaded, 20000, Key, CancellationToken.None, "int4"),
            "an unknown KV precision reached the engine");
        Check(calls.Count == before, "an invalid KV precision was sent to the engine");
        await RejectsAsync<ArgumentException>(
            () => client.SetContextAsync(unloaded, 65536, Key, CancellationToken.None),
            "an over-limit value reached the engine");
        Check(calls.Count == before, "an invalid value was sent to the engine");

        var model = new ModelInfo("model-a", "A", "ready_to_load");
        string message = await client.ChangeModelAsync(model, ModelAction.Load, Key,
            CancellationToken.None, device: null, contextTokens: 4096);
        using (var load = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(load.RootElement.GetProperty("context_tokens").GetInt32() == 4096,
                "load-with-context did not send the chosen context");
        Check(message.Contains("4,096-token context", StringComparison.Ordinal),
            "load-with-context confirmation does not name the context");
        await client.ChangeModelAsync(model, ModelAction.Load, Key, CancellationToken.None);
        using (var plain = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/models/load").Body))
            Check(!plain.RootElement.TryGetProperty("context_tokens", out _),
                "an ordinary load started sending a context");
        await RejectsAsync<ArgumentException>(() => client.ChangeModelAsync(model, ModelAction.Unload, Key,
            CancellationToken.None, contextTokens: 4096), "context was accepted for a non-load action");
        Check(!calls.Any(call => call.Path == "/v1/chat/completions"), "editing context started inference");

        Console.WriteLine("Native per-model context editor contracts: all cases passed.");
    }
}
