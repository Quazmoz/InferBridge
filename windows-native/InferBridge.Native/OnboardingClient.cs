using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record OnboardingStatus(
    bool Completed, bool RestartRequested, bool RerunScanRecommended, string RecoveryMessage)
{
    public bool NeedsSetup => !Completed || RestartRequested;
    /// <summary>The browser's "Setup and onboarding" opener label for this state.</summary>
    public string OpenerLabel => RestartRequested || RecoveryMessage.Length > 0 ? "Onboarding needs attention"
        : Completed && RerunScanRecommended ? "Rescan recommended" : "Setup and onboarding";
}

/// <summary>One labelled value; Status is the engine's ready/warning/unavailable/unknown (or a stage state).</summary>
internal sealed record OnboardingFact(string Label, string Value, string Detail = "", string Status = "");
internal sealed record OnboardingScan(IReadOnlyList<OnboardingFact> Items, IReadOnlyList<string> Warnings);
internal sealed record OnboardingNpu(string Title, string Explanation, string Fallback, string Driver, string SupportUrl);
internal sealed record OnboardingWarning(bool Blocking, string Message);
internal sealed record OnboardingRecommendation(
    string ModelId, string ModelName, string Description, string Reason, string RequestedDevice,
    bool TrustRemoteCode, IReadOnlyList<OnboardingFact> Stats, IReadOnlyList<OnboardingFact> Advanced,
    IReadOnlyList<OnboardingWarning> Warnings);
internal sealed record OnboardingJob(
    string JobId, string Status, string Stage, string StageLabel, string Message, bool Determinate,
    double? Percent, long ElapsedSeconds, bool CanCancel, bool CanRetry, bool CanFallbackToCpu,
    IReadOnlyList<OnboardingFact> Stages, IReadOnlyList<string> LogTail, string ErrorDetail,
    IReadOnlyList<OnboardingFact> Benchmark);
internal sealed record OnboardingConnection(
    string ActiveModelId, string ActualDevice, IReadOnlyList<OnboardingFact> Endpoints,
    IReadOnlyList<OnboardingFact> Snippets);

/// <summary>
/// The browser first-run wizard (<c>app/onboarding_ui.py</c>) over the same
/// <c>/v1/onboarding/*</c> endpoints. Responses are allowlist-parsed and bounded; labels
/// match the browser so <c>tests/test_web_native_parity.py</c> can detect drift.
/// </summary>
internal static partial class Onboarding
{
    public static readonly string[] Steps =
        ["Welcome", "System scan", "NPU readiness", "Recommended model", "Prepare model", "Your local benchmark", "Ready"];
    public static readonly string[] Devices = ["CPU", "GPU", "NPU", "AUTO"];
    public const string DocumentationUrl = "https://github.com/Quazmoz/InferBridge/blob/main/docs/FIRST_RUN.md";

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")]
    private static partial Regex ModelIdPattern(); // PrepareModelRequest.model_id
    [GeneratedRegex("^[A-Za-z0-9_-]{1,64}$")]
    private static partial Regex JobIdPattern();

