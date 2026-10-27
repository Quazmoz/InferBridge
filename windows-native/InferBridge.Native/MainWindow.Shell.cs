using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Navigation shell: tag-based navigation across grouped and footer items, and
// the pane-footer status pills. Pills only change text; they never touch the
// pickers, so background polls cannot close an open dropdown.
public sealed partial class MainWindow
{
    private string? _engineOrigin;
    private string _shellStatusSignature = "";

    private void NavigateTo(string tag)
    {
        NavigationViewItem? item = MainNavigation.MenuItems
            .Concat(MainNavigation.FooterMenuItems)
            .OfType<NavigationViewItem>()
            .FirstOrDefault(candidate => (string?)candidate.Tag == tag);
        if (item is not null) MainNavigation.SelectedItem = item;
    }

    private void MainNavigation_PaneChanged(NavigationView sender, object args) =>
        UpdateShellStatusVisibility(args is not NavigationViewPaneClosingEventArgs);

    // Pills need the open pane's width; the 48 px compact rail has no room for them.
    private void MainNavigation_DisplayModeChanged(
        NavigationView sender, NavigationViewDisplayModeChangedEventArgs args) =>
        UpdateShellStatusVisibility(args.DisplayMode == NavigationViewDisplayMode.Expanded && sender.IsPaneOpen);

    private void UpdateShellStatusVisibility(bool open) =>
        ShellStatusPanel.Visibility = open ? Visibility.Visible : Visibility.Collapsed;

    private void UpdateShellStatus()
    {
        if (_closing.IsCancellationRequested) return;
        var pills = new List<(string Text, string Kind)>();
        if (_engineConnected && _rejectedApiKey is null)
            pills.Add(("Engine connected", "success"));
        else if (_engineConnected)
            pills.Add(("API key required", "warning"));
        else
            pills.Add(("Engine offline", "error"));
        if (_engineConnected && _engineOrigin is { Length: > 0 } origin
            && Uri.TryCreate(origin, UriKind.Absolute, out Uri? uri))
            pills.Add(("Server " + uri.Host + ":" + uri.Port, "neutral"));
        ModelInfo[] loaded = _shownModels.Where(model => model.CanUnload).ToArray();
        if (loaded.Length == 0)
            pills.Add(("No model loaded", "neutral"));
        foreach (ModelInfo model in loaded.Take(3))
            pills.Add((model.Name + " · " + (model.Device is { Length: > 0 } device ? device : "device not reported"),
                "accent"));
        if (loaded.Length > 3)
            pills.Add(("+" + (loaded.Length - 3) + " more loaded", "accent"));
        string signature = string.Join("|", pills.Select(pill => pill.Kind + ":" + pill.Text));
        if (signature == _shellStatusSignature) return;
        _shellStatusSignature = signature;
        ShellStatusPills.Children.Clear();
        foreach (var (text, kind) in pills)
            ShellStatusPills.Children.Add(Ui.Pill(text, kind));
    }

    // RAM gauges on Engine & devices and System Doctor share one engine reading.
    private void UpdateMemoryGauges(DoctorReport? report)
    {
        static string Gb(double? value) => ChartMath.Format(value, " GB");
        double? used = report?.TotalRamGb is double total && report.AvailableRamGb is double free
            ? Math.Max(0, total - free) : null;
        double? fraction = ChartMath.Fraction(used, report?.TotalRamGb);
        string detail = fraction is null ? "RAM not reported"
            : Gb(used) + " used of " + Gb(report!.TotalRamGb) + " · " + Gb(report.AvailableRamGb) + " available";
        HardwareRamMeter.SetValue(fraction, detail);
        DoctorRamMeter.SetValue(fraction, detail);
        HardwareStorageText.Text = report?.FreeDiskGb is double disk
            ? "Model volume: " + Gb(disk) + " free" + (report.ModelsDiskGb is double models
                ? " · " + Gb(models) + " in converted models" : "") + "."
            : "Model storage not reported yet. Run System Doctor checks to read it.";
    }
}
