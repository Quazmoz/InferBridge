using System.Text.Json;
using System.Text.RegularExpressions;

namespace InferBridge.Native;

internal sealed record ModelDefinitionImportResult(
    IReadOnlyList<string> Added, IReadOnlyList<string> Updated, IReadOnlyList<string> Unchanged);

internal sealed record UnregisteredLocalModel(
    string DirectoryName, string SuggestedModelId, string SuggestedName, long SizeBytes);

/// <summary>
/// A local OpenVINO IR registration. DirectoryName adopts a folder already in the
/// managed models directory in place; otherwise SourcePath is copied in.
/// </summary>
internal sealed record LocalModelRegistration(
    string ModelId, string Name, string Backend, string WeightFormat, string Device,
    int MaxContextTokens, string? DirectoryName = null, string? SourcePath = null);

internal sealed record LocalModelRegistrationResult(string ModelId, long SizeBytes, bool ManagedInPlace);

// Verified Model Library definition exchange and local-model adoption, using the
// same protected /v1/model-library endpoints and limits as the web UI. Imports
// never request overwrite; the engine keeps refusing active or conflicting models.
internal sealed partial class BackendClient
{
    internal const int MaximumDefinitionFileBytes = 1_000_000; // Web UI and engine manifest limit.
    private const int MaximumDefinitionsPerImport = 50;
    private static readonly Regex ModelIdPattern = new(@"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        RegexOptions.CultureInvariant);
    private static readonly string[] LocalModelBackends =
        { "openvino-genai", "openvino-vlm", "openvino-embeddings" };
    private static readonly string[] LocalModelFormats = { "fp16", "int8", "int4" };
    private static readonly string[] LocalModelDevices = { "CPU", "GPU", "NPU", "AUTO" };

    internal static bool IsValidModelId(string? value) =>
        value is not null && ModelIdPattern.IsMatch(value);

    /// <summary>Validate a definitions file locally before it reaches the engine.</summary>
    internal static JsonElement ParseDefinitionFile(byte[] bytes, out int count)
    {
        ArgumentNullException.ThrowIfNull(bytes);
        if (bytes.Length is < 2 or > MaximumDefinitionFileBytes)
            throw new InvalidDataException("Definition file is empty or exceeds 1 MB.");
        JsonElement root;
        try
        {
            using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 24 });
            root = document.RootElement.Clone();
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("The definition file is not valid JSON.", error);
        }
        // Same shape the engine accepts: {"models": {...}} or a bare id → definition map.
        JsonElement models = root.ValueKind == JsonValueKind.Object
            && root.TryGetProperty("models", out var wrapped) && wrapped.ValueKind == JsonValueKind.Object
            ? wrapped : root;
        if (models.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("Definition file must contain a JSON object of model definitions.");
        count = 0;
        foreach (var entry in models.EnumerateObject())
        {
            if (entry.Value.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException("Definition '" + Bounded(entry.Name) + "' must be an object.");
            // Remote model code stays a deliberate, web-only opt-in (see NATIVE_FEATURE_PARITY.md).
            if (entry.Value.TryGetProperty("trust_remote_code", out var remote)
                && remote.ValueKind is not (JsonValueKind.False or JsonValueKind.Null))
                throw new InvalidDataException("Definition '" + Bounded(entry.Name)
                    + "' enables trust_remote_code, which runs code from the model repository. Review and import it in the browser interface instead.");
            count++;
        }
        if (count is < 1 or > MaximumDefinitionsPerImport)
            throw new InvalidDataException("Definition import must contain between 1 and 50 models.");
        return root;
    }

    private static string Bounded(string text) => text.Length <= 60 ? text : text[..60] + "…";

    public async Task<string> ExportModelDefinitionsAsync(
        bool includeAll, string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/model-library/export?include_all="
            + (includeAll ? "true" : "false"), apiKey, cancellationToken, TimeSpan.FromSeconds(30),
            MaximumDefinitionFileBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("schema_version", out var schema)
            || schema.ValueKind != JsonValueKind.Number || !schema.TryGetInt32(out int version) || version != 1
            || !root.TryGetProperty("models", out var models) || models.ValueKind != JsonValueKind.Object)
            throw new JsonException("The engine returned an unrecognized model definition export.");
        foreach (var entry in models.EnumerateObject())
            if (!IsValidModelId(entry.Name) || entry.Value.ValueKind != JsonValueKind.Object)
                throw new JsonException("The engine returned an invalid model definition.");
        return JsonSerializer.Serialize(root, new JsonSerializerOptions { WriteIndented = true }) + "\n";
    }

