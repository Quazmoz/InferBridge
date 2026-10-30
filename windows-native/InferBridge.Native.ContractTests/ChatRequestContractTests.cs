using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

/// <summary>A verified loopback fake engine for the parity contracts.</summary>
internal sealed class FakeEngine
{
    public const int Port = 19455;
    public const string Owner = "parity-session-owner-123456";
    public const string Key = "in-memory-parity-key";
    public readonly List<(string Method, string Path, string Body, bool LocalUi)> Calls = new();
    public required Func<HttpRequestMessage, string, HttpResponseMessage> Route { get; init; }

    public static HttpResponseMessage Json(string body, HttpStatusCode status = HttpStatusCode.OK)
        => new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond)
        : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
            => respond(request, cancellationToken);
    }

    public BackendClient CreateClient(string root, string name)
    {
        string directory = Path.Combine(root, name);
        Directory.CreateDirectory(directory);
        string metadata = Path.Combine(directory, "desktop-instance.json");
        File.WriteAllText(metadata, JsonSerializer.Serialize(new { nonce = Owner, port = Port }));
        return new BackendClient(new Handler(async (request, token) =>
        {
            if (request.RequestUri?.Host != "127.0.0.1" || request.RequestUri.Port != Port)
                throw new InvalidOperationException("Client left the verified loopback engine.");
            string path = request.RequestUri.AbsolutePath;
            if (path == "/desktop/instance")
                return Json(JsonSerializer.Serialize(new { instance_nonce = Owner, port = Port }));
            if (path == "/health/live") return Json("""{"status":"ok"}""");
            if (request.Headers.Authorization?.Parameter != Key)
                return Json("""{"detail":"unauthorized"}""", HttpStatusCode.Unauthorized);
            if (path == "/v1/models")
                return Json("""{"data":[{"id":"model-a","name":"A","status":"loaded"}]}""");
            string body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(token);
            bool localUi = request.Headers.TryGetValues("X-OV-LLM-UI", out var marker)
                && marker.Single() == "1";
            Calls.Add((request.Method.Method, path + request.RequestUri.Query, body, localUi));
            return Route(request, body);
        }), metadata);
    }
}

internal static class ChatRequestContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native chat request parity: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static readonly byte[] Png =
        [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52];

    public static async Task RunAsync(string root)
    {
        // Signature-based detection; extensions and arbitrary bytes are not trusted.
        Check(ChatImage.FromBytes(Png).MediaType == "image/png"
            && ChatImage.FromBytes(Png).DataUrl.StartsWith("data:image/png;base64,", StringComparison.Ordinal),
            "PNG was not encoded as an inline data URL");
        Check(ChatImage.FromBytes([0xFF, 0xD8, 0xFF, 0xE0, 0, 0, 0, 0, 0, 0, 0, 0]).MediaType == "image/jpeg",
            "JPEG signature not detected");
        Check(ChatImage.FromBytes([.. "RIFF"u8, 0, 0, 0, 0, .. "WEBP"u8]).MediaType == "image/webp",
            "WebP signature not detected");
        foreach (byte[] bad in new[] { "GIF89a-not-allowed"u8.ToArray(), new byte[4],
            "<svg onload=alert(1)>"u8.ToArray(), new byte[ChatImage.MaximumBytes + 1] })
        {
            bool rejected = false;
            try { _ = ChatImage.FromBytes(bad); } catch (ArgumentException) { rejected = true; }
            Check(rejected, "unsupported or oversized image bytes were accepted");
        }
        var five = Enumerable.Repeat(ChatImage.FromBytes(Png), 5).ToArray();
        bool tooMany = false;
        try { new ChatRequestContext(null, five).Validate(); } catch (ArgumentException) { tooMany = true; }
        Check(tooMany, "more than four images were accepted");
        bool badPrompt = false;
        try { new ChatRequestContext("bad\u0000prompt", Array.Empty<ChatImage>()).Validate(); }
        catch (ArgumentException) { badPrompt = true; }
        Check(badPrompt, "control characters in the system prompt were accepted");

        bool vision = false;
        string? visionValue = "true";
        var engine = new FakeEngine
        {
            Route = (request, body) => request.RequestUri!.AbsolutePath switch
            {
                "/v1/models/status" => FakeEngine.Json(
                    "{\"models\":{\"available\":[{\"id\":\"model-a\",\"status\":\"loaded\""
                    + (visionValue is null ? "" : ",\"supports_vision\":" + (vision ? visionValue : "false"))
                    + "}]}}"),
                "/v1/chat/completions" => FakeEngine.Json(
                    """{"choices":[{"message":{"role":"assistant","content":"ok"}}]}"""),
                _ => throw new InvalidOperationException("Unexpected route " + request.RequestUri.AbsolutePath),
            },
        };
        using var client = engine.CreateClient(root, "chat-context");
        var turns = new[] { new ChatTurn("user", "earlier"), new ChatTurn("assistant", "reply"),
            new ChatTurn("user", "describe this") };

        string answer = await client.CompleteChatAsync("model-a", turns, FakeEngine.Key,
            CancellationToken.None, null, new ChatRequestContext("  Be terse.  ", Array.Empty<ChatImage>()));
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
        {
            var messages = sent.RootElement.GetProperty("messages");
            Check(answer == "ok" && messages.GetArrayLength() == 4
                && messages[0].GetProperty("role").GetString() == "system"
                && messages[0].GetProperty("content").GetString() == "Be terse.",
                "system prompt was not sent first like the web client");
            Check(engine.Calls.All(call => call.Path != "/v1/models/status"),
                "text-only chat made an unnecessary capability request");
        }

        // Fail closed: non-vision, missing and non-boolean capabilities never send images.
        var withImage = new ChatRequestContext(null, [ChatImage.FromBytes(Png)]);
        foreach ((bool supported, string? value) in new[] { (false, "true"), (true, (string?)null), (true, "\"true\"") })
        {
            vision = supported;
            visionValue = value;
            int before = engine.Calls.Count(call => call.Path == "/v1/chat/completions");
            await RejectsAsync<EngineRequestException>(() => client.CompleteChatAsync(
                "model-a", turns, FakeEngine.Key, CancellationToken.None, null, withImage));
            Check(engine.Calls.Count(call => call.Path == "/v1/chat/completions") == before,
                "images reached the engine without a confirmed vision capability");
        }
        vision = true;
        visionValue = "true";
        Check(await client.GetModelSupportsVisionAsync("model-a", FakeEngine.Key, CancellationToken.None),
            "confirmed vision capability was not reported");
        Check(!await client.GetModelSupportsVisionAsync("other", FakeEngine.Key, CancellationToken.None),
            "unknown model was reported as vision-capable");
        await client.CompleteChatAsync("model-a", turns, FakeEngine.Key, CancellationToken.None, null, withImage);
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
        {
            var messages = sent.RootElement.GetProperty("messages");
            Check(messages[0].GetProperty("content").ValueKind == JsonValueKind.String,
                "images were attached to an earlier turn");
            var parts = messages[2].GetProperty("content");
            Check(parts.GetArrayLength() == 2
                && parts[0].GetProperty("type").GetString() == "text"
                && parts[0].GetProperty("text").GetString() == "describe this"
                && parts[1].GetProperty("type").GetString() == "image_url"
                && parts[1].GetProperty("image_url").GetProperty("url").GetString()!
                    .StartsWith("data:image/png;base64,", StringComparison.Ordinal),
                "final user turn did not carry OpenAI image_url parts like the web client");
        }
        Console.WriteLine("Native chat system prompt and vision attachment contracts: all cases passed.");
    }
}
