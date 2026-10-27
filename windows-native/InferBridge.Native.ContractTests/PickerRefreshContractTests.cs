namespace InferBridge.Native;

// Background polling must not rebuild or disable an open selector: WinUI closes
// a ComboBox dropdown whenever its items are replaced or it is disabled.
internal static class PickerRefreshContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native picker refresh: " + message);
    }

    public static void Run()
    {
        IReadOnlyList<ModelInfo> shown = new[]
        {
            new ModelInfo("a", "Alpha", "loaded") { Device = "GPU" },
            new ModelInfo("b", "Beta", "ready_to_load"),
        };
        // A fresh poll deserializes new instances with identical values.
        IReadOnlyList<ModelInfo> same = new[]
        {
            new ModelInfo("a", "Alpha", "loaded") { Device = "GPU" },
            new ModelInfo("b", "Beta", "ready_to_load"),
        };
        IReadOnlyList<ModelInfo> statusChanged = new[]
        {
            new ModelInfo("a", "Alpha", "loaded") { Device = "GPU" },
            new ModelInfo("b", "Beta", "loading"),
        };
        IReadOnlyList<ModelInfo> deviceChanged = new[]
        {
            new ModelInfo("a", "Alpha", "loaded") { Device = "NPU" },
            new ModelInfo("b", "Beta", "ready_to_load"),
        };

        Check(!PickerRefresh.Changed(shown, same), "identical poll results were treated as a change");
        Check(PickerRefresh.Decide(shown, same, dropDownOpen: false) == PickerUpdate.Skip,
            "unchanged inventory rebuilt the picker");
        Check(PickerRefresh.Decide(shown, same, dropDownOpen: true) == PickerUpdate.Skip,
            "unchanged inventory touched an open picker");
        Check(PickerRefresh.Decide(shown, statusChanged, dropDownOpen: false) == PickerUpdate.Apply,
            "a status change was not applied");
        Check(PickerRefresh.Decide(shown, deviceChanged, dropDownOpen: false) == PickerUpdate.Apply,
            "a reported device change was not applied");
        Check(PickerRefresh.Decide(shown, statusChanged, dropDownOpen: true) == PickerUpdate.Defer,
            "a change was applied under an open dropdown instead of deferred");
        Check(PickerRefresh.Decide<ModelInfo>(null, same, dropDownOpen: false) == PickerUpdate.Apply,
            "a forced or first rebuild was skipped");
        Check(PickerRefresh.Decide(shown, Array.Empty<ModelInfo>(), dropDownOpen: false) == PickerUpdate.Apply,
            "models disappearing was not applied");
        Check(PickerRefresh.Decide(shown, shown.Reverse().ToArray(), dropDownOpen: false) == PickerUpdate.Apply,
            "a reordered inventory was not applied");

        IReadOnlyList<(string, string, string)> devices = new[]
        {
            ("Engine default", "", "default"), ("AUTO · automatic routing", "AUTO", "auto"),
            ("GPU", "GPU", "gpu"),
        };
        IReadOnlyList<(string, string, string)> rescanned = devices.Select(d => d).ToList();
        Check(PickerRefresh.Decide(devices, rescanned, dropDownOpen: false) == PickerUpdate.Skip,
            "an unchanged hardware rescan rebuilt the device pickers");
        var withNpu = devices.Append(("NPU", "NPU", "npu")).ToList();
        Check(PickerRefresh.Decide(devices, withNpu, dropDownOpen: true) == PickerUpdate.Defer,
            "a device change was applied under an open dropdown");
        Check(PickerRefresh.Decide(devices, withNpu, dropDownOpen: false) == PickerUpdate.Apply,
            "a device inventory change was not applied");

        Check(!PickerRefresh.LocksControls(refreshing: true, background: true),
            "a timer poll disabled controls (closes open dropdowns every tick)");
        Check(PickerRefresh.LocksControls(refreshing: true, background: false),
            "a user-initiated refresh no longer locks controls");
        Check(!PickerRefresh.LocksControls(refreshing: false, background: false),
            "controls stayed locked after refresh finished");
        Console.WriteLine("Native picker refresh contract: all cases passed.");
    }
}
