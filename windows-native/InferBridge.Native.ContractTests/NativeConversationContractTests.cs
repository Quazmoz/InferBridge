using System.Text.Json;

namespace InferBridge.Native;

internal static class NativeConversationContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static void Expect<T>(Action action, string message) where T : Exception
    {
        try { action(); }
        catch (T) { return; }
        throw new InvalidOperationException(message);
    }

    internal static void Run(string root)
    {
        string folder = Path.Combine(root, "local-conversation-history");
        string filename = Path.Combine(folder, "native-conversations.json");
        var storage = new NativeConversationStore(filename);
        Check(!storage.Exists && storage.ReadAll().Count == 0,
            "local history must be opt-in and not create a file on read");

        string id = Guid.NewGuid().ToString("N");
        var turns = new[]
        {
            new ChatTurn("user", "How does the GPU work?"),
            new ChatTurn("assistant", "Inference is executed by the selected engine."),
        };
        var first = NativeConversationStore.Create(id, "model-a", turns);
        storage.Save(first);
        Check(storage.Exists, "history was not saved");
        var opened = new NativeConversationStore(filename).ReadAll();
        Check(opened.Count == 1 && opened[0].Id == id
            && opened[0].ModelId == "model-a" && opened[0].Turns.SequenceEqual(turns)
            && opened[0].Title == "How does the GPU work?",
            "restart did not recover the complete native transcript");

        Check(storage.Find("gpu").Count == 1
            && storage.Find("MODEL-A").Count == 1
            && storage.Find("executed by the selected").Count == 1
            && storage.Find("nothing-matches").Count == 0,
            "local search did not cover titles, models, messages, or misses");
        Check(storage.Rename(id, "  My    GPU\tNotes  "),
            "existing conversation could not be renamed");
        Check(storage.ReadAll()[0].Title == "My GPU Notes"
            && storage.Find("my gpu notes").Count == 1,
            "renamed title was not normalized or indexed");
        Expect<ArgumentException>(() => storage.Rename(id, new string('x', 81)),
            "overlong title was accepted");
        Expect<ArgumentException>(() => storage.Rename(id, "   "),
            "empty title was accepted");
        Expect<ArgumentException>(() => storage.Find(new string('x', 121)),
            "unbounded history search was accepted");
        Check(!storage.Rename(Guid.NewGuid().ToString("N"), "Absent"),
            "renaming a missing conversation unexpectedly succeeded");

        var extended = new List<ChatTurn>(turns)
        {
            new("user", "What about CPU?"),
            new("assistant", "CPU is supported as a target."),
        };
        storage.Save(NativeConversationStore.Create(id, "model-a", extended));
        Check(storage.ReadAll().Count == 1 && storage.ReadAll()[0].Turns.Count == 4
            && storage.ReadAll()[0].Title == "My GPU Notes",
            "continuing a renamed chat lost its transcript or edited title");
        Check(!File.ReadAllText(filename).Contains("api-key", StringComparison.Ordinal),
            "history unexpectedly stored credentials");
        Check(!Directory.GetFiles(folder).Any(f => f.EndsWith(".tmp", StringComparison.Ordinal)),
            "history left behind an atomic write temporary file");

        for (int i = 0; i < 43; i++)
            storage.Save(NativeConversationStore.Create(Guid.NewGuid().ToString("N"),
                "model-" + i, turns));
        Check(storage.ReadAll().Count == 40, "history retention cap was not enforced");
        Check(storage.ReadAll()[0].ModelId == "model-42", "recent chat order changed");
        Check(storage.Delete(id) == false, "evicted chat was still present");
        string last = storage.ReadAll()[0].Id;
        Check(storage.Delete(last) && storage.ReadAll().Count == 39,
            "confirmed deletion did not remove only the selected conversation");
        Check(!storage.Delete(last), "repeated deletion unexpectedly succeeded");

        Expect<ArgumentException>(() => NativeConversationStore.Create(
            Guid.NewGuid().ToString("N"), "model-a",
            new[] { new ChatTurn("user", "unfinished") }),
            "an incomplete streaming preview was allowed into history");
        Expect<ArgumentException>(() => NativeConversationStore.Create(
            Guid.NewGuid().ToString("N"), "model-a",
            new[] { new ChatTurn("assistant", "bad"), new ChatTurn("user", "order") }),
            "invalid conversation roles were persisted");

        byte[] original = File.ReadAllBytes(filename);
        File.WriteAllText(filename, """{"version":1,"conversations":[{"id":"../escape"}]}""");
        byte[] corrupted = File.ReadAllBytes(filename);
        Expect<InvalidDataException>(() => storage.ReadAll(),
            "a malformed conversation file was silently accepted");
        Expect<InvalidDataException>(() => storage.Save(first),
            "corrupted history was silently overwritten");
        Expect<InvalidDataException>(() => storage.Find("gpu"),
            "searching malformed history must not return partial results");
        Expect<InvalidDataException>(() => storage.Rename(id, "Should not persist"),
            "renaming malformed history must not replace the original file");
        Check(File.ReadAllBytes(filename).SequenceEqual(corrupted),
            "save modified a malformed history file");
        File.WriteAllBytes(filename, original);
        Check(storage.ReadAll().Count == 39, "valid history was not recoverable");

        File.WriteAllText(filename, new string('x', 2 * 1024 * 1024 + 1));
        Expect<InvalidDataException>(() => storage.ReadAll(),
            "oversized local history was accepted");
        Check(File.Exists(filename), "oversized history was destructively removed");
        // Browser <-> native interchange uses the web export format exactly.
        var now = DateTimeOffset.Parse("2026-10-27T08:00:00Z", System.Globalization.CultureInfo.InvariantCulture);
        string exported = NativeConversationStore.ExportJson("Ti\u0007tle", turns, "model-a", "Be terse.", now);
        using (var doc = JsonDocument.Parse(exported))
        {
            var r = doc.RootElement;
            Check(r.GetProperty("format").GetString() == "inferbridge-conversation"
                && r.GetProperty("schema_version").GetInt32() == 1
                && r.GetProperty("application").GetString() == "InferBridge"
                && r.GetProperty("conversation").GetProperty("title").GetString() == "Title"
                && r.GetProperty("conversation").GetProperty("messages").GetArrayLength() == 2,
                "JSON export does not match the browser conversation export schema");
        }
        var roundTrip = NativeConversationStore.ImportJson(System.Text.Encoding.UTF8.GetBytes(exported));
        Check(roundTrip.Turns.SequenceEqual(turns) && roundTrip.ModelId == "model-a"
            && roundTrip.SystemPrompt == "Be terse." && roundTrip.SourceMessages == 2,
            "native JSON export did not round-trip");
        string markdown = NativeConversationStore.ExportMarkdown("T", turns, "model-a", now);
        Check(markdown.StartsWith("# T\n", StringComparison.Ordinal) && markdown.Contains("## You\n\nHow does")
            && markdown.Contains("## Assistant"), "Markdown export differs from the browser layout");

        // A browser export with a long or irregular history opens only its newest complete pairs.
        var browserMessages = new List<object> { new { role = "assistant", content = "orphan" } };
        for (int i = 0; i < 15; i++)
        {
            browserMessages.Add(new { role = "user", content = "q" + i, meta = new { model = "m" } });
            browserMessages.Add(new { role = "assistant", content = "a" + i });
        }
        byte[] browser = JsonSerializer.SerializeToUtf8Bytes(new
        {
            format = "inferbridge-conversation", schema_version = 1, application = "InferBridge",
            conversation = new { title = "From browser", model_id = (string?)null, messages = browserMessages },
        });
        var fromBrowser = NativeConversationStore.ImportJson(browser);
        Check(fromBrowser.Turns.Count == 20 && fromBrowser.Turns[0].Content == "q5"
            && fromBrowser.Turns[^1].Content == "a14" && fromBrowser.SourceMessages == 31
            && fromBrowser.ModelId is null,
            "browser import did not keep the newest complete pairs within native limits");
        foreach (string bad in new[]
        {
            "[]", "{\"format\":\"other\",\"schema_version\":1,\"application\":\"InferBridge\",\"conversation\":{\"messages\":[]}}",
            "{\"format\":\"inferbridge-conversation\",\"schema_version\":2,\"application\":\"InferBridge\",\"conversation\":{\"messages\":[]}}",
            "{\"format\":\"inferbridge-conversation\",\"schema_version\":1,\"application\":\"InferBridge\",\"conversation\":{\"messages\":[{\"role\":\"system\",\"content\":\"x\"}]}}",
            "{\"format\":\"inferbridge-conversation\",\"schema_version\":1,\"application\":\"InferBridge\",\"conversation\":{\"messages\":[{\"role\":\"user\",\"content\":\"only a question\"}]}}",
            "not json",
        })
            Expect<InvalidDataException>(() => NativeConversationStore.ImportJson(
                System.Text.Encoding.UTF8.GetBytes(bad)), "unsupported import was accepted: " + bad);
        Expect<InvalidDataException>(() => NativeConversationStore.ImportJson(
            new byte[NativeConversationStore.MaximumImportBytes + 1]), "oversized import was accepted");
        RunPinArchiveDuplicate(root);
        Console.WriteLine("Native opt-in conversation history contract: all cases passed.");
    }

    // Pin, archive and duplicate follow the browser conversation manager and
    // stay compatible with its "inferbridge-conversation" JSON flags.
    private static void RunPinArchiveDuplicate(string root)
    {
        string folder = Path.Combine(root, "local-conversation-flags");
        string filename = Path.Combine(folder, "native-conversations.json");
        var store = new NativeConversationStore(filename);
        ChatTurn[] Pair(string text) => new[]
        {
            new ChatTurn("user", text), new ChatTurn("assistant", "Reply to " + text),
        };

        // Files written before the flags existed still load, unpinned and active.
        Directory.CreateDirectory(folder);
        string legacyId = Guid.NewGuid().ToString("N");
        File.WriteAllText(filename, JsonSerializer.Serialize(new
        {
            Version = 1,
            Conversations = new[]
            {
                new { Id = legacyId, ModelId = "model-a", Title = "Legacy", UpdatedUtc = DateTimeOffset.UtcNow,
                    Turns = Pair("legacy") },
            },
        }));
        var legacy = store.ReadAll().Single();
        Check(!legacy.Pinned && !legacy.Archived && store.Find(null).Count == 1
            && store.Find(null, archived: true).Count == 0, "legacy history without flags did not load");

        string a = Guid.NewGuid().ToString("N"), b = Guid.NewGuid().ToString("N");
        store.Save(NativeConversationStore.Create(a, "model-a", Pair("alpha")));
        store.Save(NativeConversationStore.Create(b, "model-a", Pair("beta")));
        Check(store.Find(null)[0].Id == b, "newest chat was not listed first");
        Check(store.SetPinned(a, true) && store.SetPinned(a, true), "pin failed or was not idempotent");
        store.Save(NativeConversationStore.Create(b, "model-a", Pair("beta")));
        var listed = store.Find(null);
        Check(listed[0].Id == a && listed[0].Pinned && listed[1].Id == b,
            "pinned chat is not listed before newer unpinned chats");
        store.Save(NativeConversationStore.Create(a, "model-a", Pair("alpha")));
        Check(store.ReadAll().Single(row => row.Id == a).Pinned, "continuing a chat dropped its pin");

        Check(store.SetArchived(b, true), "archive failed");
        Check(store.Find(null).All(row => row.Id != b) && store.Find(null, archived: true).Single().Id == b
            && store.Find("beta", archived: true).Count == 1 && store.Find("beta").Count == 0,
            "archived chat is not separated from the active list and search");
        store.Save(NativeConversationStore.Create(b, "model-a", Pair("beta")));
        Check(store.ReadAll().Single(row => row.Id == b).Archived, "continuing an archived chat restored it");
        Check(store.SetArchived(b, false) && store.Find(null).Any(row => row.Id == b),
            "restoring from archive failed");
        Check(!store.SetPinned(Guid.NewGuid().ToString("N"), true) && !store.SetArchived("not-a-guid", true),
            "flagging a missing or invalid conversation succeeded");

        Check(store.Rename(a, new string('t', 80)), "rename for duplicate setup failed");
        string copyId = Guid.NewGuid().ToString("N");
        var copy = store.Duplicate(a, copyId);
        Check(copy is not null && copy.Id == copyId && !copy.Pinned && !copy.Archived
            && copy.Title == new string('t', 75) + " copy" && copy.Turns.SequenceEqual(Pair("alpha")),
            "duplicate does not follow the browser copy rules");
        var reopened = new NativeConversationStore(filename).ReadAll();
        Check(reopened.Count(row => row.Turns.SequenceEqual(Pair("alpha"))) == 2
            && reopened.Single(row => row.Id == a).Pinned, "duplicate changed or lost the original");
        Check(store.Duplicate(a, copyId) is null && store.Duplicate(Guid.NewGuid().ToString("N"),
            Guid.NewGuid().ToString("N")) is null, "duplicate reused an id or copied a missing chat");

        // Retention removes the oldest unpinned chats only, never pinned ones.
        for (int i = 0; i < 45; i++)
            store.Save(NativeConversationStore.Create(Guid.NewGuid().ToString("N"), "model-a", Pair("fill " + i)));
        var retained = store.ReadAll();
        Check(retained.Count == 40 && retained.Any(row => row.Id == a && row.Pinned)
            && retained.Any(row => row.Turns[0].Content == "fill 44"), "retention removed a pinned chat");
        foreach (var row in retained) store.SetPinned(row.Id, true);
        Expect<NativeConversationStore.HistoryFullException>(() => store.Save(NativeConversationStore.Create(
            Guid.NewGuid().ToString("N"), "model-a", Pair("overflow"))), "a full pinned history dropped a chat");
        Check(store.ReadAll().Count == 40 && store.ReadAll().All(row => row.Pinned),
            "a refused save changed the pinned history");

        // JSON exchange carries the flags both ways, like the browser export.
        string exported = NativeConversationStore.ExportJson("Flags", Pair("flag"), "model-a", null,
            DateTimeOffset.UtcNow, pinned: true, archived: true);
        using (var document = JsonDocument.Parse(exported))
        {
            var conversation = document.RootElement.GetProperty("conversation");
            Check(conversation.GetProperty("pinned").GetBoolean() && conversation.GetProperty("archived").GetBoolean(),
                "JSON export dropped pin or archive flags");
        }
        var imported = NativeConversationStore.ImportJson(System.Text.Encoding.UTF8.GetBytes(exported));
        Check(imported.Pinned && imported.Archived, "JSON import dropped pin or archive flags");
        var plain = NativeConversationStore.ImportJson(System.Text.Encoding.UTF8.GetBytes(
            exported.Replace("\"pinned\": true", "\"pinned\": \"yes\"").Replace("\"archived\": true", "\"archived\": 1")));
        Check(!plain.Pinned && !plain.Archived, "a non-boolean flag was treated as true");
    }
}