    /// <summary>
    /// Web "Refresh" in Model Library: the engine downloads the official, signed-off
    /// model-library manifest (trusted GitHub URL, 1 MB cap) and merges new verified
    /// definitions. No model weights are downloaded; user definitions are never replaced.
    /// </summary>
    public async Task<ModelDefinitionImportResult> RefreshOfficialModelLibraryAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        JsonDocument document;
        try
        {
            document = await PostJsonAsync(origin + "/v1/model-library/refresh", "{}", apiKey,
                cancellationToken, TimeSpan.FromMinutes(2), 8 * 1024 * 1024);
        }
        catch (System.Net.Http.HttpRequestException error) when (error.StatusCode == System.Net.HttpStatusCode.BadGateway)
        {
            // The engine answers 502 when GitHub is unreachable or the manifest fails validation.
            throw new EngineRequestException(
                "The official verified model list could not be downloaded or failed validation. Your library was not changed.");
        }
        using var _ = document;
        var root = document.RootElement;
        if (ReadString(root, "status") != "refreshed"
            || !root.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object)
            throw new JsonException("The engine did not confirm the model library refresh.");
        IReadOnlyList<string> Ids(string key)
        {
            if (!result.TryGetProperty(key, out var list)) return Array.Empty<string>();
            if (list.ValueKind != JsonValueKind.Array || list.GetArrayLength() > 1000)
                throw new JsonException("The engine returned an invalid refresh summary.");
            return list.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String
                && IsValidModelId(item.GetString()) ? item.GetString()!
                : throw new JsonException("The engine returned an invalid model id.")).ToList();
        }
        // "conflicts" are user definitions the official list did not replace.
        return new ModelDefinitionImportResult(Ids("added"), Ids("updated"), Ids("conflicts"));
    }

    public async Task<ModelDefinitionImportResult> ImportModelDefinitionsAsync(
        byte[] file, string? apiKey, CancellationToken cancellationToken)
    {
        JsonElement payload = ParseDefinitionFile(file, out _);
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await PostJsonAsync(origin + "/v1/model-library/import-definitions",
            JsonSerializer.Serialize(new { payload, overwrite = false }), apiKey, cancellationToken,
            TimeSpan.FromMinutes(2), MaximumJsonBytes);
        var root = document.RootElement;
        if (ReadString(root, "status") != "imported")
            throw new JsonException("The engine did not confirm the definition import.");
        IReadOnlyList<string> Ids(string key)
        {
            if (!root.TryGetProperty(key, out var list) || list.ValueKind != JsonValueKind.Array
                || list.GetArrayLength() > MaximumDefinitionsPerImport)
                throw new JsonException("The engine returned an invalid import summary.");
            var ids = new List<string>();
            foreach (var item in list.EnumerateArray())
                ids.Add(item.ValueKind == JsonValueKind.String && IsValidModelId(item.GetString())
                    ? item.GetString()! : throw new JsonException("The engine returned an invalid model id."));
            return ids;
        }
        var result = new ModelDefinitionImportResult(Ids("added"), Ids("updated"), Ids("unchanged"));
        // overwrite=false: a reported replacement would mean the safety contract changed.
        if (result.Updated.Count > 0)
            throw new JsonException("The engine reported replaced definitions for a non-overwrite import.");
        return result;
    }

    public async Task<IReadOnlyList<UnregisteredLocalModel>> GetUnregisteredLocalModelsAsync(
        string? apiKey, CancellationToken cancellationToken)
    {
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        using var document = await GetJsonAsync(origin + "/v1/model-library/unregistered-managed",
            apiKey, cancellationToken, TimeSpan.FromMinutes(2));
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("models", out var models) || models.ValueKind != JsonValueKind.Array
            || models.GetArrayLength() > 500
            || !root.TryGetProperty("count", out var count) || count.ValueKind != JsonValueKind.Number
            || !count.TryGetInt32(out int total) || total != models.GetArrayLength())
            throw new JsonException("The engine returned an invalid local model scan.");
        var found = new List<UnregisteredLocalModel>();
        foreach (var model in models.EnumerateArray())
        {
            string directory = ReadString(model, "directory_name");
            string id = ReadString(model, "suggested_model_id");
            string name = ReadString(model, "suggested_name");
            if (!IsDirectChildName(directory) || !IsValidModelId(id)
                || name.Length is < 1 or > 255 || name.Any(char.IsControl)
                || !model.TryGetProperty("size_bytes", out var size) || size.ValueKind != JsonValueKind.Number
                || !size.TryGetInt64(out long bytes) || bytes < 0)
                throw new JsonException("The engine returned an invalid local model entry.");
            found.Add(new UnregisteredLocalModel(directory, id, name[..Math.Min(name.Length, 160)], bytes));
        }
        return found;
    }

    private static bool IsDirectChildName(string? name) =>
        name is { Length: >= 1 and <= 255 } && name is not ("." or "..")
        && !name.Contains('/') && !name.Contains('\\') && !name.Any(char.IsControl);

    // Windows drive or UNC paths, or a fully qualified path on the test host.
    private static bool IsAbsoluteLocalPath(string path) =>
        Path.IsPathFullyQualified(path)
        || (path.Length >= 3 && char.IsAsciiLetter(path[0]) && path[1] == ':' && path[2] is '\\' or '/')
        || path.StartsWith(@"\\", StringComparison.Ordinal);

    internal static void ValidateRegistration(LocalModelRegistration registration)
    {
        ArgumentNullException.ThrowIfNull(registration);
        if (!IsValidModelId(registration.ModelId))
            throw new ArgumentException("Model ID must start with a letter or digit and use only letters, digits, '.', '_' or '-' (max 128).");
        if (string.IsNullOrWhiteSpace(registration.Name) || registration.Name.Length > 160
            || registration.Name.Any(char.IsControl))
            throw new ArgumentException("Display name must contain 1–160 readable characters.");
        if (!LocalModelBackends.Contains(registration.Backend)
            || !LocalModelFormats.Contains(registration.WeightFormat)
            || !LocalModelDevices.Contains(registration.Device))
            throw new ArgumentException("Select a supported model type, weight format and device.");
        if (registration.MaxContextTokens is < 128 or > 262144)
            throw new ArgumentException("Maximum context must be between 128 and 262144 tokens.");
        bool adopt = registration.DirectoryName is not null;
        if (adopt == (registration.SourcePath is not null))
            throw new ArgumentException("Choose either a detected managed folder or a source directory.");
        if (adopt && !IsDirectChildName(registration.DirectoryName))
            throw new ArgumentException("Managed model folder must be one direct child directory.");
        if (!adopt && (registration.SourcePath!.Length > 1024 || registration.SourcePath.Any(char.IsControl)
            || !IsAbsoluteLocalPath(registration.SourcePath)))
            throw new ArgumentException("Enter the absolute directory that contains the OpenVINO IR files.");
    }

    public async Task<LocalModelRegistrationResult> RegisterLocalModelAsync(
        LocalModelRegistration registration, string? apiKey, CancellationToken cancellationToken)
    {
        ValidateRegistration(registration);
        bool adopt = registration.DirectoryName is not null;
        int output = registration.Backend == "openvino-embeddings"
            ? 0 : Math.Min(512, registration.MaxContextTokens - 1);
        // Identical field set to the web import form; trust_remote_code is never sent.
        string body = adopt
            ? JsonSerializer.Serialize(new
            {
                directory_name = registration.DirectoryName, model_id = registration.ModelId,
                name = registration.Name.Trim(), backend = registration.Backend,
                weight_format = registration.WeightFormat, recommended_device = registration.Device,
                max_context_len = registration.MaxContextTokens, max_output_tokens = output,
            })
            : JsonSerializer.Serialize(new
            {
                model_id = registration.ModelId, name = registration.Name.Trim(),
                source_path = registration.SourcePath, backend = registration.Backend,
                weight_format = registration.WeightFormat, recommended_device = registration.Device,
                max_context_len = registration.MaxContextTokens, max_output_tokens = output,
                overwrite = false,
            });
        string origin = await RequireVerifiedOriginAsync(apiKey, cancellationToken);
        // A copy of a multi-GB IR directory runs inside the request.
        using var document = await PostJsonAsync(origin + (adopt
                ? "/v1/model-library/adopt-managed" : "/v1/model-library/import-converted"),
            body, apiKey, cancellationToken, adopt ? TimeSpan.FromMinutes(5) : TimeSpan.FromMinutes(90),
            MaximumJsonBytes);
        var root = document.RootElement;
        if (ReadString(root, "status") != "imported" || ReadString(root, "model_id") != registration.ModelId
            || !root.TryGetProperty("size_bytes", out var size) || size.ValueKind != JsonValueKind.Number
            || !size.TryGetInt64(out long bytes) || bytes < 0)
            throw new JsonException("The engine did not confirm the local model registration.");
        bool inPlace = root.TryGetProperty("managed_in_place", out var managed)
            && managed.ValueKind == JsonValueKind.True;
        if (inPlace != adopt)
            throw new JsonException("The engine reported a different registration mode.");
        return new LocalModelRegistrationResult(registration.ModelId, bytes, inPlace);
    }
}
