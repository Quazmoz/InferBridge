using System.Globalization;

namespace InferBridge.Native;

/// <summary>One preset row in the shared context editor.</summary>
internal sealed record ContextPreset(int Tokens, bool Trial, bool Recommended, bool Current)
{
    public string Label
    {
        get
        {
            var tags = new List<string>();
            if (Current) tags.Add("current");
            if (Recommended) tags.Add("recommended");
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

    /// <summary>Common sizes inside the model's bounds, in ascending order.</summary>
    public static IReadOnlyList<ContextPreset> Presets(ContextConfiguration context)
    {
        int lower = LowerBound(context), upper = UpperBound(context);
        return StandardPresets
            .Where(value => value >= lower && value <= upper)
            .Select(value => new ContextPreset(value, IsTrial(context, value),
                value == context.Recommended, value == context.Current))
            .ToArray();
    }

    /// <summary>
    /// Linear FP16 KV-cache estimate for any value, scaled from the engine's own
    /// per-option estimates. Null when the model metadata cannot support one.
    /// </summary>
    public static double? EstimateKvGib(ContextConfiguration context, int tokens)
    {
        var sample = context.Options.FirstOrDefault(option =>
            option.KvEstimateGib is double kv && kv > 0 && option.Tokens > 0);
        if (sample?.KvEstimateGib is not double estimate) return null;
        return Math.Round(estimate / sample.Tokens * tokens, 3);
    }

    /// <summary>Checks a preset or custom value with friendly, actionable messages.</summary>
    public static ContextChoice Validate(ContextConfiguration context, double? requested)
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
        double? kv = EstimateKvGib(context, tokens);
        string message = Format(tokens - context.OutputReserve) + " prompt tokens + "
            + Format(context.OutputReserve) + " reserved output. "
            + (kv is double gib
                ? "KV cache ~" + gib.ToString("0.###", CultureInfo.InvariantCulture)
                    + " GiB (FP16 estimate, not measured device memory)."
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

    /// <summary>The primary button is enabled only for a valid value that changes something.</summary>
    public static bool CanCommit(ContextConfiguration context, ContextChoice choice, ContextEditorMode mode) =>
        choice.Valid && (mode == ContextEditorMode.LoadWith || choice.Tokens != context.Current);
}
