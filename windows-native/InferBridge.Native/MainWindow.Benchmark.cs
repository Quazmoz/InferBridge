using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    private readonly BenchmarkAttemptGate _benchmarkGate = new();
    private readonly DispatcherTimer _benchmarkActivityTimer = new()
    {
        Interval = TimeSpan.FromMilliseconds(1500),
    };
    private readonly HashSet<string> _benchmarkSelectedModels = new(StringComparer.Ordinal);
    private readonly HashSet<string> _benchmarkSelectedDevices = new(StringComparer.Ordinal);
    private BenchmarkCatalog? _benchmarkCatalog;
    private bool _benchmarkRefreshing;
    private bool _benchmarkUpdating;
    private bool _benchmarkActivityBusy;
    private bool _benchmarkAwaitingResponse;
    private string? _activeBenchmarkClientRunId;
    private string? _uncertainBenchmarkClientRunId;
    private int _benchmarkConfirmedCount;
    private BenchmarkPlan? _activeBenchmarkPlan;
    private string? _activeBenchmarkSession;

    private async void BenchmarkConfigurationRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshBenchmarkConfigurationAsync();

    private async Task RefreshBenchmarkConfigurationAsync()
    {
        if (_benchmarkRefreshing || _benchmarkGate.Active || _closing.IsCancellationRequested || _quitting)
            return;
        _benchmarkRefreshing = true;
        BenchmarkConfigurationRefreshButton.IsEnabled = false;
        try
        {
            BenchmarkCatalog latest = await _backend.GetBenchmarkCatalogAsync(
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested) return;
            bool sameSession = latest.SessionId == _benchmarkCatalog?.SessionId;
            if (!sameSession)
            {
                _benchmarkSelectedModels.Clear();
                _benchmarkSelectedDevices.Clear();
            }
            _benchmarkCatalog = latest;
            _benchmarkSelectedModels.IntersectWith(latest.Models.Where(m => m.Prepared).Select(m => m.Id));
            _benchmarkSelectedDevices.IntersectWith(latest.Devices);
            if (!sameSession)
            {
                string? first = latest.Models.FirstOrDefault(m => m.Loaded && m.Prepared)?.Id
                    ?? latest.Models.FirstOrDefault(m => m.Prepared)?.Id;
                if (first is not null) _benchmarkSelectedModels.Add(first);
                string? device = latest.Devices.Contains("CPU") ? "CPU"
                    : latest.Devices.FirstOrDefault(d => d != "AUTO");
                if (device is not null) _benchmarkSelectedDevices.Add(device);
            }

            _benchmarkUpdating = true;
            try
            {
                BenchmarkModelChoices.Children.Clear();
                foreach (var model in latest.Models.OrderByDescending(m => m.Prepared)
                    .ThenByDescending(m => m.Loaded).ThenBy(m => m.Name, StringComparer.OrdinalIgnoreCase))
                {
                    string state = model.Loaded ? "Loaded" :
                        model.Prepared ? "Prepared" : "Needs preparation";
                    var choice = new CheckBox
                    {
                        Content = new TextBlock
                        {
                            Text = model.Name + "  ·  " + state + "  ·  " + model.Detail,
                            TextWrapping = TextWrapping.Wrap,
                        },
                        Tag = model.Id,
                        IsEnabled = model.Prepared,
                        IsChecked = _benchmarkSelectedModels.Contains(model.Id),
                    };
                    AutomationProperties.SetName(choice, model.Name + ", " + state
                        + ", " + model.Detail);
                    choice.Checked += BenchmarkModelChoice_Changed;
                    choice.Unchecked += BenchmarkModelChoice_Changed;
                    BenchmarkModelChoices.Children.Add(choice);
                }
                BenchmarkDeviceChoices.Children.Clear();
                foreach (string device in latest.Devices)
                {
                    var choice = new CheckBox
                    {
                        Content = device == "AUTO" ? "AUTO · routing target; actual device reported in results" : device,
                        Tag = device,
                        IsChecked = _benchmarkSelectedDevices.Contains(device),
                    };
                    AutomationProperties.SetName(choice, "Benchmark requested device " + device);
                    choice.Checked += BenchmarkDeviceChoice_Changed;
                    choice.Unchecked += BenchmarkDeviceChoice_Changed;
                    BenchmarkDeviceChoices.Children.Add(choice);
                }
            }
            finally { _benchmarkUpdating = false; }

            BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Informational;
            BenchmarkConfigurationInfo.Message = latest.Synthetic
                ? "Synthetic mock engine. Results are not hardware evidence."
                : latest.Models.Any(m => m.Prepared)
                    ? "Choose prepared generation models and detected devices. Actual execution targets appear in saved results."
                    : "No prepared generation model is available. Prepare one on the Models page.";
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            _benchmarkCatalog = null;
            _benchmarkSelectedModels.Clear();
            _benchmarkSelectedDevices.Clear();
            BenchmarkModelChoices.Children.Clear();
            BenchmarkDeviceChoices.Children.Clear();
            BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Warning;
            BenchmarkConfigurationInfo.Message = "Benchmark choices unavailable: " + SafeError(error);
        }
        finally
        {
            _benchmarkRefreshing = false;
            if (!_closing.IsCancellationRequested)
                BenchmarkConfigurationRefreshButton.IsEnabled = !_benchmarkGate.Active && !_quitting;
            UpdateBenchmarkWorkload();
        }
    }

    private void BenchmarkModelChoice_Changed(object sender, RoutedEventArgs args)
    {
        if (_benchmarkUpdating || sender is not CheckBox) return;
        var choice = (CheckBox)sender;
        string? id = choice.Tag as string;
        if (id is null) return;
        if (choice.IsChecked == true) _benchmarkSelectedModels.Add(id);
        else _benchmarkSelectedModels.Remove(id);
        UpdateBenchmarkWorkload();
    }

    private void BenchmarkDeviceChoice_Changed(object sender, RoutedEventArgs args)
    {
        if (_benchmarkUpdating || sender is not CheckBox) return;
        var choice = (CheckBox)sender;
        string? id = choice.Tag as string;
        if (id is null) return;
        if (choice.IsChecked == true) _benchmarkSelectedDevices.Add(id);
        else _benchmarkSelectedDevices.Remove(id);
        UpdateBenchmarkWorkload();
    }

    private void BenchmarkPresetPicker_SelectionChanged(object sender, SelectionChangedEventArgs args) =>
        UpdateBenchmarkWorkload();

    private BenchmarkPlan? CurrentBenchmarkPlan()
    {
        if (_benchmarkCatalog is null) return null;
        string preset = (BenchmarkPresetPicker.SelectedItem as ComboBoxItem)?.Tag as string
            ?? "standard";
        return BenchmarkLab.CreatePlan(_benchmarkCatalog,
            _benchmarkSelectedModels, _benchmarkSelectedDevices, preset);
    }

    private void UpdateBenchmarkWorkload()
    {
        BenchmarkRunButton.IsEnabled = false;
        bool idle = !_benchmarkGate.Active && !_benchmarkGate.OutcomeUnknown && !_operationsBusy;
        BenchmarkPresetPicker.IsEnabled = idle;
        BenchmarkConfigurationRefreshButton.IsEnabled = idle && !_benchmarkRefreshing && !_quitting;
        foreach (var choice in BenchmarkModelChoices.Children.OfType<CheckBox>())
            choice.IsEnabled = idle && _benchmarkCatalog?.Models.Any(m =>
                m.Id == (string?)choice.Tag && m.Prepared) == true;
        foreach (var choice in BenchmarkDeviceChoices.Children.OfType<CheckBox>())
            choice.IsEnabled = idle;
        if (_benchmarkGate.OutcomeUnknown)
        {
            BenchmarkWorkload.Text = "The previous request's outcome is uncertain. Check engine history before restarting InferBridge; no automatic retry is permitted.";
            return;
        }
        if (_benchmarkGate.Active)
        {
            BenchmarkRunButton.IsEnabled = false;
            return;
        }
        if (_benchmarkRefreshing || _benchmarkCatalog is null)
        {
            BenchmarkWorkload.Text = "Refresh to discover prepared models and available devices.";
            return;
        }
        try
        {
            BenchmarkPlan plan = CurrentBenchmarkPlan()!;
            BenchmarkWorkload.Text = $"{plan.Models.Count} models × {plan.Devices.Count} devices = "
                + $"{plan.Combinations} combinations · {plan.Warmups} warm-ups and "
                + $"{plan.Runs} measured runs per combination · {plan.MaxTokens} output tokens "
                + $"· {plan.TotalSamples} total inference samples.";
            BenchmarkRunButton.IsEnabled = idle && !_quitting && !_closing.IsCancellationRequested;
        }
        catch (ArgumentException error)
        {
            BenchmarkWorkload.Text = error.Message;
        }
    }

    private async void BenchmarkRunButton_Click(object sender, RoutedEventArgs args)
    {
        if (_benchmarkGate.Active || _benchmarkGate.OutcomeUnknown || _operationsBusy
            || _benchmarkRefreshing || _benchmarkCatalog is null
            || _quitting || _closing.IsCancellationRequested) return;
        BenchmarkPlan plan;
        try { plan = CurrentBenchmarkPlan()!; }
        catch (ArgumentException error)
        {
            BenchmarkWorkload.Text = error.Message;
            return;
        }
        string session = _benchmarkCatalog.SessionId;
        var dialog = new ContentDialog
        {
            Title = "Run local benchmark matrix?",
            Content = $"{plan.Combinations} model/device combinations, {plan.TotalSamples} inference samples. "
                + "This can heavily use CPU, GPU, NPU, memory and power. "
                + "The engine does not offer benchmark cancellation. Closing the window only stops monitoring.",
            PrimaryButtonText = "Start benchmark",
            CloseButtonText = "Back",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        // Take the duplicate-attempt lease before showing the modal prompt.
        if (!_benchmarkGate.TryBegin()) return;
        QuickBenchmarkButton.IsEnabled = false;
        UpdateBenchmarkWorkload();
        bool confirmed = true; // No POST has been sent yet.
        string? clientRunId = null;
        try
        {
            if (await dialog.ShowAsync() != ContentDialogResult.Primary
                || _closing.IsCancellationRequested) return;
            clientRunId = Guid.NewGuid().ToString("N");
            _activeBenchmarkPlan = plan;
            _activeBenchmarkSession = session;
            _activeBenchmarkClientRunId = clientRunId;
            _benchmarkConfirmedCount = 0;
            BenchmarkActivityProgress.IsIndeterminate = true;
            BenchmarkActivityProgress.Value = 0;
            BenchmarkActivityLabel.Text = "Starting benchmark; waiting for the engine's activity events…";
            BenchmarkActivityRing.IsActive = true;
            BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Informational;
            BenchmarkConfigurationInfo.Message =
                "Benchmark request active. Cancellation is not supported by the engine.";
            _benchmarkActivityTimer.Tick -= BenchmarkActivityTimer_Tick;
            _benchmarkActivityTimer.Tick += BenchmarkActivityTimer_Tick;
            _benchmarkAwaitingResponse = true;
            _benchmarkActivityTimer.Start();
            BenchmarkCompletion completed = await _backend.RunBenchmarkLabAsync(
                plan, session, clientRunId, ApiKeyBox.Password, _closing.Token,
                onDispatchStarted: () => confirmed = false);
            _benchmarkAwaitingResponse = false;
            confirmed = true;
            if (_closing.IsCancellationRequested) return;
            BenchmarkActivityProgress.IsIndeterminate = false;
            BenchmarkActivityProgress.Value = 100;
            BenchmarkActivityLabel.Text = $"{plan.Combinations} / {plan.Combinations} combinations completed.";
            BenchmarkConfigurationInfo.Severity = completed.Failures == 0
                ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            BenchmarkConfigurationInfo.Message = completed.Synthetic
                ? $"Synthetic benchmark saved: {completed.Successes} successful, {completed.Failures} failed. Not hardware evidence."
                : $"Measured benchmark saved: {completed.Successes} successful, {completed.Failures} failed. Inspect actual devices in history.";
            await RefreshBenchmarkHistoryAsync();
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested) { }
        catch (Exception error) when (error is ArgumentException
            or InvalidOperationException or UnauthorizedAccessException)
        {
            // Validation and explicit 4xx refusals prove that no suite was accepted.
            confirmed = true;
            if (!_closing.IsCancellationRequested)
            {
                BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Warning;
                BenchmarkConfigurationInfo.Message = "Benchmark request rejected: " + SafeError(error);
                BenchmarkActivityLabel.Text = "No benchmark accepted. Refresh choices and retry.";
            }
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            // An error after dispatch does not establish that the server stopped work.
            BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Warning;
            BenchmarkConfigurationInfo.Message = confirmed
                ? "Benchmark was not started: " + SafeError(error)
                : "Unable to confirm benchmark outcome: " + SafeError(error)
                    + ". It may still be running. Refresh history before making another request.";
            BenchmarkActivityLabel.Text = confirmed ? "No benchmark started."
                : "Outcome unknown. No retry will be sent by this window.";
        }
        finally
        {
            _benchmarkActivityTimer.Stop();
            _benchmarkAwaitingResponse = false;
            _benchmarkGate.Finish(confirmed);
            _uncertainBenchmarkClientRunId = _benchmarkGate.OutcomeUnknown ? clientRunId : null;
            _activeBenchmarkPlan = null;
            _activeBenchmarkSession = null;
            _activeBenchmarkClientRunId = null;
            if (!_closing.IsCancellationRequested)
            {
                BenchmarkActivityRing.IsActive = false;
                BenchmarkConfigurationRefreshButton.IsEnabled = !_quitting;
                QuickBenchmarkButton.IsEnabled = _operationsLoaded && !_operationsBusy
                    && !_benchmarkGate.Active && !_benchmarkGate.OutcomeUnknown
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkModelId)
                    && !string.IsNullOrWhiteSpace(_quickBenchmarkDevice);
                UpdateBenchmarkWorkload();
            }
        }
    }

    private async void BenchmarkActivityTimer_Tick(object? sender, object args) =>
        await PollBenchmarkActivityAsync();

    private async Task PollBenchmarkActivityAsync()
    {
        if (_benchmarkActivityBusy || !_benchmarkGate.Active || !_benchmarkAwaitingResponse
            || _benchmarkCatalog is null || _closing.IsCancellationRequested) return;
        _benchmarkActivityBusy = true;
        try
        {
            BenchmarkPlan? plan = _activeBenchmarkPlan;
            if (plan is null || _activeBenchmarkSession is null
                || _activeBenchmarkClientRunId is null) return;
            string clientRunId = _activeBenchmarkClientRunId;
            BenchmarkActivity? activity = await _backend.GetBenchmarkActivityAsync(
                _activeBenchmarkSession, clientRunId, plan.Combinations,
                ApiKeyBox.Password, _closing.Token);
            if (_closing.IsCancellationRequested || !_benchmarkGate.Active || !_benchmarkAwaitingResponse
                || _activeBenchmarkClientRunId != clientRunId
                || activity is null || activity.Completed < _benchmarkConfirmedCount) return;
            _benchmarkConfirmedCount = activity.Completed;
            BenchmarkActivityProgress.IsIndeterminate = false;
            BenchmarkActivityProgress.Value = 100.0 * activity.Completed / activity.Total;
            BenchmarkActivityLabel.Text = $"{activity.Completed} / {activity.Total} combinations · "
                + activity.Message;
        }
        catch (UnauthorizedAccessException)
        {
            _benchmarkActivityTimer.Stop();
            BenchmarkActivityProgress.IsIndeterminate = true;
            BenchmarkActivityLabel.Text = "Progress unavailable: API key rejected. The benchmark request may continue.";
        }
        catch (InvalidOperationException)
        {
            _benchmarkActivityTimer.Stop();
            BenchmarkActivityProgress.IsIndeterminate = true;
            BenchmarkActivityProgress.Value = 0;
            BenchmarkActivityLabel.Text = "Engine session changed. Progress is no longer attributable to this run.";
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            BenchmarkActivityLabel.Text = "Progress temporarily unavailable; awaiting the benchmark response.";
            BenchmarkActivityProgress.IsIndeterminate = true;
        }
        finally { _benchmarkActivityBusy = false; }
    }

    private bool _benchmarkHistoryBusy;
    private bool _benchmarkHistoryLoaded;
    private bool _benchmarkPickerUpdating;

    private async void BenchmarkHistoryRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshBenchmarkHistoryAsync();

    private void BenchmarkOpenQuickButton_Click(object sender, RoutedEventArgs args)
    {
        NavigateTo("operations");
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
                ? "No saved manual runs. Configure and run a native benchmark above."
                : history.Count + " saved manual runs loaded from the engine. Select a run to inspect its measurements.";
            RenderBenchmarkHistory();
            if (_benchmarkGate.OutcomeUnknown
                && _uncertainBenchmarkClientRunId is { } uncertain
                && history.Any(run => run.ClientRunId == uncertain)
                && _benchmarkGate.ConfirmRecovered())
            {
                _uncertainBenchmarkClientRunId = null;
                BenchmarkActivityLabel.Text = "Previously uncertain benchmark confirmed in saved history.";
                BenchmarkConfigurationInfo.Severity = InfoBarSeverity.Success;
                BenchmarkConfigurationInfo.Message =
                    "Engine history confirms the previous benchmark was saved. No request was retried.";
                UpdateBenchmarkWorkload();
            }
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
                            + "\nContext: " + (row.ContextTokens?.ToString() ?? "Legacy / unknown")
                            + " · Reserved output: " + (row.ReservedOutputTokens?.ToString() ?? "Unknown")
                        : "Benchmark failed; no valid performance measurement was reported.")
                    + (row.Synthetic ? "\nSynthetic (mock)" : ""),
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            };
            BenchmarkDetailsRows.Children.Add(details);
            // StackPanel spacing separates result rows without a web-style divider.
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
                "Incompatible runs. Hardware, real/mock mode, methodology, output tokens, measured-run count, and warm-up count must match. Context and model identity are checked for each row. No performance delta is calculated.";
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
