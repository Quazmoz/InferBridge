namespace InferBridge.Native;

// Native chat must render the same safe Markdown subset as the browser renderer
// (renderMarkdown in web/index.html), and never turn model text into markup,
// links or remote resources.
internal static class ChatMarkdownContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Native chat Markdown: " + message);
    }

    public static void Run()
    {
        var blocks = ChatMarkdown.Parse(string.Join("\r\n",
            "# Title", "#### Deep heading", "####### not a heading",
            "Intro line one", "line two", "",
            "- first", "* second", "1. one", "7) two", "",
            "> quoted", "> again", "---",
            "```python", "print('**not bold**')", "```",
            "<script>alert(1)</script> ![x](https://evil.invalid/a.png)"), out bool truncated);
        Check(!truncated, "short message was truncated");
        var kinds = blocks.Select(block => block.Kind).ToArray();
        Check(kinds.SequenceEqual(new[]
        {
            MarkdownBlockKind.Heading, MarkdownBlockKind.Heading, MarkdownBlockKind.Paragraph,
            MarkdownBlockKind.ListItem, MarkdownBlockKind.ListItem,
            MarkdownBlockKind.ListItem, MarkdownBlockKind.ListItem,
            MarkdownBlockKind.Quote, MarkdownBlockKind.Rule, MarkdownBlockKind.Code,
            MarkdownBlockKind.Paragraph,
        }), "block structure differs from the web renderer: " + string.Join(",", kinds));
        Check(blocks[0].Level == 1 && blocks[1].Level == 4, "heading levels were not preserved");
        Check(blocks[2].Lines.SequenceEqual(new[] { "####### not a heading", "Intro line one", "line two" }),
            "seven hashes or soft line breaks were misparsed");
        Check(blocks[3].Marker == "•" && blocks[4].Marker == "•", "bullet marker missing");
        Check(blocks[5].Marker == "1." && blocks[6].Marker == "2.",
            "ordered list items are not numbered sequentially like <ol>");
        Check(blocks[7].Lines.SequenceEqual(new[] { "quoted", "again" }), "quote lines were not grouped");
        Check(blocks[9].Language == "python" && blocks[9].Lines.Single() == "print('**not bold**')",
            "fenced code lost its language or text");
        Check(blocks[10].Lines.Single().Contains("<script>", StringComparison.Ordinal)
            && blocks[10].Lines.Single().Contains("https://evil.invalid", StringComparison.Ordinal),
            "raw HTML or an image link was not kept as literal text");

        var spans = ChatMarkdown.ParseInline("Use `a**b**` with **bold**, __strong__, *em* and _also_. a*b*c");
        Check(spans.Count(span => span.Kind == MarkdownSpanKind.Code && span.Text == "a**b**") == 1,
            "inline code content was formatted");
        Check(spans.Count(span => span.Kind == MarkdownSpanKind.Strong) == 2
            && spans.Count(span => span.Kind == MarkdownSpanKind.Emphasis) == 2,
            "strong/emphasis spans differ from the web renderer");
        Check(string.Concat(spans.Where(s => s.Kind == MarkdownSpanKind.Text).Select(s => s.Text))
            .EndsWith(". a*b*c", StringComparison.Ordinal), "intra-word asterisks became emphasis");

        var unclosed = ChatMarkdown.Parse("```\ncode never closed", out _);
        Check(unclosed.Single().Kind == MarkdownBlockKind.Code
            && unclosed.Single().Lines.Single() == "code never closed", "unclosed fence lost text");

        var huge = ChatMarkdown.Parse(string.Join("\n\n", Enumerable.Range(0, 5000).Select(i => "p" + i)),
            out bool cut);
        Check(cut && huge.Count == ChatMarkdown.MaximumBlocks, "block count is unbounded");
        var many = ChatMarkdown.ParseInline(string.Concat(Enumerable.Repeat("**x** ", 500)));
        Check(many.Single().Kind == MarkdownSpanKind.Text, "inline token count is unbounded");
        Check(ChatMarkdown.Parse(null, out _).Count == 0, "null message did not render empty");
        Console.WriteLine("Native chat Markdown parity contract: all cases passed.");
    }
}
