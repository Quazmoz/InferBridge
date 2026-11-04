namespace InferBridge.Native;

// Browser preferences kept natively: chat device target (ovllm.device.v1), Combine
// devices target (ovllm.device-builder.v1), advisor profile (ovllm.advisor.profile.v1)
// and age-based conversation retention (ovllm.chatRetention.v1).
internal static class AppSettingsContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native app settings: " + message);
    }

    public static void Run(string root)
    {
        string folder = Path.Combine(root, "app-settings");
        Directory.CreateDirectory(folder);
        string path = Path.Combine(folder, "native-preferences.json");

        Check(NativePreferences.RetentionDays.SequenceEqual(new[] { 0, 30, 90, 180, 365 }),
            "retention choices differ from the browser RETENTION_DAYS");
        var combined = new CombinedTarget("HETERO:GPU,CPU", null, CombinedModes.PipelineParallel, "Pipeline-parallel");
        var saved = NativePreferences.Default with
        {
            Theme = "light", ChatDevice = "HETERO:GPU,CPU", CombinedTarget = combined,
            AdvisorProfile = "lowest-memory", ConversationRetentionDays = 90,
        };
        saved.Save(path);
        Check(NativePreferences.Load(path) == saved, "device, combined target, profile or retention did not round-trip");
        Check(NativePreferences.Load(path).CombinedTarget == combined, "the combined target lost a field");

        // One invalid later field is dropped on its own; the theme and other values survive.
        File.WriteAllText(path, """
            {"Version":1,"Theme":"dark","ChatDevice":"GPU; rm","AdvisorProfile":"../x","ConversationRetentionDays":7,
             "CombinedTarget":{"Device":"AUTO:GPU,CPU","PerformanceHint":"LATENCY","ModelDistribution":"pipeline_parallel","Label":"x"}}
            """);
        Check(NativePreferences.Load(path) is { Theme: "dark", ChatDevice: null, AdvisorProfile: null,
            ConversationRetentionDays: 0, CombinedTarget: null }, "an invalid saved field was kept or reset the theme");
        File.WriteAllText(path, """{"Version":1,"Theme":"dark","CombinedTarget":{"Device":null,"Label":null}}""");
        Check(NativePreferences.Load(path) is { Theme: "dark", CombinedTarget: null }, "a null combined target crashed or was kept");
        foreach (var bad in new[]
        {
            saved with { ConversationRetentionDays = 45 }, saved with { ChatDevice = "TPU" },
            saved with { CombinedTarget = combined with { PerformanceHint = "LATENCY" } }, // hint only applies to AUTO:
        })
        {
            try { bad.Validated(); }
            catch (ArgumentException) { continue; }
            throw new InvalidOperationException("Native app settings: an invalid preference could be saved.");
        }

        // Age retention keeps the open chat and pinned chats, like the browser.
        var store = new NativeConversationStore(Path.Combine(folder, "native-conversations.json"));
        var turns = new[] { new ChatTurn("user", "hi"), new ChatTurn("assistant", "hello") };
        DateTimeOffset now = DateTimeOffset.UtcNow;
        string Id() => Guid.NewGuid().ToString("N");
        var old = NativeConversationStore.Create(Id(), "m", turns) with { UpdatedUtc = now.AddDays(-100) };
        var oldPinned = NativeConversationStore.Create(Id(), "m", turns) with { UpdatedUtc = now.AddDays(-400) };
        var recent = NativeConversationStore.Create(Id(), "m", turns) with { UpdatedUtc = now.AddDays(-10) };
        var openOld = NativeConversationStore.Create(Id(), "m", turns) with { UpdatedUtc = now.AddDays(-200) };
        foreach (var chat in new[] { old, oldPinned, recent, openOld }) store.Save(chat);
        store.SetPinned(oldPinned.Id, true);
        var all = store.ReadAll();
        Check(NativeConversationStore.RetentionCandidates(all, 0, null, now).Count == 0, "Keep until I delete them removed chats");
        Check(NativeConversationStore.RetentionCandidates(all, 90, openOld.Id, now).Select(c => c.Id)
            .SequenceEqual(new[] { old.Id }), "retention chose the wrong chats");
        Check(store.RemoveInactive(90, openOld.Id, now) == 1
            && store.ReadAll().Select(c => c.Id).OrderBy(x => x)
                .SequenceEqual(new[] { oldPinned.Id, recent.Id, openOld.Id }.OrderBy(x => x)),
            "removing inactive chats lost a kept chat");
        // Saving with retention removes old chats except the one being saved.
        Check(store.Save(openOld, 30) == 0 && store.ReadAll().Any(c => c.Id == openOld.Id),
            "the chat being saved was treated as inactive");
        var fresh = NativeConversationStore.Create(Id(), "m", turns);
        Check(store.Save(fresh, 30) == 1 && store.ReadAll().Count == 3
            && store.ReadAll().Any(c => c.Id == oldPinned.Id), "retention on save removed the wrong chats");

        Console.WriteLine("Native app settings (device, advisor profile, retention) contracts: all cases passed.");
    }
}
