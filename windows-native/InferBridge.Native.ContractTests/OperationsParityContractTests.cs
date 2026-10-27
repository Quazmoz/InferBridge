using System.Net;
using System.Text.Json;

namespace InferBridge.Native;

// Listener settings, engine restart, Doctor remediation and update settings use
// exactly the protected endpoints the web Local API, Operations, model-health
// and About & Updates panels use. Responses are simulated.
internal static class OperationsParityContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native operations parity: " + message);
    }

    private static async Task RejectsAsync<T>(Func<Task> action) where T : Exception
    {
        try { await action(); }
        catch (T) { return; }
        throw new InvalidOperationException("Expected " + typeof(T).Name);
    }

    private static string NetworkStatus(string local, bool restart = false, string cors = "") =>
        JsonSerializer.Serialize(new
        {
            active_bind_host = "127.0.0.1", host_source = "default", lan_setting_enabled = restart,
            lan_active = false, lan_blocked_reason = (string?)null, local_endpoint = local,
            lan_endpoints = Array.Empty<string>(), api_key_configured = true, api_key_source = "secure_store",
            api_key_persistence = "dpapi", cors_origins = cors, cors_source = "default",
            host_environment_override = false, cors_environment_override = false,
            api_key_environment_override = false, wildcard_cors = cors == "*", restart_required = restart,
            warnings = new[] { "Windows Firewall may prompt when LAN access starts." },
        });

    public static async Task RunAsync(string root)
    {
        string local = "http://127.0.0.1:" + FakeEngine.Port + "/v1";
        string networkLocal = local;
        string? generated = null;
        string healthAction = "revalidate";
        bool healthAvailable = true;
        var engine = new FakeEngine
        {
            Route = (request, body) => request.RequestUri!.AbsolutePath switch
            {
                "/internal/desktop-network" when request.Method == HttpMethod.Get =>
                    FakeEngine.Json(NetworkStatus(networkLocal)),
                "/internal/desktop-network" => FakeEngine.Json(JsonSerializer.Serialize(new
                {
                    status = JsonDocument.Parse(NetworkStatus(networkLocal, true)).RootElement,
                    generated_api_key = generated,
                    message = "Network settings saved. Restart InferBridge to apply the listener changes.",
                })),
                "/v1/desktop/operations/restart-server" => FakeEngine.Json(
                    """{"status":"restarting","message":"The tray controller will restart the local server once shutdown completes."}"""),
                "/v1/runtime-health" => FakeEngine.Json(JsonSerializer.Serialize(new
                {
                    schema_version = 1,
                    runtime = new { application = "1.0.1", openvino = "2026.1", openvino_genai = (string?)null },
                    summary = new { needs_attention = 1, runtime_change_detected = true },
                    models = new object[]
                    {
                        new
                        {
                            model_id = "model-a", name = "A",
                            conversion_health = new { status = "runtime_changed", label = "Runtime changed" },
                            can_leave_unchanged = true,
                            recommendation = new { action = healthAction, label = "Revalidate", reason = "OpenVINO changed",
                                available = healthAvailable, blocked_reason = "" },
                        },
                        new
                        {
                            model_id = "model-b", name = "B",
                            conversion_health = new { status = "healthy", label = "Healthy" },
                            can_leave_unchanged = false,
                            recommendation = new { action = "leave_unchanged", label = "Leave unchanged", reason = "",
                                available = true, blocked_reason = "" },
                        },
                    },
                })),
                "/v1/runtime-health/action" => FakeEngine.Json("""{"status":"validated","model_id":"model-a"}"""),
                "/v1/runtime-health/batch" => FakeEngine.Json("""{"status":"completed","failures":[]}"""),
                "/desktop/release/status" => FakeEngine.Json(
                    """{"build":{"application_version":"1.0.1","build_channel":"stable"},"installation_mode":"installed","update_checks":{"enabled":false,"channel":"stable","skipped_versions":[]},"latest_checked_version":null,"check_due":true}"""),
                "/desktop/release/settings" => FakeEngine.Json(body),
                "/desktop/release/check" => FakeEngine.Json(
                    """{"status":"available","latest_version":"1.1.0","selected_artifact_type":"installer","manifest":{"release_notes_url":"https://evil.invalid/notes","artifacts":[{"type":"installer","url":"https://github.com/Quazmoz/InferBridge/releases/download/v1.1.0/setup.exe","sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}]}}"""),
                _ => throw new InvalidOperationException("Unexpected route " + request.RequestUri.AbsolutePath),
            },
        };
        using var client = engine.CreateClient(root, "operations-parity");
        string key = FakeEngine.Key;

        // --- Network listener -------------------------------------------------
        var status = await client.GetNetworkSettingsAsync(key, CancellationToken.None);
        Check(status.LocalEndpoint == local && status.ApiKeyConfigured && status.Warnings.Count == 1,
            "network status was not parsed strictly");
        Check(engine.Calls.Last().LocalUi, "network request omitted the local UI marker");
        await RejectsAsync<UnauthorizedAccessException>(() => client.GetNetworkSettingsAsync(
            null, CancellationToken.None));
        networkLocal = "http://192.168.1.4:" + FakeEngine.Port + "/v1";
        await RejectsAsync<JsonException>(() => client.GetNetworkSettingsAsync(key, CancellationToken.None));
        networkLocal = local;

        int posts() => engine.Calls.Count(call => call.Method == "POST" && call.Path == "/internal/desktop-network");
        foreach (var invalid in new[]
        {
            new NetworkSettingsUpdate(false, "", "abc", true, false, false),
            new NetworkSettingsUpdate(false, "", null, true, true, false),
            new NetworkSettingsUpdate(false, "*", null, false, false, false),
            new NetworkSettingsUpdate(false, "", "has space", false, false, false),
            new NetworkSettingsUpdate(false, "a\u0000", null, false, false, false),
        })
            await RejectsAsync<ArgumentException>(() => client.UpdateNetworkSettingsAsync(
                invalid, key, CancellationToken.None));
        Check(posts() == 0, "invalid network settings reached the engine");

        var saved = await client.UpdateNetworkSettingsAsync(
            new NetworkSettingsUpdate(true, "", null, false, false, false), key, CancellationToken.None);
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
            Check(sent.RootElement.GetProperty("allow_lan").GetBoolean()
                && !sent.RootElement.GetProperty("generate_api_key").GetBoolean()
                && sent.RootElement.GetProperty("api_key").ValueKind == JsonValueKind.Null
                && engine.Calls.Last().LocalUi,
                "network update body differs from the web form contract");
        Check(saved.Status.RestartRequired && saved.GeneratedApiKey is null, "restart requirement was lost");
        generated = "generated-key-abcdefghijklmnop";
        var rotated = await client.UpdateNetworkSettingsAsync(
            new NetworkSettingsUpdate(true, "", null, true, false, false), key, CancellationToken.None);
        Check(rotated.GeneratedApiKey == generated, "generated key was not returned once to the caller");
        generated = null;
        await RejectsAsync<JsonException>(() => client.UpdateNetworkSettingsAsync(
            new NetworkSettingsUpdate(true, "", null, true, false, false), key, CancellationToken.None));

        string restart = await client.RestartEngineAsync(key, CancellationToken.None);
        Check(restart.Contains("restart", StringComparison.OrdinalIgnoreCase), "restart was not confirmed");

        // --- Doctor remediation (model health) ----------------------------------
        var health = await client.GetRuntimeHealthAsync(key, CancellationToken.None);
        Check(health.NeedsAttention == 1 && health.RuntimeChangeDetected && health.Models.Count == 2
            && health.BatchCandidates("revalidate").SequenceEqual(new[] { "model-a" }),
            "runtime health report was not parsed or batch eligibility differs from the web");
        string done = await client.RunRuntimeHealthActionAsync("model-a", "revalidate", key, CancellationToken.None);
        Check(done.Contains("validated", StringComparison.Ordinal), "maintenance status was not reported");
        int actionPosts() => engine.Calls.Count(call => call.Path == "/v1/runtime-health/action");
        int before = actionPosts();
        await RejectsAsync<EngineRequestException>(() => client.RunRuntimeHealthActionAsync(
            "model-b", "reconvert", key, CancellationToken.None));
        await RejectsAsync<EngineRequestException>(() => client.RunRuntimeHealthActionAsync(
            "model-b", "leave_unchanged", key, CancellationToken.None));
        await RejectsAsync<ArgumentException>(() => client.RunRuntimeHealthActionAsync(
            "../x", "revalidate", key, CancellationToken.None));
        healthAvailable = false;
        await RejectsAsync<EngineRequestException>(() => client.RunRuntimeHealthActionAsync(
            "model-a", "revalidate", key, CancellationToken.None));
        Check(actionPosts() == before, "an action the engine did not offer was posted");
        // Acknowledging a warning stays available, as in the web "Leave unchanged" button.
        await client.RunRuntimeHealthActionAsync("model-a", "leave_unchanged", key, CancellationToken.None);
        await RejectsAsync<EngineRequestException>(() => client.RunRuntimeHealthBatchAsync(
            "revalidate", key, CancellationToken.None));
        healthAvailable = true;
        var batch = await client.RunRuntimeHealthBatchAsync("revalidate", key, CancellationToken.None);
        using (var sent = JsonDocument.Parse(engine.Calls.Last().Body))
            Check(batch.Status == "completed" && sent.RootElement.GetProperty("model_ids").GetArrayLength() == 1,
                "batch did not send exactly the engine-eligible models");
        await RejectsAsync<ArgumentException>(() => client.RunRuntimeHealthBatchAsync(
            "reconvert", key, CancellationToken.None));

        // --- About & Updates ------------------------------------------------------
        var release = await client.GetReleaseStatusAsync(key, CancellationToken.None);
        Check(release.Version == "1.0.1" && !release.Settings.Enabled && !release.StoreManaged,
            "release status differs from the web About panel");
        Check(engine.Calls.Last().LocalUi, "release request omitted the local UI marker");
        var enabled = await client.SaveReleaseSettingsAsync(
            release.Settings with { Enabled = true, Channel = "beta" }, key, CancellationToken.None);
        Check(enabled.Enabled && enabled.Channel == "beta" && engine.Calls.Last().Method == "PUT",
            "update settings were not saved through PUT /desktop/release/settings");
        await RejectsAsync<ArgumentException>(() => client.SaveReleaseSettingsAsync(
            release.Settings with { Channel = "canary" }, key, CancellationToken.None));
        var check = await client.CheckForUpdateAsync(key, CancellationToken.None);
        Check(check.Status == "available" && check.LatestVersion == "1.1.0" && check.Sha256?.Length == 64
            && check.DownloadUrl!.StartsWith("https://github.com/Quazmoz/InferBridge/", StringComparison.Ordinal),
            "update check result was not surfaced");
        Check(check.ReleaseNotesUrl is null, "a non-official release link was surfaced");
        foreach (string bad in new[] { "http://github.com/Quazmoz/InferBridge/x", "https://github.com.evil/Quazmoz/InferBridge/x",
            "https://github.com/Other/InferBridge/x", "https://github.com/Quazmoz/InferBridge/x?y=1",
            "https://user@github.com/Quazmoz/InferBridge/x" })
            Check(!BackendClient.IsOfficialReleaseUrl(bad), "unofficial URL accepted: " + bad);
        Console.WriteLine("Native network, restart, model health and update contracts: all cases passed.");
    }
}
