using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace InferBridge.Native;

// Chat parity with the browser client: request-only system prompt, vision image
// attachments gated by the engine's supports_vision contract, and explicit
// Markdown/JSON export plus JSON import in the browser's conversation format.
public sealed partial class MainWindow
{
    private readonly List<ChatImage> _pendingImages = new();
    private string? _systemPrompt;

    private ChatRequestContext CurrentChatContext() =>
        new(_systemPrompt, _pendingImages.ToArray());

    private void ClearPendingImages()
    {
        _pendingImages.Clear();
        UpdateAttachmentSummary();
    }

    private void UpdateAttachmentSummary()
    {
        long bytes = _pendingImages.Sum(image => (long)image.ByteCount);
        ChatAttachmentSummary.Text = _pendingImages.Count == 0
            ? "Images: none. Available only for models the engine reports as vision-capable."
            : _pendingImages.Count + " of " + ChatImage.MaximumImages + " image(s) attached · "
                + (bytes / 1024.0 / 1024.0).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture)
                + " MiB. Sent with your next message.";
        bool idle = !_chatBusy && !_quitting && !_closing.IsCancellationRequested;
        AttachImagesButton.IsEnabled = idle && SelectedChatModel?.Status == "loaded"
            && _pendingImages.Count < ChatImage.MaximumImages;
        ClearImagesButton.IsEnabled = idle && _pendingImages.Count > 0;
        ExportChatMarkdownButton.IsEnabled = idle && _turns.Count > 0;
        ExportChatJsonButton.IsEnabled = idle && _turns.Count > 0;
        ImportChatJsonButton.IsEnabled = idle;
    }

    private static async Task<byte[]> ReadBoundedFileAsync(StorageFile file, int maximumBytes)
    {
        var properties = await file.GetBasicPropertiesAsync();
        if (properties.Size > (ulong)maximumBytes)
            throw new ArgumentException(file.Name + " is larger than the permitted size.");
        await using Stream source = await file.OpenStreamForReadAsync();
        using var buffer = new MemoryStream();
        byte[] chunk = new byte[81920];
        int read;
        while ((read = await source.ReadAsync(chunk)) > 0)
        {
            if (buffer.Length + read > maximumBytes)
                throw new ArgumentException(file.Name + " is larger than the permitted size.");
            buffer.Write(chunk, 0, read);
        }
        return buffer.ToArray();
    }

    private async void AttachImagesButton_Click(object sender, RoutedEventArgs args)
    {
        string? modelId = SelectedModelId;
        if (_chatBusy || _quitting || modelId is null || _closing.IsCancellationRequested) return;
        AttachImagesButton.IsEnabled = false;
        try
        {
            // Same gate as the web client; the send path checks again before upload.
            if (!await _backend.GetModelSupportsVisionAsync(modelId, ApiKeyBox.Password, _closing.Token))
            {
                ShowChatInfo("Images unavailable",
                    "The engine does not report this model as vision-capable. Choose a vision-language model.",
                    InfoBarSeverity.Warning);
                return;
            }
            var picker = new FileOpenPicker { ViewMode = PickerViewMode.Thumbnail };
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            foreach (string extension in new[] { ".jpg", ".jpeg", ".png", ".webp" })
                picker.FileTypeFilter.Add(extension);
            var files = await picker.PickMultipleFilesAsync();
            if (files is null || files.Count == 0 || SelectedModelId != modelId) return;
            var rejected = new List<string>();
            foreach (StorageFile file in files)
            {
                if (_pendingImages.Count >= ChatImage.MaximumImages)
                {
                    rejected.Add(file.Name + " (limit of 4 images)");
                    continue;
                }
                try
                {
                    var image = ChatImage.FromBytes(await ReadBoundedFileAsync(file, ChatImage.MaximumBytes));
                    var next = new ChatRequestContext(null, _pendingImages.Append(image).ToArray());
                    next.Validate();
                    _pendingImages.Add(image);
                }
                catch (Exception error) when (error is ArgumentException or IOException
                    or UnauthorizedAccessException)
                {
                    rejected.Add(file.Name + " (" + error.Message + ")");
                }
            }
            if (rejected.Count > 0)
                ShowChatInfo("Some images were not attached", string.Join("; ", rejected.Take(4)),
                    InfoBarSeverity.Warning);
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Could not attach images", SafeError(error), InfoBarSeverity.Error);
        }
        finally
        {
            if (!_closing.IsCancellationRequested) UpdateAttachmentSummary();
        }
    }

    private void ClearImagesButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy) return;
        ClearPendingImages();
    }

    private string CurrentChatTitle()
    {
        string first = _turns.FirstOrDefault(turn => turn.Role == "user")?.Content ?? "";
        string title = string.Join(" ", first.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return title.Length == 0 ? "InferBridge conversation" : title[..Math.Min(title.Length, 75)];
    }

    private async void ExportChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _turns.Count == 0 || _closing.IsCancellationRequested) return;
        bool json = ReferenceEquals(sender, ExportChatJsonButton);
        try
        {
            string title = CurrentChatTitle();
            var flags = SavedFlagsForCurrentChat();
            string content = json
                ? NativeConversationStore.ExportJson(title, _turns, _conversationModelId, _systemPrompt,
                    DateTimeOffset.UtcNow, flags?.Pinned == true, flags?.Archived == true)
                : NativeConversationStore.ExportMarkdown(title, _turns, _conversationModelId,
                    DateTimeOffset.UtcNow);
            string fileName = new string(title.Select(ch =>
                char.IsLetterOrDigit(ch) || ch is '-' or '_' or ' ' ? ch : '-').ToArray()).Trim();
            var picker = new FileSavePicker
            {
                SuggestedFileName = fileName.Length == 0 ? "inferbridge-conversation" : fileName[..Math.Min(fileName.Length, 60)],
            };
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            picker.FileTypeChoices.Add(json ? "InferBridge JSON" : "Markdown",
                new List<string> { json ? ".json" : ".md" });
            StorageFile? file = await picker.PickSaveFileAsync();
            if (file is null) return;
            await FileIO.WriteTextAsync(file, content);
            ShowChatInfo(json ? "JSON export created" : "Markdown export created",
                "Saved " + file.Name + ". The browser Web UI can import the JSON format.",
                InfoBarSeverity.Success);
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Export failed", SafeError(error), InfoBarSeverity.Error);
        }
    }

    private async void ImportChatJsonButton_Click(object sender, RoutedEventArgs args)
    {
        if (_chatBusy || _quitting || _closing.IsCancellationRequested) return;
        try
        {
            var picker = new FileOpenPicker();
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            picker.FileTypeFilter.Add(".json");
            StorageFile? file = await picker.PickSingleFileAsync();
            if (file is null || _chatBusy) return;
            var imported = NativeConversationStore.ImportJson(
                await ReadBoundedFileAsync(file, NativeConversationStore.MaximumImportBytes));
            string? modelId = imported.ModelId is not null
                && ChatModelPicker.Items.Cast<ComboBoxItem>().Any(item => (string?)item.Tag == imported.ModelId)
                ? imported.ModelId : SelectedModelId;
            if (modelId is null)
                throw new InvalidDataException("Select a chat model before importing a conversation without one.");
            _currentConversationId = Guid.NewGuid().ToString("N");
            _conversationModelId = modelId;
            _turns.Clear();
            _turns.AddRange(imported.Turns);
            ResetGenerationSettings();
            _systemPrompt = imported.SystemPrompt;
            SystemPromptBox.Text = imported.SystemPrompt ?? "";
            ChatHistory.Items.Clear();
            foreach (var turn in imported.Turns)
                AppendChat(turn.Role == "user" ? "You" : "InferBridge", turn.Content);
            UpdateChatEmptyState();
            var option = ChatModelPicker.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(item => (string?)item.Tag == modelId);
            _updatingModelPicker = true;
            try { ChatModelPicker.SelectedItem = option; }
            finally { _updatingModelPicker = false; }
            bool saved = TrySaveCurrentChat();
            if (saved && SaveHistoryToggle.IsChecked == true) ApplyImportedFlags(imported);
            UpdateChatButtons();
            ShowChatInfo("Conversation imported",
                "Opened " + imported.Turns.Count + " of " + imported.SourceMessages + " message(s)"
                + (imported.Turns.Count < imported.SourceMessages
                    ? " (newest complete exchanges within native chat limits; the file is unchanged)" : "")
                + (imported.ModelId is not null && imported.ModelId != modelId
                    ? ". Its original model is not installed; the selected model is used." : ".")
                + (saved && SaveHistoryToggle.IsChecked == true ? " Saved locally." : ""),
                InfoBarSeverity.Success);
        }
        catch (Exception error) when (!_closing.IsCancellationRequested)
        {
            ShowChatInfo("Import failed", error is InvalidDataException ? error.Message : SafeError(error),
                InfoBarSeverity.Error);
        }
    }
}
