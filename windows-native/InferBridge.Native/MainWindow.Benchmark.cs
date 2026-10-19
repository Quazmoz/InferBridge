using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private bool _benchmarkHistoryBusy;
    private bool _benchmarkHistoryLoaded;
    private bool _benchmarkPickerUpdating;
    private IReadOnlyList<BenchmarkRun> _benchmarkRuns = Array.Empty<BenchmarkRun>();

    private async void BenchmarkHistoryRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshBenchmarkHistoryAsync();

    private void BenchmarkOpenQuickButton_Click(object sender, RoutedEventArgs args)
    {
        MainNavigation.SelectedItem = MainNavigation.MenuItems
            .OfType<NavigationViewItem>().First(item => (string?)item.Tag == "operations");
    }

    private async Task RefreshBenchmarkHistoryAsync()
    {
        if (_benchmarkHistoryBusy || _quitting || _closing.IsCancellationRequested) return;
        _benchmarkHistoryBusy = true;
        BenchmarkHistoryProgress.IsActive = true;
        BenchmarkHistoryRefreshButton.IsEnabled = false;
        string? selectedId = (BenchmarkPrimaryPicker.SelectedItem as ComboBoxItem)?.Tag is BenchmarkRun current
            ? current.Id : null;
        string? comparisonId = (BenchmarkComparePicker.SelectedItem as ComboBoxItem)?.Tag is BenchmarkRun other
            ? other.Id : null;
        try
        {
            var history = await _backend.GetBenchmarkHistoryAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            _benchmarkRuns = history;
            _benchmarkHistoryLoaded = true;
            _benchmarkPickerUpdating = true;
            try
            {
                BenchmarkPrimaryPicker.Items.Clear();
                BenchmarkComparePicker.Items.Clear();
                foreach (BenchmarkRun run in history)
                {
                    BenchmarkPrimaryPicker.Items.Add(new ComboBoxItem { Content = run.Label, Tag = run });
                    BenchmarkComparePicker.Items.Add(new ComboBoxItem { Content = run.Label, Tag = run });
                }
                BenchmarkPrimaryPicker.SelectedItem = BenchmarkPrimaryPicker.Items
                    .OfType<ComboBoxItem>()
                    .FirstOrDefault(item => (item.Tag as BenchmarkRun)?.Id == selectedId)
                    ?? BenchmarkPrimaryPicker.Items.FirstOrDefault();
                BenchmarkComparePicker.SelectedItem = BenchmarkComparePicker.Items
                    .OfType<ComboBoxItem>()
                    .FirstOrDefault(item => (item.Tag as BenchmarkRun)?.Id == comparisonId);
            }
            finally
            {
                _benchmarkPickerUpdating = false;
            }
            BenchmarkHistoryInfo.Severity = InfoBarSeverity.Informational;
            BenchmarkHistoryInfo.Message = history.Count == 0
                ? "No saved manual runs. Start with Quick benchmark in Operations or run the full matrix in the optional web interface."
                : history.Count + " saved manual runs loaded from the engine. Select a run to inspect its measurements.";
            RenderBenchmarkHistory();
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            BenchmarkHistoryInfo.Severity = InfoBarSeverity.Warning;
            BenchmarkHistoryInfo.Message = "Could not refresh benchmark history: " + SafeError(error)
                + (_benchmarkHistoryLoaded ? " Earlier read-only results remain visible." : "");
        }
        finally
        {
            _benchmarkHistoryBusy = false;
            if (!_closing.IsCancellationRequested)
            {
                BenchmarkHistoryProgress.IsActive = false;
                BenchmarkHistoryRefreshButton.IsEnabled = !_quitting;
            }
        }
    }

    private void BenchmarkPicker_SelectionChanged(
        object sender, SelectionChangedEventArgs args)
    {
        if (!_benchmarkPickerUpdating) RenderBenchmarkHistory();
    }

    private void RenderBenchmarkHistory()
    {
        BenchmarkDetailsRows.Children.Clear();
        if (BenchmarkPrimaryPicker.SelectedItem is not ComboBoxItem { Tag: BenchmarkRun run })
        {
            BenchmarkDetailsRows.Children.Add(new TextBlock
            {
                Text = _benchmarkHistoryLoaded ? "No manual benchmark runs were found."
                    : "Refresh to load benchmark history.",
                TextWrapping = TextWrapping.Wrap,
            });
            BenchmarkComparisonSummary.Text = "Choose two compatible runs to compare measurements.";
            return;
        }

        BenchmarkDetailsRows.Children.Add(new TextBlock
        {
            Text = "Run: " + run.Timestamp
                + "\nPreset: " + run.Preset
                + " · Methodology v" + run.MethodologyVersion
                + "\nOutput target: " + run.OutputTokens + " tokens"
                + " · Measured runs: " + run.MeasuredRuns
                + " · Warm-ups: " + run.Warmups
                + "\nEvidence: " + (run.Synthetic ? "SYNTHETIC mock results — not hardware evidence"
                    : "Local engine measurements; actual device shown per result"),
            TextWrapping = TextWrapping.Wrap,
            IsTextSelectionEnabled = true,
        });

        foreach (var row in run.Results)
        {
            string speed = row.DecodeTokensPerSecond is not null
                ? "Decode throughput: " + BenchmarkHistory.FormatMetric(row.DecodeTokensPerSecond, " tok/s")
                : "Legacy overall throughput (includes TTFT): "
                    + BenchmarkHistory.FormatMetric(row.LegacyTokensPerSecond, " tok/s");
            var details = new TextBlock
            {
                Text = row.ModelId
                    + (row.Precision.Length > 0 ? " · " + row.Precision : "")
                    + "\nRequested " + row.RequestedDevice
                    + " → actual " + (row.ActualDevice.Length > 0 ? row.ActualDevice : "Not reported")
                    + "\n" + (row.Success
                        ? speed
                            + "\nFirst token: " + BenchmarkHistory.FormatMetric(row.FirstTokenMilliseconds, " ms")
                            + " · Total latency: " + BenchmarkHistory.FormatMetric(row.TotalMilliseconds, " ms")
                            + "\nLoad: " + BenchmarkHistory.FormatMetric(row.LoadMilliseconds, " ms")
                            + " · Peak process RSS: " + BenchmarkHistory.FormatMetric(row.ProcessRamMegabytes, " MB")
                            + "\nPrompt tokens: " + (row.PromptTokens?.ToString() ?? "Not reported")
                            + " · Completion tokens: " + (row.CompletionTokens?.ToString() ?? "Not reported")
                        : "Benchmark failed; no valid performance measurement was reported.")
                    + (row.Synthetic ? "\nSynthetic (mock)" : ""),
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            };
            BenchmarkDetailsRows.Children.Add(details);
            BenchmarkDetailsRows.Children.Add(new Separator());
        }
        if (run.Results.Count == 0)
            BenchmarkDetailsRows.Children.Add(new TextBlock
            {
                Text = "This saved run contains no displayable result rows.",
                TextWrapping = TextWrapping.Wrap,
            });

        if (BenchmarkComparePicker.SelectedItem is not ComboBoxItem { Tag: BenchmarkRun earlier })
        {
            BenchmarkComparisonSummary.Text = "Select another saved run to compare.";
            return;
        }
        if (!run.IsComparableTo(earlier))
        {
            BenchmarkComparisonSummary.Text =
                "Incompatible runs. Hardware, real/mock mode, methodology, output tokens, measured-run count, and warm-up count must match. No performance delta is calculated.";
            return;
        }
        var lines = new List<string>();
        foreach (var result in run.Results.Where(row => row.Success))
        {
            var previous = earlier.ComparableTo(result);
            if (previous is null) continue;
            bool decode = result.DecodeTokensPerSecond is not null
                && previous.DecodeTokensPerSecond is not null;
            double? now = decode ? result.DecodeTokensPerSecond : result.LegacyTokensPerSecond;
            double? before = decode ? previous.DecodeTokensPerSecond : previous.LegacyTokensPerSecond;
            // Never compare decode-only throughput with the older inclusive-TTFT metric.
            if (!decode && (result.DecodeTokensPerSecond is not null
                || previous.DecodeTokensPerSecond is not null)) now = before = null;
            lines.Add(result.ModelId + " · " + result.Precision + " · "
                + result.RequestedDevice + " → " + result.ActualDevice
                + "\n" + (decode ? "Decode throughput" : "Legacy overall throughput")
                + ": " + BenchmarkHistory.Delta(now, before, false)
                + "\nFirst-token latency: " + BenchmarkHistory.Delta(
                    result.FirstTokenMilliseconds, previous.FirstTokenMilliseconds, true)
                + "\nTotal latency: " + BenchmarkHistory.Delta(
                    result.TotalMilliseconds, previous.TotalMilliseconds, true));
        }
        BenchmarkComparisonSummary.Text = lines.Count > 0
            ? string.Join("\n\n", lines)
            : "Methodology matches, but no successful result has identical model, source, precision, requested device, and reported actual device. No delta is calculated.";
    }
}
