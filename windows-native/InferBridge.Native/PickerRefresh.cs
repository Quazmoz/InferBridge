namespace InferBridge.Native;

internal enum PickerUpdate
{
    Skip,
    Apply,
    Defer,
}

// Background polling must never disturb a selector the user is working in.
// WinUI closes an open ComboBox dropdown when its items are rebuilt or the
// control is disabled, so periodic refreshes rebuild only on a real content
// change and wait for an open dropdown to close before applying it.
internal static class PickerRefresh
{
    public static bool Changed<T>(IReadOnlyList<T>? shown, IReadOnlyList<T> next) =>
        shown is null || !shown.SequenceEqual(next);

    public static PickerUpdate Decide<T>(
        IReadOnlyList<T>? shown, IReadOnlyList<T> next, bool dropDownOpen) =>
        !Changed(shown, next) ? PickerUpdate.Skip
            : dropDownOpen ? PickerUpdate.Defer
            : PickerUpdate.Apply;

    // A timer poll is presentation-only: it must not toggle IsEnabled on
    // controls (disabling a ComboBox closes its dropdown). User-initiated
    // refreshes still lock controls while they run.
    public static bool LocksControls(bool refreshing, bool background) =>
        refreshing && !background;
}
