using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Native "Rerank for RAG": the same POST /v1/rerank body as the browser Connection
// Hub (reranker slot model, query, one document per line, return_documents), strict
// result parsing, and guidance instead of a raw error when no reranker is resident.
internal static class RerankContractTests
{
    private const int Port = 19479;
    private const string Owner = "rerank-panel-owner-1234567";
    private const string Key = "rerank-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native rerank panel: " + message);
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
        throw new InvalidOperationException("Native rerank panel: " + message);
    }

    public static async Task RunAsync(string root)
    {
        Check(Rerank.SplitDocuments("  a \r b\r\n\r\nc\n  \n").SequenceEqual(new[] { "a", "b", "c" }),
            "WinUI \\r line breaks or blank lines were not handled");
        Check(Rerank.SplitDocuments(string.Join("\r", Enumerable.Range(0, 80).Select(i => "d" + i))).Count == 50,
            "more than 50 documents would be sent (browser form limit)");

        string directory = Path.Combine(root, "rerank");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Path, string Body)>();
        string slots = """{"slots":[{"role":"chat","model":"chat-a","status":"loaded"},{"role":"rerank","model":"bge-reranker-base","name":"BGE Reranker Base","device":"CPU","status":"loaded"}]}""";
        Func<HttpResponseMessage> rerank = () => Json("""
            {"id":"rerank-1","object":"rerank","model":"bge-reranker-base","results":[
              {"index":1,"relevance_score":0.998,"document":{"text":"<b>spoofed</b>"}},
              {"index":0,"relevance_score":0.271},
              {"index":2,"relevance_score":0.0001}]}
            """);
        using var client = new BackendClient(new Handler(async (request, token) =>
        {
            string path = request.RequestUri!.AbsolutePath;
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path is "/health/live" or "/health/ready") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            calls.Add((path, body));
            return path switch
            {
                "/v1/models" => Json("""{"data":[]}"""),
                "/v1/models/slots" => Json(slots),
                "/v1/rerank" => rerank(),
                _ => throw new InvalidOperationException("Unexpected route " + path),
            };
        }), metadata);

        ModelSlot? slot = await client.GetRerankSlotAsync(Key, CancellationToken.None);
        Check(slot is { ModelId: "bge-reranker-base", Device: "CPU" }, "the resident reranker was not found");
        var documents = Rerank.SplitDocuments("The Eiffel Tower is in Paris.\rParis is the capital of France.\rBananas are a good source of potassium.");
        RerankOutcome outcome = await client.RerankAsync(slot!.ModelId, " What is the capital of France? ",
            documents, Key, CancellationToken.None);
        using (var sent = JsonDocument.Parse(calls.Last(call => call.Path == "/v1/rerank").Body))
        {
            var body = sent.RootElement;
            Check(body.GetProperty("model").GetString() == "bge-reranker-base"
                && body.GetProperty("query").GetString() == "What is the capital of France?"
                && body.GetProperty("documents").GetArrayLength() == 3
                && body.GetProperty("return_documents").GetBoolean(),
                "the rerank request differs from the browser form");
        }
        Check(outcome.Model == "bge-reranker-base"
            && outcome.Results.Select(r => r.Index).SequenceEqual(new[] { 1, 0, 2 })
            && outcome.Results[0].Text == "Paris is the capital of France.",
            "results lost the engine order or displayed the echoed text instead of the sent document");
        Check(Rerank.Describe(outcome.Results[0], 1) == "1. 0.998 · Paris is the capital of France.",
            "result lines are not rank · score · document");

        int before = calls.Count;
        await RejectsAsync<ArgumentException>(() => client.RerankAsync(null, "  ", documents, Key,
            CancellationToken.None), "a blank query was sent");
        await RejectsAsync<ArgumentException>(() => client.RerankAsync(null, "q", Array.Empty<string>(), Key,
            CancellationToken.None), "an empty document list was sent");
        Check(calls.Count == before, "an invalid rerank request reached the engine");

        rerank = () => Json("""{"results":[{"index":7,"relevance_score":0.5}]}""");
        await RejectsAsync<JsonException>(() => client.RerankAsync(null, "q", documents, Key,
            CancellationToken.None), "an out-of-range result index was accepted");
        rerank = () => Json("""{"detail":"No reranker model is loaded."}""", HttpStatusCode.ServiceUnavailable);
        try
        {
            await client.RerankAsync(null, "q", documents, Key, CancellationToken.None);
            throw new InvalidOperationException("Native rerank panel: a 503 was not reported");
        }
        catch (EngineRequestException error)
        {
            Check(error.Message.Contains("Load as reranker", StringComparison.Ordinal),
                "a missing reranker did not explain how to load one");
        }

        slots = """{"slots":[{"role":"rerank","model":null,"status":"empty"}]}""";
        Check(await client.GetRerankSlotAsync(Key, CancellationToken.None) is null,
            "an empty reranker slot enabled the panel");
        Check(Rerank.CurlExample("http://127.0.0.1:8000/v1", true, "bge-reranker-base")
                .StartsWith("curl.exe -sS \"http://127.0.0.1:8000/v1/rerank\"", StringComparison.Ordinal)
            && Rerank.CurlExample("http://127.0.0.1:8000/v1", true, null).Contains("YOUR_INFERBRIDGE_API_KEY", StringComparison.Ordinal),
            "the curl example is wrong or could carry a real key");

        Console.WriteLine("Native rerank test panel contracts: all cases passed.");
    }
}
