using System.Globalization;
using System.Text.Json;

namespace InferBridge.Native;

/// <summary>
/// Pure chat-session rules shared by Send, Regenerate and the context check, so
/// all three build exactly the same request window. Mirrors the web composer.
/// </summary>
internal static class ChatTranscript
{
    public const int MaximumRequestTurns = 19;
    public const int MaximumRequestChars = 30000;

    /// <summary>History plus the next user turn, trimmed by whole pairs from the front.</summary>
    public static List<ChatTurn> ForRequest(IReadOnlyList<ChatTurn> history, ChatTurn? next)
    {
        var pending = new List<ChatTurn>(history);
        if (next is not null) pending.Add(next);
        // Keep complete user/assistant pairs and stay below backend/UI limits.
        while (pending.Count > 1
            && (pending.Count > MaximumRequestTurns
                || pending.Sum(turn => turn.Content.Length) > MaximumRequestChars))
            pending.RemoveRange(0, Math.Min(2, pending.Count - 1));
        return pending;
    }

    /// <summary>Like the web client, only the newest completed reply can be regenerated.</summary>
    public static bool CanRegenerate(IReadOnlyList<ChatTurn> turns) =>
        turns.Count >= 2 && turns[^1].Role == "assistant" && turns[^2].Role == "user";

    /// <summary>The request that produced the newest reply: everything except that reply.</summary>
    public static List<ChatTurn> ForRegenerate(IReadOnlyList<ChatTurn> turns)
    {
        if (!CanRegenerate(turns))
            throw new InvalidOperationException("There is no completed reply to regenerate.");
        return ForRequest(turns.Take(turns.Count - 1).ToList(), null);
    }
}

/// <summary>Model lifecycle rules shared by Chat (Eject, picker loads) and Model library.</summary>
internal static class ModelLifecycle
{
    /// <summary>Other models still in memory when <paramref name="targetId"/> loads (web "Load another model?").</summary>
    public static IReadOnlyList<ModelInfo> LoadedPeers(IEnumerable<ModelInfo> models, string targetId) =>
        models.Where(model => model.CanUnload && model.Id != targetId).ToList();

    /// <summary>The engine refuses to unload while a generation still holds the model lock (HTTP 409 "is busy").</summary>
    public static bool IsBusyRefusal(Exception error) =>
        error is EngineRequestException && error.Message.Contains("is busy", StringComparison.OrdinalIgnoreCase);

    /// <summary>Chat picker placeholder once nothing is selected (for example after Eject).</summary>
    public static string PickerPlaceholder(IEnumerable<ModelInfo> models) =>
        models.Any(model => model.CanUnload) ? "Select a model" : "No model loaded";
}

internal sealed record ContextBudgetOmission(string Role, string Preview);

