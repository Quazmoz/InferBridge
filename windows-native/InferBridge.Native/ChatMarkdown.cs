using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal enum MarkdownBlockKind { Paragraph, Heading, ListItem, Quote, Code, Rule }

internal enum MarkdownSpanKind { Text, Strong, Emphasis, Code }

internal sealed record MarkdownSpan(MarkdownSpanKind Kind, string Text);

/// <summary>
/// One rendered block. <see cref="Lines"/> holds paragraph/quote lines, the
/// heading or list-item text, or raw code lines. <see cref="Marker"/> is the
/// list bullet or sequential ordered-list number.
/// </summary>
internal sealed record MarkdownBlock(
    MarkdownBlockKind Kind, IReadOnlyList<string> Lines, int Level = 0,
    string Marker = "", string Language = "");

/// <summary>
/// The same deliberately small Markdown subset as the browser renderer
/// (<c>renderMarkdown</c> in web/index.html): ATX headings, fenced code,
/// thematic breaks, flat bullet/ordered lists, block quotes and paragraphs, with
/// inline code, strong and emphasis. Links, pictures and raw HTML stay literal
/// text, so model output can never load a remote resource or execute markup.
/// Pure and UI-free so the parity contract runs in the .NET harness.
/// </summary>
internal static class ChatMarkdown
{
    internal const int MaximumBlocks = 200;
    internal const int MaximumInlineTokens = 128;

    private static readonly TimeSpan RegexBudget = TimeSpan.FromMilliseconds(100);
    private static readonly Regex Fence = new(@"^```([A-Za-z0-9_+.#-]{0,32})\s*$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Heading = new(@"^(#{1,6})\s+(.+)$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Rule = new(@"^ {0,3}([-*_])(?:\s*\1){2,}\s*$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Unordered = new(@"^\s*[-+*]\s+(.+)$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Ordered = new(@"^\s*\d+[.)]\s+(.+)$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Quote = new(@"^\s*>\s?(.*)$",
        RegexOptions.CultureInvariant, RegexBudget);
    private static readonly Regex Inline = new(
        @"`(?<code>[^`\n]+)`|\*\*(?<strong>[^*\n]+)\*\*|__(?<strong>[^_\n]+)__"
        + @"|(?<=^|[\s(])\*(?<em>[^*\n]+)\*(?=$|[\s).,!?:;])"
        + @"|(?<=^|[\s(])_(?<em>[^_\n]+)_(?=$|[\s).,!?:;])",
        RegexOptions.CultureInvariant, RegexBudget);

    public static IReadOnlyList<MarkdownBlock> Parse(string? text, out bool truncated)
    {
        truncated = false;
        var output = new List<MarkdownBlock>();
        var paragraph = new List<string>();
        var quote = new List<string>();
        var code = new List<string>();
        bool? orderedList = null;
        int listNumber = 0;
        bool inCode = false;
        string language = "";

        void FlushParagraph()
        {
            if (paragraph.Count == 0) return;
            output.Add(new MarkdownBlock(MarkdownBlockKind.Paragraph, paragraph.ToArray()));
            paragraph.Clear();
        }
        void FlushQuote()
        {
            if (quote.Count == 0) return;
            output.Add(new MarkdownBlock(MarkdownBlockKind.Quote, quote.ToArray()));
            quote.Clear();
        }
        void EndList()
        {
            orderedList = null;
            listNumber = 0;
        }
        void FlushBlocks()
        {
            FlushParagraph();
            FlushQuote();
            EndList();
        }
        void FlushCode()
        {
            output.Add(new MarkdownBlock(MarkdownBlockKind.Code, code.ToArray(), Language: language));
            code.Clear();
            language = "";
        }

        string[] lines = (text ?? "").Replace("\r\n", "\n", StringComparison.Ordinal)
            .Replace('\r', '\n').Split('\n');
        try
        {
            foreach (string line in lines)
            {
                if (output.Count >= MaximumBlocks)
                {
                    truncated = true;
                    break;
                }
                Match fence = Fence.Match(line);
                if (fence.Success)
                {
                    if (inCode)
                    {
                        FlushCode();
                        inCode = false;
                    }
                    else
                    {
                        FlushBlocks();
                        inCode = true;
                        language = fence.Groups[1].Value;
                    }
                    continue;
                }
                if (inCode)
                {
                    code.Add(line);
                    continue;
                }
                if (string.IsNullOrWhiteSpace(line))
                {
                    FlushBlocks();
                    continue;
                }
                Match heading = Heading.Match(line);
                if (heading.Success)
                {
                    FlushBlocks();
                    output.Add(new MarkdownBlock(MarkdownBlockKind.Heading,
                        [heading.Groups[2].Value], heading.Groups[1].Length));
                    continue;
                }
                if (Rule.IsMatch(line))
                {
                    FlushBlocks();
                    output.Add(new MarkdownBlock(MarkdownBlockKind.Rule, Array.Empty<string>()));
                    continue;
                }
                Match unordered = Unordered.Match(line);
                Match ordered = Ordered.Match(line);
                if (unordered.Success || ordered.Success)
                {
                    FlushParagraph();
                    FlushQuote();
                    bool isOrdered = ordered.Success;
                    if (orderedList is not null && orderedList != isOrdered) EndList();
                    orderedList = isOrdered;
                    listNumber++;
                    // Like <ol>, ordered items are numbered sequentially within one list.
                    output.Add(new MarkdownBlock(MarkdownBlockKind.ListItem,
                        [(isOrdered ? ordered : unordered).Groups[1].Value],
                        Marker: isOrdered ? listNumber + "." : "•"));
                    continue;
                }
                Match quoted = Quote.Match(line);
                if (quoted.Success)
                {
                    FlushParagraph();
                    EndList();
                    quote.Add(quoted.Groups[1].Value);
                    continue;
                }
                EndList();
                FlushQuote();
                paragraph.Add(line);
            }
        }
        catch (RegexMatchTimeoutException)
        {
            // Never discard model text: fall back to one plain paragraph.
            truncated = false;
            return [new MarkdownBlock(MarkdownBlockKind.Paragraph, lines.Take(MaximumBlocks).ToArray())];
        }
        if (inCode) FlushCode(); // An unclosed fence still shows its text as code.
        FlushParagraph();
        FlushQuote();
        return output;
    }

    public static IReadOnlyList<MarkdownSpan> ParseInline(string value)
    {
        var spans = new List<MarkdownSpan>();
        int cursor = 0;
        int tokens = 0;
        try
        {
            foreach (Match match in Inline.Matches(value))
            {
                if (++tokens > MaximumInlineTokens)
                    return [new MarkdownSpan(MarkdownSpanKind.Text, value)];
                if (match.Index > cursor)
                    spans.Add(new MarkdownSpan(MarkdownSpanKind.Text, value[cursor..match.Index]));
                if (match.Groups["code"].Success)
                    spans.Add(new MarkdownSpan(MarkdownSpanKind.Code, match.Groups["code"].Value));
                else if (match.Groups["strong"].Success)
                    spans.Add(new MarkdownSpan(MarkdownSpanKind.Strong, match.Groups["strong"].Value));
                else
                    spans.Add(new MarkdownSpan(MarkdownSpanKind.Emphasis, match.Groups["em"].Value));
                cursor = match.Index + match.Length;
            }
        }
        catch (RegexMatchTimeoutException)
        {
            return [new MarkdownSpan(MarkdownSpanKind.Text, value)];
        }
        if (cursor < value.Length)
            spans.Add(new MarkdownSpan(MarkdownSpanKind.Text, value[cursor..]));
        return spans;
    }
}
