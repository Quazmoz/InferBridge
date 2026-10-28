using System.Text.Json;

namespace InferBridge.Native;

/// <summary>
/// Opt-in, per-installation local transcript history. Browser history remains
/// independent. No API keys, engine state, prompts in logs, or automatic sync.
/// Plain JSON is intentional and disclosed in the native UI; do not enable it
/// without user consent.
/// </summary>
internal sealed class NativeConversationStore
{
    private const int MaximumFileBytes = 2 * 1024 * 1024;
    private const int MaximumConversations = 40;
    private readonly string _path;

    // Pinned/Archived mirror the browser conversation flags. They default to
    // false so history files written before these flags existed still load.
    internal sealed record SavedChat(string Id, string ModelId, string Title,
        DateTimeOffset UpdatedUtc, List<ChatTurn> Turns, bool Pinned = false, bool Archived = false);

    /// <summary>Retention never removes a pinned chat, so a store of only pinned chats is full.</summary>
    internal sealed class HistoryFullException() : InvalidOperationException(
        "Every saved conversation slot is pinned. Unpin or delete one before saving another.");

    private sealed record Envelope(int Version, List<SavedChat> Conversations);

    internal NativeConversationStore(string path)
    {
        _path = Path.GetFullPath(path);
        if (!Path.IsPathFullyQualified(path))
            throw new ArgumentException("History path must be absolute.", nameof(path));
    }

    internal static NativeConversationStore ForCurrentInstallation() =>
        new(Path.Combine(Path.GetDirectoryName(NativeDataPaths.ResolveMetadataFile())
            ?? throw new IOException("Invalid data root."), "native-conversations.json"));

    internal bool Exists => File.Exists(_path);

    internal IReadOnlyList<SavedChat> ReadAll()
    {
        if (!File.Exists(_path)) return Array.Empty<SavedChat>();
        RejectReparsePoint(_path);
        var info = new FileInfo(_path);
        if (info.Length is < 1 or > MaximumFileBytes)
            throw new InvalidDataException("History file exceeds the permitted size.");
        byte[] bytes = File.ReadAllBytes(_path);
        if (bytes.Length > MaximumFileBytes)
            throw new InvalidDataException("History file exceeds the permitted size.");
        Envelope? envelope;
        try
        {
            envelope = JsonSerializer.Deserialize<Envelope>(
                bytes, new JsonSerializerOptions { MaxDepth = 12 });
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("Invalid history JSON; original file preserved.", error);
        }
        if (envelope is null || envelope.Version != 1
            || envelope.Conversations is null
            || envelope.Conversations.Count > MaximumConversations)
            throw new InvalidDataException("Unrecognized conversation history format.");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (SavedChat chat in envelope.Conversations)
        {
            if (chat is null || !Guid.TryParseExact(chat.Id, "N", out _)
                || !ids.Add(chat.Id) || string.IsNullOrWhiteSpace(chat.ModelId)
                || chat.ModelId.Length > 512 || string.IsNullOrWhiteSpace(chat.Title)
                || chat.Title.Any(char.IsControl)
                || chat.Title.Length is < 1 or > 80 || chat.Turns is null
                || chat.Turns.Count is < 2 or > 20 || chat.Turns.Count % 2 != 0
                || chat.UpdatedUtc == default
                || chat.Turns.Sum(turn => turn?.Content?.Length ?? 100000) > 40000)
                throw new InvalidDataException("Invalid saved conversation; original file preserved.");
            for (int i = 0; i < chat.Turns.Count; i++)
            {
                ChatTurn? turn = chat.Turns[i];
                if (turn is null || turn.Role != (i % 2 == 0 ? "user" : "assistant")
                    || string.IsNullOrWhiteSpace(turn.Content) || turn.Content.Length > 8000)
                    throw new InvalidDataException("Invalid saved turn; original file preserved.");
            }
        }
        return envelope.Conversations;
    }