    private static string Text(JsonElement root, string name, int max = 400)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(name, out var value)) return "";
        string text = value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.TryGetInt64(out long whole) ? whole.ToString(CultureInfo.InvariantCulture)
                : value.GetDouble().ToString("0.##", CultureInfo.InvariantCulture),
            JsonValueKind.True => "Yes",
            JsonValueKind.False => "No",
            _ => "",
        };
        text = text.Trim();
        return text.Length <= max ? text : text[..max] + "…";
    }

    private static bool Flag(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;

    private static double? Number(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            && value.TryGetDouble(out double number) && double.IsFinite(number) ? number : null;

    private static IEnumerable<JsonElement> Array(JsonElement root, string name, int max) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().Take(max) : [];

    private static string Gb(JsonElement root, string name) =>
        Number(root, name) is double gb ? gb.ToString("0.00", CultureInfo.InvariantCulture) + " GB" : "Unknown";

    private static string Metric(JsonElement root, string name, string suffix) =>
        Number(root, name) is double value ? value.ToString("0.00", CultureInfo.InvariantCulture) + suffix : "Unknown";

    private static string Or(string value, string fallback) => value.Length > 0 ? value : fallback;

    public static bool IsValidModelId(string id) => ModelIdPattern().IsMatch(id);

    public static OnboardingStatus ParseStatus(JsonElement root) => new(
        Flag(root, "completed"), Flag(root, "restart_requested"), Flag(root, "rerun_scan_recommended"),
        Text(root, "recovery_message"));

    public static OnboardingScan ParseScan(JsonElement root) => new(
        Array(root, "items", 40).Select(item => new OnboardingFact(Text(item, "label", 80), Text(item, "value", 200),
            Text(item, "detail"), Text(item, "status", 20))).Where(item => item.Label.Length > 0).ToArray(),
        Array(root, "warnings", 20).Select(item => item.ValueKind == JsonValueKind.String
            ? (item.GetString() ?? "").Trim() : "").Where(text => text.Length is > 0 and <= 400).ToArray());

    public static OnboardingNpu ParseNpu(JsonElement root)
    {
        string url = Text(root, "support_url", 300);
        // Only the official Intel driver page is opened; anything else is dropped.
        return new(Or(Text(root, "title", 120), "NPU readiness unknown"), Text(root, "explanation", 800),
            Or(Text(root, "fallback_device", 64), "None reported"), Or(Text(root, "driver_version", 64), "Unknown"),
            url.StartsWith("https://www.intel.com/", StringComparison.Ordinal) ? url : "");
    }

    public static OnboardingRecommendation ParseRecommendation(JsonElement root)
    {
        string id = Text(root, "model_id", 200);
        if (!IsValidModelId(id)) throw new JsonException("The engine returned an invalid model recommendation.");
        string requested = Text(root, "requested_device", 64);
        string firstLoad = Number(root, "first_load_seconds") is double seconds
            ? seconds.ToString("0.##", CultureInfo.InvariantCulture) + "s" : "Unknown";
        bool remote = Flag(root, "trust_remote_code");
        return new(id, Or(Text(root, "model_name", 160), id), Text(root, "description", 600), Text(root, "reason", 600),
            BackendClient.IsSupportedInferenceDevice(requested) ? requested : "CPU", remote,
            [
                new("Requested device", Or(requested, "Unknown")),
                new("Expected actual device", Or(Text(root, "expected_actual_device", 64), "Unknown")),
                new("Download", Gb(root, "download_size_gb")),
                new("Converted", Gb(root, "converted_size_gb")),
                new("Runtime memory", Gb(root, "runtime_memory_gb")),
                new("First load", firstLoad),
                new("Context / output", Text(root, "context_length", 12) + " / " + Text(root, "output_tokens", 12)),
                new("Required free disk", Gb(root, "required_free_disk_gb")),
            ],
            [
                new("Profile", Text(root, "profile", 40)),
                new("Precision", Text(root, "precision", 40)),
                new("Compatibility", Text(root, "compatibility", 20)),
                new("Fit score", Text(root, "fit_score", 12)),
                new("Remote model code", remote ? "Reviewed catalog entry, explicit opt-in required" : "Disabled"),
            ],
            Array(root, "warnings", 20).Select(item => new OnboardingWarning(
                Text(item, "severity", 20) == "blocking", Text(item, "message")))
                .Where(item => item.Message.Length > 0).ToArray());
    }

    public static OnboardingJob ParseJob(JsonElement root)
    {
        string id = Text(root, "job_id", 80);
        string status = Text(root, "status", 20);
        if (!JobIdPattern().IsMatch(id) || status is not ("running" or "ready" or "failed" or "cancelled"))
            throw new JsonException("The engine returned an invalid preparation status.");
        double? percent = Number(root, "percent") is double value ? Math.Clamp(value, 0, 100) : null;
        IReadOnlyList<OnboardingFact> benchmark = [];
        if (root.TryGetProperty("benchmark", out var run) && run.ValueKind == JsonValueKind.Object)
            benchmark =
            [
                new("Model", Text(run, "model_id", 160)),
                new("Actual device", Or(Text(run, "actual_device", 64), Text(root, "actual_device", 64))),
                new("Load or compilation time", Metric(run, "load_time_ms", " ms")),
                new("Time to first token", Metric(run, "time_to_first_token_ms", " ms")),
                new("Generation throughput", Metric(run, "tokens_sec", " tokens/s")),
                new("Completion tokens", Or(Text(run, "completion_tokens", 12), "0")),
                new("Benchmark status", Flag(run, "success") ? "Passed" : "Failed"),
                new("Evidence", Flag(run, "mock") ? "Deterministic mock" : "Measured on the active runtime"),
            ];
        return new(id, status, Text(root, "stage", 20), Or(Text(root, "stage_label", 120), "Prepare model"),
            Or(Text(root, "message", 400), "Working"), Flag(root, "determinate") && percent is not null, percent,
            (long)(Number(root, "elapsed_seconds") ?? 0), Flag(root, "can_cancel"), Flag(root, "can_retry"),
            Flag(root, "can_fallback_to_cpu"),
            Array(root, "stages", 12).Select(item => new OnboardingFact(Text(item, "label", 120), "",
                Status: Text(item, "status", 20))).Where(item => item.Label.Length > 0).ToArray(),
            Array(root, "safe_log_tail", 40).Select(item => item.ValueKind == JsonValueKind.String
                ? (item.GetString() ?? "") : "").Select(line => line.Length <= 400 ? line : line[..400]).ToArray(),
            Text(root, "error_detail", 600), benchmark);
    }

    public static OnboardingConnection ParseConnection(JsonElement root)
    {
        string model = Text(root, "active_model_id", 200), device = Text(root, "actual_device", 64);
        if (model.Length == 0 || device.Length == 0)
            throw new JsonException("The engine returned no verified active model.");
        string Service(string name)
        {
            root.TryGetProperty(name, out var service);
            return "Base URL: " + Text(service, "base_url", 200) + "\nAPI key: " + Text(service, "api_key", 80)
                + "\n" + Text(service, "note", 300);
        }
        return new(model, device,
            [
                new("Chat URL", Text(root, "chat_url", 200)),
                new("Base URL", Text(root, "base_url", 200)),
                new("Health", Text(root, "health_url", 200)),
            ],
            [
                new("OpenAI Python client", Text(root, "openai_python", 2000)),
                new("Environment variables", Text(root, "environment_variables", 1000)),
                new("Open WebUI", Service("open_webui")),
                new("n8n", Service("n8n")),
            ]);
    }

    public static string JobPath(string jobId) => JobIdPattern().IsMatch(jobId)
        ? "/v1/onboarding/preparation/" + jobId
        : throw new ArgumentException("Invalid preparation job.", nameof(jobId));
}

internal sealed partial class BackendClient
{
    private const string OnboardingApi = "/v1/onboarding";

    private async Task<JsonElement> OnboardingGetAsync(string path, string? apiKey,
        CancellationToken cancellationToken, int seconds = 15)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + path, apiKey, cancellationToken,
            TimeSpan.FromSeconds(seconds));
        return document.RootElement.Clone();
    }

    private async Task<JsonElement> OnboardingPostAsync(string path, object? body, string? apiKey,
        CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + path, body is null ? "{}" : JsonSerializer.Serialize(body),
            apiKey, cancellationToken, TimeSpan.FromSeconds(30), MaximumJsonBytes);
        return document.RootElement.Clone();
    }

    public async Task<OnboardingStatus> GetOnboardingStatusAsync(string? apiKey, CancellationToken token) =>
        Onboarding.ParseStatus(await OnboardingGetAsync(OnboardingApi + "/status", apiKey, token));

    // The first scan can take a while; later ones are cached by the engine unless refresh is set.
    public async Task<OnboardingScan> GetOnboardingScanAsync(bool refresh, string? apiKey, CancellationToken token) =>
        Onboarding.ParseScan(await OnboardingGetAsync(OnboardingApi + "/system-scan?refresh="
            + (refresh ? "true" : "false"), apiKey, token, 45));

    public async Task<OnboardingNpu> GetNpuReadinessAsync(bool refresh, string? apiKey, CancellationToken token) =>
        Onboarding.ParseNpu(await OnboardingGetAsync(OnboardingApi + "/npu-readiness?refresh="
            + (refresh ? "true" : "false"), apiKey, token, 45));

    public async Task<OnboardingRecommendation> GetOnboardingRecommendationAsync(bool refresh, string? apiKey,
        CancellationToken token) =>
        Onboarding.ParseRecommendation(await OnboardingGetAsync(OnboardingApi + "/recommendation?refresh="
            + (refresh ? "true" : "false"), apiKey, token, 45));

    /// <summary>Same body as the browser form; native never enables reviewed repository code.</summary>
    public async Task<OnboardingJob> StartOnboardingPreparationAsync(OnboardingRecommendation recommendation,
        string device, string? storage, bool confirmLicense, bool confirmDisk, bool acknowledgeWarnings,
        string? apiKey, CancellationToken token)
    {
        if (recommendation.TrustRemoteCode)
            throw new EngineRequestException(
                "This model needs reviewed repository code, which the native app never enables. "
                + "Prepare it from the web UI or choose another model.");
        if (!Onboarding.IsValidModelId(recommendation.ModelId) || !IsSupportedInferenceDevice(device))
            throw new ArgumentException("Choose a valid model and device.");
        storage = string.IsNullOrWhiteSpace(storage) ? null : storage.Trim();
        if (storage is { Length: > 1024 } || (storage is not null && storage.Any(char.IsControl)))
            throw new ArgumentException("The model storage location is too long or invalid.");
        return Onboarding.ParseJob(await OnboardingPostAsync(OnboardingApi + "/prepare", new
        {
            model_id = recommendation.ModelId,
            device,
            model_storage_location = storage,
            confirm_license = confirmLicense,
            confirm_disk_requirement = confirmDisk,
            acknowledge_warnings = acknowledgeWarnings,
            trust_remote_code = false,
        }, apiKey, token));
    }

    public async Task<OnboardingJob> GetOnboardingJobAsync(string jobId, string? apiKey, CancellationToken token) =>
        Onboarding.ParseJob(await OnboardingGetAsync(Onboarding.JobPath(jobId), apiKey, token));

    public async Task CancelOnboardingJobAsync(string jobId, string? apiKey, CancellationToken token) =>
        await OnboardingPostAsync(Onboarding.JobPath(jobId) + "/cancel", null, apiKey, token);

    public async Task<OnboardingConnection> CompleteOnboardingAsync(string jobId, string? apiKey,
        CancellationToken token)
    {
        _ = Onboarding.JobPath(jobId); // validates the identifier
        return Onboarding.ParseConnection(await OnboardingPostAsync(OnboardingApi + "/complete",
            new { job_id = jobId }, apiKey, token));
    }

    public async Task<OnboardingConnection> GetOnboardingConnectionAsync(string? apiKey, CancellationToken token) =>
        Onboarding.ParseConnection(await OnboardingGetAsync(OnboardingApi + "/connection", apiKey, token));

    public async Task RestartOnboardingAsync(string? apiKey, CancellationToken token) =>
        await OnboardingPostAsync(OnboardingApi + "/restart", null, apiKey, token);
}
