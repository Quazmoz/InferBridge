using System.Net;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

// Native "Hugging Face access check" dialog contract (web: app/huggingface_access_ui.py):
// the engine's 409 {"detail": {"code": "hf_…"}} refusal survives as a validated issue,
// is presented with the browser's titles and actions, and "Check access again" sends the
// same preflight body as the browser. The Gated card pill comes from /v1/models is_gated.
internal static class HuggingFaceAccessContractTests
{
    private const int Port = 19483;
    private const string Owner = "hf-access-owner-1234567";
    private const string Key = "hf-access-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native HF access dialog: " + message);
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

    private static async Task<EngineRequestException> RefusedAsync(Func<Task> action, string message)
    {
        try { await action(); }
        catch (EngineRequestException refusal) { return refusal; }
        throw new InvalidOperationException("Native HF access dialog: " + message);
    }

    public static async Task RunAsync(string root)
    {
        string directory = Path.Combine(root, "hf-access");
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        var calls = new List<(string Path, string Body)>();
        var convert = new Queue<HttpResponseMessage>(new[]
        {
            // The engine-supplied URL is ignored; the link is rebuilt from the repository ID.
            Json("""{"detail":{"code":"hf_approval_required","message":"Your token is valid, but this account is not approved for the model.","source_model":"meta-llama/Llama-3.2-1B","access_type":"gated","model_url":"https://evil.example/x","license_url":"https://evil.example/x","token_configured":true}}""",
                HttpStatusCode.Conflict),
            Json("""{"detail":{"code":"hf_surprise","message":"Odd\u0007 refusal","source_model":"../../etc/passwd","access_type":"admin"}}""",
                HttpStatusCode.Conflict),
            Json("""{"detail":"Model is busy."}""", HttpStatusCode.Conflict),
            Json("""{"detail":{"code":"hf_access_granted","message":"ready"}}""", HttpStatusCode.Conflict),
        });
        var preflight = new Queue<HttpResponseMessage>(new[]
        {
            Json("""{"detail":{"code":"hf_token_invalid","message":"The Hugging Face token is invalid, expired, or lacks read access.","source_model":"meta-llama/Llama-3.2-1B","access_type":"gated"}}""",
                HttpStatusCode.Conflict),
            Json("""{"code":"hf_access_granted","message":"Hugging Face model access is ready."}"""),
            Json("""{"code":"hf_access_granted","message":"Hugging Face model access is ready."}"""),
        });
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
                "/v1/models" => Json("""{"data":[{"id":"llama-gated","name":"Llama","status":"not_downloaded","is_gated":true},{"id":"open","name":"Open","status":"not_downloaded","is_gated":"true"}]}"""),
                "/v1/models/slots" => Json("""{"slots":[]}"""),
                "/v1/models/convert" => convert.Dequeue(),
                "/v1/huggingface/preflight" => preflight.Dequeue(),
                _ => Json("""{"detail":"not found"}""", HttpStatusCode.NotFound),
            };
        }), metadata);

        var snapshot = await client.InspectAsync(CancellationToken.None, Key);
        ModelInfo gated = snapshot.Models.Single(model => model.Id == "llama-gated");
        Check(gated.IsGated && !snapshot.Models.Single(model => model.Id == "open").IsGated,
            "is_gated was not parsed strictly (only a JSON true marks a model gated)");

        Task Download() => client.ChangeModelAsync(gated, ModelAction.Download, Key, CancellationToken.None);
        HuggingFaceAccessIssue? issue = (await RefusedAsync(Download, "a gated refusal was accepted")).HuggingFaceAccess;
        Check(issue is { Code: "hf_approval_required", SourceModel: "meta-llama/Llama-3.2-1B", AccessType: "gated" }
            && issue.Message == "Your token is valid, but this account is not approved for the model.",
            "the structured refusal was lost");
        Check(issue!.ModelUrl == "https://huggingface.co/meta-llama/Llama-3.2-1B",
            "the model page link was not rebuilt from the validated repository ID");
        Check(issue.Presentation is { Title: "Publisher approval required", Configure: true, OpenPage: true },
            "approval-required is not presented like the browser dialog");

        HuggingFaceAccessIssue? hostile = (await RefusedAsync(Download, "an unknown hf_ refusal was accepted")).HuggingFaceAccess;
        Check(hostile is { Code: "hf_surprise", SourceModel: "", ModelUrl: "", AccessType: "unknown" }
            && !hostile.Message.Any(char.IsControl),
            "an unsafe repository, access type or control character was accepted");
        Check(hostile!.Presentation is { Title: "Hugging Face access check failed", Configure: true, OpenPage: false },
            "an unknown code did not fall back to the browser's generic presentation");
        Check((await RefusedAsync(Download, "plain refusal accepted")).HuggingFaceAccess is null,
            "a plain-text refusal was treated as a Hugging Face access issue");
        Check((await RefusedAsync(Download, "granted refusal accepted")).HuggingFaceAccess is null,
            "hf_access_granted was treated as an access issue");

        // Check access again: catalog models by ID (engine metadata), custom downloads by repository.
        HuggingFaceAccessIssue? retry = (await RefusedAsync(
            () => client.CheckHuggingFaceAccessAsync(issue, gated.Id, Key, CancellationToken.None),
            "a refused preflight was reported as granted")).HuggingFaceAccess;
        Check(retry is { Code: "hf_token_invalid" } && retry.Presentation is { Configure: true, OpenPage: true }
            && retry.Presentation.Title == "Hugging Face access required",
            "the new issue from Check access again was not returned");
        await client.CheckHuggingFaceAccessAsync(issue, gated.Id, Key, CancellationToken.None);
        await client.CheckHuggingFaceAccessAsync(issue, null, Key, CancellationToken.None);
        var bodies = calls.Where(call => call.Path == "/v1/huggingface/preflight").Select(call => call.Body).ToArray();
        Check(bodies.Length == 3 && bodies[0] == """{"model_id":"llama-gated"}""" && bodies[1] == bodies[0]
            && bodies[2] == """{"source_model":"meta-llama/Llama-3.2-1B","access_type":"gated"}""",
            "Check access again does not send the browser's preflight body");
        await RefusedAsync(() => client.CheckHuggingFaceAccessAsync(hostile, null, Key, CancellationToken.None),
            "a preflight without any repository was sent");
        Check(calls.Count(call => call.Path == "/v1/huggingface/preflight") == 3,
            "a preflight without a repository reached the engine");

        Check(new HuggingFaceAccessIssue("hf_network_error", "x", "a/b", "public").Presentation
                is { Title: "Hugging Face check unavailable", Configure: false, OpenPage: false }
            && new HuggingFaceAccessIssue("hf_model_not_found", "x", "a/b", "public").Presentation
                is { Configure: false, OpenPage: true },
            "network and not-found refusals differ from the browser actions");
        Console.WriteLine("Native Hugging Face access dialog contract: all cases passed.");
    }
}
