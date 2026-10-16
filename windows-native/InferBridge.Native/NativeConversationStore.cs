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

    internal sealed record SavedChat(string Id, string ModelId, string Title,
        DateTimeOffset UpdatedUtc, List<ChatTurn> Turns);

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
                || chat.ModelId.Length > 512 || chat.Title is null
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
        var chats = ReadAll().Where(row => row.Id != chat.Id).ToList();
        chats.Insert(0, chat);
        Write(chats.Take(MaximumConversations).ToList());
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
