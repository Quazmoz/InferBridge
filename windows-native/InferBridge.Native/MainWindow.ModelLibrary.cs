using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace InferBridge.Native;

// Verified Model Library parity: definition export/import and registering
// already-converted OpenVINO models, through the same protected
// /v1/model-library endpoints as the web UI. The engine still validates IR
// markers, links, disk space and active-model conflicts; nothing here loads,
// converts or trusts remote code.
public sealed partial class MainWindow
{
    private bool ModelLibraryToolsReady => MaintenanceReady && !_isManaging && !_chatBusy
        && !RefreshLocksControls;

    private async Task RunModelLibraryOperationAsync(Func<Task> operation)
    {
        if (!ModelLibraryToolsReady || _isRefreshing) return;
        _isManaging = true;
        UpdateModelButtons();
        UpdateChatButtons();
        bool changed = false;
        try
        {
            await operation();
            changed = true;
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            ShowModelsInfo(error is InvalidDataException or ArgumentException
                ? error.Message : SafeError(error), InfoBarSeverity.Error);
        }
        finally
        {
            _isManaging = false;
            if (!_closing.IsCancellationRequested)
            {
                UpdateModelButtons();
                UpdateChatButtons();
                if (changed) await RefreshAsync();
            }
        }
    }

    private async void ExportDefinitionsMenuItem_Click(object sender, RoutedEventArgs args)
    {
        bool includeAll = ReferenceEquals(sender, ExportAllDefinitionsMenuItem);
        await RunModelLibraryOperationAsync(async () =>
        {
            string content = await _backend.ExportModelDefinitionsAsync(
                includeAll, ApiKeyBox.Password, _closing.Token);
            var picker = new FileSavePicker { SuggestedFileName = "openvino-model-definitions" };
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            picker.FileTypeChoices.Add("Model definitions JSON", new List<string> { ".json" });
            StorageFile? file = await picker.PickSaveFileAsync();
            if (file is null) return;
            await FileIO.WriteTextAsync(file, content);
            ShowModelsInfo("Saved " + file.Name + ". Definitions contain no API keys, tokens, chats or local model paths.",
                InfoBarSeverity.Success);
        });
    }

    private async void ImportDefinitionsMenuItem_Click(object sender, RoutedEventArgs args)
    {
        await RunModelLibraryOperationAsync(async () =>
        {
            var picker = new FileOpenPicker();
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            picker.FileTypeFilter.Add(".json");
            StorageFile? file = await picker.PickSingleFileAsync();
            if (file is null) return;
            byte[] bytes = await ReadBoundedFileAsync(file, BackendClient.MaximumDefinitionFileBytes);
            BackendClient.ParseDefinitionFile(bytes, out int count);
            if (!await ConfirmAsync("Import model definitions?",
                    "Register " + count + " model definition(s) from " + file.Name
                    + ". Existing models with a different definition are refused, never replaced. "
                    + "No model files are downloaded until you choose to download or load a model.",
                    "Import definitions"))
                return;
            var result = await _backend.ImportModelDefinitionsAsync(bytes, ApiKeyBox.Password, _closing.Token);
            ShowModelsInfo("Definitions imported: " + result.Added.Count + " added, "
                + result.Unchanged.Count + " already identical.", InfoBarSeverity.Success);
        });
    }

    private async void AdoptLocalModelMenuItem_Click(object sender, RoutedEventArgs args)
    {
        await RunModelLibraryOperationAsync(async () =>
        {
            var detected = await _backend.GetUnregisteredLocalModelsAsync(ApiKeyBox.Password, _closing.Token);
            if (detected.Count == 0)
            {
                ShowModelsInfo("No unregistered converted OpenVINO folders were found in the managed models directory.",
                    InfoBarSeverity.Informational);
                return;
            }
            await RegisterLocalModelAsync(detected);
        });
    }

    private async void ImportConvertedMenuItem_Click(object sender, RoutedEventArgs args) =>
        await RunModelLibraryOperationAsync(() => RegisterLocalModelAsync(null));

