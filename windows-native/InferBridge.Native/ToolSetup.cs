using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

/// <summary>
/// Values a coding tool needs, always taken from the verified running engine
/// (never a hard-coded port). <see cref="KeyPlaceholder"/> is the Hub's generic
/// placeholder: snippets, config files and environment variables never carry a
/// real API key.
/// </summary>
internal sealed record ToolConnectionValues(
    string BaseUrl, string ModelId, bool AuthenticationRequired, int? ContextTokens, int? MaxOutputTokens)
{
    public string KeyPlaceholder => AuthenticationRequired ? "YOUR_INFERBRIDGE_API_KEY" : "not-required";

    public int? MaxInputTokens => ContextTokens is int context
        ? context - (MaxOutputTokens ?? 0) : null;

    public string ChatCompletionsUrl => BaseUrl + "/chat/completions";

    public static ToolConnectionValues From(LocalApiMetadata metadata, string modelId)
    {
        var model = metadata.UsableModels.FirstOrDefault(m => m.Id == modelId)
            ?? throw new ToolSetupException("Select a loaded chat model first.");
        return new ToolConnectionValues(metadata.BaseUrl, model.Id, metadata.AuthenticationRequired,
            model.ContextTokens, model.MaxOutputTokens).Validated();
    }

    /// <summary>Defence in depth before values reach a file, YAML or PowerShell.</summary>
    public ToolConnectionValues Validated()
    {
        if (!Uri.TryCreate(BaseUrl, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttp
            || !uri.IsLoopback || uri.AbsolutePath != "/v1" || BaseUrl != uri.GetLeftPart(UriPartial.Authority) + "/v1")
            throw new ToolSetupException("The engine address is not a verified loopback /v1 URL. Refresh the connection.");
        if (ModelId.Length is < 1 or > 128 || ModelId.Any(char.IsControl))
            throw new ToolSetupException("The selected model ID cannot be used in a tool configuration.");
        if (ContextTokens is < 1 or > 10_000_000 || MaxOutputTokens is < 1
            || (MaxOutputTokens is int output && (ContextTokens is not int context || output >= context)))
            throw new ToolSetupException("The engine reported inconsistent token limits. Refresh the connection.");
        return this;
    }
}

/// <summary>A failure whose message is already safe and readable for an InfoBar.</summary>
internal sealed class ToolSetupException(string message) : Exception(message);

internal enum ToolSetupKind { OneClick, Guided }

internal sealed record ToolClient(
    string Id, string Title, ToolSetupKind Kind, string Summary, string DocUrl, string DocLabel);

/// <summary>Per-client snippets and guidance. Pure: no WinUI, no I/O.</summary>
internal static class ToolSnippets
{
    public const string ContinueModelName = "InferBridge (local)";

    public static readonly IReadOnlyList<ToolClient> Clients =
    [
        new("continue", "Continue (VS Code · JetBrains)", ToolSetupKind.OneClick,
            "Adds or updates one model named \u201cInferBridge (local)\u201d in your Continue config.yaml.",
            "https://docs.continue.dev/reference", "Continue config.yaml reference"),
        new("copilot-cli", "GitHub Copilot CLI", ToolSetupKind.OneClick,
            "Sets the Copilot CLI bring-your-own-model environment variables for your Windows account.",
            "https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models",
            "GitHub Docs: Adding LLM models to Copilot CLI"),
        new("vscode-copilot", "VS Code · GitHub Copilot Chat", ToolSetupKind.Guided,
            "Add InferBridge as a Custom Endpoint in VS Code\u2019s Language Models editor.",
            "https://code.visualstudio.com/docs/agent-customization/language-models",
            "VS Code: AI language models"),
        new("copilot-app", "GitHub Copilot app", ToolSetupKind.Guided,
            "Add InferBridge as an OpenAI-compatible model provider (public preview).",
            "https://docs.github.com/en/copilot/how-tos/github-copilot-app/use-byok-models",
            "GitHub Docs: Adding LLM models to the Copilot app"),
        new("cline", "Cline", ToolSetupKind.Guided,
            "Choose the OpenAI Compatible API provider in Cline settings.",
            "https://docs.cline.bot/provider-config/openai-compatible", "Cline: OpenAI Compatible"),
        new("roo", "Roo Code / Zoo Code", ToolSetupKind.Guided,
            "Roo Code was discontinued on 15 May 2026; its community fork Zoo Code keeps the same OpenAI Compatible provider.",
            "https://github.com/Zoo-Code-Org/Zoo-Code-Docs/blob/main/docs/providers/openai-compatible.md",
            "Zoo Code: OpenAI Compatible"),
        new("generic", "Other apps (Open WebUI, n8n, scripts)", ToolSetupKind.Guided,
            "Any client with a custom OpenAI-compatible Base URL.",
            "https://github.com/Quazmoz/InferBridge/blob/main/docs/INTEGRATIONS.md", "InferBridge integrations guide"),
    ];

    public static IReadOnlyList<string> Steps(string clientId, ToolConnectionValues v) => clientId switch
    {
        "continue" =>
        [
            "Select Preview change, review the entry, then confirm. InferBridge backs up config.yaml first.",
            "Continue reloads config.yaml automatically. Pick \u201cInferBridge (local)\u201d in its model selector.",
            v.AuthenticationRequired
                ? "Authentication is on: open config.yaml and replace YOUR_INFERBRIDGE_API_KEY with your key. InferBridge never writes it."
                : "Authentication is off, so the entry uses apiKey: not-required.",
            "Select Undo to restore the backed-up file if anything looks wrong.",
        ],
        "copilot-cli" =>
        [
            "Select Preview change, review the variables, then confirm.",
            "Open a new terminal (existing terminals keep their old environment) and run copilot.",
            v.AuthenticationRequired
                ? "Authentication is on: in that terminal run $env:COPILOT_PROVIDER_API_KEY = \"<your key>\". InferBridge never stores it."
                : "Authentication is off, so no API key variable is needed.",
            "Select Undo to put the previous values back.",
        ],
        "vscode-copilot" =>
        [
            "In VS Code open the Command Palette and run Chat: Manage Language Models.",
            "Select Add Models \u2192 Custom Endpoint, enter a group name such as InferBridge.",
            "Enter a display name and the API key value below, and choose the Chat Completions API type.",
            "VS Code opens chatLanguageModels.json: paste the model entry below and save.",
            "Pick the model in the chat model picker. Restart VS Code if it does not appear.",
        ],
        "copilot-app" =>
        [
            "Open the GitHub Copilot app settings and select Model providers.",
            "Select Add provider, then choose OpenAI-compatible (any OpenAI-compatible HTTP endpoint).",
            "Paste the Base URL and API key below, then select Add provider.",
            "Choose the model ID below in the model picker of a new session.",
        ],
        "cline" =>
        [
            "Open Cline settings (gear icon) and set API Provider to OpenAI Compatible.",
            "Paste the Base URL, API key and Model ID below.",
            "Under Model Configuration set Context Window and Max Output Tokens to the values below.",
            "Use Verify or start a task to confirm the connection.",
        ],
        "roo" =>
        [
            "Install Zoo Code (Roo Code no longer receives updates).",
            "Open its settings and set API Provider to OpenAI Compatible.",
            "Paste the Base URL, API key and Model ID below.",
            "Under Model Configuration set Context Window and Max Output Tokens to the values below.",
        ],
        _ =>
        [
            "In the app choose an OpenAI-compatible or custom OpenAI provider.",
            "Paste the Base URL, API key and Model ID below.",
            "Use Test connection here to confirm the engine answers.",
        ],
    };

    /// <summary>Labelled values a card shows with copy buttons (placeholders only).</summary>
    public static IReadOnlyList<KeyValuePair<string, string>> Values(string clientId, ToolConnectionValues v)
    {
        var values = new List<KeyValuePair<string, string>>
        {
            new("Base URL", v.BaseUrl),
            new("Model ID", v.ModelId),
            new("API key", v.KeyPlaceholder),
        };
        if (clientId is "cline" or "roo" or "generic" && v.ContextTokens is int context)
        {
            values.Add(new("Context window", context.ToString(System.Globalization.CultureInfo.InvariantCulture)));
            if (v.MaxOutputTokens is int output)
                values.Add(new("Max output tokens", output.ToString(System.Globalization.CultureInfo.InvariantCulture)));
        }
        return values;
    }

    private static string Q(string value) => JsonSerializer.Serialize(value);

    private static string PowerShellQuote(string value) => "'" + value.Replace("'", "''") + "'";

    /// <summary>The managed Continue model entry, at the given list indentation.</summary>
    public static IReadOnlyList<string> ContinueEntry(ToolConnectionValues v, string indent = "  ")
    {
        var lines = new List<string>
        {
            indent + ContinueMarkers.Start,
            indent + "- name: " + Q(ContinueModelName),
            indent + "  provider: openai",
            indent + "  model: " + Q(v.ModelId),
            indent + "  apiBase: " + Q(v.BaseUrl),
            indent + "  apiKey: " + Q(v.KeyPlaceholder),
            indent + "  roles:",
            indent + "    - chat",
            indent + "    - edit",
            indent + "    - apply",
        };
        if (v.ContextTokens is int context)
        {
            lines.Add(indent + "  defaultCompletionOptions:");
            lines.Add(indent + "    contextLength: " + context);
            if (v.MaxOutputTokens is int output) lines.Add(indent + "    maxTokens: " + output);
        }
        lines.Add(indent + ContinueMarkers.End);
        return lines;
    }

    public static string ContinueSnippet(ToolConnectionValues v) =>
        "models:\n" + string.Join("\n", ContinueEntry(v)) + "\n";

    public static string CopilotCliPowerShell(ToolConnectionValues v)
    {
        var builder = new StringBuilder("# Run in PowerShell, then open a new terminal.\n");
        foreach (var (name, value) in CopilotCliEnvironment.Plan(v))
            builder.Append("[Environment]::SetEnvironmentVariable(")
                .Append(PowerShellQuote(name)).Append(", ").Append(PowerShellQuote(value))
                .Append(", 'User')\n");
        if (v.AuthenticationRequired)
            builder.Append("# In each terminal: $env:COPILOT_PROVIDER_API_KEY = 'YOUR_INFERBRIDGE_API_KEY'\n");
        return builder.ToString();
    }

    /// <summary>chatLanguageModels.json provider entry for VS Code's Custom Endpoint.</summary>
    public static string VsCodeCustomEndpoint(ToolConnectionValues v)
    {
        var model = new Dictionary<string, object>
        {
            ["id"] = v.ModelId,
            ["name"] = "InferBridge \u00b7 " + v.ModelId,
            ["url"] = v.ChatCompletionsUrl,
            ["toolCalling"] = true,
            ["vision"] = false,
        };
        AddTokenLimits(model, v);
        var provider = new Dictionary<string, object>
        {
            ["name"] = "InferBridge",
            ["vendor"] = "customendpoint",
            // VS Code resolves ${input:...} from secret storage, so no key is written.
            ["apiKey"] = v.AuthenticationRequired ? "${input:inferbridgeApiKey}" : "not-required",
            ["apiType"] = "chat-completions",
            ["models"] = new[] { model },
        };
        return JsonSerializer.Serialize(new[] { provider }, Indented) + "\n";
    }

    /// <summary>Deprecated stable-channel settings.json entry, labelled as such in the UI.</summary>
    public static string VsCodeDeprecatedCustomOaiModels(ToolConnectionValues v)
    {
        var model = new Dictionary<string, object>
        {
            ["name"] = "InferBridge \u00b7 " + v.ModelId,
            ["url"] = v.ChatCompletionsUrl,
            ["toolCalling"] = true,
            ["vision"] = false,
        };
        AddTokenLimits(model, v);
        model["requiresAPIKey"] = v.AuthenticationRequired;
        string body = JsonSerializer.Serialize(new Dictionary<string, object> { [v.ModelId] = model }, Indented);
        return "// Deprecated: github.copilot.chat.customOAIModels. Prefer Custom Endpoint.\n"
            + "\"github.copilot.chat.customOAIModels\": " + body + "\n";
    }

    private static void AddTokenLimits(Dictionary<string, object> model, ToolConnectionValues v)
    {
        if (v.MaxInputTokens is int input) model["maxInputTokens"] = input;
        if (v.MaxOutputTokens is int output) model["maxOutputTokens"] = output;
    }

    private static readonly JsonSerializerOptions Indented = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static string Snippet(string clientId, ToolConnectionValues v) => clientId switch
    {
        "continue" => ContinueSnippet(v),
        "copilot-cli" => CopilotCliPowerShell(v),
        "vscode-copilot" => VsCodeCustomEndpoint(v),
        _ => "",
    };

    /// <summary>
    /// Honest guidance: agentic coding tools need tool calling and a large context.
    /// GitHub recommends at least 128k tokens for Copilot CLI.
    /// </summary>
    public static (bool Warning, string Text) AgenticNote(int? contextTokens) => contextTokens switch
    {
        null => (true, "Context window unknown. Agentic coding tools need tool calling and a large context window; check the model before relying on agent mode."),
        < 32_768 => (true, $"Small context window ({contextTokens:N0} tokens). Agentic coding tools need tool calling and much larger context; expect truncated or failed agent tasks. Chat and short edits work best."),
        < 131_072 => (true, $"Context window {contextTokens:N0} tokens. Fine for chat; agentic tools also need tool calling, and GitHub recommends at least 128k tokens for Copilot CLI."),
        _ => (false, $"Context window {contextTokens:N0} tokens. Agentic tools also need a model that supports tool calling."),
    };
}

internal static class ContinueMarkers
{
    public const string Start = "# >>> InferBridge (local): managed by InferBridge, Undo in InferBridge restores your backup";
    public const string End = "# <<< InferBridge (local)";
}

/// <summary>
/// Line-based merge of one managed model into Continue's config.yaml. No YAML
/// library ships with the app, so the merge only touches a recognised safe
/// shape and refuses anything else instead of risking the user's file.
/// </summary>
internal static class ContinueConfigMerge
{
    private static readonly Regex TopLevelLine = new(
        @"^(%YAML\b.*|---(\s.*)?|([A-Za-z_][\w.\-]*|""[^""]*""|'[^']*')\s*:(\s.*)?|- .*|-)$", RegexOptions.CultureInvariant);
    private static readonly Regex ModelsKey = new(@"^models\s*:(?<rest>.*)$", RegexOptions.CultureInvariant);
    private static readonly Regex UnmanagedName = new(
        @"^\s*-?\s*name\s*:\s*[""']?InferBridge \(local\)[""']?\s*(#.*)?$", RegexOptions.CultureInvariant);

    public const int MaximumBytes = 1024 * 1024;

    public static string Merge(string? existing, ToolConnectionValues values)
    {
        values = values.Validated();
        if (string.IsNullOrWhiteSpace(existing))
            return "name: Local Config\nversion: 1.0.0\nschema: v1\nmodels:\n"
                + string.Join("\n", ToolSnippets.ContinueEntry(values)) + "\n";

        string newline = existing.Contains("\r\n", StringComparison.Ordinal) ? "\r\n" : "\n";
        var lines = existing.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n').ToList();
        bool trailingNewline = lines.Count > 0 && lines[^1].Length == 0;
        if (trailingNewline) lines.RemoveAt(lines.Count - 1);
        Validate(lines);

        int start = lines.FindIndex(l => l.Trim() == ContinueMarkers.Start);
        int startCount = lines.Count(l => l.Trim() == ContinueMarkers.Start);
        int endCount = lines.Count(l => l.Trim() == ContinueMarkers.End);
        if (startCount != endCount || startCount > 1)
            throw Refuse("InferBridge\u2019s markers in config.yaml are incomplete or duplicated.");
        if (startCount == 1)
        {
            int end = lines.FindIndex(l => l.Trim() == ContinueMarkers.End);
            if (end < start) throw Refuse("InferBridge\u2019s markers in config.yaml are out of order.");
            string indent = lines[start][..(lines[start].Length - lines[start].TrimStart().Length)];
            lines.RemoveRange(start, end - start + 1);
            lines.InsertRange(start, ToolSnippets.ContinueEntry(values, indent));
            return Join(lines, newline);
        }

        if (lines.Any(l => UnmanagedName.IsMatch(l)))
            throw Refuse("config.yaml already has a hand-written model named \u201cInferBridge (local)\u201d. Rename or remove it, then try again.");

        var keys = lines.Select((line, index) => (line, index))
            .Where(item => ModelsKey.IsMatch(item.line)).ToList();
        if (keys.Count > 1) throw Refuse("config.yaml defines models more than once.");
        if (keys.Count == 0)
        {
            lines.Add("models:");
            lines.AddRange(ToolSnippets.ContinueEntry(values));
            return Join(lines, newline);
        }

        int keyIndex = keys[0].index;
        string rest = ModelsKey.Match(keys[0].line).Groups["rest"].Value.Trim();
        if (rest.Length > 0 && !rest.StartsWith('#'))
        {
            if (!Regex.IsMatch(rest, @"^\[\s*\]\s*(#.*)?$"))
                throw Refuse("config.yaml uses an inline models list. Convert it to one model per line, then try again.");
            lines[keyIndex] = "models:";
        }
        string itemIndent = "  ";
        for (int i = keyIndex + 1; i < lines.Count; i++)
        {
            string trimmed = lines[i].TrimStart();
            if (trimmed.Length == 0 || trimmed.StartsWith('#')) continue;
            if (trimmed.StartsWith("- ", StringComparison.Ordinal) || trimmed == "-")
                itemIndent = lines[i][..(lines[i].Length - trimmed.Length)];
            else if (lines[i].Length == trimmed.Length)
                break; // Next top-level key: models was empty.
            else
                throw Refuse("The models section of config.yaml is not a list.");
            break;
        }
        lines.InsertRange(keyIndex + 1, ToolSnippets.ContinueEntry(values, itemIndent));
        return Join(lines, newline);
    }

    private static string Join(List<string> lines, string newline) =>
        string.Join(newline, lines) + newline;

    private static ToolSetupException Refuse(string reason) =>
        new(reason + " InferBridge did not change the file.");

    private static void Validate(List<string> lines)
    {
        bool sawContent = false;
        for (int i = 0; i < lines.Count; i++)
        {
            string line = lines[i];
            if (line.Contains('\0'))
                throw Refuse("config.yaml is not a text file.");
            string trimmed = line.TrimStart(' ');
            if (trimmed.StartsWith('\t'))
                throw Refuse($"config.yaml line {i + 1} is indented with a tab, which YAML does not allow.");
            if (trimmed.Length == 0 || trimmed.StartsWith('#')) continue;
            if (line.Length != trimmed.Length) { sawContent = true; continue; }
            if (trimmed is "..." || (trimmed.StartsWith("---", StringComparison.Ordinal) && sawContent))
                throw Refuse("config.yaml contains more than one YAML document.");
            if (!TopLevelLine.IsMatch(trimmed.TrimEnd()))
                throw Refuse($"config.yaml line {i + 1} is not valid YAML for Continue.");
            if (!sawContent && trimmed.StartsWith('-') && !trimmed.StartsWith("---", StringComparison.Ordinal))
                throw Refuse("config.yaml must be a mapping of settings, not a list.");
            if (!trimmed.StartsWith("%", StringComparison.Ordinal) && !trimmed.StartsWith("---", StringComparison.Ordinal))
                sawContent = true;
        }
    }
}

internal sealed record ContinuePlan(string Path, bool Existed, string? OriginalText, string NewText)
{
    public bool Unchanged => OriginalText == NewText;

    public string Preview => (Existed ? "Update " : "Create ") + Path + "\n\n"
        + string.Join("\n", NewText.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n')
            .SkipWhile(l => l.Trim() != ContinueMarkers.Start)
            .TakeWhile(l => l.Trim() != ContinueMarkers.End)
            .Append(ContinueMarkers.End));
}

internal sealed record ContinueUndo(string Path, string? BackupPath, string WrittenText);

/// <summary>Backup-first, compare-before-write file operations for Continue.</summary>
internal sealed class ContinueConfigFile(string path)
{
    public string Path { get; } = path;

    public static string DefaultPath => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".continue", "config.yaml");

    private string? Read()
    {
        if (!File.Exists(Path)) return null;
        var info = new FileInfo(Path);
        if (info.Length > ContinueConfigMerge.MaximumBytes)
            throw new ToolSetupException("config.yaml is larger than 1 MB. InferBridge did not change it.");
        return File.ReadAllText(Path, Encoding.UTF8);
    }

    public ContinuePlan Plan(ToolConnectionValues values)
    {
        string? current = Read();
        return new ContinuePlan(Path, current is not null, current, ContinueConfigMerge.Merge(current, values));
    }

    /// <summary>Writes only if the file still matches the previewed original.</summary>
    public ContinueUndo Apply(ContinuePlan plan)
    {
        if (Read() != plan.OriginalText)
            throw new ToolSetupException("config.yaml changed after the preview. Preview the change again.");
        string? backup = null;
        if (plan.Existed)
        {
            backup = Path + ".inferbridge-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff",
                System.Globalization.CultureInfo.InvariantCulture) + ".bak";
            File.Copy(Path, backup, overwrite: false);
        }
        else
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        }
        WriteAtomic(plan.NewText);
        return new ContinueUndo(Path, backup, plan.NewText);
    }

    /// <summary>Restores the backup (or removes a file InferBridge created) unless the user edited it since.</summary>
    public void Undo(ContinueUndo undo)
    {
        if (Read() != undo.WrittenText)
            throw new ToolSetupException("config.yaml changed after InferBridge wrote it, so Undo did not overwrite your edits."
                + (undo.BackupPath is null ? "" : " Your original is still at " + undo.BackupPath));
        if (undo.BackupPath is null)
        {
            File.Delete(Path);
            return;
        }
        if (!File.Exists(undo.BackupPath))
            throw new ToolSetupException("The backup file is missing, so Undo did not change config.yaml.");
        WriteAtomic(File.ReadAllText(undo.BackupPath, Encoding.UTF8));
    }

    private void WriteAtomic(string text)
    {
        string temp = Path + ".inferbridge-" + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temp, text, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            File.Move(temp, Path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temp)) File.Delete(temp);
        }
    }
}