/// <summary>
/// Exact tokenizer preflight from <c>POST /v1/chat/context-budget</c>, the same
/// endpoint the web composer's context chip uses. Values are engine-reported.
/// </summary>
internal sealed record ContextBudget(
    int PromptTokens, int MaxPromptTokens, double PromptBudgetPercent,
    int MaxContextTokens, int RequestedOutputTokens, int AvailableOutputTokens,
    int EffectiveOutputTokens, bool OutputLimited, int ContextUsageTokens,
    int MessageCount, int RetainedMessageCount, int DroppedTurnCount,
    bool WillTruncate, bool PromptOverBudget, bool Blocked,
    IReadOnlyList<ContextBudgetOmission> Omitted, bool OmittedPreviewTruncated, int AttachmentCount)
{
    private static string N(int value) => value.ToString("N0", CultureInfo.InvariantCulture);

    /// <summary>ready, warning or danger, with the web chip's thresholds.</summary>
    public string State => Blocked || PromptOverBudget ? "danger"
        : WillTruncate || OutputLimited || PromptBudgetPercent >= 80 ? "warning" : "ready";

    public string Summary => "Context " + N(PromptTokens) + " / " + N(MaxPromptTokens)
        + (DroppedTurnCount > 0
            ? " · " + N(DroppedTurnCount) + (DroppedTurnCount == 1 ? " turn" : " turns") + " omitted"
            : "");

    public string Notice => Blocked
        ? "The retained prompt leaves no generation room. Start a new chat or shorten the message."
        : WillTruncate
            ? N(DroppedTurnCount) + (DroppedTurnCount == 1 ? " older turn" : " older turns")
                + " will be omitted before generation. The system prompt is kept."
        : OutputLimited
            ? "The reply will be capped at " + N(EffectiveOutputTokens) + " tokens for this prompt."
        : PromptBudgetPercent >= 80
            ? "This chat is nearing its prompt budget. Older turns may be omitted after more messages."
        : "Everything fits. The system prompt is always kept.";

    public static ContextBudget Parse(JsonElement root, string expectedModel)
    {
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("model", out var model) || model.ValueKind != JsonValueKind.String
            || model.GetString() != expectedModel)
            throw new JsonException("The context check did not describe the selected model.");
        static int Count(JsonElement owner, string name)
        {
            if (!owner.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Number
                || !value.TryGetInt32(out int count) || count is < 0 or > 10_000_000)
                throw new JsonException("Invalid context check field " + name + ".");
            return count;
        }
        static bool Flag(JsonElement owner, string name)
        {
            if (!owner.TryGetProperty(name, out var value)
                || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new JsonException("Invalid context check field " + name + ".");
            return value.GetBoolean();
        }
        if (!root.TryGetProperty("prompt_budget_percent", out var percentValue)
            || percentValue.ValueKind != JsonValueKind.Number
            || !percentValue.TryGetDouble(out double percent) || !double.IsFinite(percent) || percent < 0)
            throw new JsonException("Invalid context check percentage.");
        var omitted = new List<ContextBudgetOmission>();
        if (root.TryGetProperty("dropped_messages", out var dropped))
        {
            if (dropped.ValueKind != JsonValueKind.Array)
                throw new JsonException("Invalid omitted message preview.");
            foreach (var item in dropped.EnumerateArray())
            {
                if (omitted.Count >= 12) break;
                string role = ReadText(item, "role", 32);
                string preview = ReadText(item, "preview", 200);
                omitted.Add(new ContextBudgetOmission(role.Length > 0 ? role : "message",
                    preview.Length > 0 ? preview : "(empty message)"));
            }
        }
        int maxPrompt = Count(root, "max_prompt_tokens");
        int maxContext = Count(root, "max_context_tokens");
        if (maxPrompt < 1 || maxContext < 1)
            throw new JsonException("The context check reported no context window.");
        return new ContextBudget(
            Count(root, "prompt_tokens"), maxPrompt, Math.Min(percent, 1000),
            maxContext, Count(root, "requested_output_tokens"), Count(root, "available_output_tokens"),
            Count(root, "effective_output_tokens"), Flag(root, "output_limited"),
            Count(root, "context_usage_tokens"), Count(root, "message_count"),
            Count(root, "retained_message_count"), Count(root, "dropped_turn_count"),
            Flag(root, "will_truncate"), Flag(root, "prompt_over_budget"), Flag(root, "blocked"),
            omitted,
            root.TryGetProperty("dropped_preview_truncated", out var more) && more.ValueKind == JsonValueKind.True,
            root.TryGetProperty("attachment_count", out _) ? Count(root, "attachment_count") : 0);
    }

    private static string ReadText(JsonElement owner, string name, int maximum)
    {
        if (owner.ValueKind != JsonValueKind.Object || !owner.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.String) return "";
        string text = string.Join(' ', (value.GetString() ?? "").Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        text = new string(text.Where(ch => !char.IsControl(ch)).ToArray());
        return text.Length > maximum ? text[..maximum] + "…" : text;
    }
}

/// <summary>One entry of the engine activity log (<c>GET /v1/events</c>), shown like the web activity feed.</summary>
internal sealed record EngineEvent(long Id, DateTimeOffset Time, string Level, string Message)
{
    public const int MaximumShown = 20;

    public static IReadOnlyList<EngineEvent> ParsePage(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("data", out var data)
            || data.ValueKind != JsonValueKind.Array)
            throw new JsonException("Invalid engine activity response.");
        var events = new List<EngineEvent>();
        foreach (var item in data.EnumerateArray())
        {
            if (events.Count >= 100) break;
            if (item.ValueKind != JsonValueKind.Object
                || !item.TryGetProperty("timestamp", out var stamp) || stamp.ValueKind != JsonValueKind.Number
                || !stamp.TryGetInt64(out long seconds) || seconds is < 0 or > 253402300799
                || !item.TryGetProperty("message", out var messageValue)
                || messageValue.ValueKind != JsonValueKind.String) continue;
            string message = string.Join(' ', (messageValue.GetString() ?? "").Split(
                (char[]?)null, StringSplitOptions.RemoveEmptyEntries));
            message = new string(message.Where(ch => !char.IsControl(ch)).ToArray());
            if (message.Length == 0) continue;
            if (message.Length > 300) message = message[..300] + "…";
            string level = item.TryGetProperty("level", out var levelValue)
                && levelValue.ValueKind == JsonValueKind.String
                && levelValue.GetString() is "warning" or "error" ? levelValue.GetString()! : "info";
            long id = item.TryGetProperty("id", out var idValue) && idValue.ValueKind == JsonValueKind.Number
                && idValue.TryGetInt64(out long parsed) ? parsed : 0;
            events.Add(new EngineEvent(id, DateTimeOffset.FromUnixTimeSeconds(seconds), level, message));
        }
        // Newest first, bounded like the web feed.
        return events.OrderByDescending(e => e.Time).ThenByDescending(e => e.Id)
            .Take(MaximumShown).ToList();
    }

    /// <summary>"just now", "5m ago", … exactly like the web feed's relativeTime.</summary>
    public static string Relative(DateTimeOffset time, DateTimeOffset now)
    {
        long diff = Math.Max(0, (long)(now - time).TotalSeconds);
        return diff < 5 ? "just now"
            : diff < 60 ? diff + "s ago"
            : diff < 3600 ? diff / 60 + "m ago"
            : diff < 86400 ? diff / 3600 + "h ago"
            : diff / 86400 + "d ago";
    }
}

