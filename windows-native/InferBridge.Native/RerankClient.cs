using System.Net;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record RerankResult(int Index, double Score, string Text);
internal sealed record RerankOutcome(string Model, IReadOnlyList<RerankResult> Results);

/// <summary>
/// The native "Rerank for RAG" panel: the same request as the browser Connection Hub
/// (<c>POST /v1/rerank</c> with the reranker slot's model and one document per line).
/// </summary>
internal static class Rerank
{
    // Same bounds as the browser form (query 2,000 characters, 50 documents, 20,000
    // characters of documents); the engine enforces its own larger limits too.
    public const int MaximumQuery = 2000;
    public const int MaximumDocuments = 50;
    public const int MaximumDocumentText = 20000;

    public static IReadOnlyList<string> SplitDocuments(string text) =>
        // WinUI TextBox reports line breaks as "\r"; pasted text may carry "\r\n" or "\n".
        (text ?? "").Split(new[] { '\r', '\n' }).Select(line => line.Trim()).Where(line => line.Length > 0)
            .Take(MaximumDocuments).ToArray();

    /// <summary>Allowlist-parse the response; results keep the engine's order (best first).</summary>
    public static RerankOutcome Parse(JsonElement root, IReadOnlyList<string> documents)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("results", out var rows)
            || rows.ValueKind != JsonValueKind.Array)
            throw new JsonException("The engine returned an invalid rerank response.");
        var results = new List<RerankResult>();
        var seen = new HashSet<int>();
        foreach (var row in rows.EnumerateArray())
        {
            if (results.Count >= documents.Count) break;
            if (row.ValueKind != JsonValueKind.Object
                || !row.TryGetProperty("index", out var index) || index.ValueKind != JsonValueKind.Number
                || !index.TryGetInt32(out int position) || position < 0 || position >= documents.Count
                || !seen.Add(position)
                || !row.TryGetProperty("relevance_score", out var score) || score.ValueKind != JsonValueKind.Number
                || !score.TryGetDouble(out double value) || !double.IsFinite(value))
                throw new JsonException("The engine returned an invalid rerank result.");
            // Show the text we sent for that index; the echo is not trusted for display.
            results.Add(new RerankResult(position, value, documents[position]));
        }
        string model = root.TryGetProperty("model", out var name) && name.ValueKind == JsonValueKind.String
            ? (name.GetString() ?? "") : "";
        return new RerankOutcome(model.Length is > 0 and <= 512 ? model : "the reranker", results);
    }

    public static string Describe(RerankResult result, int rank) =>
        rank + ". " + result.Score.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture)
        + " · " + result.Text;

    public static string CurlExample(string baseUrl, bool authenticationRequired, string? model) =>
        "curl.exe -sS \"" + baseUrl + "/rerank\" -H \"Content-Type: application/json\""
        + (authenticationRequired ? " -H \"Authorization: Bearer YOUR_INFERBRIDGE_API_KEY\"" : "")
        + " -d '" + JsonSerializer.Serialize(new
        {
            model = model ?? "bge-reranker-base",
            query = "What is the capital of France?",
            documents = new[] { "Paris is the capital of France.", "Bananas are yellow." },
            top_n = 2,
        }) + "'\n";
}

internal sealed partial class BackendClient
{
    /// <summary>The resident reranker, or null when the reranker slot is empty.</summary>
    public async Task<ModelSlot?> GetRerankSlotAsync(string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/models/slots", apiKey,
            cancellationToken, TimeSpan.FromSeconds(6));
        return ModelSlot.Parse(document.RootElement)
            .FirstOrDefault(slot => slot.Role == "rerank" && slot.Resident && slot.Status == "loaded");
    }

    public async Task<RerankOutcome> RerankAsync(string? model, string query, IReadOnlyList<string> documents,
        string? apiKey, CancellationToken cancellationToken)
    {
        query = (query ?? "").Trim();
        if (query.Length is < 1 or > Rerank.MaximumQuery)
            throw new ArgumentException("Enter a query of up to 2,000 characters.", nameof(query));
        if (documents.Count is < 1 or > Rerank.MaximumDocuments
            || documents.Any(text => string.IsNullOrWhiteSpace(text))
            || documents.Sum(text => text.Length) > Rerank.MaximumDocumentText)
            throw new ArgumentException("Enter 1 to 50 documents, one per line.", nameof(documents));
        if (model is not null && model.Length is < 1 or > 512)
            throw new ArgumentException("Invalid reranker model.", nameof(model));
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        try
        {
            using var reply = await PostJsonAsync(origin + "/v1/rerank", JsonSerializer.Serialize(new
            {
                model, query, documents, return_documents = true,
            }), apiKey, cancellationToken, TimeSpan.FromSeconds(120), MaximumChatBytes);
            return Rerank.Parse(reply.RootElement, documents);
        }
        catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.ServiceUnavailable)
        {
            throw new EngineRequestException("No reranker is loaded. Load one with Load as reranker in the Model library.");
        }
        catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.UnprocessableEntity)
        {
            throw new EngineRequestException("The engine refused these documents. Remove empty or very long lines and try again.");
        }
    }
}
