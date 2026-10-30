using System.Globalization;

namespace InferBridge.Native;

/// <summary>One preset row in the shared context editor.</summary>
internal sealed record ContextPreset(int Tokens, bool Trial, bool Recommended, bool Current,
    bool ModelMax = false)
{
    public string Label
    {
        get
        {
            var tags = new List<string>();
            if (Current) tags.Add("current");
            if (Recommended) tags.Add("recommended");
            if (ModelMax) tags.Add("max for this model");
            if (Trial) tags.Add("trial");
            return ContextEditor.ShortLabel(Tokens) + " · " + ContextEditor.Format(Tokens) + " tokens"
                + (tags.Count == 0 ? "" : " (" + string.Join(", ", tags) + ")");
        }
    }
}

/// <summary>The outcome of checking one requested context value.</summary>
internal sealed record ContextChoice(bool Valid, int Tokens, bool Trial, string Message, double? KvEstimateGib);

/// <summary>What the editor's primary button does.</summary>
internal enum ContextEditorMode
{
    /// <summary>Change the saved value; applies now when loaded, next load otherwise.</summary>
    Edit,
    /// <summary>Choose or confirm the value as part of loading an unloaded model.</summary>
    LoadWith,
}

/// <summary>
/// The single source of context-editor rules for every native surface (model
/// cards, load step, chat pane and Settings). Mirrors
/// app/context_configuration.validate_context so the engine rarely has to refuse
/// a value; the engine still validates and stays authoritative.
/// </summary>
internal static class ContextEditor
{
    public const int Minimum = 128;
    public const int Maximum = 262144;
    public static readonly IReadOnlyList<int> StandardPresets =
        new[] { 2048, 4096, 8192, 16384, 32768, 65536, 131072 };

    public static string Format(int tokens) => tokens.ToString("N0", CultureInfo.InvariantCulture);

    public static string ShortLabel(int tokens) =>
        tokens >= 1024 && tokens % 1024 == 0 ? tokens / 1024 + "K" : Format(tokens);

    /// <summary>"Context: 8,192 tokens", the line every model card shows.</summary>
    public static string ChipText(int? tokens) =>
        tokens is int value ? "Context: " + Format(value) + " tokens" : "Context: not reported";

    /// <summary>Largest accepted value: the verified architecture limit, else the recommendation.</summary>
    public static int UpperBound(ContextConfiguration context) =>
        Math.Min(context.ArchitectureLimit ?? context.Recommended, Maximum);

    /// <summary>Smallest accepted value: at least 64 prompt tokens beyond the output reserve.</summary>
    public static int LowerBound(ContextConfiguration context) =>
        Math.Max(Minimum, context.OutputReserve + 65);

    public static bool IsTrial(ContextConfiguration context, int tokens) => tokens > context.Recommended;

    /// <summary>
    /// Common sizes inside the model's bounds plus "max for this model" (the
    /// verified config.json limit), in ascending order. CPU/GPU can use any of
    /// them; the small catalog defaults exist for the NPU's static shapes.
    /// </summary>
    public static IReadOnlyList<ContextPreset> Presets(ContextConfiguration context)
    {
        int lower = LowerBound(context), upper = UpperBound(context);
        var values = StandardPresets.Where(value => value >= lower && value <= upper).ToList();
        bool verifiedMax = context.ArchitectureLimit is not null && upper >= lower;
        if (verifiedMax && !values.Contains(upper)) values.Add(upper);
        return values.OrderBy(value => value)
            .Select(value => new ContextPreset(value, IsTrial(context, value),
                value == context.Recommended, value == context.Current,
                verifiedMax && value == upper))
            .ToArray();
    }

    // --- KV-cache precision (KV_CACHE_PRECISION; CPU/GPU only) -------------------

    /// <summary>Auto keeps OpenVINO's choice; the rest are sent to CPU/GPU only.</summary>
    public static readonly IReadOnlyList<string> KvPrecisions = new[] { "auto", "f16", "u8", "u4" };

    /// <summary>Estimated stored bytes per K/V value, scale overhead included.</summary>
    public static double KvBytes(string? precision) => precision switch
    {
        "u8" => 1.125,
        "u4" => 0.625,
        _ => 2.0, // f16, and Auto shown at the FP16 upper bound
    };

    public static string KvLabel(string value) => value switch
    {
        "f16" => "FP16 (no KV compression)",
        "u8" => "INT8 (u8, ~half of FP16)",
        "u4" => "INT4 (u4, ~a third of FP16)",
        _ => "Auto (OpenVINO default)",
    };

    private static string KvShort(string value) => value switch
    {
        "f16" => "FP16",
        "u8" => "INT8",
        "u4" => "INT4",
        _ => "Auto, FP16 upper bound",
    };

    /// <summary>The choices to show; explicit values are unavailable on an NPU load.</summary>
    public static IReadOnlyList<KvPrecisionOption> KvChoices(ContextConfiguration context) =>
        context.KvOptions is { Count: > 0 } rows
            ? rows
            : KvPrecisions.Select(value => new KvPrecisionOption(value, KvLabel(value),
                value == "auto" || context.KvApplies)).ToArray();