    // Local-only lookup: search stays on the user's PC, never the inference API.
    // Like the browser list: one view (active or archived), pinned first, newest next.
    internal IReadOnlyList<SavedChat> Find(string? query, bool archived = false)
    {
        string search = query?.Trim() ?? string.Empty;
        if (search.Length > 120)
            throw new ArgumentException("Search query is too long.", nameof(query));
        var chats = ReadAll().Where(chat => chat.Archived == archived)
            .OrderByDescending(chat => chat.Pinned).ThenByDescending(chat => chat.UpdatedUtc);
        if (search.Length == 0) return chats.ToList();
        return chats.Where(chat =>
            chat.Title.Contains(search, StringComparison.OrdinalIgnoreCase)
            || chat.ModelId.Contains(search, StringComparison.OrdinalIgnoreCase)
            || chat.Turns.Any(turn => turn.Content.Contains(
                search, StringComparison.OrdinalIgnoreCase))).ToList();
    }

    internal static SavedChat Create(string id, string modelId, IReadOnlyList<ChatTurn> turns)
    {
        if (!Guid.TryParseExact(id, "N", out _) || string.IsNullOrWhiteSpace(modelId)
            || modelId.Length > 512 || turns.Count is < 2 or > 20
            || turns.Count % 2 != 0 || turns.Sum(t => t.Content.Length) > 40000)
            throw new ArgumentException("Cannot save an invalid or incomplete conversation.");
        for (int i = 0; i < turns.Count; i++)
        {
            ChatTurn turn = turns[i];
            if (turn.Role != (i % 2 == 0 ? "user" : "assistant")
                || string.IsNullOrWhiteSpace(turn.Content) || turn.Content.Length > 8000)
                throw new ArgumentException("Cannot save an incomplete chat exchange.");
        }
        string title = string.Join(" ", turns[0].Content.Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        if (title.Length > 75) title = title[..75] + "…";
        return new SavedChat(id, modelId, title, DateTimeOffset.UtcNow, turns.ToList());
    }

    internal void Save(SavedChat chat)
    {
        var chats = ReadAll().ToList();
        var existing = chats.FirstOrDefault(row => row.Id == chat.Id);
        // Continuing a chat must not erase its user-edited title or its flags.
        if (existing is not null)
            chat = chat with { Title = existing.Title, Pinned = existing.Pinned, Archived = existing.Archived };
        chats.RemoveAll(row => row.Id == chat.Id);
        chats.Insert(0, chat);
        Write(Retain(chats));
    }

    // Drop the oldest unpinned chats beyond the limit; the chat being written
    // (index 0) and every pinned chat are kept, matching browser retention.
    private static List<SavedChat> Retain(List<SavedChat> chats)
    {
        for (int i = chats.Count - 1; i >= 1 && chats.Count > MaximumConversations; i--)
            if (!chats[i].Pinned) chats.RemoveAt(i);
        if (chats.Count > MaximumConversations) throw new HistoryFullException();
        return chats;
    }

    internal bool SetPinned(string id, bool pinned) =>
        Update(id, chat => chat.Pinned == pinned ? null : chat with { Pinned = pinned });

    internal bool SetArchived(string id, bool archived) =>
        Update(id, chat => chat.Archived == archived ? null : chat with { Archived = archived });

    /// <summary>Copy a saved chat under a new id, like the browser: "… copy", unpinned, active.</summary>
    internal SavedChat? Duplicate(string id, string newId)
    {
        if (!Guid.TryParseExact(id, "N", out _) || !Guid.TryParseExact(newId, "N", out _))
            return null;
        var chats = ReadAll().ToList();
        var source = chats.FirstOrDefault(row => row.Id == id);
        if (source is null || chats.Any(row => row.Id == newId)) return null;
        string title = source.Title.Length > 75 ? source.Title[..75] : source.Title;
        var copy = source with
        {
            Id = newId, Title = title + " copy", UpdatedUtc = DateTimeOffset.UtcNow,
            Turns = source.Turns.ToList(), Pinned = false, Archived = false,
        };
        chats.Insert(0, copy);
        Write(Retain(chats));
        return copy;
    }

    // Apply one flag change; a null result means "already in that state".
    private bool Update(string id, Func<SavedChat, SavedChat?> change)
    {
        if (!Guid.TryParseExact(id, "N", out _)) return false;
        var chats = ReadAll().ToList();
        int index = chats.FindIndex(row => row.Id == id);
        if (index < 0) return false;
        SavedChat? updated = change(chats[index]);
        if (updated is null) return true;
        chats.RemoveAt(index);
        chats.Insert(0, updated with { UpdatedUtc = DateTimeOffset.UtcNow });
        Write(chats);
        return true;
    }

    internal bool Rename(string id, string title)
    {
        if (!Guid.TryParseExact(id, "N", out _)) return false;
        ArgumentNullException.ThrowIfNull(title);
        string normalized = string.Join(" ", title.Split(
            (char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        if (normalized.Length is < 1 or > 80 || normalized.Any(char.IsControl))
            throw new ArgumentException("Title must contain 1–80 readable characters.", nameof(title));
        var chats = ReadAll().ToList();
        int index = chats.FindIndex(row => row.Id == id);
        if (index < 0) return false;
        if (chats[index].Title == normalized) return true;
        var renamed = chats[index] with { Title = normalized, UpdatedUtc = DateTimeOffset.UtcNow };
        chats.RemoveAt(index);
        chats.Insert(0, renamed);
        Write(chats);
        return true;
    }

    internal bool Delete(string id)
    {
        if (!Guid.TryParseExact(id, "N", out _)) return false;
        var chats = ReadAll().ToList();
        if (!chats.RemoveAll(row => row.Id == id).Equals(1))
            return false;
        Write(chats);
        return true;
    }

    // --- Interchange with the browser's conversation export (format
    // "inferbridge-conversation", schema 1). Browser history lives in the
    // browser profile; this is explicit, user-initiated file exchange, not sync.
    internal const string ExportFormat = "inferbridge-conversation";
    internal const int MaximumImportBytes = 4 * 1024 * 1024; // Same as the web import.

    internal sealed record ImportedChat(string Title, string? ModelId, string? SystemPrompt,
        List<ChatTurn> Turns, int SourceMessages, bool Pinned = false, bool Archived = false);

    internal static string ExportMarkdown(string title, IReadOnlyList<ChatTurn> turns,
        string? modelId, DateTimeOffset now)
    {
        var lines = new List<string>
        {
            "# " + SafeTitle(title), "",
            "> Exported from InferBridge native. This conversation was stored locally on the PC that created the export.",
            "", "- Exported: " + now.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ss.fffZ",
                System.Globalization.CultureInfo.InvariantCulture),
        };
        if (!string.IsNullOrWhiteSpace(modelId)) lines.Add("- Model: " + modelId);
        lines.AddRange(new[] { "", "---", "" });
        foreach (ChatTurn turn in turns)
            lines.AddRange(new[] { "## " + (turn.Role == "user" ? "You" : "Assistant"), "", turn.Content, "" });
        return string.Join("\n", lines).TrimEnd() + "\n";
    }

    internal static string ExportJson(string title, IReadOnlyList<ChatTurn> turns,
        string? modelId, string? systemPrompt, DateTimeOffset now,
        bool pinned = false, bool archived = false)
    {
        long stamp = now.ToUnixTimeMilliseconds();
        return JsonSerializer.Serialize(new
        {
            format = ExportFormat,
            schema_version = 1,
            application = "InferBridge",
            exported_at = now.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ss.fffZ",
                System.Globalization.CultureInfo.InvariantCulture),
            conversation = new
            {
                title = SafeTitle(title),
                created = stamp,
                updated = stamp,
                pinned,
                archived,
                model_id = string.IsNullOrWhiteSpace(modelId) ? null : modelId,
                system_prompt = systemPrompt ?? "",
                messages = turns.Select(turn => new { role = turn.Role, content = turn.Content }),
            },
        }, new JsonSerializerOptions { WriteIndented = true }) + "\n";
    }

    private static string SafeTitle(string title)
    {
        string clean = string.Join(" ", (title ?? "").Split((char[]?)null,
            StringSplitOptions.RemoveEmptyEntries)).Trim();
        clean = new string(clean.Where(ch => !char.IsControl(ch)).ToArray());
        return clean.Length == 0 ? "InferBridge conversation" : clean[..Math.Min(clean.Length, 120)];
    }

    /// <summary>
    /// Validate a browser or native JSON export. The native transcript keeps its
    /// stricter limits (alternating user/assistant pairs, 20 turns, 8,000
    /// characters per turn): only the most recent complete pairs are opened and
    /// the caller reports how many source messages were left in the file.
    /// </summary>
    internal static ImportedChat ImportJson(byte[] bytes)
    {
        ArgumentNullException.ThrowIfNull(bytes);
        if (bytes.Length is < 2 or > MaximumImportBytes)
            throw new InvalidDataException("JSON import is empty or larger than the 4 MiB safety limit.");
        JsonDocument document;
        try { document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 16 }); }
        catch (JsonException error) { throw new InvalidDataException("The file is not valid JSON.", error); }
        using (document)
        {
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("format", out var format) || format.ValueKind != JsonValueKind.String
                || format.GetString() != ExportFormat
                || !root.TryGetProperty("schema_version", out var schema)
                || schema.ValueKind != JsonValueKind.Number || !schema.TryGetInt32(out int version) || version != 1
                || !root.TryGetProperty("application", out var app) || app.ValueKind != JsonValueKind.String
                || app.GetString() != "InferBridge"
                || !root.TryGetProperty("conversation", out var source) || source.ValueKind != JsonValueKind.Object
                || !source.TryGetProperty("messages", out var messages) || messages.ValueKind != JsonValueKind.Array)
                throw new InvalidDataException("This is not a supported InferBridge conversation export.");
            if (messages.GetArrayLength() > 2000)
                throw new InvalidDataException("Conversation exports may contain at most 2000 messages.");
            string? Text(string name, int limit)
            {
                if (!source.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null) return null;
                if (value.ValueKind != JsonValueKind.String)
                    throw new InvalidDataException("Conversation " + name + " must be text.");
                string text = value.GetString() ?? "";
                return text[..Math.Min(text.Length, limit)];
            }
            var turns = new List<ChatTurn>();
            foreach (JsonElement message in messages.EnumerateArray())
            {
                string role = message.ValueKind == JsonValueKind.Object
                    && message.TryGetProperty("role", out var r) && r.ValueKind == JsonValueKind.String
                    ? r.GetString() ?? "" : "";
                if (role is not ("user" or "assistant")
                    || !message.TryGetProperty("content", out var content)
                    || content.ValueKind != JsonValueKind.String)
                    throw new InvalidDataException("Imported messages may only be user or assistant text.");
                turns.Add(new ChatTurn(role, content.GetString() ?? ""));
            }
            // Keep the newest complete, valid user/assistant pairs.
            var kept = new List<ChatTurn>();
            for (int i = turns.Count - 1; i >= 1 && kept.Count < 20; i--)
            {
                ChatTurn reply = turns[i], ask = turns[i - 1];
                if (reply.Role != "assistant" || ask.Role != "user"
                    || string.IsNullOrWhiteSpace(reply.Content) || string.IsNullOrWhiteSpace(ask.Content)
                    || reply.Content.Length > 8000 || ask.Content.Length > 8000
                    || kept.Sum(t => t.Content.Length) + reply.Content.Length + ask.Content.Length > 30000)
                    break;
                kept.InsertRange(0, new[] { ask, reply });
                i--;
            }
            if (kept.Count == 0)
                throw new InvalidDataException(
                    "The export has no complete user/assistant exchange that fits the native chat limits.");
            string? model = Text("model_id", 512)?.Trim();
            string? system = Text("system_prompt", ChatRequestContext.MaximumSystemPromptChars);
            string title = SafeTitle(Text("title", 120) ?? "Imported conversation");
            // Same rule as the browser import: only a literal JSON true sets a flag.
            bool Flag(string name) => source.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.True;
            return new ImportedChat(title[..Math.Min(title.Length, 80)],
                string.IsNullOrWhiteSpace(model) ? null : model,
                string.IsNullOrWhiteSpace(system) ? null : system, kept, turns.Count,
                Flag("pinned"), Flag("archived"));
        }
    }

    private void Write(List<SavedChat> chats)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(new Envelope(1, chats));
        if (bytes.Length > MaximumFileBytes)
            throw new InvalidDataException("Conversation history size limit reached.");
        string folder = Path.GetDirectoryName(_path)
            ?? throw new InvalidDataException("Invalid conversation history directory.");
        Directory.CreateDirectory(folder);
        RejectReparsePoint(folder);
        if (File.Exists(_path)) RejectReparsePoint(_path);
        string temporary = _path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew,
                FileAccess.Write, FileShare.None, 8192, FileOptions.WriteThrough))
            {
                stream.Write(bytes);
                stream.Flush(flushToDisk: true);
            }
            if (File.Exists(_path)) RejectReparsePoint(_path);
            File.Move(temporary, _path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    private static void RejectReparsePoint(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("A linked conversation history path is not supported.");
    }
}
