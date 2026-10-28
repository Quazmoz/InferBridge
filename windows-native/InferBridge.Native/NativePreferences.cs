using System.Text.Json;

namespace InferBridge.Native;

/// <summary>
/// Small per-installation preferences the browser keeps in localStorage: the app
/// theme and the chat defaults (system prompt, temperature, top-p, maximum
/// tokens). Plain JSON beside the conversation history; never an API key. A
/// missing or invalid file means defaults, and the original file is left alone.
/// </summary>
internal sealed record NativePreferences(
    string Theme, string? DefaultSystemPrompt, GenerationOptions DefaultGeneration)
{
    public const int MaximumFileBytes = 64 * 1024;
    public static readonly string[] Themes = ["system", "light", "dark"];
    public static NativePreferences Default { get; } = new("system", null, GenerationOptions.Default);

    private sealed record Envelope(int Version, string? Theme, string? SystemPrompt,
        int? MaxTokens, double? Temperature, double? TopP);

    public static string PathForCurrentInstallation() =>
        Path.Combine(Path.GetDirectoryName(NativeDataPaths.ResolveMetadataFile())
            ?? throw new IOException("Invalid data root."), "native-preferences.json");

    /// <summary>Validated copy; throws <see cref="ArgumentException"/> for values the UI must refuse.</summary>
    public NativePreferences Validated()
    {
        if (!Themes.Contains(Theme, StringComparer.Ordinal))
            throw new ArgumentException("Theme must be system, light or dark.");
        DefaultGeneration.Validate();
        string? prompt = string.IsNullOrWhiteSpace(DefaultSystemPrompt) ? null : DefaultSystemPrompt;
        new ChatRequestContext(prompt, Array.Empty<ChatImage>()).Validate();
        return this with { DefaultSystemPrompt = prompt };
    }

    public static NativePreferences Load(string path)
    {
        if (!Path.IsPathFullyQualified(path)) throw new ArgumentException("Preferences path must be absolute.");
        try
        {
            if (!File.Exists(path)) return Default;
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return Default;
            var info = new FileInfo(path);
            if (info.Length is < 1 or > MaximumFileBytes) return Default;
            var envelope = JsonSerializer.Deserialize<Envelope>(File.ReadAllBytes(path),
                new JsonSerializerOptions { MaxDepth = 4 });
            if (envelope is null || envelope.Version != 1) return Default;
            var generation = new GenerationOptions(
                envelope.MaxTokens ?? GenerationOptions.Default.MaxTokens,
                envelope.Temperature ?? GenerationOptions.Default.Temperature,
                envelope.TopP ?? GenerationOptions.Default.TopP);
            return new NativePreferences(envelope.Theme ?? "system", envelope.SystemPrompt, generation)
                .Validated();
        }
        catch (Exception error) when (error is JsonException or ArgumentException
            or IOException or UnauthorizedAccessException or NotSupportedException)
        {
            return Default; // Preferences are a convenience; never block startup.
        }
    }

    public void Save(string path)
    {
        if (!Path.IsPathFullyQualified(path)) throw new ArgumentException("Preferences path must be absolute.");
        NativePreferences valid = Validated();
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(new Envelope(1, valid.Theme,
            valid.DefaultSystemPrompt, valid.DefaultGeneration.MaxTokens,
            valid.DefaultGeneration.Temperature, valid.DefaultGeneration.TopP));
        if (bytes.Length > MaximumFileBytes) throw new InvalidDataException("Preferences are too large.");
        string folder = Path.GetDirectoryName(path) ?? throw new IOException("Invalid preferences folder.");
        Directory.CreateDirectory(folder);
        if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0
            || (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0))
            throw new IOException("A linked preferences path is not supported.");
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew,
                FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                stream.Write(bytes);
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }
}
