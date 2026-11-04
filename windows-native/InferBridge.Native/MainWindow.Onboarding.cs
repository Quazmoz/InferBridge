using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

// First-run setup: the browser wizard's seven steps (app/onboarding_ui.py) in one
// dialog over the same /v1/onboarding endpoints. It opens by itself once, while the
// engine reports setup as not completed; "Setup and onboarding" on Engine & devices
// reopens it. Preparation, benchmarking and completion stay engine-owned.
public sealed partial class MainWindow
{
    private sealed record WizardAction(string Label, Func<Task> Run, bool Primary = false, bool Enabled = true);

    private ContentDialog? _onboardingDialog;
    private TextBlock? _onboardingStepText;
    private StackPanel? _onboardingBody;
    private Button[] _onboardingButtons = [];
    private int _onboardingStep;
    private int _onboardingView; // bumped per view and on close, so stale async results are dropped
    private bool _onboardingChecked;
    private OnboardingStatus? _onboardingStatus;
    private OnboardingRecommendation? _onboardingRecommendation;
    private OnboardingJob? _onboardingJob;

    private async Task CheckOnboardingAsync()
    {
        try
        {
            _onboardingStatus = await _backend.GetOnboardingStatusAsync(ApiKeyBox.Password, _closing.Token);
        }
        catch (Exception)
        {
            return; // The button stays disabled; the Best model card still works.
        }
        if (_closing.IsCancellationRequested) return;
        UpdateOnboardingButton();
        // Like the browser's one-time auto-open key: only when the flag could be saved.
        if (_onboardingStatus.NeedsSetup && !_preferences.OnboardingAutoOpened
            && TrySavePreferences(_preferences with { OnboardingAutoOpened = true }, out _))
            await ShowOnboardingAsync();
    }

    private void UpdateOnboardingButton()
    {
        OnboardingButton.Content = _onboardingStatus?.OpenerLabel ?? "Setup and onboarding";
        OnboardingButton.IsEnabled = _onboardingStatus is not null;
    }

    private async void OnboardingButton_Click(object sender, RoutedEventArgs args) => await ShowOnboardingAsync();

    private async Task ShowOnboardingAsync()
    {
        if (_onboardingDialog is not null || !_engineConnected || _rejectedApiKey is not null
            || _quitting || _closing.IsCancellationRequested) return;
        _onboardingStepText = Ui.Text("", "CaptionTextBlockStyle", secondary: true);
        AutomationProperties.SetLiveSetting(_onboardingStepText, AutomationLiveSetting.Polite);
        _onboardingBody = new StackPanel { Spacing = 12 };
        var layout = new StackPanel { Spacing = 8 };
        layout.Children.Add(_onboardingStepText);
        layout.Children.Add(_onboardingBody);
        var dialog = _onboardingDialog = new ContentDialog
        {
            XamlRoot = ((FrameworkElement)Content).XamlRoot,
            Title = "Setup and onboarding",
            Content = new ScrollViewer
            {
                Content = layout,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            },
            CloseButtonText = "Close",
            DefaultButton = ContentDialogButton.Close,
        };
        dialog.Resources["ContentDialogMaxWidth"] = 720d;
        // Resume where setup is: a running preparation, the Ready page, or Welcome.
        if (_onboardingJob is { Status: "running" }) _ = OnboardingProgressAsync();
        else if (_onboardingStatus is { NeedsSetup: false })
            _ = OnboardingLoadAsync(6, "Ready", "Reading the connection details…",
                token => _backend.GetOnboardingConnectionAsync(ApiKeyBox.Password, token),
                connection => OnboardingReady(connection, []), OnboardingWelcome);
        else _ = OnboardingWelcome();
        try
        {
            await dialog.ShowAsync();
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            // Another dialog is open; the Setup and onboarding button stays available.
        }
        finally
        {
            _onboardingView++;
            _onboardingDialog = null;
            _onboardingBody = null;
            _onboardingStepText = null;
            _onboardingButtons = [];
        }
    }

