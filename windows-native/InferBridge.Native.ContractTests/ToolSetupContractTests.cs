using System.Text.Json;

namespace InferBridge.Native;

/// <summary>"Connect a tool": Continue merge, Copilot CLI env plan/undo, per-client snippets.</summary>
internal static class ToolSetupContractTests
{
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException("Tool setup: " + message);
    }

    private static void Refuses(Action action, string label)
    {
        try { action(); }
        catch (ToolSetupException) { return; }
        throw new InvalidOperationException("Tool setup: expected refusal for " + label);
    }

    private sealed class FakeEnvironment : IUserEnvironment
    {
        public readonly Dictionary<string, string> Values = new(StringComparer.Ordinal);
        public string? FailOn;

        public string? Get(string name) => Values.TryGetValue(name, out var value) ? value : null;

        public void Set(string name, string? value)
        {
            if (name == FailOn) throw new UnauthorizedAccessException("denied");
            if (value is null) Values.Remove(name); else Values[name] = value;
        }
    }

    private static readonly ToolConnectionValues Open =
        new("http://127.0.0.1:19437/v1", "qwen-coder", false, 32768, 4096);

    private static readonly ToolConnectionValues Secured =
        new("http://127.0.0.1:19437/v1", "qwen-coder", true, null, null);

    public static void Run(string root)
    {
        Snippets();
        Merge();
        Files(Path.Combine(root, "tools"));
        EnvironmentPlan();
        Console.WriteLine("Native tool setup contract: all cases passed.");
    }

    private static void Snippets()
    {
        Check(Open.KeyPlaceholder == "not-required" && Secured.KeyPlaceholder == "YOUR_INFERBRIDGE_API_KEY",
            "placeholder follows engine authentication");
        Check(Open.MaxInputTokens == 28672, "input budget leaves room for output");
        foreach (var client in ToolSnippets.Clients)
        {
            var values = ToolSnippets.Values(client.Id, Open);
            Check(values.Any(v => v.Key == "Base URL" && v.Value == Open.BaseUrl), client.Id + " base URL");
            Check(values.Any(v => v.Key == "Model ID" && v.Value == "qwen-coder"), client.Id + " model");
            Check(values.Any(v => v.Key == "API key" && v.Value == "not-required"), client.Id + " key");
            Check(ToolSnippets.Steps(client.Id, Open).Count >= 3, client.Id + " numbered steps");
            Check(client.DocUrl.StartsWith("https://", StringComparison.Ordinal), client.Id + " doc URL");
        }
        Check(ToolSnippets.Clients.Count(c => c.Kind == ToolSetupKind.OneClick) == 2, "only two one-click clients");

        string yaml = ToolSnippets.ContinueSnippet(Secured);
        Check(yaml.Contains("provider: openai") && yaml.Contains("apiBase: \"http://127.0.0.1:19437/v1\"")
            && yaml.Contains("model: \"qwen-coder\"") && yaml.Contains("apiKey: \"YOUR_INFERBRIDGE_API_KEY\""),
            "Continue entry values");
        Check(!yaml.Contains("contextLength"), "unknown context is not invented");
        Check(ToolSnippets.ContinueSnippet(Open).Contains("contextLength: 32768"), "known context is written");

        string ps = ToolSnippets.CopilotCliPowerShell(Secured);
        Check(ps.Contains("'COPILOT_PROVIDER_BASE_URL', 'http://127.0.0.1:19437/v1', 'User'")
            && ps.Contains("'COPILOT_MODEL', 'qwen-coder'") && ps.Contains("'COPILOT_PROVIDER_TYPE', 'openai'"),
            "Copilot CLI variables");
        Check(ps.Contains("$env:COPILOT_PROVIDER_API_KEY") && !ps.Contains("SetEnvironmentVariable('COPILOT_PROVIDER_API_KEY'"),
            "API key is never persisted");
        string quoted = ToolSnippets.CopilotCliPowerShell(Open with { ModelId = "o'brien" });
        Check(quoted.Contains("'o''brien'"), "PowerShell single quotes are escaped");

        using (var custom = JsonDocument.Parse(ToolSnippets.VsCodeCustomEndpoint(Open)))
        {
            var provider = custom.RootElement[0];
            Check(provider.GetProperty("vendor").GetString() == "customendpoint", "Custom Endpoint vendor");
            var model = provider.GetProperty("models")[0];
            Check(model.GetProperty("url").GetString() == "http://127.0.0.1:19437/v1/chat/completions"
                && model.GetProperty("id").GetString() == "qwen-coder"
                && model.GetProperty("maxInputTokens").GetInt32() == 28672
                && model.GetProperty("maxOutputTokens").GetInt32() == 4096, "Custom Endpoint model");
        }
        using (var secured = JsonDocument.Parse(ToolSnippets.VsCodeCustomEndpoint(Secured)))
            Check(secured.RootElement[0].GetProperty("apiKey").GetString() == "${input:inferbridgeApiKey}",
                "VS Code key comes from secret input, not a file");
        string legacy = ToolSnippets.VsCodeDeprecatedCustomOaiModels(Open);
        Check(legacy.StartsWith("// Deprecated", StringComparison.Ordinal)
            && legacy.Contains("\"github.copilot.chat.customOAIModels\"")
            && legacy.Contains("\"requiresAPIKey\": false"), "deprecated settings snippet is labelled");

        Check(ToolSnippets.AgenticNote(null).Warning && ToolSnippets.AgenticNote(null).Text.Contains("unknown"),
            "unknown context flagged");
        Check(ToolSnippets.AgenticNote(8192).Text.Contains("Small context"), "small context flagged");
        Check(ToolSnippets.AgenticNote(65536).Text.Contains("128k"), "Copilot CLI recommendation shown");
        Check(!ToolSnippets.AgenticNote(200_000).Warning, "large context is not a warning");

        Refuses(() => (Open with { BaseUrl = "http://192.168.1.5:8000/v1" }).Validated(), "non-loopback URL");
        Refuses(() => (Open with { BaseUrl = "http://127.0.0.1:8000/v1/../x" }).Validated(), "path trick");
        Refuses(() => (Open with { ModelId = "a\nb" }).Validated(), "control characters in model");
        Refuses(() => (Open with { MaxOutputTokens = 40000 }).Validated(), "output above context");
    }

    private static void Merge()
    {
        string fresh = ContinueConfigMerge.Merge(null, Open);
        Check(fresh.StartsWith("name: Local Config\nversion: 1.0.0\nschema: v1\nmodels:\n", StringComparison.Ordinal),
            "fresh file has required top-level keys");
        Check(ContinueConfigMerge.Merge(fresh, Open) == fresh, "re-run is idempotent");

        const string existing =
            "name: My Config\nversion: 1.0.0\nschema: v1\nmodels:\n  - name: GPT-4o\n    provider: openai\n    model: gpt-4o\n"
            + "rules:\n  - Give concise responses\n";
        string merged = ContinueConfigMerge.Merge(existing, Open);
        Check(merged.Contains("  - name: GPT-4o\n    provider: openai\n    model: gpt-4o\n"), "other models kept");
        Check(merged.Contains("rules:\n  - Give concise responses\n"), "other sections kept");
        Check(merged.IndexOf(ContinueMarkers.Start, StringComparison.Ordinal)
            < merged.IndexOf("GPT-4o", StringComparison.Ordinal), "entry inserted inside models");
        Check(ContinueConfigMerge.Merge(merged, Open) == merged, "existing file re-run is idempotent");
        string updated = ContinueConfigMerge.Merge(merged, Open with { ModelId = "other-model" });
        Check(updated.Contains("model: \"other-model\"") && !updated.Contains("model: \"qwen-coder\"")
            && updated.Contains("GPT-4o"), "re-run updates only our own entry");
        Check(updated.Split(ContinueMarkers.Start).Length == 2, "never duplicates the entry");

        string flush = ContinueConfigMerge.Merge("name: c\nversion: 1\nschema: v1\nmodels:\n- name: a\n  provider: ollama\n", Open);
        Check(flush.Contains("\n" + ContinueMarkers.Start + "\n- name: \"InferBridge (local)\""), "matches column-0 list style");
        string none = ContinueConfigMerge.Merge("name: c\nversion: 1\nschema: v1\n", Open);
        Check(none.EndsWith("models:\n  " + ContinueMarkers.Start + "\n" + string.Join("\n", ToolSnippets.ContinueEntry(Open).Skip(1)) + "\n",
            StringComparison.Ordinal), "models section appended when missing");
        string empty = ContinueConfigMerge.Merge("name: c\nversion: 1\nschema: v1\nmodels: []\nrules: []\n", Open);
        Check(empty.Contains("models:\n  " + ContinueMarkers.Start) && empty.Contains("rules: []"), "empty inline list replaced");
        string crlf = ContinueConfigMerge.Merge("name: c\r\nversion: 1\r\nschema: v1\r\n", Open);
        Check(crlf.Contains("\r\nmodels:\r\n") && !crlf.Replace("\r\n", "").Contains('\n'), "CRLF preserved");

        Refuses(() => ContinueConfigMerge.Merge("name: c\nmodels: [ {name: a} ]\n", Open), "flow list");
        Refuses(() => ContinueConfigMerge.Merge("name: c\nmodels:\n\t- name: a\n", Open), "tab indentation");
        Refuses(() => ContinueConfigMerge.Merge("name: c\n<<<<<<< HEAD\nmodels:\n", Open), "merge conflict");
        Refuses(() => ContinueConfigMerge.Merge("- name: a\n", Open), "root list");
        Refuses(() => ContinueConfigMerge.Merge("name: c\nmodels:\n  a: b\n", Open), "models mapping");
        Refuses(() => ContinueConfigMerge.Merge("models:\n  - name: a\nmodels:\n  - name: b\n", Open), "duplicate key");
        Refuses(() => ContinueConfigMerge.Merge("name: c\nmodels:\n  - name: InferBridge (local)\n", Open), "hand-written entry");
        Refuses(() => ContinueConfigMerge.Merge("name: c\nmodels:\n  " + ContinueMarkers.Start + "\n", Open), "half marker");
        Refuses(() => ContinueConfigMerge.Merge("name: c\n---\nname: d\n", Open), "multi-document");
    }

    private static void Files(string directory)
    {
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, ".continue", "config.yaml");
        var file = new ContinueConfigFile(path);

        var create = file.Plan(Open);
        Check(!create.Existed && create.Preview.StartsWith("Create ", StringComparison.Ordinal), "fresh plan");
        var created = file.Apply(create);
        Check(File.Exists(path) && created.BackupPath is null, "fresh file written without backup");
        Check(file.Plan(Open).Unchanged, "second plan is a no-op");
        file.Undo(created);
        Check(!File.Exists(path), "undo removes the file InferBridge created");

        const string original = "name: Mine\nversion: 1.0.0\nschema: v1\nmodels:\n  - name: Local\n    provider: ollama\n    model: llama3\n";
        File.WriteAllText(path, original);
        var plan = file.Plan(Open);
        Check(plan.Existed && plan.Preview.Contains("InferBridge (local)"), "preview shows our entry");
        var undo = file.Apply(plan);
        Check(undo.BackupPath is not null && File.ReadAllText(undo.BackupPath) == original, "backup taken first");
        Check(File.ReadAllText(path).Contains("model: llama3"), "merge kept user model");
        file.Undo(undo);
        Check(File.ReadAllText(path) == original, "undo restores the backup exactly");

        // Undo never overwrites edits made after InferBridge wrote the file.
        var again = file.Apply(file.Plan(Open));
        File.AppendAllText(path, "# user edit\n");
        Refuses(() => file.Undo(again), "edited after write");
        Check(File.ReadAllText(path).EndsWith("# user edit\n", StringComparison.Ordinal), "user edit kept");

        // A stale preview is not written.
        File.WriteAllText(path, original);
        var stale = file.Plan(Open);
        File.WriteAllText(path, original + "rules: []\n");
        Refuses(() => file.Apply(stale), "changed since preview");

        // Malformed files are refused and left byte-for-byte untouched.
        const string malformed = "name: c\nmodels: [ {name: a} ]\n";
        File.WriteAllText(path, malformed);
        Refuses(() => file.Plan(Open), "malformed file");
        Check(File.ReadAllText(path) == malformed, "malformed file not clobbered");
        Check(Directory.GetFiles(Path.GetDirectoryName(path)!, "*.tmp").Length == 0, "no temp files left");
    }

    private static void EnvironmentPlan()
    {
        var env = new FakeEnvironment();
        env.Values["COPILOT_MODEL"] = "gpt-4o";
        var preview = CopilotCliEnvironment.Preview(env, Open);
        Check(preview.Single(c => c.Name == "COPILOT_MODEL").Before == "gpt-4o", "preview shows prior value");
        Check(preview.Any(c => c.Name == "COPILOT_PROVIDER_BASE_URL" && c.After == Open.BaseUrl), "base URL planned");
        Check(preview.Any(c => c.Name == "COPILOT_PROVIDER_MAX_PROMPT_TOKENS" && c.After == "28672"), "prompt budget planned");
        Check(!CopilotCliEnvironment.Plan(Secured).Any(p => p.Name.Contains("KEY", StringComparison.Ordinal)),
            "no key variable is ever planned");
        Check(!CopilotCliEnvironment.Plan(Secured).Any(p => p.Name.Contains("MAX_", StringComparison.Ordinal)),
            "unknown limits are not invented");

        var undo = CopilotCliEnvironment.Apply(env, Open);
        Check(env.Get("COPILOT_MODEL") == "qwen-coder" && env.Get("COPILOT_PROVIDER_TYPE") == "openai", "applied");
        Check(CopilotCliEnvironment.Undo(env, undo).Count == 0, "clean undo");
        Check(env.Get("COPILOT_MODEL") == "gpt-4o" && env.Get("COPILOT_PROVIDER_BASE_URL") is null,
            "undo restores prior values and removes new ones");

        undo = CopilotCliEnvironment.Apply(env, Open);
        env.Values["COPILOT_MODEL"] = "user-choice";
        var skipped = CopilotCliEnvironment.Undo(env, undo);
        Check(skipped.SequenceEqual(new[] { "COPILOT_MODEL" }) && env.Get("COPILOT_MODEL") == "user-choice",
            "undo keeps variables the user changed since");

        var failing = new FakeEnvironment { FailOn = "COPILOT_MODEL" };
        try { CopilotCliEnvironment.Apply(failing, Open); throw new InvalidOperationException("expected failure"); }
        catch (UnauthorizedAccessException) { }
        Check(failing.Values.Count == 0, "partial apply is rolled back");
    }
}