    /// <summary>
    /// Linear KV-cache estimate for any value at the chosen precision, scaled from
    /// the engine's own FP16 per-option estimates. Null when metadata is incomplete.
    /// </summary>
    public static double? EstimateKvGib(ContextConfiguration context, int tokens,
        string? precision = null)
    {
        var sample = context.Options.FirstOrDefault(option =>
            option.KvEstimateGib is double kv && kv > 0 && option.Tokens > 0);
        if (sample?.KvEstimateGib is not double estimate) return null;
        double bytes = context.KvApplies ? KvBytes(precision ?? context.KvPrecision) : 2.0;
        return Math.Round(estimate / sample.Tokens * tokens * bytes / 2.0, 3);
    }

    /// <summary>Checks a preset or custom value with friendly, actionable messages.</summary>
    public static ContextChoice Validate(ContextConfiguration context, double? requested,
        string? precision = null)
    {
        int lower = LowerBound(context), upper = UpperBound(context);
        if (requested is not double raw || !double.IsFinite(raw) || raw != Math.Floor(raw)
            || raw < Minimum || raw > Maximum)
            return new ContextChoice(false, 0, false,
                "Enter a whole number between " + Format(lower) + " and " + Format(upper) + ".", null);
        int tokens = (int)raw;
        if (tokens < lower)
            return new ContextChoice(false, tokens, false,
                "Leave room for the prompt: use at least " + Format(lower)
                + " tokens (output reserve is " + Format(context.OutputReserve) + ").", null);
        if (tokens > upper)
            return new ContextChoice(false, tokens, false, context.ArchitectureLimit is null
                ? "Without verified model metadata the maximum is the recommended "
                    + Format(upper) + " tokens."
                : "This model supports at most " + Format(upper) + " tokens.", null);
        bool trial = IsTrial(context, tokens);
        string kvPrecision = context.KvApplies ? precision ?? context.KvPrecision : "auto";
        double? kv = EstimateKvGib(context, tokens, kvPrecision);
        string message = Format(tokens - context.OutputReserve) + " prompt tokens + "
            + Format(context.OutputReserve) + " reserved output. "
            + (kv is double gib
                ? "KV cache ~" + gib.ToString("0.###", CultureInfo.InvariantCulture)
                    + " GiB (" + KvShort(kvPrecision) + " estimate, not measured device memory)."
                : "KV-cache estimate unavailable: model metadata is incomplete.")
            + (trial ? " Trial: above the recommended " + Format(context.Recommended)
                + " tokens and unverified on this hardware." : "");
        return new ContextChoice(true, tokens, trial, message, kv);
    }

    /// <summary>The facts line: current, recommended and the model's architecture maximum.</summary>
    public static string Facts(ContextConfiguration context) =>
        "Current " + Format(context.Current) + " · Recommended " + Format(context.Recommended)
        + " · Model maximum " + (context.ArchitectureLimit is int limit ? Format(limit) : "not verified");

    /// <summary>The primary button text for the editor.</summary>
    public static string PrimaryAction(ContextEditorMode mode, bool loaded) => mode switch
    {
        ContextEditorMode.LoadWith => "Load with this context",
        _ => loaded ? "Apply now (reloads engine, chat kept)" : "Save for next load",
    };

    /// <summary>When the value takes effect, said plainly under the controls.</summary>
    public static string WhenItApplies(ContextEditorMode mode, bool loaded) => mode switch
    {
        ContextEditorMode.LoadWith => "The model loads with this context and keeps it for next time.",
        _ => loaded
            ? "Apply now rebuilds the engine with the new context. Your chat history is kept."
            : "Used next time this model loads.",
    };

    /// <summary>The confirmation shown after a successful save.</summary>
    public static string SavedMessage(int tokens, bool applied) => applied
        ? "Applied " + Format(tokens) + " tokens. The engine was rebuilt; your chat history is kept."
        : "Saved " + Format(tokens) + " tokens. Used next time this model loads.";

    /// <summary>
    /// The busy line shown from the moment a value is applied until the engine
    /// confirms it: "Applying 16K context… rebuilding engine" for a loaded model,
    /// "Saving 16K context…" for an unloaded one. A KV-only change names the precision.
    /// </summary>
    public static string ApplyingText(int? tokens, bool loaded, string? kvChange = null)
    {
        string what = kvChange is { Length: > 0 } kv ? KvName(kv) + " KV cache"
            : tokens is int value ? ShortLabel(value) + " context" : "the new context";
        return loaded ? "Applying " + what + "… rebuilding engine" : "Saving " + what + "…";
    }

    /// <summary>The brief success title: "Context set to 16K".</summary>
    public static string AppliedText(int tokens, string? kvChange = null) =>
        kvChange is { Length: > 0 } kv ? "KV cache set to " + KvName(kv) + " · context " + ShortLabel(tokens)
            : "Context set to " + ShortLabel(tokens);

    /// <summary>Composer note while the selected model's engine rebuilds; sends wait for it.</summary>
    public static string SendPausedText(int? tokens) =>
        "Rebuilding the engine" + (tokens is int value ? " for " + ShortLabel(value) + " context" : "")
        + "… Send is paused until it is ready; your message is kept.";

    private static string KvName(string value) => value == "auto" ? "Auto" : KvShort(value);

    /// <summary>The primary button is enabled only for a valid value that changes something.</summary>
    public static bool CanCommit(ContextConfiguration context, ContextChoice choice, ContextEditorMode mode,
        string? precision = null) =>
        choice.Valid && (mode == ContextEditorMode.LoadWith || choice.Tokens != context.Current
            || (precision is not null && precision != context.KvPrecision));
}
