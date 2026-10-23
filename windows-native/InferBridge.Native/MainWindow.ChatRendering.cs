using System.Text;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    // All model text is rendered into WinUI text controls; Markdown is never
    // interpreted as HTML, launched as a URL, or loaded as a remote resource.
    private static readonly Regex InlineMarkdown = new(
        @"\*\*(?<strong>[^*\r\n]+)\*\*|(?<!\*)\*(?<em>[^*\r\n]+)\*(?!\*)|`(?<code>[^`\r\n]+)`",
        RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));

    private void CopyChatMessage(string value, string label)
    {
        try
        {
            var data = new DataPackage();
            data.SetText(value);
            Clipboard.SetContent(data);
            ShowChatInfo("Copied", label + " copied to clipboard.", InfoBarSeverity.Success);
        }
        catch (Exception)
        {
            ShowChatInfo("Clipboard unavailable", "Select the message text to copy it manually.",
                InfoBarSeverity.Warning);
        }
    }

    private static RichTextBlock RenderInlineMarkdown(string value, bool heading = false)
    {
        var text = new RichTextBlock { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        var paragraph = new Paragraph();
        int cursor = 0;
        int tokens = 0;
        try
        {
            foreach (Match match in InlineMarkdown.Matches(value))
            {
                if (++tokens > 128)
                {
                    paragraph.Inlines.Clear();
                    cursor = 0; // Bound rich-text elements even for adversarial model output.
                    break;
                }
                if (match.Index > cursor)
                    paragraph.Inlines.Add(new Run { Text = value[cursor..match.Index] });
                if (match.Groups["strong"].Success)
                    paragraph.Inlines.Add(new Bold { Inlines = { new Run { Text = match.Groups["strong"].Value } } });
                else if (match.Groups["em"].Success)
                    paragraph.Inlines.Add(new Italic { Inlines = { new Run { Text = match.Groups["em"].Value } } });
                else
                    paragraph.Inlines.Add(new Run { Text = match.Groups["code"].Value });
                cursor = match.Index + match.Length;
            }
        }
        catch (RegexMatchTimeoutException)
        {
            paragraph.Inlines.Clear();
            cursor = 0; // Plain text remains safe if inline parsing takes too long.
        }
        if (cursor < value.Length)
            paragraph.Inlines.Add(new Run { Text = value[cursor..] });
        if (heading) paragraph.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        text.Blocks.Add(paragraph);
        return text;
    }

    private UIElement RenderCodeBlock(string code, string language)
    {
        var container = new StackPanel { Spacing = 5 };
        var toolbar = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        toolbar.Children.Add(new TextBlock
        {
            Text = string.IsNullOrWhiteSpace(language) ? "Code" : language,
            VerticalAlignment = VerticalAlignment.Center,
        });
        var copy = new Button { Content = "Copy code" };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(copy,
            "Copy " + (string.IsNullOrWhiteSpace(language) ? "code" : language + " code") + " block");
        copy.Click += (_, _) => CopyChatMessage(code, "Code block");
        toolbar.Children.Add(copy);
        container.Children.Add(toolbar);
        container.Children.Add(new TextBox
        {
            Text = code, IsReadOnly = true, AcceptsReturn = true,
            TextWrapping = TextWrapping.NoWrap, MinHeight = 64, MaxHeight = 240,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
            HorizontalAlignment = HorizontalAlignment.Stretch,
        });
        return container;
    }

    private UIElement BuildChatContent(string speaker, string message)
    {
        var container = new StackPanel { Spacing = 5, Margin = new Thickness(8) };
        container.Children.Add(new TextBlock
        {
            Text = speaker, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        var lines = message.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n');
        var code = new StringBuilder();
        bool inCode = false;
        string language = "";
        int blocks = 0;
        foreach (string line in lines)
        {
            // A reply can contain thousands of short lines. Limit the number of
            // WinUI elements per turn; full text remains available via Copy message.
            if (++blocks > 160)
            {
                container.Children.Add(new TextBlock
                {
                    Text = "Long message preview limited to 160 lines. Use Copy message for all text.",
                    TextWrapping = TextWrapping.Wrap,
                });
                inCode = false;
                break;
            }
            if (line.TrimStart().StartsWith("```", StringComparison.Ordinal))
            {
                if (inCode)
                {
                    container.Children.Add(RenderCodeBlock(code.ToString().TrimEnd('\n'), language));
                    code.Clear();
                    inCode = false;
                }
                else
                {
                    language = line.Trim()[3..];
                    if (language.Length > 32 || language.Any(char.IsControl)) language = "";
                    inCode = true;
                }
                continue;
            }
            if (inCode)
            {
                code.AppendLine(line);
                continue;
            }
            if (string.IsNullOrWhiteSpace(line)) continue;
            string content = line.TrimStart();
            bool heading = content.StartsWith('#');
            if (heading)
            {
                int count = content.TakeWhile(ch => ch == '#').Count();
                if (count <= 6 && count < content.Length && content[count] == ' ')
                    content = content[(count + 1)..];
                else heading = false;
            }
            else if (content.StartsWith("- ", StringComparison.Ordinal)
                || content.StartsWith("* ", StringComparison.Ordinal))
                content = "• " + content[2..];
            else if (content.StartsWith("> ", StringComparison.Ordinal))
                content = "│ " + content[2..];
            container.Children.Add(RenderInlineMarkdown(content, heading));
        }
        if (inCode) // Keep malformed/unclosed fences readable; never discard model text.
            container.Children.Add(RenderInlineMarkdown("```" + language + "\n" + code));
        var copyMessage = new Button { Content = "Copy message" };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(copyMessage,
            "Copy " + speaker + " message");
        copyMessage.Click += (_, _) => CopyChatMessage(message, speaker + " message");
        container.Children.Add(copyMessage);
        return container;
    }
}
