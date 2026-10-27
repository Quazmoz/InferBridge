using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Verified Model Library export/import and local-model adoption use exactly the
// protected /v1/model-library endpoints and request bodies of the web UI.
// Responses are simulated; no model files are touched.
internal static class ModelLibraryContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native model library parity: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static void Rejects<T>(Action action, string message) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Native model library parity: " + message);
    }

    public static async Task RunAsync(string root)
    {
        string export = """{"schema_version":1,"application":"InferBridge","exported_at":"2026-10-27T00:00:00Z","models":{"model-a":{"model_id":"model-a","name":"A"}}}""";
        string importReply = """{"status":"imported","added":["model-b"],"updated":[],"unchanged":["model-a"]}""";
        string scan = """{"models":[{"directory_name":"qwen3-int4-ov","suggested_model_id":"qwen3-int4-ov","suggested_name":"qwen3 int4 ov","size_bytes":2147483648}],"count":1}""";
        string adoptReply = """{"status":"imported","model_id":"qwen3-local","target_path":"C:\\m","size_bytes":10,"managed_in_place":true}""";
        string convertedReply = """{"status":"imported","model_id":"copy-a","target_path":"C:\\m","size_bytes":20}""";
        HttpStatusCode importStatus = HttpStatusCode.OK;
        var engine = new FakeEngine
        {
            Route = (request, body) => request.RequestUri!.AbsolutePath switch
            {
                "/v1/model-library/export" => FakeEngine.Json(export),
                "/v1/model-library/import-definitions" => importStatus == HttpStatusCode.OK
                    ? FakeEngine.Json(importReply)
                    : FakeEngine.Json("""{"detail":"Model 'model-a' already exists with a different definition."}""", importStatus),
                "/v1/model-library/unregistered-managed" => FakeEngine.Json(scan),
                "/v1/model-library/adopt-managed" => FakeEngine.Json(adoptReply),
                "/v1/model-library/import-converted" => FakeEngine.Json(convertedReply),
                _ => throw new InvalidOperationException("Unexpected route " + request.RequestUri.AbsolutePath),
            },
        };
        using var client = engine.CreateClient(root, "model-library-parity");
        string key = FakeEngine.Key;
        int Posts(string path) => engine.Calls.Count(call => call.Method == "POST" && call.Path == path);

        // --- Export -------------------------------------------------------------
        string exported = await client.ExportModelDefinitionsAsync(true, key, CancellationToken.None);
        Check(engine.Calls.Last().Path == "/v1/model-library/export?include_all=true",
            "export did not use the web include_all query");
        using (var document = JsonDocument.Parse(exported))
            Check(document.RootElement.GetProperty("models").TryGetProperty("model-a", out _),
                "export did not preserve the engine definitions");
        await client.ExportModelDefinitionsAsync(false, key, CancellationToken.None);
        Check(engine.Calls.Last().Path.EndsWith("include_all=false", StringComparison.Ordinal),
            "default export included every registered model");
        await RejectsAsync<UnauthorizedAccessException>(() => client.ExportModelDefinitionsAsync(
            false, null, CancellationToken.None));
        foreach (string bad in new[]
        {
            """{"schema_version":2,"models":{}}""", """{"schema_version":1,"models":[]}""",
            """{"schema_version":1,"models":{"../escape":{}}}""",
        })
        {
            export = bad;
            await RejectsAsync<JsonException>(() => client.ExportModelDefinitionsAsync(false, key, CancellationToken.None));
        }

        // --- Definition import ----------------------------------------------------
        byte[] file = Encoding.UTF8.GetBytes(
            """{"schema_version":1,"models":{"model-a":{"name":"A"},"model-b":{"name":"B"}}}""");
        BackendClient.ParseDefinitionFile(file, out int count);
        Check(count == 2, "definition count differs from the engine's wrapped models shape");
        BackendClient.ParseDefinitionFile(Encoding.UTF8.GetBytes("""{"bare":{"name":"B"}}"""), out count);
        Check(count == 1, "bare definition maps were not accepted like the engine");
        var imported = await client.ImportModelDefinitionsAsync(file, key, CancellationToken.None);
        Check(imported.Added.SequenceEqual(new[] { "model-b" }) && imported.Unchanged.SequenceEqual(new[] { "model-a" }),
            "import summary was not parsed");
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
            Check(sent.RootElement.GetProperty("overwrite").ValueKind == JsonValueKind.False
                && sent.RootElement.GetProperty("payload").GetProperty("models").TryGetProperty("model-b", out _),
                "import body differs from the web {payload, overwrite:false} contract");
        int before = Posts("/v1/model-library/import-definitions");
        var manyModels = string.Join(",", Enumerable.Range(0, 51).Select(i => "\"m" + i + "\":{}"));
        foreach (string bad in new[]
        {
            "", "[]", "not json", """{"models":{}}""", """{"models":{"a":"text"}}""",
            "{\"models\":{" + manyModels + "}}",
            """{"models":{"remote":{"name":"R","trust_remote_code":true}}}""",
        })
            await RejectsAsync<InvalidDataException>(() => client.ImportModelDefinitionsAsync(
                Encoding.UTF8.GetBytes(bad), key, CancellationToken.None));
        await RejectsAsync<InvalidDataException>(() => client.ImportModelDefinitionsAsync(
            new byte[BackendClient.MaximumDefinitionFileBytes + 1], key, CancellationToken.None));
        Check(Posts("/v1/model-library/import-definitions") == before, "an invalid definition file reached the engine");
        BackendClient.ParseDefinitionFile(Encoding.UTF8.GetBytes(
            """{"models":{"safe":{"name":"S","trust_remote_code":false}}}"""), out count);
        importStatus = HttpStatusCode.Conflict;
        await RejectsAsync<EngineRequestException>(() => client.ImportModelDefinitionsAsync(file, key, CancellationToken.None));
        importStatus = HttpStatusCode.OK;
        importReply = """{"status":"imported","added":[],"updated":["model-a"],"unchanged":[]}""";
        await RejectsAsync<JsonException>(() => client.ImportModelDefinitionsAsync(file, key, CancellationToken.None));
        importReply = """{"status":"queued","added":[],"updated":[],"unchanged":[]}""";
        await RejectsAsync<JsonException>(() => client.ImportModelDefinitionsAsync(file, key, CancellationToken.None));

        // --- Detected local models (adopt in place) ---------------------------------
        var detected = await client.GetUnregisteredLocalModelsAsync(key, CancellationToken.None);
        Check(detected.Count == 1 && detected[0].DirectoryName == "qwen3-int4-ov"
            && detected[0].SuggestedModelId == "qwen3-int4-ov" && detected[0].SizeBytes == 2147483648,
            "local model scan was not parsed strictly");
        foreach (string bad in new[]
        {
            """{"models":[],"count":1}""",
            """{"models":[{"directory_name":"..","suggested_model_id":"a","suggested_name":"a","size_bytes":1}],"count":1}""",
            """{"models":[{"directory_name":"a\\b","suggested_model_id":"a","suggested_name":"a","size_bytes":1}],"count":1}""",
            """{"models":[{"directory_name":"a","suggested_model_id":"-bad","suggested_name":"a","size_bytes":1}],"count":1}""",
            """{"models":[{"directory_name":"a","suggested_model_id":"a","suggested_name":"a","size_bytes":-1}],"count":1}""",
        })
        {
            scan = bad;
            await RejectsAsync<JsonException>(() => client.GetUnregisteredLocalModelsAsync(key, CancellationToken.None));
        }

        var adopt = new LocalModelRegistration("qwen3-local", "Qwen3 local", "openvino-genai", "int4", "NPU", 4096,
            DirectoryName: "qwen3-int4-ov");
        var adopted = await client.RegisterLocalModelAsync(adopt, key, CancellationToken.None);
        Check(adopted.ManagedInPlace && adopted.ModelId == "qwen3-local", "adoption result was not confirmed");
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
        {
            var body = sent.RootElement;
            Check(engine.Calls.Last().Path == "/v1/model-library/adopt-managed"
                && body.GetProperty("directory_name").GetString() == "qwen3-int4-ov"
                && body.GetProperty("recommended_device").GetString() == "NPU"
                && body.GetProperty("max_context_len").GetInt32() == 4096
                && body.GetProperty("max_output_tokens").GetInt32() == 512
                && !body.TryGetProperty("source_path", out _) && !body.TryGetProperty("overwrite", out _)
                && !body.TryGetProperty("trust_remote_code", out _),
                "adopt body differs from the web adopt-managed form");
        }
        adoptReply = """{"status":"imported","model_id":"other","size_bytes":10,"managed_in_place":true}""";
        await RejectsAsync<JsonException>(() => client.RegisterLocalModelAsync(adopt, key, CancellationToken.None));
        adoptReply = """{"status":"imported","model_id":"qwen3-local","size_bytes":10}""";
        await RejectsAsync<JsonException>(() => client.RegisterLocalModelAsync(adopt, key, CancellationToken.None));

        // --- Import a converted directory as a managed copy --------------------------
        var copy = new LocalModelRegistration("copy-a", "Copy A", "openvino-embeddings", "fp16", "CPU", 512,
            SourcePath: @"C:\Models\copy-a");
        var copied = await client.RegisterLocalModelAsync(copy, key, CancellationToken.None);
        Check(!copied.ManagedInPlace && copied.SizeBytes == 20, "converted import result was not confirmed");
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
        {
            var body = sent.RootElement;
            Check(engine.Calls.Last().Path == "/v1/model-library/import-converted"
                && body.GetProperty("source_path").GetString() == @"C:\Models\copy-a"
                && body.GetProperty("overwrite").ValueKind == JsonValueKind.False
                && body.GetProperty("max_output_tokens").GetInt32() == 0
                && !body.TryGetProperty("directory_name", out _),
                "converted import body differs from the web import-converted form");
        }
        int registrations = Posts("/v1/model-library/adopt-managed") + Posts("/v1/model-library/import-converted");
        foreach (var invalid in new[]
        {
            adopt with { ModelId = "-bad" },
            adopt with { ModelId = new string('a', 129) },
            adopt with { Name = " " },
            adopt with { Backend = "transformers" },
            adopt with { WeightFormat = "int2" },
            adopt with { Device = "TPU" },
            adopt with { MaxContextTokens = 64 },
            adopt with { DirectoryName = "nested/dir" },
            adopt with { DirectoryName = ".." },
            adopt with { SourcePath = @"C:\Models\x" },
            copy with { SourcePath = @"relative\path" },
            copy with { SourcePath = "C:\\Models\\bad\u0000" },
            copy with { SourcePath = null },
        })
            await RejectsAsync<ArgumentException>(() => client.RegisterLocalModelAsync(invalid, key, CancellationToken.None));
        Check(Posts("/v1/model-library/adopt-managed") + Posts("/v1/model-library/import-converted") == registrations,
            "an invalid registration reached the engine");
        Rejects<ArgumentException>(() => BackendClient.ValidateRegistration(copy with { SourcePath = "" }),
            "empty source path was accepted");
        Console.WriteLine("Native Verified Model Library import/export/adopt contracts: all cases passed.");
    }
}
