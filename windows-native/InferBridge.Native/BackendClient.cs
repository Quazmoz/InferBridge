using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace InferBridge.Native;

internal enum ModelAction { Load, SwitchDevice, Unload, Download }

internal sealed record ModelInfo(string Id, string Name, string Status)
{
    public bool CanLoad => Status == "ready_to_load";
    public string? Device { get; init; } // Actual engine-reported device, never a selection preference.
    public bool CanUnload => Status == "loaded";
    public bool CanDelete => Status == "ready_to_load";
    public bool AvailableInChat => CanLoad || CanUnload || Status is "queued" or "loading";
    // The engine's convert route downloads, converts, then loads; on a model whose
    // files already exist it simply retries the load after a failure/cancellation.
    public bool CanDownload => Status is "not_downloaded" or "error" or "cancelled";

    public bool Permits(ModelAction action) => action switch
    {
        ModelAction.Load => CanLoad,
        ModelAction.SwitchDevice => CanUnload,
        ModelAction.Unload => CanUnload,
        _ => CanDownload,
    };

    public string StatusLabel => Status switch
    {
        "loaded" => "Loaded",
        "ready_to_load" => "Downloaded",
        "not_downloaded" => "Not downloaded",
        "queued" => "Queued to load",
        "loading" => "Loading…",
        "queued_convert" => "Queued to download",
        "converting" => "Downloading and converting…",
        "cancelled" => "Download cancelled",
        "error" => "Failed (retry available)",
        _ => "Unknown state",
    };
}

/// <summary>The engine refused a request and explained why in plain text.</summary>
internal sealed class EngineRequestException(string message) : InvalidOperationException(message);

internal sealed record ChatTurn(string Role, string Content);

// These request-scoped options are validated before contacting the Python engine.
internal sealed record GenerationOptions(int MaxTokens, double Temperature, double TopP)
{
    public static GenerationOptions Default => new(512, 0.7, 1.0);

    public static GenerationOptions FromNumbers(double tokens, double temperature, double topP)
    {
        if (!double.IsFinite(tokens) || tokens != Math.Truncate(tokens)
            || tokens is < 1 or > 4096)
            throw new ArgumentException("Maximum tokens must be a whole number from 1 to 4096.");
        var options = new GenerationOptions((int)tokens, temperature, topP);
        options.Validate();
        return options;
    }

    public void Validate()
    {
        if (MaxTokens is < 1 or > 4096
            || !double.IsFinite(Temperature) || Temperature is < 0 or > 2
            || !double.IsFinite(TopP) || TopP is < 0 or > 1)
            throw new ArgumentException("Tokens: 1–4096; temperature: 0–2; top-p: 0–1.");
    }
}

internal sealed record ModelRecoveryInfo(
    string ModelId, string ModelName, string RecoveryId, string FailedStage,
    string LastCompletedStage, string DownloadedFiles, string ConversionOutput,
    string RecommendedAction, string FailureMessage, IReadOnlyList<string> LogTail,
    IReadOnlyList<string> AvailableActions)
{
    public bool CanRun(string action) => AvailableActions.Contains(action);
}

internal sealed record StorageCleanupOption(
    string Action, string? ModelId, string Name, string Detail,
    long SizeBytes, long ReclaimableBytes, bool Available, string Reason);

internal sealed record StorageInventory(
    long ManagedBytes, long ReclaimableBytes, long ProtectedBytes,
    IReadOnlyList<StorageCleanupOption> Options, bool Truncated);

internal sealed record ModelRecommendation(string Id, string Reason);

internal sealed record DeviceSuggestion(string Device, bool Experimental, string Note);
internal sealed record DeviceInventory(string Default, IReadOnlyList<string> Available, bool Mock)
{
    public IReadOnlyList<DeviceSuggestion> Suggestions { get; init; } = Array.Empty<DeviceSuggestion>();
}

internal sealed record DesktopOperationsInfo(
    string Version,
    string InstallationMode,
    string ServerStatus,
    bool ControllerAvailable,
    int ServerPort,
    bool Mock,
    bool StartWithWindows,
    bool BenchmarkRunning,
    string LatestBenchmark,
    string LastDiagnosticsExport,
    string? ActiveModelId,
    string? ActualDevice);

internal sealed record DiagnosticsExportInfo(string Filename, IReadOnlyList<string> CollectionErrors);

internal sealed record DoctorCheck(string Level, string Title, string Detail, string NextStep);

internal sealed record DoctorReport(
    string Overall, int Errors, int Warnings, string Devices,
    string ModelSummary, IReadOnlyList<DoctorCheck> Checks, string SupportReport);


internal sealed record BackendSnapshot(
    bool Connected,
    string Message,
    string Origin,
    IReadOnlyList<ModelInfo> Models,
    bool RequiresAuthentication,
    string SessionNonce = "")
{
    public static BackendSnapshot Offline(string message) =>
        new(false, message, string.Empty, Array.Empty<ModelInfo>(), false);
}

/// <summary>
/// Session-only client for the authoritative loopback engine. No proxy, redirects,
/// alternate hostname, persistent key, or private tray-control credential.
/// </summary>
internal sealed partial class BackendClient : IDisposable
{
    private const int MaximumJsonBytes = 256 * 1024;
    private const int MaximumChatBytes = 1024 * 1024;
    private const int MaximumMetadataBytes = 16 * 1024;
    private readonly HttpClient _http;
    private readonly string? _metadataFile;

    public BackendClient() : this(new SocketsHttpHandler
    {
        AllowAutoRedirect = false,
        UseProxy = false,
    }, null)
    {
    }