    private async Task RegisterLocalModelAsync(IReadOnlyList<UnregisteredLocalModel>? detected)
    {
        var layout = new StackPanel { Spacing = 10, MinWidth = 380 };
        layout.Children.Add(new TextBlock
        {
            Text = detected is null
                ? "InferBridge checks the OpenVINO IR files, refuses symbolic links, checks free disk space and copies the directory into its managed models folder. The source is not changed."
                : "InferBridge found converted OpenVINO folders in its managed models directory that are not registered yet. Registration keeps the folder in place.",
            TextWrapping = TextWrapping.Wrap,
        });
        ComboBox? folders = null;
        TextBox? sourcePath = null;
        if (detected is not null)
        {
            folders = new ComboBox { Header = "Detected local model", HorizontalAlignment = HorizontalAlignment.Stretch };
            foreach (var item in detected)
                folders.Items.Add(new ComboBoxItem
                {
                    Content = item.DirectoryName + " · " + (item.SizeBytes / 1024d / 1024 / 1024).ToString("0.0") + " GB",
                    Tag = item,
                });
            layout.Children.Add(folders);
        }
        else
        {
            sourcePath = new TextBox
            {
                Header = "Absolute directory containing OpenVINO IR", MaxLength = 1024,
                PlaceholderText = @"C:\Models\my-model-int4-ov",
            };
            layout.Children.Add(sourcePath);
        }
        var modelId = new TextBox { Header = "Model ID", MaxLength = 128, PlaceholderText = "my-model-int4" };
        var name = new TextBox { Header = "Display name", MaxLength = 160 };
        ComboBox Choice(string header, params (string Label, string Tag)[] options)
        {
            var box = new ComboBox { Header = header, MinWidth = 230, HorizontalAlignment = HorizontalAlignment.Left };
            foreach (var (label, tag) in options) box.Items.Add(new ComboBoxItem { Content = label, Tag = tag });
            box.SelectedIndex = 0;
            return box;
        }
        var backend = Choice("Model type", ("Text generation", "openvino-genai"),
            ("Vision language", "openvino-vlm"), ("Embeddings", "openvino-embeddings"));
        var format = Choice("Weight format", ("FP16", "fp16"), ("INT8", "int8"), ("INT4", "int4"));
        var device = Choice("Recommended device", ("CPU", "CPU"), ("GPU", "GPU"), ("NPU", "NPU"), ("AUTO", "AUTO"));
        var context = new NumberBox
        {
            Header = "Max context length (128–262,144 tokens)", Value = 2048, Minimum = 128, Maximum = 262144,
            SmallChange = 512, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
            ValidationMode = NumberBoxValidationMode.Disabled, HorizontalAlignment = HorizontalAlignment.Left,
            MinWidth = 230,
        };
        var error = new TextBlock { TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        foreach (var control in new FrameworkElement[] { modelId, name, backend, format, device, context, error })
            layout.Children.Add(control);
        layout.Children.Add(new TextBlock
        {
            Text = "Compatibility stays unverified until you load and benchmark the model on this PC. Remote model code is never enabled.",
            TextWrapping = TextWrapping.Wrap, Opacity = 0.8,
        });
        if (folders is not null)
        {
            folders.SelectionChanged += (_, _) =>
            {
                if (folders.SelectedItem is ComboBoxItem { Tag: UnregisteredLocalModel pick })
                {
                    modelId.Text = pick.SuggestedModelId;
                    name.Text = pick.SuggestedName;
                }
            };
            folders.SelectedIndex = 0;
        }

        LocalModelRegistration? registration = null;
        string SelectedTag(ComboBox box) => (box.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
        var dialog = new ContentDialog
        {
            XamlRoot = DialogRoot,
            Title = detected is null ? "Import local / converted OpenVINO model" : "Register detected local model",
            Content = new ScrollViewer { Content = layout, MaxHeight = 460, VerticalScrollBarVisibility = ScrollBarVisibility.Auto },
            PrimaryButtonText = detected is null ? "Import and manage copy" : "Register model",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        dialog.PrimaryButtonClick += (_, click) =>
        {
            double raw = context.Value;
            var candidate = new LocalModelRegistration(
                modelId.Text.Trim(), name.Text.Trim(), SelectedTag(backend), SelectedTag(format), SelectedTag(device),
                double.IsFinite(raw) ? (int)Math.Clamp(Math.Truncate(raw), 128, 262144) : 2048,
                (folders?.SelectedItem as ComboBoxItem)?.Tag is UnregisteredLocalModel chosen ? chosen.DirectoryName : null,
                sourcePath is null ? null : sourcePath.Text.Trim());
            try
            {
                BackendClient.ValidateRegistration(candidate);
                registration = candidate;
            }
            catch (ArgumentException invalid)
            {
                error.Text = invalid.Message;
                error.Visibility = Visibility.Visible;
                click.Cancel = true;
            }
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || registration is null
            || _closing.IsCancellationRequested)
            return;
        ShowModelsInfo(registration.DirectoryName is null
            ? "Copying and validating the OpenVINO directory. Large models can take several minutes…"
            : "Registering the detected model…", InfoBarSeverity.Informational);
        var result = await _backend.RegisterLocalModelAsync(registration, ApiKeyBox.Password, _closing.Token);
        ShowModelsInfo("Registered " + result.ModelId + " (" + (result.SizeBytes / 1024d / 1024 / 1024).ToString("0.0")
            + " GB" + (result.ManagedInPlace ? ", kept in place" : ", managed copy") + "). Load and benchmark it before relying on it.",
            InfoBarSeverity.Success);
    }
}
