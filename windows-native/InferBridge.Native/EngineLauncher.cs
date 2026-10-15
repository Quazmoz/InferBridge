using System.Diagnostics;

namespace InferBridge.Native;

internal static class EngineLauncher
{
    // In a combined desktop distribution, the bundled Python executable
    // lives beside the native/ directory. Never execute PATH or user input.
    private static readonly string EngineDirectory = Path.GetFullPath(
        Path.Combine(AppContext.BaseDirectory, ".."));
    private static readonly string EngineExecutable = Path.Combine(EngineDirectory, "InferBridge.exe");

    public static bool IsBundled => File.Exists(EngineExecutable);

    public static (bool Started, string Message) Start()
    {
        if (!IsBundled)
            return (false,
                "The packaged InferBridge engine was not found beside this native app. " +
                "For development, start the Python desktop engine separately.");

        try
        {
            var info = CreateStartInfo(Environment.GetEnvironmentVariable("OV_LLM_DATA_DIR"));
            using var process = Process.Start(info);
            if (process is null)
                return (false, "Windows could not launch the local engine.");
            // A second launch may exit immediately when the tray already owns
            // the server. The verified metadata and health APIs decide readiness.
            return (true, "Engine launch requested. Refresh status to verify readiness.");
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException or ArgumentException
            or System.ComponentModel.Win32Exception)
        {
            return (false,
                "The local engine could not be started. Check its installation, data directory and permissions.");
        }
    }

    internal static ProcessStartInfo CreateStartInfo(string? dataDirectory)
    {
        var info = new ProcessStartInfo(EngineExecutable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = EngineDirectory,
        };
        info.ArgumentList.Add("--no-browser");
        // The Python desktop launcher resets inherited launch flags. Forward the
        // shared data override explicitly so the client and engine find one session.
        if (dataDirectory is not null)
        {
            if (!Path.IsPathFullyQualified(dataDirectory))
                throw new ArgumentException("OV_LLM_DATA_DIR must be absolute.");
            info.ArgumentList.Add("--data-dir");
            info.ArgumentList.Add(Path.GetFullPath(dataDirectory));
        }
        return info;
    }
}