    // The same production request pipeline is exercised with an in-memory
    // response handler and disposable metadata file in native contract tests.
    internal BackendClient(HttpMessageHandler handler, string? metadataFile)
    {
        _http = new HttpClient(handler, disposeHandler: true)
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };
        if (metadataFile is not null)
        {
            _metadataFile = metadataFile;
            return;
        }
        try
        {
            _metadataFile = NativeDataPaths.ResolveMetadataFile();
        }
        catch (Exception error) when (error is ArgumentException or IOException
            or NotSupportedException)
        {
            // A malformed environment override must not crash the entire WinUI
            // window or silently attach to a different engine's data directory.
            _metadataFile = null;
        }
    }

    public async Task<BackendSnapshot> InspectAsync(
        CancellationToken cancellationToken, string? apiKey = null)
    {
        if (_metadataFile is null)
            return BackendSnapshot.Offline(
                "Invalid native data directory. Check INFERBRIDGE_NATIVE_DATA_DIR and OV_LLM_DATA_DIR.");
        if (!File.Exists(_metadataFile))
            return BackendSnapshot.Offline("No local engine session found. Start the engine.");

        try
        {
            byte[] metadataBytes;
            using (var stream = new FileStream(
                _metadataFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite,
                bufferSize: 4096, FileOptions.Asynchronous | FileOptions.SequentialScan))
            {
                if (stream.Length is <= 0 or > MaximumMetadataBytes)
                    return BackendSnapshot.Offline("Invalid engine session metadata.");
                metadataBytes = new byte[checked((int)stream.Length)];
                await stream.ReadExactlyAsync(metadataBytes, cancellationToken);
            }

            using var metadata = JsonDocument.Parse(metadataBytes, new JsonDocumentOptions
            {
                MaxDepth = 8,
            });
            var root = metadata.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("port", out var portElement)
                || portElement.ValueKind != JsonValueKind.Number
                || !portElement.TryGetInt32(out int port)
                || port is < 1 or > 65535)
                return BackendSnapshot.Offline("Invalid local engine port.");

            string nonce = ReadString(root, "nonce");
            if (nonce.Length is < 8 or > 512)
                return BackendSnapshot.Offline("Invalid engine session identity.");

            string origin = "http://127.0.0.1:" + port;
            using var identity = await GetJsonAsync(
                origin + "/desktop/instance", null, cancellationToken, TimeSpan.FromSeconds(4));
            if (ReadString(identity.RootElement, "instance_nonce") != nonce
                || !identity.RootElement.TryGetProperty("port", out var claimedPort)
                || claimedPort.ValueKind != JsonValueKind.Number
                || !claimedPort.TryGetInt32(out int reportedPort)
                || reportedPort != port)
                return BackendSnapshot.Offline("The local engine changed ownership. Reconnect.");

            using var health = await GetJsonAsync(
                origin + "/health/live", null, cancellationToken, TimeSpan.FromSeconds(4));
            if (ReadString(health.RootElement, "status") != "ok")
                return BackendSnapshot.Offline("The local engine is not live.");

            try
            {
                using var modelsPayload = await GetJsonAsync(
                    origin + "/v1/models", apiKey, cancellationToken, TimeSpan.FromSeconds(4));
                if (modelsPayload.RootElement.ValueKind != JsonValueKind.Object
                    || !modelsPayload.RootElement.TryGetProperty("data", out var data)
                    || data.ValueKind != JsonValueKind.Array)
                    throw new JsonException("Invalid model inventory schema.");

                var models = new List<ModelInfo>();
                foreach (var item in data.EnumerateArray())
                {
                    if (models.Count >= 2000) break;
                    string id = ReadString(item, "id");
                    if (id.Length is < 1 or > 512) continue;
                    string name = ReadString(item, "name");
                    string status = ReadString(item, "status");
                    string actualDevice = ReadString(item, "device");
                    models.Add(new ModelInfo(id,
                        string.IsNullOrWhiteSpace(name) ? id : name,
                        status.Length is > 0 and <= 64 ? status : "unknown")
                    {
                        Device = actualDevice.Length is > 0 and <= 64
                            && actualDevice.All(ch => char.IsLetterOrDigit(ch) || ch is ':' or '.' or ',')
                            ? actualDevice : null,
                    });
                }

                return new BackendSnapshot(
                    true, "Connected to the local InferBridge engine.", origin,
                    models, false, nonce);
            }
            catch (UnauthorizedAccessException)
            {
                return new BackendSnapshot(
                    true, "Engine verified. Enter the configured API key to view models and chat.",
                    origin, Array.Empty<ModelInfo>(), true, nonce);
            }
            catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.TooManyRequests)
            {
                // The engine throttles a source after repeated invalid keys. It is
                // still the verified owner, so do not report it offline.
                return new BackendSnapshot(
                    true, "Engine verified, but it is refusing requests after repeated invalid API keys. Wait a few minutes, check the key, then refresh.",
                    origin, Array.Empty<ModelInfo>(), true, nonce);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception error) when (error is IOException
            or UnauthorizedAccessException
            or JsonException
            or HttpRequestException
            or InvalidOperationException
            or TaskCanceledException
            or ArgumentException)
        {
            return BackendSnapshot.Offline(
                "Could not verify the local engine. Check the tray and refresh.");
        }
    }

    public async Task<string> CompleteChatAsync(
        string modelId, IReadOnlyList<ChatTurn> turns, string? apiKey,
        CancellationToken cancellationToken, GenerationOptions? options = null)
    {
        ValidateChatTurns(modelId, turns);
        var generation = options ?? GenerationOptions.Default;
        generation.Validate();

        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        var messages = turns.Select(t => new { role = t.Role, content = t.Content }).ToArray();
        var body = JsonSerializer.Serialize(new
        {
            model = modelId,
            messages,
            stream = false,
            max_tokens = generation.MaxTokens,
            temperature = generation.Temperature,
            top_p = generation.TopP,
        });
        using var document = await PostJsonAsync(
            origin + "/v1/chat/completions", body, apiKey,
            cancellationToken, TimeSpan.FromSeconds(120), MaximumChatBytes);

        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("choices", out var choices)
            || choices.ValueKind != JsonValueKind.Array
            || choices.GetArrayLength() == 0
            || !choices[0].TryGetProperty("message", out var message)
            || message.ValueKind != JsonValueKind.Object
            || !message.TryGetProperty("content", out var content)
            || content.ValueKind != JsonValueKind.String)
            throw new JsonException("The engine returned an invalid chat response.");

        string answer = content.GetString() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(answer))
            throw new JsonException("The engine returned an empty answer.");
        return answer;
    }

    private static void ValidateChatTurns(string modelId, IReadOnlyList<ChatTurn> turns)
    {
        if (string.IsNullOrWhiteSpace(modelId) || modelId.Length > 512
            || turns.Count is < 1 or > 20
            || turns.Any(t => (t.Role != "user" && t.Role != "assistant")
                || t.Content.Length is < 1 or > 8000)
            || turns.Sum(t => t.Content.Length) > 32000)
            throw new ArgumentException("Chat context is empty or exceeds the native client limit.");
    }

    /// <summary>
    /// Consume the existing OpenAI SSE stream over a verified loopback connection.
    /// A partial callback is presentation-only: no reply is complete without a
    /// finish chunk and [DONE]. Cancelling closes the response body.
    /// </summary>
    public async Task<string> StreamChatAsync(
        string modelId, IReadOnlyList<ChatTurn> turns, string? apiKey,
        Action<string> onPartial, CancellationToken cancellationToken,
        GenerationOptions? options = null)
    {
        ValidateChatTurns(modelId, turns);
        var generation = options ?? GenerationOptions.Default;
        generation.Validate();
        ArgumentNullException.ThrowIfNull(onPartial);
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var request = new HttpRequestMessage(
            HttpMethod.Post, origin + "/v1/chat/completions");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        request.Content = new StringContent(JsonSerializer.Serialize(new
        {
            model = modelId,
            messages = turns.Select(t => new { role = t.Role, content = t.Content }).ToArray(),
            stream = true,
            max_tokens = generation.MaxTokens,
            temperature = generation.Temperature,
            top_p = generation.TopP,
        }), Encoding.UTF8, "application/json");
        SetAuth(request, apiKey);

        using var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        limit.CancelAfter(TimeSpan.FromSeconds(120));
        using var response = await _http.SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead, limit.Token);
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden)
            throw new UnauthorizedAccessException("The engine rejected the API key.");
        if (response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.NotFound
            or HttpStatusCode.Conflict)
            throw new EngineRequestException(await ReadRefusalAsync(response, limit.Token));
        response.EnsureSuccessStatusCode();
        if (!string.Equals(response.Content.Headers.ContentType?.MediaType,
            "text/event-stream", StringComparison.OrdinalIgnoreCase))
            throw new JsonException("The engine did not return a chat event stream.");
        if (response.Content.Headers.ContentLength > MaximumChatBytes)
            throw new IOException("The engine chat stream is too large.");

        const int maximumLineBytes = 64 * 1024;
        const int maximumAnswerChars = 32_000;
        await using var source = await response.Content.ReadAsStreamAsync(limit.Token);
        using var lineBytes = new MemoryStream();
        var utf8 = new UTF8Encoding(false, true);
        var answer = new StringBuilder();
        string? pendingData = null;
        bool done = false;
        bool finished = false;
        int totalBytes = 0;

        void ProcessEvent(string payload)
        {
            if (payload == "[DONE]")
            {
                done = true;
                return;
            }
            using var document = JsonDocument.Parse(payload, new JsonDocumentOptions
            {
                MaxDepth = 16,
            });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("choices", out var choices)
                || choices.ValueKind != JsonValueKind.Array)
                throw new JsonException("Invalid streaming chat event.");
            if (choices.GetArrayLength() == 0) return; // Optional usage event.
            var choice = choices[0];
            if (choice.ValueKind != JsonValueKind.Object)
                throw new JsonException("Invalid streaming chat choice.");
            if (choice.TryGetProperty("finish_reason", out var reason)
                && reason.ValueKind != JsonValueKind.Null)
            {
                // A non-stop finish must not be persisted as a completed reply.
                if (reason.ValueKind != JsonValueKind.String || reason.GetString() != "stop")
                    throw new EngineRequestException(
                        "The engine stopped before completing the reply. Check the model and token limit.");
                finished = true;
            }
            if (!choice.TryGetProperty("delta", out var delta)
                || delta.ValueKind != JsonValueKind.Object
                || !delta.TryGetProperty("content", out var content)
                || content.ValueKind == JsonValueKind.Null)
                return; // Role-only or finish chunk.
            if (content.ValueKind != JsonValueKind.String)
                throw new JsonException("Invalid streaming chat content.");
            string text = content.GetString() ?? "";
            if (text.Contains("[error: generation failed; see server logs for the request ID]",
                    StringComparison.Ordinal))
                throw new EngineRequestException("Local generation failed. Check engine logs.");
            if (answer.Length + text.Length > maximumAnswerChars)
                throw new IOException("The chat reply exceeds the native context limit.");
            answer.Append(text);
            if (text.Length > 0) onPartial(answer.ToString());
        }

        byte[] buffer = new byte[4096];
        while (!done)
        {
            int read = await source.ReadAsync(buffer, limit.Token);
            if (read == 0) break;
            totalBytes += read;
            if (totalBytes > MaximumChatBytes)
                throw new IOException("The engine chat stream is too large.");
            for (int i = 0; i < read; i++)
            {
                byte value = buffer[i];
                if (value != (byte)'\n')
                {
                    if (lineBytes.Length >= maximumLineBytes)
                        throw new IOException("An engine stream event is too large.");
                    lineBytes.WriteByte(value);
                    continue;
                }
                string line = utf8.GetString(lineBytes.ToArray()).TrimEnd('\r');
                lineBytes.SetLength(0);
                if (line.Length == 0)
                {
                    if (pendingData is not null) ProcessEvent(pendingData);
                    pendingData = null;
                }
                else if (line.StartsWith("data:", StringComparison.Ordinal))
                {
                    string data = line[5..].TrimStart(' ');
                    pendingData = pendingData is null ? data : pendingData + "\n" + data;
                    if (pendingData.Length > maximumLineBytes)
                        throw new IOException("An engine stream event is too large.");
                }
                else if (!line.StartsWith(':') && !line.StartsWith("event:", StringComparison.Ordinal))
                    throw new JsonException("Unexpected engine stream field.");
                if (done) break;
            }
        }
        limit.Token.ThrowIfCancellationRequested();
        if (!done || !finished)
            throw new IOException("The engine disconnected before completing the chat stream.");
        if (answer.Length == 0)
            throw new JsonException("The engine returned an empty streamed answer.");
        return answer.ToString();
    }

    // Mirrors the backend's bounded OpenVINO target grammar. The engine remains
    // authoritative for available hardware and model-specific compatibility.
    internal static bool IsSupportedInferenceDevice(string? device)
    {
        if (device is null) return true; // Engine default.
        if (device.Length is < 1 or > 64 || device != device.Trim()
            || device != device.ToUpperInvariant()) return false;
        if (device is "NPU" or "GPU" or "CPU" or "AUTO") return true;
        int separator = device.IndexOf(':');
        if (separator < 1 || device[(separator + 1)..].Contains(':')) return false;
        if (device[..separator] is not ("AUTO" or "MULTI" or "HETERO")) return false;
        string[] targets = device[(separator + 1)..].Split(',');
        return targets.Length is >= 2 and <= 3
            && targets.All(target => target is "CPU" or "GPU" or "NPU")
            && targets.Distinct(StringComparer.Ordinal).Count() == targets.Length;
    }

    public async Task<string> ChangeModelAsync(
        ModelInfo model, ModelAction action, string? apiKey, CancellationToken cancellationToken,
        string? device = null)
    {
        if (!IsSupportedInferenceDevice(device))
            throw new ArgumentException("Unsupported inference device.", nameof(device));
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        // Reconcile before mutation; do not act on a stale UI selection.
        using var modelsPayload = await GetJsonAsync(
            origin + "/v1/models", apiKey, cancellationToken, TimeSpan.FromSeconds(4));
        if (!modelsPayload.RootElement.TryGetProperty("data", out var items)
            || items.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid current model inventory.");

        bool permitted = items.EnumerateArray().Any(item =>
            ReadString(item, "id") == model.Id
            && new ModelInfo(model.Id, model.Name, ReadString(item, "status")).Permits(action));
        if (!permitted)
            throw new InvalidOperationException(
                "The model state changed. Refresh the library before trying again.");

        // The convert route checks Hugging Face access before queueing, so allow
        // for a slow network; the download itself continues in the engine.
        (string path, string body, TimeSpan deadline) = action switch
        {
            ModelAction.Load or ModelAction.SwitchDevice => ("/v1/models/load",
                JsonSerializer.Serialize(new { model = model.Id, device }), TimeSpan.FromSeconds(20)),
            ModelAction.Unload => ("/v1/models/unload",
                JsonSerializer.Serialize(new { model = model.Id }), TimeSpan.FromSeconds(20)),
            _ => ("/v1/models/convert",
                JsonSerializer.Serialize(new { model = model.Id, load_after = true, device }),
                TimeSpan.FromSeconds(60)),
        };
        using var document = await PostJsonAsync(
            origin + path, body, apiKey, cancellationToken, deadline, MaximumJsonBytes);
        string status = ReadString(document.RootElement, "status");
        if (status.Length == 0)
            throw new JsonException("The engine did not confirm the model operation.");
        return action switch
        {
            ModelAction.Load => "Load requested. The library updates as the model loads.",
            ModelAction.SwitchDevice => "Device switch requested. The running model remains available while the engine prepares the replacement.",
            ModelAction.Unload => "Model unload accepted. The library updates shortly.",
            _ => "Download started. Progress appears in the library, and the model loads when it is ready.",
        };
    }

    /// <summary>The engine's hardware-aware first model, or null when it has none to offer.</summary>
    public async Task<string> GetRuntimeSummaryAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/system/status",
            apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        var root = document.RootElement;
        if (!root.TryGetProperty("device", out var device)
            || device.ValueKind != JsonValueKind.Object
            || !device.TryGetProperty("loaded", out var loaded)
            || loaded.ValueKind != JsonValueKind.Object)
            throw new JsonException("Invalid active-device status.");
        var entries = new List<string>();
        foreach (var model in loaded.EnumerateObject())
        {
            if (entries.Count >= 32) break;
            string name = model.Name;
            if (name.Length is < 1 or > 128 || model.Value.ValueKind != JsonValueKind.String)
                continue;
            string target = model.Value.GetString() ?? "";
            if (target.Length is < 1 or > 64) continue;
            entries.Add(name + " → " + target);
        }
        return entries.Count == 0
            ? "No models currently loaded."
            : "Loaded model devices: " + string.Join("; ", entries);
    }

    public async Task<DeviceInventory> GetDevicesAsync(string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(
            origin + "/v1/devices", apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("available", out var entries)
            || entries.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid device inventory.");
        var available = new List<string>();
        foreach (var entry in entries.EnumerateArray())
        {
            if (available.Count >= 32) break;
            if (entry.ValueKind != JsonValueKind.String) continue;
            string device = entry.GetString() ?? "";
            if (device.Length is < 1 or > 64) continue;
            if (!System.Text.RegularExpressions.Regex.IsMatch(device,
                @"^(CPU|GPU|NPU)(\.[0-9]{1,3})?$", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                continue;
            available.Add(device.ToUpperInvariant());
        }
        var suggestions = new List<DeviceSuggestion>();
        if (root.TryGetProperty("suggestions", out var advanced)
            && advanced.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in advanced.EnumerateArray())
            {
                if (suggestions.Count >= 16) break;
                if (entry.ValueKind != JsonValueKind.Object) continue;
                string target = ReadString(entry, "device");
                if (!IsSupportedInferenceDevice(target) || target is "AUTO" or "CPU" or "GPU" or "NPU")
                    continue;
                if (!target.Contains(':') || suggestions.Any(existing => existing.Device == target))
                    continue;
                string note = ReadString(entry, "note");
                suggestions.Add(new DeviceSuggestion(target,
                    entry.TryGetProperty("experimental", out var flag)
                        && flag.ValueKind == JsonValueKind.True,
                    note.Length <= 240 ? note : note[..240]));
            }
        }
        return new DeviceInventory(ReadString(root, "default_device"),
            available.Distinct(StringComparer.Ordinal).ToArray(),
            root.TryGetProperty("mock", out var mock) && mock.ValueKind == JsonValueKind.True)
        {
            Suggestions = suggestions,
        };
    }

    public async Task<ModelRecommendation?> RecommendModelAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        // The first request may run a hardware scan; later ones are cached by the engine.
        using var document = await GetJsonAsync(origin + "/v1/onboarding/recommendation",
            apiKey, cancellationToken, TimeSpan.FromSeconds(45));
        var root = document.RootElement;
        string id = ReadString(root, "model_id");
        if (id.Length is < 1 or > 512 || ReadString(root, "compatibility") == "blocked")
            return null;
        string reason = ReadString(root, "reason").Trim();
        return new ModelRecommendation(id, reason.Length <= 300 ? reason : reason[..300] + "…");
    }

    /// <summary>Native presentation over the same desktop-operations contract used by the web GUI.</summary>
    public async Task<DesktopOperationsInfo> GetDesktopOperationsAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(
            origin + "/v1/desktop/operations/status", apiKey,
            cancellationToken, TimeSpan.FromSeconds(15));
        var root = document.RootElement;
        string status = ReadString(root, "server_status");
        if (status.Length is < 1 or > 64
            || !root.TryGetProperty("server_port", out var portValue)
            || portValue.ValueKind != JsonValueKind.Number
            || !portValue.TryGetInt32(out int port)
            || port is < 1 or > 65535)
            throw new JsonException("Invalid desktop operations status.");

        string benchmark = "No saved benchmark";
        if (root.TryGetProperty("benchmark", out var run)
            && run.ValueKind == JsonValueKind.Object)
        {
            benchmark = "Latest benchmark available";
            if (run.TryGetProperty("results", out var results)
                && results.ValueKind == JsonValueKind.Array && results.GetArrayLength() > 0)
            {
                var first = results[0];
                string model = ReadString(first, "model_id");
                string summaryDevice = ReadString(first, "device");
                if (model.Length is > 0 and <= 128
                    && summaryDevice.Length is > 0 and <= 64)
                    benchmark = model + " on " + summaryDevice;
            }
        }

        return new DesktopOperationsInfo(
            ReadString(root, "application_version") is { Length: > 0 and <= 64 } version
                ? version : "Unknown",
            ReadString(root, "installation_mode") is { Length: > 0 and <= 64 } mode
                ? mode : "Unknown",
            status,
            root.TryGetProperty("controller_available", out var controller)
                && controller.ValueKind == JsonValueKind.True,
            port,
            root.TryGetProperty("mock", out var mock) && mock.ValueKind == JsonValueKind.True,
            root.TryGetProperty("start_with_windows", out var startup)
                && startup.ValueKind == JsonValueKind.True,
            root.TryGetProperty("benchmark_running", out var running)
                && running.ValueKind == JsonValueKind.True,
            benchmark,
            ReadString(root, "last_diagnostics_export") is { Length: > 0 and <= 200 } latest
                ? latest : "None",
            root.TryGetProperty("active_model", out var active)
                && active.ValueKind == JsonValueKind.Object
                && ReadString(active, "id") is { Length: > 0 and <= 128 } modelId
                ? modelId : null,
            root.TryGetProperty("active_model", out var activeDevice)
                && activeDevice.ValueKind == JsonValueKind.Object
                && ReadString(activeDevice, "actual_device") is { Length: > 0 and <= 64 } device
                ? device : null);
    }

    /// <summary>
    /// System Doctor uses the same two read-only, authenticated status routes as
    /// the web GUI. Only allowlisted device and numeric data reaches the report:
    /// no local paths, prompts, credentials, or backend model-error strings.
    /// </summary>
    public async Task<DoctorReport> RunSystemDoctorAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var statusPayload = await GetJsonAsync(
            origin + "/v1/system/status", apiKey, cancellationToken,
            TimeSpan.FromSeconds(15), MaximumChatBytes);
        using var devicesPayload = await GetJsonAsync(
            origin + "/v1/devices", apiKey, cancellationToken,
            TimeSpan.FromSeconds(15));
        JsonElement status = statusPayload.RootElement;
        JsonElement devices = devicesPayload.RootElement;
        if (status.ValueKind != JsonValueKind.Object
            || devices.ValueKind != JsonValueKind.Object
            || !status.TryGetProperty("memory", out var memory)
            || memory.ValueKind != JsonValueKind.Object
            || !status.TryGetProperty("disk", out var disk)
            || disk.ValueKind != JsonValueKind.Object
            || !status.TryGetProperty("models", out var models)
            || models.ValueKind != JsonValueKind.Object
            || !models.TryGetProperty("available", out var modelsList)
            || modelsList.ValueKind != JsonValueKind.Array
            || !status.TryGetProperty("device", out var deviceStatus)
            || deviceStatus.ValueKind != JsonValueKind.Object
            || !devices.TryGetProperty("available", out var deviceList)
            || deviceList.ValueKind != JsonValueKind.Array)
            throw new JsonException("Incomplete System Doctor response.");

        var available = new List<string>();
        foreach (var device in deviceList.EnumerateArray())
        {
            if (available.Count >= 32) break;
            if (device.ValueKind != JsonValueKind.String) continue;
            string name = (device.GetString() ?? "").ToUpperInvariant();
            if (System.Text.RegularExpressions.Regex.IsMatch(name,
                @"^(CPU|GPU|NPU)(\.[0-9]{1,3})?$")
                && !available.Contains(name, StringComparer.Ordinal))
                available.Add(name);
        }
        bool mock = devices.TryGetProperty("mock", out var mockFlag)
            && mockFlag.ValueKind == JsonValueKind.True;
        bool runtime = devices.TryGetProperty("openvino_available", out var ovFlag)
            && ovFlag.ValueKind == JsonValueKind.True;
        int catalog = 0, converted = 0, loaded = 0, failures = 0;
        foreach (var model in modelsList.EnumerateArray())
        {
            if (catalog >= 2000) break;
            if (model.ValueKind != JsonValueKind.Object) continue;
            catalog++;
            if (model.TryGetProperty("is_loaded", out var isLoaded)
                && isLoaded.ValueKind == JsonValueKind.True) loaded++;
            if (!mock && model.TryGetProperty("is_downloaded", out var isDownloaded)
                && isDownloaded.ValueKind == JsonValueKind.True) converted++;
            if (ReadString(model, "status") == "error") failures++;
        }
        static double Metric(JsonElement owner, string property)
        {
            if (owner.ValueKind != JsonValueKind.Object
                || !owner.TryGetProperty(property, out var number)
                || number.ValueKind != JsonValueKind.Number
                || !number.TryGetDouble(out double value)
                || !double.IsFinite(value) || value < 0)
                return 0;
            return value;
        }
        static string Gb(double value) => value.ToString("0.#",
            System.Globalization.CultureInfo.InvariantCulture) + " GB";
        double availableRam = Metric(memory, "available_gb");
        double totalRam = Metric(memory, "total_gb");
        double freeDisk = Metric(disk, "free_gb");
        double modelsDisk = Metric(disk, "models_gb");
        bool npu = available.Any(d => d == "NPU" || d.StartsWith("NPU.", StringComparison.Ordinal));

        var checks = new List<DoctorCheck>();
        void Add(string level, string title, string detail, string next = "") =>
            checks.Add(new DoctorCheck(level, title, detail, next));
        Add("pass", "Local engine reachable", "Verified session and authenticated status endpoints responded.");
        if (mock)
            Add("warning", "Mock inference mode", "Results are simulated; this does not verify OpenVINO hardware.",
                "Disable mock mode and restart the engine to test real hardware.");
        else if (!runtime)
            Add("error", "OpenVINO runtime unavailable", "The engine did not detect OpenVINO.",
                "Repair OpenVINO dependencies and restart InferBridge.");
        else
            Add("pass", "OpenVINO runtime", "The engine reports a working OpenVINO runtime.");
        if (available.Count == 0)
            Add("error", "No inference devices", "No CPU, GPU, or NPU targets were detected.",
                "Repair the OpenVINO installation and hardware drivers.");
        else
            Add("pass", "Inference devices", string.Join(", ", available));
        if (npu)
            Add("pass", "NPU visible", "OpenVINO reports an Intel NPU device.");
        else
            Add("warning", "NPU not detected", "The NPU may be absent or its driver unavailable.",
                "On compatible hardware, update the Intel NPU driver, reboot, and run checks again.");

        string requested = ReadString(deviceStatus, "default").Trim().ToUpperInvariant();
        string target = requested.Length <= 96
            && System.Text.RegularExpressions.Regex.IsMatch(
                requested, @"^(AUTO|CPU|GPU|NPU)(\.[0-9]{1,3})?$")
            ? requested : "Custom/advanced";
        if (target != "AUTO" && target != "Custom/advanced"
            && !available.Any(d => d.Split('.')[0] == target.Split('.')[0]))
            Add("warning", "Default device unavailable", "The requested device is not in the detected inventory.",
                "Select a detected load target or repair the driver.");
        else
            Add("pass", "Default load target", target);
        var activeRoutes = new List<string>();
        if (deviceStatus.TryGetProperty("loaded", out var routes)
            && routes.ValueKind == JsonValueKind.Object)
        {
            foreach (var route in routes.EnumerateObject().Take(2000))
            {
                if (route.Value.ValueKind != JsonValueKind.String) continue;
                string candidate = route.Value.GetString()?.ToUpperInvariant() ?? "";
                if (candidate.Length is > 0 and <= 96)
                    activeRoutes.Add(candidate);
            }
        }
        if (activeRoutes.Any(r => r.StartsWith("AUTO", StringComparison.Ordinal)
            || r.StartsWith("MULTI", StringComparison.Ordinal)
            || r.StartsWith("HETERO", StringComparison.Ordinal)))
            Add("warning", "Fallback routing in use", "AUTO/MULTI/HETERO may select a different device.",
                "Use an explicit hardware target when strict placement matters.");
        else if (activeRoutes.Count > 0)
            Add("pass", "Explicit runtime routing", "Active models have non-fallback execution targets.");
        else
            Add("info", "No active route", "Load a converted model to inspect its running device.");
        if (failures > 0)
            Add("error", "Model preparation errors", failures + " model(s) reported an error.",
                "Review Model library and retry preparation after correcting the cause.");
        if (mock)
            Add("warning", "Model conversion unverified", "Mock mode cannot confirm local OpenVINO IR.");
        else if (converted == 0)
            Add("warning", "No converted models", "No local model is marked ready for OpenVINO.",
                "Download and convert a model in Model library.");
        else
            Add("pass", "Converted models", converted + " of " + catalog + " model(s) are prepared.");
        if (availableRam <= 0)
            Add("info", "Memory telemetry unavailable", "The engine did not report available RAM.");
        else if (availableRam < 2)
            Add("error", "Low available RAM", Gb(availableRam) + " available of " + Gb(totalRam) + ".",
                "Close large applications or load a smaller model.");
        else if (availableRam < 6)
            Add("warning", "Limited available RAM", Gb(availableRam) + " available of " + Gb(totalRam) + ".",
                "Prefer a smaller INT4 model and shorter context.");
        else
            Add("pass", "Available RAM", Gb(availableRam) + " available of " + Gb(totalRam) + ".");
        if (freeDisk <= 0)
            Add("info", "Disk telemetry unavailable", "No free-space reading was reported.");
        else if (freeDisk < 3)
            Add("error", "Low model storage", Gb(freeDisk) + " free on the model volume.",
                "Free disk space before converting models.");
        else if (freeDisk < 12)
            Add("warning", "Limited model storage", Gb(freeDisk) + " free on the model volume.",
                "Make space before converting larger models.");
        else
            Add("pass", "Model storage", Gb(freeDisk) + " free; " + Gb(modelsDisk) + " in converted models.");

        int errors = checks.Count(c => c.Level == "error");
        int warnings = checks.Count(c => c.Level == "warning");
        string overall = errors > 0 ? "Action required"
            : warnings > 0 ? "Ready with warnings" : "System looks ready";
        string devicesText = available.Count == 0 ? "None detected" : string.Join(", ", available);
        string modelsText = mock
            ? catalog + " catalog models (conversion simulated); " + loaded + " loaded"
            : converted + "/" + catalog + " converted; " + loaded + " loaded; " + failures + " errors";
        string report = "InferBridge System Doctor\nOverall: " + overall
            + "\nDevices: " + devicesText + "\nNPU visible: " + (npu ? "Yes" : "No")
            + "\nDefault load target: " + target
            + "\nModels: " + modelsText + "\n"
            + "RAM available: " + Gb(availableRam) + " / " + Gb(totalRam)
            + "\nDisk free: " + Gb(freeDisk) + "\nChecks:\n"
            + string.Join("\n", checks.Select(c => "- [" + c.Level + "] "
                + c.Title + ": " + c.Detail
                + (c.NextStep.Length > 0 ? " Next: " + c.NextStep : "")))
            + "\nPrivacy: API keys, prompts, conversations, model errors, and local paths are excluded.";
        return new DoctorReport(overall, errors, warnings, devicesText,
            modelsText, checks, report);
    }

    /// <summary>One explicitly requested 32-token measurement on a verified loaded model.</summary>
    public async Task<string> RunQuickBenchmarkAsync(
        string confirmedModelId, string confirmedDevice, string? apiKey,
        CancellationToken cancellationToken)
    {
        var state = await GetDesktopOperationsAsync(apiKey, cancellationToken);
        if (state.ActiveModelId != confirmedModelId
            || state.ActualDevice != confirmedDevice
            || string.IsNullOrWhiteSpace(confirmedModelId)
            || string.IsNullOrWhiteSpace(confirmedDevice)
            || state.BenchmarkRunning)
            throw new InvalidOperationException(
                "The loaded model or device changed. Refresh before running a benchmark.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(
            origin + "/v1/benchmarks/run",
            JsonSerializer.Serialize(new
            {
                model = confirmedModelId,
                devices = new[] { confirmedDevice },
                max_tokens = 32,
                runs = 1,
            }),
            apiKey, cancellationToken, TimeSpan.FromMinutes(8), MaximumChatBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("synthetic", out var syntheticFlag)
            || syntheticFlag.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
            || !root.TryGetProperty("results", out var rows)
            || rows.ValueKind != JsonValueKind.Array
            || rows.GetArrayLength() != 1)
            throw new JsonException("Unexpected benchmark result shape.");
        bool synthetic = syntheticFlag.ValueKind == JsonValueKind.True;
        if (root.TryGetProperty("mock", out var mockFlag)
            && (mockFlag.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
                || (mockFlag.ValueKind == JsonValueKind.True && !synthetic)))
            throw new JsonException("Mock benchmark cannot be labeled as measured.");
        var result = rows[0];
        if (result.ValueKind != JsonValueKind.Object
            || ReadString(result, "model_id") != confirmedModelId
            || ReadString(result, "requested_device") != confirmedDevice
            || !result.TryGetProperty("success", out var success)
            || success.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new JsonException("The benchmark did not confirm its model and device.");
        foreach (string flag in new[] { "synthetic", "mock" })
        {
            if (result.TryGetProperty(flag, out var rowFlag)
                && (rowFlag.ValueKind is not (JsonValueKind.True or JsonValueKind.False)
                    || (rowFlag.ValueKind == JsonValueKind.True && !synthetic)))
                throw new JsonException("Synthetic benchmark row cannot be labeled as measured.");
        }
        if (success.ValueKind == JsonValueKind.False)
            return "The engine could not measure " + confirmedModelId +
                " on " + confirmedDevice + ". Review System Doctor or the model status.";
        string actual = ReadString(result, "actual_device");
        if (actual.Length is < 1 or > 64)
            actual = "unreported";
        string Metric(string field, string suffix)
        {
            if (!result.TryGetProperty(field, out var value)
                || value.ValueKind != JsonValueKind.Number
                || !value.TryGetDouble(out double measured)
                || !double.IsFinite(measured) || measured < 0)
                return "Unavailable";
            return measured.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture)
                + suffix;
        }
        return (synthetic ? "Synthetic benchmark (not hardware evidence): "
            : "Measured ") + confirmedModelId +
            "\nRequested: " + confirmedDevice + " · Actual: " + actual +
            "\nDecode speed: " + Metric("decode_tokens_sec", " tokens/s") +
            "\nTime to first token: " + Metric("time_to_first_token_ms", " ms") +
            "\nTotal latency: " + Metric("total_latency_ms", " ms") +
            "\nSaved to the engine's benchmark history.";
    }

    /// <summary>Creates a sanitized ZIP on the engine host; never transfers the ZIP or secrets.</summary>
    public async Task<DiagnosticsExportInfo> ExportDiagnosticsAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(
            origin + "/v1/desktop/operations/diagnostics/export", "{}",
            apiKey, cancellationToken, TimeSpan.FromSeconds(90), MaximumJsonBytes);
        var root = document.RootElement;
        string filename = ReadString(root, "filename");
        if (ReadString(root, "status") != "created"
            || filename.Length is < 5 or > 200
            || filename != Path.GetFileName(filename)
            || !filename.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
            throw new JsonException("Invalid diagnostics export confirmation.");

        var errors = new List<string>();
        if (root.TryGetProperty("collection_errors", out var reported)
            && reported.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in reported.EnumerateArray().Take(10))
            {
                if (item.ValueKind != JsonValueKind.String) continue;
                string message = item.GetString() ?? "";
                if (message.Length is > 0 and <= 200) errors.Add(message);
            }
        }
        return new DiagnosticsExportInfo(filename, errors);
    }

    private static readonly string[] RecoveryActions =
    [
        "resume", "retry_failed_stage", "restart_download", "remove_incomplete_files",
    ];

    private static void ValidateRecoveryModelId(string modelId)
    {
        if (string.IsNullOrEmpty(modelId) || modelId.Length > 128
            || !char.IsAsciiLetterOrDigit(modelId[0])
            || modelId.Skip(1).Any(c => !char.IsAsciiLetterOrDigit(c)
                && c is not ('.' or '_' or '-')))
            throw new ArgumentException("Invalid catalog model identifier.", nameof(modelId));
    }

    private static ModelRecoveryInfo ParseRecovery(JsonElement root, string expectedModelId)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("available", out var available)
            || available.ValueKind != JsonValueKind.True
            || ReadString(root, "model_id") != expectedModelId)
            throw new JsonException("Invalid model recovery identity.");

        string recoveryId = ReadString(root, "recovery_id");
        if (recoveryId.Length is < 1 or > 128 || recoveryId.Any(char.IsControl))
            throw new JsonException("Invalid recovery state identifier.");
        if (!root.TryGetProperty("actions", out var capabilities)
            || capabilities.ValueKind != JsonValueKind.Object)
            throw new JsonException("Missing engine recovery capabilities.");
        var actions = RecoveryActions.Where(name =>
            capabilities.TryGetProperty(name, out var state)
            && state.ValueKind == JsonValueKind.True).ToArray();

        static string Limit(string value, int max) =>
            value.Length > max ? value[..max] + "…" : value;
        string name = ReadString(root, "model_name");
        string message = "";
        var logs = new List<string>();
        if (root.TryGetProperty("failure_details", out var failure)
            && failure.ValueKind == JsonValueKind.Object)
        {
            message = Limit(ReadString(failure, "message"), 350);
            if (failure.TryGetProperty("log_tail", out var tail)
                && tail.ValueKind == JsonValueKind.Array)
                foreach (var row in tail.EnumerateArray().Take(10))
                    if (row.ValueKind == JsonValueKind.String)
                        logs.Add(Limit(row.GetString() ?? "", 250));
        }
        string recommended = ReadString(root, "recommended_action");
        return new ModelRecoveryInfo(expectedModelId,
            name.Length is > 0 and <= 200 ? name : expectedModelId, recoveryId,
            Limit(ReadString(root, "failed_stage"), 80),
            Limit(ReadString(root, "last_completed_stage"), 80),
            Limit(ReadString(root, "downloaded_files"), 80),
            Limit(ReadString(root, "conversion_output"), 80),
            actions.Contains(recommended) ? recommended : "",
            message, logs, actions);
    }

    /// <summary>Read sanitized recovery details from the verified local engine.</summary>
    public async Task<ModelRecoveryInfo> GetModelRecoveryAsync(
        string modelId, string? apiKey, CancellationToken cancellationToken)
    {
        ValidateRecoveryModelId(modelId);
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var payload = await GetJsonAsync(
            origin + "/v1/models/recovery/" + Uri.EscapeDataString(modelId),
            apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        return ParseRecovery(payload.RootElement, modelId);
    }

    /// <summary>
    /// Recheck the engine's recovery identity and capabilities immediately before
    /// user-confirmed mutation. Never retry an ambiguous timeout automatically.
    /// </summary>
    public async Task<string> ApplyModelRecoveryAsync(
        ModelRecoveryInfo selected, string action, string? device, string? apiKey,
        CancellationToken cancellationToken)
    {
        ValidateRecoveryModelId(selected.ModelId);
        if (!RecoveryActions.Contains(action) || !selected.CanRun(action))
            throw new ArgumentException("This recovery action is unavailable.", nameof(action));
        if (device is not null && device is not ("NPU" or "GPU" or "CPU"))
            throw new ArgumentException("Unsupported inference device.", nameof(device));
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var currentPayload = await GetJsonAsync(
            origin + "/v1/models/recovery/" + Uri.EscapeDataString(selected.ModelId),
            apiKey, cancellationToken, TimeSpan.FromSeconds(10));
        var current = ParseRecovery(currentPayload.RootElement, selected.ModelId);
        if (selected.RecoveryId != current.RecoveryId || !current.CanRun(action))
            throw new InvalidOperationException(
                "Recovery state changed. Review the latest details before acting.");
        using var result = await PostJsonAsync(
            origin + "/v1/models/recovery/action",
            JsonSerializer.Serialize(new
            {
                model = selected.ModelId,
                recovery_id = selected.RecoveryId,
                action,
                device = action == "remove_incomplete_files" ? null : device,
            }),
            apiKey, cancellationToken, TimeSpan.FromSeconds(120), MaximumJsonBytes);
        string status = ReadString(result.RootElement, "status");
        if (ReadString(result.RootElement, "action") != action
            || status is not ("started" or "cleaned")
            || (action == "remove_incomplete_files" ? status != "cleaned" : status != "started"))
            throw new JsonException("The engine did not confirm the recovery operation.");
        return status == "cleaned"
            ? "Incomplete model files were cleaned. Refresh the model library."
            : "Recovery started. Follow progress in the model library.";
    }

    private static readonly string[] StorageActions =
    [
        "delete_converted_model", "remove_huggingface_cache",
        "remove_incomplete_data", "clear_compiled_cache",
    ];

    private static long RequireStorageBytes(JsonElement value, string name)
    {
        if (value.ValueKind != JsonValueKind.Object
            || !value.TryGetProperty(name, out var number)
            || number.ValueKind != JsonValueKind.Number
            || !number.TryGetInt64(out long size) || size < 0)
            throw new JsonException("Invalid storage size in engine inventory.");
        return size;
    }

    private static StorageInventory ParseStorageInventory(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("schema_version", out var version)
            || version.ValueKind != JsonValueKind.Number
            || !version.TryGetInt32(out int schemaVersion) || schemaVersion != 1
            || !root.TryGetProperty("totals", out var totals)
            || !root.TryGetProperty("notes", out var notes)
            || notes.ValueKind != JsonValueKind.Object
            || !notes.TryGetProperty("paths_exposed", out var pathsExposed)
            || pathsExposed.ValueKind != JsonValueKind.False
            || !notes.TryGetProperty("transaction_backups_protected", out var backupsProtected)
            || backupsProtected.ValueKind != JsonValueKind.True)
            throw new JsonException("Unrecognized or unsafe storage inventory contract.");

        long managed = RequireStorageBytes(totals, "managed_storage_bytes");
        long reclaimable = RequireStorageBytes(totals, "currently_reclaimable_bytes");
        long protectedBytes = RequireStorageBytes(totals, "protected_transaction_backup_bytes");
        if (reclaimable > managed || protectedBytes > managed)
            throw new JsonException("Inconsistent storage inventory totals.");

        var options = new List<StorageCleanupOption>();
        bool truncated = false;
        static string Label(string text, int length = 180)
        {
            text = string.Join(' ', text.Split((char[]?)null,
                StringSplitOptions.RemoveEmptyEntries));
            return text.Length > length ? text[..length] + "…" : text;
        }

        void Append(JsonElement row, string expectedAction, string? modelId,
            string name, string detail, string sizeProperty)
        {
            if (options.Count >= 200) { truncated = true; return; }
            if (row.ValueKind != JsonValueKind.Object
                || !row.TryGetProperty("cleanup", out var cleanup)
                || cleanup.ValueKind != JsonValueKind.Object
                || ReadString(cleanup, "action") != expectedAction
                || !cleanup.TryGetProperty("available", out var permitted)
                || permitted.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new JsonException("Invalid storage cleanup capability.");
            if (expectedAction != "clear_compiled_cache")
                ValidateRecoveryModelId(modelId ?? "");
            else if (modelId is not null)
                throw new JsonException("Compiled cache cleanup must be global.");

            long size = RequireStorageBytes(row, sizeProperty);
            long availableBytes = RequireStorageBytes(cleanup, "reclaimable_bytes");
            if (availableBytes > size || (permitted.ValueKind == JsonValueKind.True
                && availableBytes == 0))
                throw new JsonException("Inconsistent storage cleanup capability.");
            options.Add(new StorageCleanupOption(expectedAction, modelId,
                Label(name), Label(detail), size, availableBytes,
                permitted.ValueKind == JsonValueKind.True,
                Label(ReadString(cleanup, "reason"), 250)));
        }

        void ReadRows(string collection, string action, string idProperty,
            string nameProperty, string sizeProperty)
        {
            if (!root.TryGetProperty(collection, out var rows)
                || rows.ValueKind != JsonValueKind.Array)
                throw new JsonException("Missing storage inventory section.");
            int shownInSection = 0;
            foreach (var row in rows.EnumerateArray())
            {
                if (row.ValueKind != JsonValueKind.Object)
                    throw new JsonException("Invalid storage inventory item.");
                long size = RequireStorageBytes(row, sizeProperty);
                // Avoid filling the native UI with catalog entries that own no files.
                if (collection == "models" && size == 0) continue;
                // Bound WinUI controls without hiding all later storage categories.
                if (shownInSection >= 60) { truncated = true; continue; }
                shownInSection++;
                string? id = ReadString(row, idProperty);
                string name = ReadString(row, nameProperty);
                if (collection == "source_caches")
                {
                    if (!row.TryGetProperty("cleanup", out var cleanup))
                        throw new JsonException("Missing Hugging Face cache cleanup details.");
                    id = ReadString(cleanup, "model_id");
                }
                if (string.IsNullOrWhiteSpace(name)) name = id ?? "Managed storage";
                string detail = collection switch
                {
                    "models" => "Converted OpenVINO model",
                    "source_caches" => "Reusable Hugging Face source files"
                        + (row.TryGetProperty("shared", out var shared)
                            && shared.ValueKind == JsonValueKind.True
                            ? " · shared by multiple models" : ""),
                    _ => "Incomplete preparation and recovery data",
                };
                if (collection == "models"
                    && row.TryGetProperty("conversion_health", out var health)
                    && health.ValueKind == JsonValueKind.Object)
                    detail += " · " + ReadString(health, "label");
                if (collection == "recovery_items")
                    detail += " · " + RequireStorageBytes(
                        row, "protected_backup_bytes") + " protected backup bytes";
                Append(row, action, id, name, detail, sizeProperty);
            }
        }

        ReadRows("models", "delete_converted_model", "model_id", "name",
            "converted_size_bytes");
        ReadRows("source_caches", "remove_huggingface_cache", "model_id",
            "source_model", "size_bytes");
        ReadRows("recovery_items", "remove_incomplete_data", "model_id",
            "name", "size_bytes");
        if (!root.TryGetProperty("compiled_cache", out var compiled)
            || compiled.ValueKind != JsonValueKind.Object)
            throw new JsonException("Missing compiled cache inventory.");
        Append(compiled, "clear_compiled_cache", null,
            "OpenVINO compiled cache", "Next model load may take longer", "size_bytes");
        return new StorageInventory(managed, reclaimable, protectedBytes,
            options, truncated);
    }

    /// <summary>Read native storage data without exposing engine filesystem paths.</summary>
    public async Task<StorageInventory> GetStorageInventoryAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var response = await GetJsonAsync(origin + "/v1/storage", apiKey,
            cancellationToken, TimeSpan.FromSeconds(20));
        return ParseStorageInventory(response.RootElement);
    }

    /// <summary>
    /// Recheck the exact storage capability and measured reclaimable bytes after
    /// confirmation. The Python engine still arbitrates concurrent file changes.
    /// Never retry a timeout-after-success automatically.
    /// </summary>
    public async Task<string> CleanupStorageAsync(
        StorageCleanupOption selected, string? apiKey, CancellationToken cancellationToken)
    {
        if (!StorageActions.Contains(selected.Action) || !selected.Available
            || selected.ReclaimableBytes <= 0)
            throw new ArgumentException("Selected cleanup is not available.");
        if (selected.Action == "clear_compiled_cache")
        {
            if (selected.ModelId is not null)
                throw new ArgumentException("Global cleanup cannot target a model.");
        }
        else ValidateRecoveryModelId(selected.ModelId ?? "");

        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var fresh = await GetJsonAsync(origin + "/v1/storage", apiKey,
            cancellationToken, TimeSpan.FromSeconds(20));
        StorageInventory inventory = ParseStorageInventory(fresh.RootElement);
        var current = inventory.Options.SingleOrDefault(item =>
            item.Action == selected.Action && item.ModelId == selected.ModelId);
        if (current is null || !current.Available
            || current.ReclaimableBytes != selected.ReclaimableBytes
            || current.SizeBytes != selected.SizeBytes)
            throw new InvalidOperationException(
                "Storage state changed. Refresh the inventory before cleaning.");

        using var request = new HttpRequestMessage(HttpMethod.Post,
            origin + "/v1/storage/cleanup");
        request.Headers.Add("X-OV-LLM-UI", "1");
        SetAuth(request, apiKey);
        request.Content = new StringContent(JsonSerializer.Serialize(new
        {
            action = selected.Action,
            model_id = selected.ModelId,
        }), Encoding.UTF8, "application/json");
        using var result = await RequestJsonAsync(request, cancellationToken,
            TimeSpan.FromSeconds(120), MaximumJsonBytes);
        if (ReadString(result.RootElement, "status") != "completed"
            || ReadString(result.RootElement, "action") != selected.Action
            || (selected.ModelId is not null
                && ReadString(result.RootElement, "model_id") != selected.ModelId))
            throw new JsonException("The engine did not confirm the requested storage cleanup.");
        long freed = RequireStorageBytes(result.RootElement, "freed_bytes");
        return "Engine confirmed cleanup. Freed " + (freed / 1024d / 1024d).ToString(
            "0.##", System.Globalization.CultureInfo.InvariantCulture) + " MiB.";
    }

    /// <summary>Remove local files only after confirming the model is still unloaded.</summary>
    public async Task<string> DeleteModelAsync(
        ModelInfo model, string? apiKey, CancellationToken cancellationToken)
    {
        if (!model.CanDelete)
            throw new InvalidOperationException("Unload the model before deleting its local files.");
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var inventory = await GetJsonAsync(
            origin + "/v1/models", apiKey, cancellationToken, TimeSpan.FromSeconds(4));
        if (!inventory.RootElement.TryGetProperty("data", out var entries)
            || entries.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid current model inventory.");
        if (!entries.EnumerateArray().Any(entry =>
            ReadString(entry, "id") == model.Id
            && ReadString(entry, "status") == "ready_to_load"))
            throw new InvalidOperationException(
                "The model is no longer confirmed unloaded. Refresh before deleting.");

        using var document = await PostJsonAsync(
            origin + "/v1/models/delete", JsonSerializer.Serialize(new { model = model.Id }),
            apiKey, cancellationToken, TimeSpan.FromSeconds(60), MaximumJsonBytes);
        return ReadString(document.RootElement, "status") switch
        {
            "deleted" => "Local files deleted for " + model.Name + ".",
            "noop" => "No local model files remain for " + model.Name + ".",
            _ => throw new JsonException("The engine did not confirm model deletion."),
        };
    }

    public async Task QuitApplicationAsync(CancellationToken cancellationToken, bool waitForStartup = false)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (_metadataFile is null)
            throw new IOException("Invalid native data directory.");
        string directory = Path.GetDirectoryName(_metadataFile)!;
        string lockFile = Path.Combine(directory, "desktop-instance.lock");
        if (waitForStartup)
        {
            using var startup = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            startup.CancelAfter(TimeSpan.FromSeconds(30));
            try
            {
                // The bundled launcher can still be importing before it owns
                // the lock. Do not close and leave a just-starting engine behind.
                while (!TrayIsRunning(lockFile)) await Task.Delay(200, startup.Token);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                throw new TimeoutException("Engine startup is still pending. Check the tray and retry.");
            }
        }
        if (!TrayIsRunning(lockFile))
        {
            if ((await InspectAsync(cancellationToken)).Connected)
                throw new InvalidOperationException("This engine has no tray controller. Quit it where it was started.");
            return;
        }

        // Reuse the tray's same-user IPC, including when its server is stopped.
        // Never read private tray credentials or kill a process by name/PID.
        string temporary = Path.Combine(directory, "tray-command-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            await File.WriteAllTextAsync(temporary,
                JsonSerializer.Serialize(new { command = "quit", created_at = DateTimeOffset.UtcNow }),
                cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            File.Move(temporary, Path.Combine(directory, "tray-command.json"), overwrite: true);
        }
        finally
        {
            File.Delete(temporary);
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            while (TrayIsRunning(lockFile))
                await Task.Delay(200, timeout.Token);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new TimeoutException("InferBridge is still shutting down. Check the tray and retry.");
        }
    }

    private static bool TrayIsRunning(string lockFile)
    {
        if (!File.Exists(lockFile)) return false;
        try
        {
            using var probe = new FileStream(lockFile, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            return false;
        }
        catch (FileNotFoundException) { return false; }
        catch (IOException) { return true; }
    }

    private async Task<string> RequireVerifiedOriginAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        var status = await InspectAsync(cancellationToken, apiKey);
        if (!status.Connected)
            throw new IOException(status.Message);
        if (status.RequiresAuthentication)
            throw new UnauthorizedAccessException("The engine requires a valid API key.");
        return status.Origin;
    }

    private async Task<JsonDocument> GetJsonAsync(
        string url, string? apiKey, CancellationToken cancellationToken, TimeSpan deadline,
        int maximumBytes = MaximumJsonBytes)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        SetAuth(request, apiKey);
        return await RequestJsonAsync(request, cancellationToken, deadline, maximumBytes);
    }

    private async Task<JsonDocument> PostJsonAsync(
        string url, string body, string? apiKey, CancellationToken cancellationToken,
        TimeSpan deadline, int maximumBytes)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, url);
        request.Content = new StringContent(body, Encoding.UTF8, "application/json");
        SetAuth(request, apiKey);
        return await RequestJsonAsync(request, cancellationToken, deadline, maximumBytes);
    }

    private static void SetAuth(HttpRequestMessage request, string? apiKey)
    {
        if (!string.IsNullOrWhiteSpace(apiKey))
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
    }

    private async Task<JsonDocument> RequestJsonAsync(
        HttpRequestMessage request, CancellationToken cancellationToken,
        TimeSpan deadline, int maximumBytes)
    {
        using var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        limit.CancelAfter(deadline);
        using var response = await _http.SendAsync(
            request, HttpCompletionOption.ResponseHeadersRead, limit.Token);
        if (response.StatusCode == HttpStatusCode.Unauthorized
            || response.StatusCode == HttpStatusCode.Forbidden)
            throw new UnauthorizedAccessException("The engine rejected the API key.");
        if (response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.NotFound
            or HttpStatusCode.Conflict)
            throw new EngineRequestException(await ReadRefusalAsync(response, limit.Token));
        response.EnsureSuccessStatusCode();
        byte[] body = await ReadBoundedAsync(response, maximumBytes, limit.Token);
        return JsonDocument.Parse(body, new JsonDocumentOptions { MaxDepth = 24 });
    }

    private static async Task<byte[]> ReadBoundedAsync(
        HttpResponseMessage response, int maximumBytes, CancellationToken cancellationToken)
    {
        if (response.Content.Headers.ContentLength > maximumBytes)
            throw new IOException("The engine response is too large.");

        await using var source = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var buffer = new MemoryStream();
        byte[] chunk = new byte[8192];
        while (true)
        {
            int count = await source.ReadAsync(chunk, cancellationToken);
            if (count == 0) break;
            if (buffer.Length + count > maximumBytes)
                throw new IOException("The engine response is too large.");
            buffer.Write(chunk, 0, count);
        }
        return buffer.ToArray();
    }

    // FastAPI reports refusals as {"detail": "text"} or {"detail": {"message": "text"}}.
    // Only a bounded plain-text reason is shown; anything else gets a generic message.
    private static async Task<string> ReadRefusalAsync(
        HttpResponseMessage response, CancellationToken cancellationToken)
    {
        const string fallback = "The engine is busy or the model state changed. Refresh and retry.";
        try
        {
            byte[] bytes = await ReadBoundedAsync(response, MaximumMetadataBytes, cancellationToken);
            using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 8 });
            var root = document.RootElement;
            string message = ReadString(root, "detail");
            if (message.Length == 0 && root.ValueKind == JsonValueKind.Object
                && root.TryGetProperty("detail", out var detail))
                message = ReadString(detail, "message");
            message = string.Join(' ', message.Split((char[]?)null,
                StringSplitOptions.RemoveEmptyEntries));
            return message.Length is > 0 and <= 400 ? message : fallback;
        }
        catch (Exception error) when (error is JsonException or IOException)
        {
            return fallback;
        }
    }

    private static string ReadString(JsonElement element, string property)
    {
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? string.Empty
            : string.Empty;
    }

    public void Dispose() => _http.Dispose();
}