internal sealed partial class BackendClient
{
    /// <summary>
    /// Unload through <c>POST /v1/models/unload</c>. After the user stops a reply
    /// the engine can hold the model lock briefly; only that "is busy" refusal is
    /// retried, every other refusal (not loaded, context change) is reported.
    /// </summary>
    public async Task<string> UnloadModelAsync(
        ModelInfo model, string? apiKey, CancellationToken cancellationToken,
        int busyRetries = 20, TimeSpan? retryDelay = null)
    {
        if (busyRetries is < 0 or > 100)
            throw new ArgumentOutOfRangeException(nameof(busyRetries));
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                return await ChangeModelAsync(model, ModelAction.Unload, apiKey, cancellationToken);
            }
            catch (Exception error) when (ModelLifecycle.IsBusyRefusal(error) && attempt < busyRetries)
            {
                await Task.Delay(retryDelay ?? TimeSpan.FromMilliseconds(500), cancellationToken);
            }
        }
    }

    /// <summary>Exact context preflight for the request Send would make; images are counted, not uploaded.</summary>
    public async Task<ContextBudget> GetContextBudgetAsync(
        string modelId, IReadOnlyList<ChatTurn> turns, string? apiKey,
        CancellationToken cancellationToken, GenerationOptions? options = null,
        ChatRequestContext? context = null)
    {
        ValidateChatTurns(modelId, turns);
        var generation = options ?? GenerationOptions.Default;
        generation.Validate();
        context ??= ChatRequestContext.Empty;
        context.Validate();
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        string body = JsonSerializer.Serialize(new
        {
            model = modelId,
            messages = BuildChatMessages(turns, context with { Images = Array.Empty<ChatImage>() }),
            max_tokens = generation.MaxTokens,
            temperature = generation.Temperature,
            top_p = generation.TopP,
            image_count = context.Images.Count,
        });
        using var document = await PostJsonAsync(origin + "/v1/chat/context-budget", body, apiKey,
            cancellationToken, TimeSpan.FromSeconds(20), MaximumJsonBytes);
        return ContextBudget.Parse(document.RootElement, modelId);
    }

    /// <summary>Recent engine activity (loads, unloads, errors) from the bounded event log.</summary>
    public async Task<IReadOnlyList<EngineEvent>> GetEngineEventsAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/events?limit=100", apiKey,
            cancellationToken, TimeSpan.FromSeconds(8));
        return EngineEvent.ParsePage(document.RootElement);
    }
}
