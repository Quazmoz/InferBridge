using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Windows.ApplicationModel.DataTransfer;

namespace InferBridge.Native;

public sealed partial class MainWindow
{
    // All model text is rendered into WinUI text controls through ChatMarkdown;
    // Markdown is never interpreted as HTML, launched as a URL, or loaded as a
    // remote resource.

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

    private static void AddInlines(InlineCollection inlines, string value)
    {
        foreach (MarkdownSpan span in ChatMarkdown.ParseInline(value))
        {
            Inline inline = span.Kind switch
            {
                MarkdownSpanKind.Strong => new Bold { Inlines = { new Run { Text = span.Text } } },
                MarkdownSpanKind.Emphasis => new Italic { Inlines = { new Run { Text = span.Text } } },
                MarkdownSpanKind.Code => new Run
                {
                    Text = span.Text,
                    FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                },
                _ => new Run { Text = span.Text },
            };
            inlines.Add(inline);
        }
    }

    private static RichTextBlock RenderInlineMarkdown(
        IReadOnlyList<string> lines, int headingLevel = 0, string marker = "")
    {
        var text = new RichTextBlock
        {
            TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
            FontSize = headingLevel switch { 1 => 22, 2 => 20, 3 => 18, 4 => 16, 5 => 15, _ => 14 },
        };
        var paragraph = new Paragraph();
        if (marker.Length > 0)
        {
            paragraph.Inlines.Add(new Run { Text = marker + " " });
            text.Margin = new Thickness(12, 0, 0, 0);
        }
        for (int i = 0; i < lines.Count; i++)
        {
            if (i > 0) paragraph.Inlines.Add(new LineBreak());
            AddInlines(paragraph.Inlines, lines[i]);
        }
        if (headingLevel > 0) paragraph.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
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
        var container = new StackPanel { Spacing = 7, Margin = new Thickness(2) };
        var header = new Grid { ColumnSpacing = 10 };
        header.ColumnDefinitions.Add(new ColumnDefinition
        {
            Width = new GridLength(1, GridUnitType.Star),
        });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.Children.Add(new TextBlock
        {
            Text = speaker, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center, Opacity = 0.88,
        });
        var copyMessage = new Button
        {
            Content = "Copy", Padding = new Thickness(8, 4, 8, 4),
            VerticalAlignment = VerticalAlignment.Center,
        };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(copyMessage,
            "Copy " + speaker + " message");
        copyMessage.Click += (_, _) => CopyChatMessage(message, speaker + " message");
        Grid.SetColumn(copyMessage, 1);
        header.Children.Add(copyMessage);
        container.Children.Add(header);
        var blocks = ChatMarkdown.Parse(message, out bool truncated);
        foreach (MarkdownBlock block in blocks)
        {
            container.Children.Add(block.Kind switch
            {
                MarkdownBlockKind.Code => RenderCodeBlock(string.Join("\n", block.Lines), block.Language),
                MarkdownBlockKind.Rule => new Border
                {
                    Height = 1, Opacity = 0.4, Margin = new Thickness(0, 4, 0, 4),
                    Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Gray),
                },
                MarkdownBlockKind.Quote => new Border
                {
                    BorderThickness = new Thickness(3, 0, 0, 0), Padding = new Thickness(10, 2, 0, 2),
                    BorderBrush = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Gray),
                    Child = RenderInlineMarkdown(block.Lines),
                },
                MarkdownBlockKind.Heading => RenderInlineMarkdown(block.Lines, block.Level),
                MarkdownBlockKind.ListItem => RenderInlineMarkdown(block.Lines, marker: block.Marker),
                _ => RenderInlineMarkdown(block.Lines),
            });
        }
        // A reply can contain thousands of short blocks. Limit WinUI elements per
        // turn; full text remains available via Copy message.
        if (truncated)
            container.Children.Add(new TextBlock
            {
                Text = "Long message preview limited to " + ChatMarkdown.MaximumBlocks
                    + " blocks. Use Copy message for all text.",
                TextWrapping = TextWrapping.Wrap,
            });
        return container;
    }
}
