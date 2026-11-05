using System.Net;
using System.Text;

namespace InferBridge.Native;

// Model conversion and recovery parity with the browser (app/progress_reliability.py,
// app/model_recovery_ui.py): sanitized progress detail, stale-progress wording, the
// embedded recovery summary that gates Review interrupted preparation, and the
// engine's own cancellation reason.
internal static class ConversionRecoveryContractTests
{
    private const string Key = "conversion-key";

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Conversion and recovery: " + message);
    }

    private sealed class Handler(string status) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            string body = request.RequestUri!.AbsolutePath switch
            {
                "/desktop/instance" => """{"instance_nonce":"conversion-nonce","port":18777}""",
                "/health/live" => """{"status":"ok"}""",
                "/v1/models" => """{"data":[]}""",
                "/v1/models/status" => status,
                _ => throw new InvalidOperationException("Unexpected route " + request.RequestUri.AbsolutePath),
            };
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json"),
            });
        }
    }

    public static async Task RunAsync(string root)
    {
        string folder = Path.Combine(root, "conversion-recovery");
        Directory.CreateDirectory(folder);
        string metadata = Path.Combine(folder, "desktop-instance.json");
        File.WriteAllText(metadata, """{"nonce":"conversion-nonce","port":18777}""");
        const string status = """
            {"schema_version":1,"models":{"available":[
             {"id":"model-a","status":"converting","can_cancel":true,"cancel_mode":"conversion",
              "progress":{"phase":"converting","operation_id":"op:1","percent":40,"message":"Exporting\u0007 layer 3 of 28",
                          "completed":2,"total":5,"updated_at":1000,
                          "log_tail":["l1","l2","l3","l4","l5","l6","l7","token hf_ABCDEFGHijkl leaked","l9","l10"]}},
             {"id":"model-b","status":"error","can_cancel":false,"cancel_reason":"The model preparation operation has already finished.",
              "progress":{"phase":"error","operation_id":"op:2","completed":9,"total":3},
              "recovery":{"available":true,"recovery_id":"rec-42","model_id":"model-b","recommended_action":"retry_failed_stage"}},
             {"id":"model-c","status":"available","recovery":{"available":true,"recovery_id":"bad\nid"}},
             {"id":"model-d","status":"available","recovery":{"available":true,"recovery_id":"rec-7","recommended_action":"rm -rf"}}]}}
            """;
        using var client = new BackendClient(new Handler(status), metadata);
        var items = await client.GetModelPreparationsAsync(Key, CancellationToken.None);

        ModelPreparationInfo a = items["model-a"];
        Check(a.Message == "Exporting layer 3 of 28", "the engine progress message lost text or kept a control character");
        Check(a.Files == (2, 5), "files completed / total was not read");
        Check(a.LogTail.Count == 7 && a.LogTail[0] == "l3" && a.LogTail.Last() == "l10"
            && !a.LogTail.Any(line => line.Contains("hf_", StringComparison.Ordinal)),
            "the log tail is not the last 8 lines with token-like lines dropped");
        Check(a.StaleNotice(1010) == "" && a.StaleNotice(1045) == "No recent progress update for 45s · still running"
            && a.StaleNotice(1000 + 185) == "Taking longer than usual · last update 3m 5s ago",
            "stale-progress wording differs from the browser");
        Check(a.RecoveryId is null, "a model without recovery state offered recovery");

        ModelPreparationInfo b = items["model-b"];
        Check(b is { RecoveryId: "rec-42", RecoveryAction: "retry_failed_stage", CanCancel: false, Files: null }
            && b.CancelReason == "The model preparation operation has already finished."
            && b.StaleNotice(99999) == "", "recovery summary, engine cancel reason or finished-stage wording is wrong");
        Check(items["model-c"].RecoveryId is null, "a recovery id with control characters was accepted");
        Check(items["model-d"] is { RecoveryId: "rec-7", RecoveryAction: "" }, "an unknown recommended action was shown");
        Check(BackendClient.SafeProgressText(new string('x', 300), 180).Length == 180,
            "progress text is not bounded");

        Console.WriteLine("Native conversion and recovery progress contracts: all cases passed.");
    }
}