    private int OnboardingPanel(int step, string heading, IEnumerable<UIElement> content, params WizardAction[] actions)
    {
        if (_onboardingBody is null || _onboardingStepText is null) return -1;
        _onboardingStep = step;
        _onboardingStepText.Text = "Step " + (step + 1) + " of " + Onboarding.Steps.Length + ": " + Onboarding.Steps[step];
        (FrameworkElementAutomationPeer.FromElement(_onboardingStepText)
            ?? FrameworkElementAutomationPeer.CreatePeerForElement(_onboardingStepText))
            ?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
        _onboardingBody.Children.Clear();
        _onboardingBody.Children.Add(Ui.Text(heading, "SubtitleTextBlockStyle"));
        foreach (UIElement element in content) _onboardingBody.Children.Add(element);
        var row = new WrapRow { Spacing = 8 };
        var buttons = new List<Button>();
        foreach (WizardAction action in actions)
        {
            var button = new Button { Content = action.Label, IsEnabled = action.Enabled };
            if (action.Primary && Ui.ResStyle("AccentButtonStyle") is Style accent) button.Style = accent;
            button.Click += async (_, _) =>
            {
                // Until the action renders its next view, nothing else in this row runs.
                foreach (Button other in buttons) other.IsEnabled = false;
                await action.Run();
            };
            buttons.Add(button);
            row.Children.Add(button);
        }
        _onboardingBody.Children.Add(row);
        _onboardingButtons = buttons.ToArray();
        return ++_onboardingView;
    }

    private async Task OnboardingLoadAsync<T>(int step, string heading, string loading,
        Func<CancellationToken, Task<T>> load, Action<T> show, Func<Task> retry)
    {
        int view = OnboardingPanel(step, heading, [Ui.Text(loading, secondary: true)]);
        try
        {
            T value = await load(_closing.Token);
            if (view == _onboardingView) show(value);
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (view == _onboardingView) OnboardingFail(error, retry);
        }
    }

    private void OnboardingFail(Exception error, Func<Task> retry) =>
        OnboardingPanel(_onboardingStep, "Setup could not continue", [Notice(SafeError(error), InfoBarSeverity.Error)],
            new WizardAction("Retry", retry, Primary: true));

    private static InfoBar Notice(string message, InfoBarSeverity severity) =>
        new() { IsOpen = true, IsClosable = false, Severity = severity, Message = message };

    private static StackPanel Facts(IEnumerable<OnboardingFact> facts)
    {
        var list = new StackPanel { Spacing = 4 };
        foreach (OnboardingFact fact in facts)
        {
            var row = new WrapRow { Spacing = 8 };
            row.Children.Add(Ui.Text(fact.Label, "BodyStrongTextBlockStyle"));
            if (fact.Value.Length > 0) row.Children.Add(Ui.Text(fact.Value));
            if (fact.Status.Length > 0)
                row.Children.Add(Ui.Pill(fact.Status, fact.Status switch
                {
                    "ready" or "complete" => "success", "warning" => "warning",
                    "unavailable" or "failed" => "error", "active" => "accent", _ => "neutral",
                }));
            list.Children.Add(row);
            if (fact.Detail.Length > 0) list.Children.Add(Ui.Text(fact.Detail, "CaptionTextBlockStyle", secondary: true));
        }
        return list;
    }

