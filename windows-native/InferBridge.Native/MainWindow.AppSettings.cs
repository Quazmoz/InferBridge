using Microsoft.UI.Xaml.Controls;

namespace InferBridge.Native;

// Browser preferences kept natively in NativePreferences: the chat device target and
// Combine devices target (restored on the first detected device list), the advisor
// profile, and the age-based conversation retention in Settings → Conversation storage.
public sealed partial class MainWindow
{
    private bool _applyingDeviceOptions;
    private bool _chatDeviceRestored;

    private static string RetentionRemovedMessage(int count) =>
        "Retention removed " + count + " old local chat" + (count == 1 ? "" : "s") + ".";

    private async void ConversationRetentionPicker_SelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        // _applyingTheme also guards restoring this picker from saved preferences.
        if (_applyingTheme || ConversationRetentionPicker.SelectedItem is not ComboBoxItem { Tag: string tag }
            || !int.TryParse(tag, System.Globalization.CultureInfo.InvariantCulture, out int days)) return;
        int previous = _preferences.ConversationRetentionDays;
        if (days == previous) return;
        void Revert(string message)
        {
            _applyingTheme = true;
            try
            {
                ConversationRetentionPicker.SelectedItem = ConversationRetentionPicker.Items.Cast<ComboBoxItem>()
                    .FirstOrDefault(item => (string?)item.Tag == previous.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            finally { _applyingTheme = false; }
            ConversationRetentionStatus.Text = message;
        }
        try
        {
            int candidates = _conversationStore is not null && _historyAvailable
                ? NativeConversationStore.RetentionCandidates(_conversationStore.ReadAll(), days,
                    _currentConversationId, DateTimeOffset.UtcNow).Count
                : 0;
            if (candidates > 0)
            {
                var confirm = new ContentDialog
                {
                    XamlRoot = ConversationRetentionPicker.XamlRoot,
                    Title = "Remove old chats?",
                    Content = "This retention setting will remove " + candidates + " inactive local chat"
                        + (candidates == 1 ? "" : "s") + " now.\n\nPinned chats and the open chat will be kept.",
                    PrimaryButtonText = "Remove and save",
                    CloseButtonText = "Cancel",
                    DefaultButton = ContentDialogButton.Close,
                };
                if (await confirm.ShowAsync() != ContentDialogResult.Primary || _closing.IsCancellationRequested)
                {
                    Revert("Retention unchanged.");
                    return;
                }
            }
            if (!TrySavePreferences(_preferences with { ConversationRetentionDays = days }, out string error))
            {
                Revert(error);
                return;
            }
            int removed = candidates > 0
                ? _conversationStore!.RemoveInactive(days, _currentConversationId, DateTimeOffset.UtcNow) : 0;
            if (removed > 0) RefreshSavedChats(_currentConversationId);
            ConversationRetentionStatus.Text = removed > 0 ? RetentionRemovedMessage(removed) : "Conversation retention updated.";
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException
            or InvalidDataException or System.Text.Json.JsonException)
        {
            // Nothing was removed; the history file is preserved as it was.
            if (_preferences.ConversationRetentionDays != previous)
                TrySavePreferences(_preferences with { ConversationRetentionDays = previous }, out _);
            Revert("Saved chats could not be read or written, so retention was not changed.");
        }
        catch (Exception) when (!_closing.IsCancellationRequested)
        {
            Revert("Retention unchanged."); // another dialog is open
        }
    }
}
