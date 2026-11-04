using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

// Local API → "Rerank for RAG": query plus one document per line in, results ordered
// by relevance out, through the same POST /v1/rerank the browser Connection Hub uses.
// Disabled with guidance while the reranker slot is empty.
public sealed partial class MainWindow
{
    private ModelSlot? _rerankSlot;
    private bool _rerankBusy;
    private bool _rerankChecking;
    private int _rerankGeneration;

    private void InvalidateRerankPanel()
    {
        _rerankGeneration++;
        _rerankSlot = null;
        RerankResults.Children.Clear();
        SetRerankStatus("");
        RerankInfo.Severity = InfoBarSeverity.Informational;
        RerankInfo.Message = "Refresh the connection to check for a loaded reranker.";
        UpdateRerankButtons();
    }

    private void UpdateRerankButtons()
    {
        bool connected = _hubMetadata is not null && !_quitting && !_closing.IsCancellationRequested;
        RerankProgress.IsActive = _rerankBusy || _rerankChecking;
        RerankRefreshButton.IsEnabled = connected && !_rerankBusy && !_rerankChecking;
        RerankCopyCurlButton.IsEnabled = connected;
        RerankRunButton.IsEnabled = connected && _rerankSlot is not null && !_rerankBusy && !_rerankChecking
            && RerankQueryBox.Text.Trim().Length > 0
            && Rerank.SplitDocuments(RerankDocumentsBox.Text).Count > 0;
    }

    private void SetRerankStatus(string text)
    {
        if (RerankStatus.Text == text) return;
        RerankStatus.Text = text;
        (FrameworkElementAutomationPeer.FromElement(RerankStatus)
            ?? FrameworkElementAutomationPeer.CreatePeerForElement(RerankStatus))
            ?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
    }

    private void RerankInput_TextChanged(object sender, TextChangedEventArgs args) => UpdateRerankButtons();

    private async void RerankRefreshButton_Click(object sender, RoutedEventArgs args) =>
        await RefreshRerankSlotAsync();

    private async Task RefreshRerankSlotAsync()
    {
        if (_rerankChecking || _rerankBusy || _hubMetadata is null || _quitting
            || _closing.IsCancellationRequested) return;
        _rerankChecking = true;
        int generation = ++_rerankGeneration;
        UpdateRerankButtons();
        try
        {
            ModelSlot? slot = await _backend.GetRerankSlotAsync(ApiKeyBox.Password, _closing.Token);
            if (generation != _rerankGeneration || _closing.IsCancellationRequested) return;
            _rerankSlot = slot;
            if (slot is null)
            {
                RerankInfo.Severity = InfoBarSeverity.Informational;
                RerankInfo.Message = "No reranker is loaded. In Model library, choose a reranker such as "
                    + "BGE Reranker Base and select Load as reranker, then Check reranker. It stays loaded "
                    + "beside your chat and embedding models.";
            }
            else
            {
                RerankInfo.Severity = InfoBarSeverity.Success;
                RerankInfo.Message = "POST /v1/rerank scores documents with the loaded reranker "
                    + (slot.Name ?? slot.ModelId) + (slot.Device is { } device ? " on " + device : "") + ".";
            }
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (generation == _rerankGeneration)
            {
                _rerankSlot = null;
                RerankInfo.Severity = InfoBarSeverity.Warning;
                RerankInfo.Message = "Could not check the reranker slot: " + SafeError(error);
            }
        }
        finally
        {
            _rerankChecking = false;
            if (!_closing.IsCancellationRequested) UpdateRerankButtons();
        }
    }

    private async void RerankRunButton_Click(object sender, RoutedEventArgs args)
    {
        if (_rerankBusy || _rerankSlot is not { } slot || _quitting || _closing.IsCancellationRequested) return;
        IReadOnlyList<string> documents = Rerank.SplitDocuments(RerankDocumentsBox.Text);
        string query = RerankQueryBox.Text.Trim();
        _rerankBusy = true;
        int generation = _rerankGeneration;
        UpdateRerankButtons();
        RerankResults.Children.Clear();
        SetRerankStatus("Scoring " + documents.Count + " document" + (documents.Count == 1 ? "" : "s") + "…");
        try
        {
            RerankOutcome outcome = await _backend.RerankAsync(slot.ModelId, query, documents,
                ApiKeyBox.Password, _closing.Token);
            if (generation != _rerankGeneration || _closing.IsCancellationRequested) return;
            int rank = 0;
            foreach (RerankResult result in outcome.Results)
            {
                var line = Ui.Text(Rerank.Describe(result, ++rank));
                line.IsTextSelectionEnabled = true;
                AutomationProperties.SetName(line, "Rank " + rank + ", score "
                    + result.Score.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture)
                    + ", document " + (result.Index + 1) + ": " + result.Text);
                RerankResults.Children.Add(line);
            }
            SetRerankStatus("Scored " + documents.Count + " document" + (documents.Count == 1 ? "" : "s")
                + " with " + outcome.Model + ". Scores are 0–1 relevance, highest first.");
        }
        catch (Exception) when (_closing.IsCancellationRequested) { }
        catch (Exception error)
        {
            SetRerankStatus("Rerank failed: " + SafeError(error));
            // A 503 means the slot emptied since the last check; show the guidance again.
            if (error is EngineRequestException && error.Message.StartsWith("No reranker", StringComparison.Ordinal))
                _ = RefreshRerankSlotAsync();
        }
        finally
        {
            _rerankBusy = false;
            if (!_closing.IsCancellationRequested) UpdateRerankButtons();
        }
    }

    private void RerankCopyCurlButton_Click(object sender, RoutedEventArgs args)
    {
        if (_hubMetadata is not { } metadata || _quitting) return;
        try
        {
            var data = new DataPackage();
            data.SetText(Rerank.CurlExample(metadata.BaseUrl, metadata.AuthenticationRequired, _rerankSlot?.ModelId));
            Clipboard.SetContent(data);
            SetRerankStatus("Rerank curl.exe example copied. No stored API key was included.");
        }
        catch (Exception)
        {
            SetRerankStatus("The clipboard is unavailable. Try copying again.");
        }
    }
}
