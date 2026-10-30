using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Pin, archive and duplicate for opt-in local history, matching the browser
// conversation manager and its "inferbridge-conversation" JSON flags. Only the
// local history file changes; nothing is sent to the engine.
public sealed partial class MainWindow
{
    private NativeConversationStore.SavedChat? SelectedSavedChat =>
        SavedChatsPicker.SelectedItem is ComboBoxItem { Tag: NativeConversationStore.SavedChat saved }
            ? saved : null;

    private bool SavedChatActionsReady => !_chatBusy && !_quitting && _historyAvailable
        && _conversationStore is not null && !_closing.IsCancellationRequested;

    private void UpdateSavedChatFlagLabels()
    {
        var saved = SelectedSavedChat;
        PinSavedChatButton.Content = saved?.Pinned == true ? "Unpin" : "Pin";
        ArchiveSavedChatButton.Content = saved?.Archived == true ? "Restore" : "Archive";
    }

    private void MarkSavedHistoryUnavailable(string title)
    {
        _historyAvailable = false;
        SaveHistoryToggle.IsChecked = false;
        SaveHistoryToggle.IsEnabled = false;
        SavedChatsSearch.IsEnabled = false;
        SavedChatsPicker.IsEnabled = false;
        ShowArchivedChatsToggle.IsEnabled = false;
        RenameSavedChatButton.IsEnabled = false;
        DeleteSavedChatButton.IsEnabled = false;
        PinSavedChatButton.IsEnabled = false;
        ArchiveSavedChatButton.IsEnabled = false;
        DuplicateSavedChatButton.IsEnabled = false;
        ShowChatInfo(title, "Local history was not safely updated. The existing file was preserved.",
            InfoBarSeverity.Error);
    }

    private static bool IsHistoryStorageError(Exception error) => error is IOException
        or UnauthorizedAccessException or InvalidDataException or ArgumentException
        or System.Text.Json.JsonException;

    private NativeConversationStore.SavedChat? SavedFlagsForCurrentChat()
    {
        if (_conversationStore is null || !_historyAvailable) return null;
        try { return _conversationStore.ReadAll().FirstOrDefault(chat => chat.Id == _currentConversationId); }
        catch (Exception error) when (IsHistoryStorageError(error)) { return null; }
    }

    private void ShowArchivedChatsToggle_Changed(object sender, RoutedEventArgs args)
    {
        if (_updatingSavedChats || !_historyAvailable) return;
        try { RefreshSavedChats(_currentConversationId); }
        catch (Exception error) when (IsHistoryStorageError(error))
        {
            MarkSavedHistoryUnavailable("Saved history unavailable");
        }
    }

    private void PinSavedChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (!SavedChatActionsReady || SelectedSavedChat is not { } saved) return;
        try
        {
            if (!_conversationStore!.SetPinned(saved.Id, !saved.Pinned))
                throw new IOException("Saved conversation is no longer available.");
            RefreshSavedChats(saved.Id);
            ShowChatInfo(saved.Pinned ? "Conversation unpinned" : "Conversation pinned",
                saved.Pinned ? "It is listed by date again and can be removed by retention."
                    : "Pinned chats are listed first and never removed by retention.",
                InfoBarSeverity.Success);
        }
        catch (Exception error) when (IsHistoryStorageError(error))
        {
            MarkSavedHistoryUnavailable("Could not pin saved conversation");
        }
    }

    private void ArchiveSavedChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (!SavedChatActionsReady || SelectedSavedChat is not { } saved) return;
        bool archiving = !saved.Archived;
        try
        {
            if (!_conversationStore!.SetArchived(saved.Id, archiving))
                throw new IOException("Saved conversation is no longer available.");
            // Like the browser, archiving the open chat moves you to a new chat.
            if (archiving && saved.Id == _currentConversationId)
            {
                _turns.Clear();
                ChatHistory.Items.Clear();
                UpdateChatEmptyState();
                _currentConversationId = Guid.NewGuid().ToString("N");
                _conversationModelId = SelectedModelId;
                ResetGenerationSettings();
            }
            RefreshSavedChats();
            UpdateChatButtons();
            ShowChatInfo(archiving ? "Conversation archived" : "Conversation restored",
                archiving ? "Turn on \"Show archived conversations\" to find or restore it."
                    : "It is back in the active saved conversation list.",
                InfoBarSeverity.Success);
        }
        catch (Exception error) when (IsHistoryStorageError(error))
        {
            MarkSavedHistoryUnavailable("Could not archive saved conversation");
        }
    }

    private void DuplicateSavedChatButton_Click(object sender, RoutedEventArgs args)
    {
        if (!SavedChatActionsReady || SelectedSavedChat is not { } saved) return;
        try
        {
            var copy = _conversationStore!.Duplicate(saved.Id, Guid.NewGuid().ToString("N"))
                ?? throw new IOException("Saved conversation is no longer available.");
            _updatingSavedChats = true;
            try
            {
                // The copy is active and unpinned; show it in the active list.
                ShowArchivedChatsToggle.IsChecked = false;
                SavedChatsSearch.Text = string.Empty;
            }
            finally { _updatingSavedChats = false; }
            RefreshSavedChats();
            // Selecting outside the refresh guard opens the copy, like the browser.
            SavedChatsPicker.SelectedItem = SavedChatsPicker.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(item => item.Tag is NativeConversationStore.SavedChat chat && chat.Id == copy.Id);
            ShowChatInfo("Conversation duplicated", "Saved locally as \"" + copy.Title + "\".",
                InfoBarSeverity.Success);
        }
        catch (NativeConversationStore.HistoryFullException error)
        {
            ShowChatInfo("Conversation not duplicated", error.Message, InfoBarSeverity.Warning);
        }
        catch (Exception error) when (IsHistoryStorageError(error))
        {
            MarkSavedHistoryUnavailable("Could not duplicate saved conversation");
        }
    }

    // Browser exports may carry pinned/archived; keep them on the local copy.
    private void ApplyImportedFlags(NativeConversationStore.ImportedChat imported)
    {
        if (_conversationStore is null || !_historyAvailable || !(imported.Pinned || imported.Archived))
            return;
        try
        {
            if (imported.Pinned) _conversationStore.SetPinned(_currentConversationId, true);
            if (imported.Archived) _conversationStore.SetArchived(_currentConversationId, true);
            _updatingSavedChats = true;
            try { if (imported.Archived) ShowArchivedChatsToggle.IsChecked = true; }
            finally { _updatingSavedChats = false; }
            RefreshSavedChats(_currentConversationId);
        }
        catch (Exception error) when (IsHistoryStorageError(error))
        {
            MarkSavedHistoryUnavailable("Imported, but flags not saved");
        }
    }
}