/// <summary>User-scope environment access, behind an interface so tests never touch Windows.</summary>
internal interface IUserEnvironment
{
    string? Get(string name);
    void Set(string name, string? value);
}

internal sealed class WindowsUserEnvironment : IUserEnvironment
{
    // EnvironmentVariableTarget.User writes HKCU\Environment and broadcasts
    // WM_SETTINGCHANGE, so new terminals see the change.
    public string? Get(string name) => Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User);

    public void Set(string name, string? value) =>
        Environment.SetEnvironmentVariable(name, value, EnvironmentVariableTarget.User);
}

internal sealed record EnvChange(string Name, string? Before, string After);

internal sealed record EnvUndo(IReadOnlyList<EnvChange> Changes);

/// <summary>Copilot CLI BYOK variables, as documented by GitHub.</summary>
internal static class CopilotCliEnvironment
{
    public static IReadOnlyList<(string Name, string Value)> Plan(ToolConnectionValues v)
    {
        v = v.Validated();
        var plan = new List<(string, string)>
        {
            ("COPILOT_PROVIDER_BASE_URL", v.BaseUrl),
            ("COPILOT_PROVIDER_TYPE", "openai"),
            ("COPILOT_MODEL", v.ModelId),
        };
        if (v.MaxInputTokens is int input) plan.Add(("COPILOT_PROVIDER_MAX_PROMPT_TOKENS", input.ToString(System.Globalization.CultureInfo.InvariantCulture)));
        if (v.MaxOutputTokens is int output) plan.Add(("COPILOT_PROVIDER_MAX_OUTPUT_TOKENS", output.ToString(System.Globalization.CultureInfo.InvariantCulture)));
        return plan;
    }

    public static IReadOnlyList<EnvChange> Preview(IUserEnvironment env, ToolConnectionValues v) =>
        Plan(v).Select(item => new EnvChange(item.Name, env.Get(item.Name), item.Value)).ToList();

    public static EnvUndo Apply(IUserEnvironment env, ToolConnectionValues v)
    {
        var changes = Preview(env, v);
        var applied = new List<EnvChange>();
        try
        {
            foreach (var change in changes)
            {
                if (change.Before != change.After) env.Set(change.Name, change.After);
                applied.Add(change);
            }
        }
        catch
        {
            // Never leave a half-configured CLI: roll back what was already set.
            Undo(env, new EnvUndo(applied));
            throw;
        }
        return new EnvUndo(changes);
    }

    /// <summary>Restores previous values; returns names left alone because the user changed them since.</summary>
    public static IReadOnlyList<string> Undo(IUserEnvironment env, EnvUndo undo)
    {
        var skipped = new List<string>();
        foreach (var change in undo.Changes)
        {
            if (env.Get(change.Name) != change.After) { skipped.Add(change.Name); continue; }
            if (change.Before != change.After) env.Set(change.Name, change.Before);
        }
        return skipped;
    }
}
