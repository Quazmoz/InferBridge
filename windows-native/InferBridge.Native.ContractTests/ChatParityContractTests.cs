using System.Net;
using System.Text.Json;

namespace InferBridge.Native;

// Eject, Regenerate, the exact context budget, saved chat defaults/theme and
// the engine activity feed: shared native logic and simulated HTTP contracts.
internal static class ChatParityContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native chat parity: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static string Budget(string model = "model-a", bool truncate = false, bool limited = false,
        bool blocked = false, double percent = 12.5, string? omit = null) => JsonSerializer.Serialize(new
    {
        model, model_name = "A", prompt_tokens = 384, max_prompt_tokens = 3072, prompt_budget_percent = percent,
        max_context_tokens = 4096, model_output_reserve_tokens = 1024, requested_output_tokens = 512,
        available_output_tokens = blocked ? 0 : 3704, effective_output_tokens = limited ? 200 : 512,
        output_limited = limited, safety_tokens = 8, context_usage_tokens = 904, context_usage_percent = 22.1,
        message_count = 5, retained_message_count = truncate ? 3 : 5, dropped_message_count = truncate ? 2 : 0,
        dropped_turn_count = truncate ? 1 : 0,
        dropped_messages = truncate
            ? new object[] { new { index = 1, role = "user", preview = omit ?? "first question" },
                new { index = 2, role = "assistant", preview = "first answer" } }
            : Array.Empty<object>(),
        dropped_preview_truncated = false, will_truncate = truncate, prompt_over_budget = false,
        blocked, system_instructions_retained = true, attachment_count = 1,
        attachment_token_estimate = 512, attachment_estimate_per_image = 512,
    });

    public static async Task RunAsync(string root)
    {
        // --- Shared transcript rules (Send, Regenerate and context check) ---
        var history = new List<ChatTurn>();
        for (int i = 0; i < 24; i++)
            history.Add(new ChatTurn(i % 2 == 0 ? "user" : "assistant", "turn " + i));
        var request = ChatTranscript.ForRequest(history, new ChatTurn("user", "next"));
        Check(request.Count <= ChatTranscript.MaximumRequestTurns && request[0].Role == "user"
            && request[^1].Content == "next", "request window was not trimmed by whole pairs");
        var big = new List<ChatTurn> { new("user", new string('a', 7000)), new("assistant", new string('b', 7000)),
            new("user", new string('c', 7000)), new("assistant", new string('d', 7000)) };
        var trimmed = ChatTranscript.ForRequest(big, new ChatTurn("user", new string('e', 7000)));
        Check(trimmed.Sum(t => t.Content.Length) <= ChatTranscript.MaximumRequestChars
            && trimmed[0].Role == "user", "character budget was not enforced");
        Check(!ChatTranscript.CanRegenerate(Array.Empty<ChatTurn>())
            && !ChatTranscript.CanRegenerate([new ChatTurn("user", "q")])
            && ChatTranscript.CanRegenerate([new ChatTurn("user", "q"), new ChatTurn("assistant", "a")]),
            "only a completed newest reply can be regenerated");
        var regenerate = ChatTranscript.ForRegenerate(
            [new ChatTurn("user", "q1"), new ChatTurn("assistant", "a1"), new ChatTurn("user", "q2"), new ChatTurn("assistant", "a2")]);
        Check(regenerate.Count == 3 && regenerate[^1] == new ChatTurn("user", "q2"),
            "regenerate did not resend the history without the newest reply");
        Check(Throws<InvalidOperationException>(() => ChatTranscript.ForRegenerate([new ChatTurn("user", "q")])),
            "regenerate without a reply was accepted");

        // --- Model lifecycle helpers ---
        ModelInfo[] models =
        [
            new("model-a", "A", "loaded"), new("model-b", "B", "loaded"),
            new("model-c", "C", "ready_to_load"),
        ];
        Check(ModelLifecycle.LoadedPeers(models, "model-c").Select(m => m.Id).SequenceEqual(["model-a", "model-b"])
            && ModelLifecycle.LoadedPeers(models, "model-a").Single().Id == "model-b",
            "loaded peers were not the other loaded models");
        Check(ModelLifecycle.PickerPlaceholder(models[2..]) == "No model loaded"
            && ModelLifecycle.PickerPlaceholder(models) == "Select a model", "picker placeholder changed");
        Check(ModelLifecycle.IsBusyRefusal(new EngineRequestException("Model 'model-a' is busy"))
            && !ModelLifecycle.IsBusyRefusal(new EngineRequestException("Model 'model-a' is not loaded"))
            && !ModelLifecycle.IsBusyRefusal(new InvalidOperationException("is busy")),
            "busy refusal detection is too broad or too narrow");

        // --- Eject: POST /v1/models/unload, retrying only "is busy" ---
        int busyLeft = 2;
        string unloadDetail = "";
        string budgetBody = Budget();
        string eventsBody = "{}";
        string telemetryBody = "{}";
        var engine = new FakeEngine
        {
            Route = (request, body) => request.RequestUri!.AbsolutePath switch
            {
                "/v1/models/unload" when unloadDetail.Length > 0 => FakeEngine.Json(
                    JsonSerializer.Serialize(new { detail = unloadDetail }), HttpStatusCode.Conflict),
                "/v1/models/unload" when busyLeft-- > 0 => FakeEngine.Json(
                    """{"detail":"Model 'model-a' is busy"}""", HttpStatusCode.Conflict),
                "/v1/models/unload" => FakeEngine.Json(
                    """{"status":"unloaded","message":"Unloaded A.","model":{"id":"model-a"}}"""),
                "/v1/chat/context-budget" => FakeEngine.Json(budgetBody),
                "/v1/events" => FakeEngine.Json(eventsBody),
                "/v1/system/telemetry" => FakeEngine.Json(telemetryBody),
                _ => throw new InvalidOperationException("Unexpected route " + request.RequestUri.AbsolutePath),
            },
        };
        using var client = engine.CreateClient(root, "chat-parity");
        string key = FakeEngine.Key;
        var loaded = new ModelInfo("model-a", "A", "loaded");
        int unloads() => engine.Calls.Count(call => call.Path == "/v1/models/unload");
        string message = await client.UnloadModelAsync(loaded, key, CancellationToken.None,
            retryDelay: TimeSpan.FromMilliseconds(1));
        Check(message.Contains("unload", StringComparison.OrdinalIgnoreCase) && unloads() == 3,
            "unload did not retry the busy refusal until accepted");
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
            Check(sent.RootElement.GetProperty("model").GetString() == "model-a"
                && sent.RootElement.EnumerateObject().Count() == 1, "unload body was not just the model id");
        busyLeft = 5;
        await RejectsAsync<EngineRequestException>(() => client.UnloadModelAsync(
            loaded, key, CancellationToken.None, busyRetries: 0));
        Check(unloads() == 4, "a busy refusal was retried with retries disabled");
        busyLeft = 0;
        unloadDetail = "Model 'model-a' is not loaded";
        await RejectsAsync<EngineRequestException>(() => client.UnloadModelAsync(
            loaded, key, CancellationToken.None, retryDelay: TimeSpan.FromMilliseconds(1)));
        Check(unloads() == 5, "a non-busy refusal was retried");
        unloadDetail = "";
        // The engine inventory, not the window's stale row, decides: an unknown model is refused before POST.
        await RejectsAsync<InvalidOperationException>(() => client.UnloadModelAsync(
            new ModelInfo("model-z", "Z", "loaded"), key, CancellationToken.None));
        Check(unloads() == 5, "an unload for a model the engine does not report as loaded was sent");
        await RejectsAsync<UnauthorizedAccessException>(() => client.UnloadModelAsync(
            loaded, null, CancellationToken.None));

        // --- Context budget: POST /v1/chat/context-budget ---
        var turns = new List<ChatTurn> { new("user", "hello") };
        var image = ChatImage.FromBytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 13]);
        var budget = await client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None,
            new GenerationOptions(256, 0.3, 0.9), new ChatRequestContext("Be brief.", [image]));
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
        {
            var body = sent.RootElement;
            Check(body.GetProperty("model").GetString() == "model-a"
                && body.GetProperty("max_tokens").GetInt32() == 256
                && body.GetProperty("image_count").GetInt32() == 1
                && body.GetProperty("messages")[0].GetProperty("role").GetString() == "system"
                && body.GetProperty("messages")[1].GetProperty("content").GetString() == "hello"
                && !engine.Calls.Last().Body.Contains("image_url", StringComparison.Ordinal),
                "context check did not mirror the chat request (images counted, not uploaded)");
        }
        Check(budget.PromptTokens == 384 && budget.MaxPromptTokens == 3072 && budget.State == "ready"
            && budget.Summary == "Context 384 / 3,072" && budget.Notice.StartsWith("Everything fits", StringComparison.Ordinal)
            && budget.AttachmentCount == 1, "context budget was not parsed");
        budgetBody = Budget(truncate: true, omit: "secret\u0007 question");
        budget = await client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None);
        Check(budget.State == "warning" && budget.Summary.EndsWith("· 1 turn omitted", StringComparison.Ordinal)
            && budget.Omitted.Count == 2 && budget.Omitted[0].Preview == "secret question"
            && budget.Notice.Contains("1 older turn", StringComparison.Ordinal), "omissions were not reported");
        budgetBody = Budget(limited: true);
        budget = await client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None);
        Check(budget.OutputLimited && budget.EffectiveOutputTokens == 200 && budget.Notice.Contains("200"),
            "output limit was not reported");
        budgetBody = Budget(blocked: true);
        budget = await client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None);
        Check(budget.State == "danger" && budget.Notice.Contains("no generation room"), "blocked prompt not reported");
        budgetBody = Budget(model: "model-b");
        await RejectsAsync<JsonException>(() => client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None));
        budgetBody = """{"model":"model-a","prompt_tokens":"many"}""";
        await RejectsAsync<JsonException>(() => client.GetContextBudgetAsync("model-a", turns, key, CancellationToken.None));
        int posts = engine.Calls.Count(call => call.Path == "/v1/chat/context-budget");
        await RejectsAsync<ArgumentException>(() => client.GetContextBudgetAsync(
            "model-a", Array.Empty<ChatTurn>(), key, CancellationToken.None));
        await RejectsAsync<ArgumentException>(() => client.GetContextBudgetAsync(
            "model-a", turns, key, CancellationToken.None, new GenerationOptions(0, 0.7, 1)));
        Check(engine.Calls.Count(call => call.Path == "/v1/chat/context-budget") == posts,
            "invalid context checks reached the engine");

        // --- Engine activity: GET /v1/events ---
        long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var items = new List<object>();
        for (int i = 1; i <= 30; i++)
            items.Add(new { id = i, timestamp = now - 100 + i, level = i == 30 ? "error" : i == 29 ? "bogus" : "info",
                message = i == 28 ? "line\u0000break  spaced" : "Event " + i });
        items.Add(new { id = 31, timestamp = "soon", level = "info", message = "bad" });
        eventsBody = JsonSerializer.Serialize(new { @object = "list", data = items });
        var events = await client.GetEngineEventsAsync(key, CancellationToken.None);
        Check(engine.Calls.Last().Path == "/v1/events?limit=100", "events request changed");
        Check(events.Count == EngineEvent.MaximumShown && events[0].Message == "Event 30" && events[0].Level == "error"
            && events[1].Level == "info" && events[2].Message == "linebreak spaced"
            && events.All(e => e.Message != "bad"), "events were not newest-first, bounded and sanitized");
        eventsBody = """{"data":"nope"}""";
        await RejectsAsync<JsonException>(() => client.GetEngineEventsAsync(key, CancellationToken.None));
        var at = DateTimeOffset.FromUnixTimeSeconds(1_000_000);
        Check(EngineEvent.Relative(at, at.AddSeconds(3)) == "just now"
            && EngineEvent.Relative(at, at.AddSeconds(42)) == "42s ago"
            && EngineEvent.Relative(at, at.AddMinutes(5)) == "5m ago"
            && EngineEvent.Relative(at, at.AddHours(3)) == "3h ago"
            && EngineEvent.Relative(at, at.AddDays(2)) == "2d ago"
            && EngineEvent.Relative(at, at.AddSeconds(-30)) == "just now", "relative times differ from the web feed");

        // --- Hardware advisor: metrics.advisor of GET /v1/system/telemetry ---
        telemetryBody = JsonSerializer.Serialize(new
        {
            metrics = new
            {
                advisor = new
                {
                    default_profile = "balanced",
                    profile_order = new[] { "fastest", "balanced", "Bad Slug", "lowest-memory" },
                    profile_labels = new Dictionary<string, string> { ["fastest"] = "Fastest", ["balanced"] = "Balanced" },
                    profiles = new Dictionary<string, object?>
                    {
                        ["fastest"] = new
                        {
                            model_id = "model-a", model_name = "A", device = "GPU", precision = "int4",
                            context_length = 4096, output_tokens = 512, compatibility = "compatible", fit_score = 91.6,
                            reason = "Fast on this GPU.", warnings = new[] { new { message = "Uses most free memory" } },
                            benchmark = new { success = true, decode_tokens_sec = 21.5, time_to_first_token_ms = 240.0, synthetic = false },
                        },
                        ["balanced"] = new
                        {
                            model_id = "model-c", model_name = "C", device = "CPU", precision = "int8",
                            context_length = 2048, output_tokens = 256, compatibility = "weird", fit_score = 400,
                            reason = "Balanced.", warnings = Array.Empty<object>(),
                            benchmark = new { success = false, decode_tokens_sec = 99 },
                        },
                        ["lowest-memory"] = null,
                    },
                    models = new object[]
                    {
                        new { id = "model-c", name = "C", backend = "openvino-genai", compatibility = "caution", fit_score = 40, recommended_device = "CPU", runtime_memory_gb = 3.2, warnings = Array.Empty<object>() },
                        new { id = "model-a", name = "A", backend = "openvino-genai", compatibility = "compatible", fit_score = 91.6, recommended_device = "GPU", warnings = new[] { new { message = "Tight" } } },
                        new { id = "embed", name = "E", backend = "openvino-embeddings", compatibility = "compatible", fit_score = 99 },
                    },
                    estimates_notice = "Estimates until benchmarked.",
                },
            },
        });
        var advisor = await client.GetHardwareAdvisorAsync(key, CancellationToken.None);
        Check(advisor.Profiles.Select(p => p.Id).SequenceEqual(["fastest", "balanced", "lowest-memory"])
            && advisor.Profiles[2].Label == "lowest-memory" && advisor.DefaultProfile == "balanced",
            "advisor profiles were not read in order with labels");
        var fastest = advisor.Recommendations["fastest"]!;
        Check(fastest.ModelId == "model-a" && fastest.Pills.SequenceEqual(["GPU", "INT4", "4,096 context", "512 output", "Fit 92/100"])
            && fastest.MeasuredTokensPerSecond == 21.5 && fastest.MeasuredFirstTokenMs == 240 && fastest.Warnings.Single() == "Uses most free memory",
            "advisor recommendation was not parsed");
        var balanced = advisor.Recommendations["balanced"]!;
        Check(balanced.Compatibility == "unknown" && balanced.FitScore == 0 && balanced.MeasuredTokensPerSecond is null,
            "out-of-range advisor values or failed benchmarks were trusted");
        Check(advisor.Recommendations["lowest-memory"] is null, "an empty profile invented a recommendation");
        Check(advisor.Models.Select(m => m.Id).SequenceEqual(["model-a", "model-c"])
            && advisor.Models[1].RuntimeMemoryGb == 3.2 && advisor.Notice == "Estimates until benchmarked.",
            "advisor table was not generation-only and sorted by fit");
        telemetryBody = """{"metrics":{}}""";
        await RejectsAsync<JsonException>(() => client.GetHardwareAdvisorAsync(key, CancellationToken.None));

        // --- Saved chat defaults and theme ---
        string folder = Path.Combine(root, "preferences");
        string path = Path.Combine(folder, "native-preferences.json");
        Check(NativePreferences.Load(path) == NativePreferences.Default, "missing preferences were not defaults");
        var mine = new NativePreferences("dark", "Answer in French.", new GenerationOptions(1024, 0.2, 0.8));
        mine.Save(path);
        var loadedPrefs = NativePreferences.Load(path);
        Check(loadedPrefs.Theme == "dark" && loadedPrefs.DefaultSystemPrompt == "Answer in French."
            && loadedPrefs.DefaultGeneration == new GenerationOptions(1024, 0.2, 0.8), "preferences did not round-trip");
        Check(!File.ReadAllText(path).Contains("key", StringComparison.OrdinalIgnoreCase)
            && Directory.GetFiles(folder).Length == 1, "preferences stored extra data or left temporary files");
        Check(Throws<ArgumentException>(() => new NativePreferences("neon", null, GenerationOptions.Default).Save(path))
            && Throws<ArgumentException>(() => new NativePreferences("light", null, new GenerationOptions(512, 3, 1)).Save(path))
            && NativePreferences.Load(path).Theme == "dark", "invalid preferences were saved");
        Check(new NativePreferences("light", "   ", GenerationOptions.Default).Validated().DefaultSystemPrompt is null,
            "a blank default system prompt was kept");
        File.WriteAllText(path, "{not json");
        Check(NativePreferences.Load(path) == NativePreferences.Default && File.ReadAllText(path) == "{not json",
            "corrupt preferences were not ignored and preserved");
        File.WriteAllText(path, """{"Version":1,"Theme":"light","Temperature":9}""");
        Check(NativePreferences.Load(path) == NativePreferences.Default, "out-of-range saved values were accepted");
        File.WriteAllText(path, """{"Version":2,"Theme":"light"}""");
        Check(NativePreferences.Load(path) == NativePreferences.Default, "unknown preferences version was accepted");
        Check(Throws<ArgumentException>(() => NativePreferences.Load("relative.json")), "relative path accepted");

        Console.WriteLine("Native chat parity (eject, regenerate, context budget, advisor, defaults, activity) contracts: all cases passed.");
    }

    private static bool Throws<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { return true; }
        return false;
    }
}