    private void OpenOnboardingLink(string url)
    {
        try
        {
            using var browser = Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (Exception) when (!_closing.IsCancellationRequested) { }
    }

    private Task OnboardingWelcome()
    {
        var content = new List<UIElement>
        {
            Ui.Text("Prompts and chat content are processed on this machine. Models are downloaded separately and may "
                + "require several gigabytes of disk space. Compatible Intel CPU, GPU, or NPU devices can be used "
                + "through OpenVINO. Localhost is the default endpoint."),
            Ui.Text("Download → Install → Choose recommended model → Chat", "BodyStrongTextBlockStyle"),
            Ui.Text("The first setup can take time because each preparation stage is performed separately.", secondary: true),
        };
        if (_onboardingStatus is { RecoveryMessage.Length: > 0 } status)
            content.Add(Notice(status.RecoveryMessage, InfoBarSeverity.Error));
        OnboardingPanel(0, "Run local AI through OpenVINO", content,
            new WizardAction("Documentation", () => { OpenOnboardingLink(Onboarding.DocumentationUrl); return OnboardingWelcome(); }),
            new WizardAction("Continue", () => OnboardingScanAsync(false), Primary: true));
        return Task.CompletedTask;
    }

    private Task OnboardingScanAsync(bool refresh) => OnboardingLoadAsync(1, "System scan",
        "Scanning the existing hardware advisor and OpenVINO devices…",
        token => _backend.GetOnboardingScanAsync(refresh, ApiKeyBox.Password, token),
        scan => OnboardingPanel(1, "System scan",
            [.. scan.Warnings.Select(warning => Notice(warning, InfoBarSeverity.Warning)), Facts(scan.Items)],
            new WizardAction("Back", OnboardingWelcome),
            new WizardAction("Rescan", () => OnboardingScanAsync(true)),
            new WizardAction("Continue", () => OnboardingNpuAsync(false), Primary: true)),
        () => OnboardingScanAsync(true));

    private Task OnboardingNpuAsync(bool refresh) => OnboardingLoadAsync(2, "NPU readiness",
        "Checking system and OpenVINO evidence…",
        token => _backend.GetNpuReadinessAsync(refresh, ApiKeyBox.Password, token),
        npu =>
        {
            OnboardingPanel(2, "NPU readiness",
                [Ui.Text(npu.Title, "BodyStrongTextBlockStyle"), Ui.Text(npu.Explanation),
                    Facts([new("Fallback", npu.Fallback), new("Driver", npu.Driver)])],
                new WizardAction("Back", () => OnboardingScanAsync(false)),
                new WizardAction("Official Intel support", () =>
                {
                    OpenOnboardingLink(npu.SupportUrl);
                    return OnboardingNpuAsync(false);
                }, Enabled: npu.SupportUrl.Length > 0),
                new WizardAction("Rescan", () => OnboardingNpuAsync(true)),
                new WizardAction("Continue", () => OnboardingRecommendationAsync(false), Primary: true));
        },
        () => OnboardingNpuAsync(true));

    private Task OnboardingRecommendationAsync(bool refresh) => OnboardingLoadAsync(3, "Recommended model",
        "Selecting a conservative compatible model…",
        token => _backend.GetOnboardingRecommendationAsync(refresh, ApiKeyBox.Password, token),
        rec =>
        {
            _onboardingRecommendation = rec;
            OnboardingPanel(3, "Recommended model",
                [
                    Ui.Text(rec.ModelName, "BodyStrongTextBlockStyle"), Ui.Text(rec.Description), Ui.Text(rec.Reason, secondary: true),
                    Facts(rec.Stats),
                    .. rec.Warnings.Select(w => Notice(w.Message, w.Blocking ? InfoBarSeverity.Error : InfoBarSeverity.Warning)),
                    new Expander
                    {
                        Header = "Advanced details", Content = Facts(rec.Advanced),
                        HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                    },
                ],
                new WizardAction("Back", () => OnboardingNpuAsync(false)),
                new WizardAction("Rescan hardware", () => OnboardingRecommendationAsync(true)),
                new WizardAction("Choose another model", () =>
                {
                    _onboardingDialog?.Hide();
                    NavigateTo("models");
                    return Task.CompletedTask;
                }),
                new WizardAction("Change model storage location", () => OnboardingPrepare(null, focusStorage: true)),
                new WizardAction("Use recommended", () => OnboardingPrepare(null), Primary: true));
        },
        () => OnboardingRecommendationAsync(true));

    private Task OnboardingPrepare(string? device, bool focusStorage = false)
    {
        if (_onboardingRecommendation is not { } rec) return OnboardingRecommendationAsync(false);
        var devicePicker = new ComboBox { Header = "Device", MinWidth = 160 };
        foreach (string option in Onboarding.Devices.Append(rec.RequestedDevice).Distinct()) devicePicker.Items.Add(option);
        devicePicker.SelectedItem = device ?? rec.RequestedDevice;
        var storage = new TextBox
        {
            Header = "Model storage location, optional", PlaceholderText = "Use default writable storage", MaxLength = 1024,
        };
        var license = new CheckBox { Content = "I accept the model source license implications." };
        var disk = new CheckBox { Content = "I confirm the estimated disk requirement." };
        var warnings = new CheckBox { Content = "I acknowledge the preflight warnings." };
        var content = new List<UIElement>
        {
            Ui.Text("Confirm the model source, disk requirement, and warnings before downloading."),
            Facts([new("Model", rec.ModelName)]), devicePicker, storage, license, disk, warnings,
        };
        if (rec.TrustRemoteCode)
            content.Add(Notice("This model needs reviewed repository code, which the native app never enables. "
                + "Prepare it from the web UI or choose another model.", InfoBarSeverity.Error));
        OnboardingPanel(4, "Prepare model", content,
            new WizardAction("Back", () => OnboardingRecommendationAsync(false)),
            new WizardAction("Prepare model", async () =>
            {
                try
                {
                    _onboardingJob = await _backend.StartOnboardingPreparationAsync(rec,
                        devicePicker.SelectedItem as string ?? rec.RequestedDevice, storage.Text,
                        license.IsChecked == true, disk.IsChecked == true, warnings.IsChecked == true,
                        ApiKeyBox.Password, _closing.Token);
                }
                catch (Exception) when (_closing.IsCancellationRequested) { return; }
                catch (Exception error)
                {
                    OnboardingFail(error, () => OnboardingRecommendationAsync(false));
                    return;
                }
                await OnboardingProgressAsync();
            }, Primary: true, Enabled: !rec.TrustRemoteCode));
        (focusStorage ? storage : (Control)devicePicker).Focus(FocusState.Programmatic);
        return Task.CompletedTask;
    }

    private async Task OnboardingProgressAsync()
    {
        if (_onboardingJob is not { } job) return;
        // Built once per view and updated in place, so keyboard focus survives each poll.
        var message = Ui.Text("");
        var bar = new ProgressBar { Minimum = 0, Maximum = 100 };
        var timing = Ui.Text("", "CaptionTextBlockStyle", secondary: true);
        var stages = new WrapRow { Spacing = 6 };
        var error = Notice("", InfoBarSeverity.Error);
        var logs = new TextBox
        {
            IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MaxHeight = 220,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
        };
        var troubleshooting = new Expander
        {
            Header = "Troubleshooting details", Content = logs,
            HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(logs, "Sanitized preparation log");
        int view = OnboardingPanel(job.Stage == "benchmarking" ? 5 : 4, job.StageLabel,
            [message, bar, timing, stages, error, troubleshooting],
            new WizardAction("Cancel", async () =>
            {
                try { await _backend.CancelOnboardingJobAsync(job.JobId, ApiKeyBox.Password, _closing.Token); }
                catch (Exception failure) when (!_closing.IsCancellationRequested)
                {
                    error.Message = SafeError(failure);
                    error.IsOpen = true;
                }
            }),
            new WizardAction("Retry", () => OnboardingPrepare(null)),
            new WizardAction("Change device", () => OnboardingPrepare(null)),
            new WizardAction("Fall back to CPU", () => OnboardingPrepare("CPU")));
        if (view < 0) return;
        Button[] actions = _onboardingButtons;
        var heading = (TextBlock)_onboardingBody!.Children[0];
        while (view == _onboardingView)
        {
            int step = job.Stage == "benchmarking" ? 5 : 4;
            _onboardingStepText!.Text = "Step " + (step + 1) + " of " + Onboarding.Steps.Length + ": " + Onboarding.Steps[step];
            heading.Text = job.StageLabel;
            message.Text = job.Message;
            bar.IsIndeterminate = job.Status == "running" && !job.Determinate;
            bar.Value = job.Percent ?? 0;
            AutomationProperties.SetName(bar, job.StageLabel);
            timing.Text = (job.Determinate ? Math.Round(job.Percent ?? 0) + "%" : "Indeterminate")
                + " · " + job.ElapsedSeconds + "s";
            stages.Children.Clear();
            foreach (OnboardingFact stage in job.Stages)
                stages.Children.Add(Ui.Pill(stage.Label + " (" + stage.Status + ")", stage.Status switch
                {
                    "complete" => "success", "active" => "accent", "failed" => "error", _ => "neutral",
                }));
            if (job.ErrorDetail.Length > 0) error.Message = job.ErrorDetail;
            error.IsOpen = error.Message.Length > 0;
            logs.Text = job.LogTail.Count > 0 ? string.Join("\n", job.LogTail)
                : "No additional sanitized log lines are available.";
            troubleshooting.Visibility = job.LogTail.Count > 0 || job.ErrorDetail.Length > 0
                ? Visibility.Visible : Visibility.Collapsed;
            if (job.Status == "failed") troubleshooting.IsExpanded = true;
            actions[0].IsEnabled = job.CanCancel;
            actions[1].IsEnabled = actions[2].IsEnabled = job.CanRetry;
            actions[3].IsEnabled = job.CanFallbackToCpu;

            if (job.Status == "ready")
            {
                await OnboardingCompleteAsync(job);
                return;
            }
            if (job.Status != "running") return;
            try
            {
                await Task.Delay(1000, _closing.Token);
                if (view != _onboardingView) return; // closed or navigated; reopening resumes polling
                job = _onboardingJob = await _backend.GetOnboardingJobAsync(job.JobId, ApiKeyBox.Password, _closing.Token);
            }
            catch (Exception) when (_closing.IsCancellationRequested) { return; }
            catch (Exception failure)
            {
                if (view == _onboardingView) OnboardingFail(failure, OnboardingProgressAsync);
                return;
            }
        }
    }

    private Task OnboardingCompleteAsync(OnboardingJob job) => OnboardingLoadAsync(6, "Ready",
        "Confirming the verified model and connection details…",
        token => _backend.CompleteOnboardingAsync(job.JobId, ApiKeyBox.Password, token),
        connection =>
        {
            _onboardingJob = null;
            _onboardingStatus = new OnboardingStatus(true, false, false, "");
            UpdateOnboardingButton();
            OnboardingReady(connection, job.Benchmark);
            _ = RefreshAsync(background: true); // show the loaded model in Chat and the library
        },
        () => OnboardingCompleteAsync(job));

    private void OnboardingReady(OnboardingConnection connection, IReadOnlyList<OnboardingFact> benchmark)
    {
        var content = new List<UIElement>
        {
            Ui.Text(connection.ActiveModelId + " is ready on " + connection.ActualDevice, "BodyStrongTextBlockStyle"),
            Ui.Text("The endpoint below uses the actual selected port.", secondary: true),
        };
        if (benchmark.Count > 0)
        {
            content.Add(Ui.Text("Benchmark verification", "BodyStrongTextBlockStyle"));
            content.Add(Facts(benchmark));
        }
        content.Add(Facts(connection.Endpoints));
        foreach (OnboardingFact snippet in connection.Snippets)
        {
            var copy = new Button { Content = "Copy" };
            AutomationProperties.SetName(copy, "Copy " + snippet.Label);
            copy.Click += (_, _) =>
            {
                try
                {
                    var data = new DataPackage();
                    data.SetText(snippet.Value);
                    Clipboard.SetContent(data);
                    copy.Content = "Copied";
                }
                catch (Exception) { copy.Content = "Clipboard unavailable"; }
            };
            var box = new TextBox
            {
                Text = snippet.Value, IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap,
                FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
            };
            AutomationProperties.SetName(box, snippet.Label);
            var header = new WrapRow { Spacing = 8 };
            header.Children.Add(Ui.Text(snippet.Label, "BodyStrongTextBlockStyle"));
            header.Children.Add(copy);
            content.Add(header);
            content.Add(box);
        }
        OnboardingPanel(6, "Ready", content,
            new WizardAction("Restart onboarding", async () =>
            {
                try { await _backend.RestartOnboardingAsync(ApiKeyBox.Password, _closing.Token); }
                catch (Exception) when (_closing.IsCancellationRequested) { return; }
                catch (Exception error)
                {
                    OnboardingFail(error, () => { OnboardingReady(connection, benchmark); return Task.CompletedTask; });
                    return;
                }
                _onboardingStatus = new OnboardingStatus(false, false, false, "");
                UpdateOnboardingButton();
                await OnboardingWelcome();
            }),
            new WizardAction("Open chat", () =>
            {
                _onboardingDialog?.Hide();
                NavigateTo("chat");
                return Task.CompletedTask;
            }, Primary: true));
    }
}
