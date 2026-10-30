using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// "Best model for this PC": the engine's hardware advisor (web parity with the
// browser advisor panel). Read-only; the only actions are the existing Model
// library operations. Unlike the browser's optional auto routing, the native
// app never switches the chat model by itself.
public sealed partial class MainWindow
{
    private HardwareAdvisor? _advisor;
    private bool _advisorBusy;
    private bool _updatingAdvisorProfiles;

    private async void AdvisorRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshHardwareAdvisorAsync();

    private async Task RefreshHardwareAdvisorAsync()
    {
        if (_advisorBusy || !_engineConnected || _rejectedApiKey is not null
            || _closing.IsCancellationRequested) return;
        _advisorBusy = true;
        AdvisorRefreshButton.IsEnabled = false;
        try
        {
            HardwareAdvisor advisor = await _backend.GetHardwareAdvisorAsync(ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _advisor = advisor;
            string? chosen = (AdvisorProfilePicker.SelectedItem as ComboBoxItem)?.Tag as string;
            bool changed = AdvisorProfilePicker.Items.Count != advisor.Profiles.Count
                || !AdvisorProfilePicker.Items.Cast<ComboBoxItem>().Select(i => (string?)i.Tag)
                    .SequenceEqual(advisor.Profiles.Select(p => p.Id));
            // Never rebuild under an open dropdown (PickerRefresh convention).
            if (changed && !AdvisorProfilePicker.IsDropDownOpen)
            {
                _updatingAdvisorProfiles = true;
                try
                {
                    AdvisorProfilePicker.Items.Clear();
                    foreach ((string id, string label) in advisor.Profiles)
                        AdvisorProfilePicker.Items.Add(new ComboBoxItem { Content = label, Tag = id });
                    string target = chosen is not null && advisor.Profiles.Any(p => p.Id == chosen)
                        ? chosen : advisor.DefaultProfile;
                    AdvisorProfilePicker.SelectedItem = AdvisorProfilePicker.Items.Cast<ComboBoxItem>()
                        .FirstOrDefault(item => (string?)item.Tag == target);
                }
                finally { _updatingAdvisorProfiles = false; }
            }
            AdvisorProfilePicker.IsEnabled = true;
            AdvisorNotice.Text = advisor.Notice.Length > 0 ? advisor.Notice
                : "Estimates are conservative until a benchmark on this PC is available.";
            ShowAdvisorModels(advisor);
            ShowAdvisorRecommendation();
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            AdvisorModelName.Text = "Advisor unavailable";
            AdvisorReason.Text = SafeError(error);
        }
        finally
        {
            _advisorBusy = false;
            if (!_closing.IsCancellationRequested) AdvisorRefreshButton.IsEnabled = true;
        }
    }

    private void AdvisorProfilePicker_SelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!_updatingAdvisorProfiles) ShowAdvisorRecommendation();
    }

    private AdvisorRecommendation? CurrentAdvisorRecommendation =>
        _advisor is not null && (AdvisorProfilePicker.SelectedItem as ComboBoxItem)?.Tag is string profile
            && _advisor.Recommendations.TryGetValue(profile, out var rec) ? rec : null;

    private void ShowAdvisorRecommendation()
    {
        AdvisorPills.Children.Clear();
        AdvisorWarnings.Children.Clear();
        AdvisorRecommendation? rec = CurrentAdvisorRecommendation;
        if (rec is null)
        {
            AdvisorModelName.Text = _advisor is null ? "Not checked yet" : "No compatible recommendation";
            AdvisorReason.Text = _advisor is null
                ? "Select Refresh to ask the engine which model suits this PC."
                : "No text-generation model fits this profile. Check model warnings and free memory.";
            AdvisorMeasured.Text = string.Empty;
            UpdateAdvisorActions(null);
            return;
        }
        AdvisorModelName.Text = rec.ModelName;
        AdvisorReason.Text = rec.Reason;
        foreach (string pill in rec.Pills) AdvisorPills.Children.Add(Ui.Pill(pill, "neutral"));
        AdvisorPills.Children.Add(Ui.Pill(rec.Compatibility switch
        {
            "compatible" => "Compatible", "caution" => "Caution", "blocked" => "Blocked", _ => "Unverified",
        }, rec.Compatibility switch { "compatible" => "success", "caution" => "warning", "blocked" => "error", _ => "neutral" }));
        AdvisorMeasured.Text = rec.MeasuredTokensPerSecond is double speed
            ? (rec.MeasuredSynthetic ? "Synthetic mock evidence: " : "Measured on this PC: ")
                + speed.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + " tok/s"
                + (rec.MeasuredFirstTokenMs is double first
                    ? " · first token " + first.ToString("N0", System.Globalization.CultureInfo.InvariantCulture) + " ms" : "")
            : "Not benchmarked on this PC yet; values are estimates.";
        foreach (string warning in rec.Warnings)
            AdvisorWarnings.Children.Add(Ui.Text("• " + warning, secondary: true));
        UpdateAdvisorActions(rec);
        AutomationProperties.SetName(AdvisorPills, "Recommendation details: " + string.Join(", ", rec.Pills));
    }

    private void UpdateAdvisorActions(AdvisorRecommendation? rec)
    {
        ModelInfo? model = rec is null ? null : _shownModels.FirstOrDefault(m => m.Id == rec.ModelId);
        AdvisorShowModelButton.IsEnabled = model is not null;
        (string label, bool enabled) = model switch
        {
            null => ("Not in the library", false),
            { CanUnload: true } => ("Already loaded", false),
            { CanLoad: true } => ("Load recommended model", true),
            { CanDownload: true } => ("Download and load", true),
            _ => ("Preparing…", false),
        };
        AdvisorPrepareButton.Content = label;
        AdvisorPrepareButton.IsEnabled = enabled && !_isManaging && !_chatBusy && !RefreshLocksControls && !_quitting;
    }

    private void ShowAdvisorModels(HardwareAdvisor advisor)
    {
        AdvisorModelsList.Children.Clear();
        foreach (AdvisorModel model in advisor.Models.Take(30))
        {
            var row = new WrapRow { Spacing = 8 };
            row.Children.Add(Ui.Text(model.Name, "BodyStrongTextBlockStyle"));
            row.Children.Add(Ui.Pill(model.Compatibility + " · fit " + Math.Round(model.FitScore) + "/100",
                model.Compatibility switch { "compatible" => "success", "caution" => "warning", "blocked" => "error", _ => "neutral" }));
            row.Children.Add(Ui.Text((model.RecommendedDevice.Length > 0 ? model.RecommendedDevice : "AUTO")
                + (model.RuntimeMemoryGb is double gb
                    ? " · ~" + gb.ToString("0.#", System.Globalization.CultureInfo.InvariantCulture) + " GB in memory" : ""),
                "CaptionTextBlockStyle", secondary: true));
            var cell = new StackPanel { Spacing = 2 };
            cell.Children.Add(row);
            foreach (string warning in model.Warnings.Take(2))
                cell.Children.Add(Ui.Text("• " + warning, "CaptionTextBlockStyle", secondary: true));
            AutomationProperties.SetName(cell, model.Name + ", " + model.Compatibility + ", fit "
                + Math.Round(model.FitScore) + " of 100");
            AdvisorModelsList.Children.Add(cell);
        }
        if (advisor.Models.Count == 0)
            AdvisorModelsList.Children.Add(Ui.Text("No generation models are registered.", secondary: true));
    }

    private void AdvisorShowModelButton_Click(object sender, RoutedEventArgs args)
    {
        if (CurrentAdvisorRecommendation is not { } rec) return;
        NavigateTo("models");
        ListViewItem? item = ModelsList.Items.OfType<ListViewItem>()
            .FirstOrDefault(candidate => (candidate.Tag as ModelInfo)?.Id == rec.ModelId);
        if (item is null) return;
        ModelsList.SelectedItem = item;
        ModelsList.ScrollIntoView(item);
    }

    private async void AdvisorPrepareButton_Click(object sender, RoutedEventArgs args)
    {
        if (CurrentAdvisorRecommendation is not { } rec
            || _shownModels.FirstOrDefault(m => m.Id == rec.ModelId) is not { } model) return;
        ModelAction action = model.CanLoad ? ModelAction.Load : ModelAction.Download;
        await ManageModelAsync(action, model);
        if (!_closing.IsCancellationRequested) UpdateAdvisorActions(CurrentAdvisorRecommendation);
    }
}
