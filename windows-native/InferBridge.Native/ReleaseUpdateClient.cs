using System.Net.Http;
using System.Text.Json;

namespace InferBridge.Native;

internal sealed record ReleaseUpdateSettings(bool Enabled, string Channel, IReadOnlyList<string> SkippedVersions)
{
    public static readonly string[] Channels = ["stable", "beta", "nightly"];

    public void Validate()
    {
        if (!Channels.Contains(Channel) || SkippedVersions is null || SkippedVersions.Count > 50
            || SkippedVersions.Any(version => version.Length is < 5 or > 64
                || !version.All(ch => char.IsAsciiLetterOrDigit(ch) || ch is '.' or '-' or '+')))
            throw new ArgumentException("Invalid update settings.");
    }
}

internal sealed record ReleaseStatusInfo(
    string Version, string Channel, string InstallationMode, ReleaseUpdateSettings Settings,
    string? LatestCheckedVersion, bool CheckDue)
{
    public bool StoreManaged => InstallationMode == "store";
}

internal sealed record ReleaseCheckInfo(
    string Status, string? LatestVersion, string? DownloadUrl, string? Sha256,
    string? ReleaseNotesUrl, string? CompatibilityWarning, string Message);

/// <summary>
/// The web "About &amp; Updates" surface: opt-in update discovery through the
/// engine's local-UI-only release routes. Store installs are updated by the
/// Microsoft Store and the engine refuses changes; native mirrors that.
/// </summary>
internal sealed partial class BackendClient
{
    // Same allowlist as app/release_models.py: https://github.com/Quazmoz/... only.
    internal static bool IsOfficialReleaseUrl(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == Uri.UriSchemeHttps && uri.Host == "github.com" && uri.IsDefaultPort
        && uri.UserInfo.Length == 0 && uri.Query.Length == 0 && uri.Fragment.Length == 0
        && (uri.AbsolutePath.StartsWith("/Quazmoz/InferBridge/", StringComparison.Ordinal)
            || uri.AbsolutePath.StartsWith("/Quazmoz/openvino-windows-llm/", StringComparison.Ordinal));

    private static ReleaseUpdateSettings ParseUpdateSettings(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object
            || !element.TryGetProperty("enabled", out var enabled)
            || enabled.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new JsonException("Invalid update settings.");
        var settings = new ReleaseUpdateSettings(enabled.GetBoolean(), ReadString(element, "channel"),
            HubStringList(element, "skipped_versions", 50, 64));
        try { settings.Validate(); }
        catch (ArgumentException error) { throw new JsonException(error.Message); }
        return settings;
    }

    private async Task<JsonDocument> ReleaseRequestAsync(HttpMethod method, string path, object? body,
        string? apiKey, CancellationToken cancellationToken, TimeSpan deadline)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var request = new HttpRequestMessage(method, origin + path);
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        if (body is not null)
            request.Content = new StringContent(JsonSerializer.Serialize(body),
                System.Text.Encoding.UTF8, "application/json");
        return await RequestJsonAsync(request, cancellationToken, deadline, MaximumChatBytes);
    }

    public async Task<ReleaseStatusInfo> GetReleaseStatusAsync(string? apiKey, CancellationToken cancellationToken)
    {
        using var document = await ReleaseRequestAsync(HttpMethod.Get, "/desktop/release/status", null,
            apiKey, cancellationToken, TimeSpan.FromSeconds(8));
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("build", out var build)
            || !root.TryGetProperty("update_checks", out var checks))
            throw new JsonException("Invalid release status.");
        string mode = ReadString(root, "installation_mode");
        if (mode is not ("store" or "portable" or "installed"))
            throw new JsonException("Invalid installation mode.");
        string? latest = HubOptionalString(root, "latest_checked_version", 64);
        return new ReleaseStatusInfo(HealthText(build, "application_version", 64),
            HealthText(build, "build_channel", 32), mode, ParseUpdateSettings(checks), latest,
            root.TryGetProperty("check_due", out var due) && due.ValueKind == JsonValueKind.True);
    }

    public async Task<ReleaseUpdateSettings> SaveReleaseSettingsAsync(
        ReleaseUpdateSettings settings, string? apiKey, CancellationToken cancellationToken)
    {
        settings.Validate();
        using var document = await ReleaseRequestAsync(HttpMethod.Put, "/desktop/release/settings", new
        {
            enabled = settings.Enabled,
            channel = settings.Channel,
            skipped_versions = settings.SkippedVersions,
        }, apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        var saved = ParseUpdateSettings(document.RootElement);
        if (saved.Enabled != settings.Enabled || saved.Channel != settings.Channel)
            throw new JsonException("The engine saved different update settings.");
        return saved;
    }

    /// <summary>Manual check. Network access happens in the engine, only when requested.</summary>
    public async Task<ReleaseCheckInfo> CheckForUpdateAsync(string? apiKey, CancellationToken cancellationToken)
    {
        using var document = await ReleaseRequestAsync(HttpMethod.Post, "/desktop/release/check", new { },
            apiKey, cancellationToken, TimeSpan.FromSeconds(60));
        var root = document.RootElement;
        string status = ReadString(root, "status");
        if (status is not ("disabled" or "not_due" or "offline" or "current" or "available" or "rejected"))
            throw new JsonException("Invalid update check result.");
        string? download = null, sha = null, notes = null;
        if (root.TryGetProperty("manifest", out var manifest) && manifest.ValueKind == JsonValueKind.Object)
        {
            notes = ReadString(manifest, "release_notes_url");
            string wanted = ReadString(root, "selected_artifact_type");
            if (manifest.TryGetProperty("artifacts", out var artifacts) && artifacts.ValueKind == JsonValueKind.Array)
                foreach (var artifact in artifacts.EnumerateArray())
                    if (wanted.Length > 0 && ReadString(artifact, "type") == wanted)
                    {
                        download = ReadString(artifact, "url");
                        sha = ReadString(artifact, "sha256");
                        break;
                    }
        }
        // Never surface a link outside the official repository, even if the engine relays one.
        if (!IsOfficialReleaseUrl(download)) download = null;
        if (!IsOfficialReleaseUrl(notes)) notes = null;
        if (sha is not null && (sha.Length != 64 || !sha.All(char.IsAsciiHexDigit))) sha = null;
        return new ReleaseCheckInfo(status, HubOptionalString(root, "latest_version", 64), download, sha, notes,
            HubOptionalString(root, "compatibility_warning", 512),
            HubOptionalString(root, "message", 512) ?? "");
    }
}
