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
            var info = new ProcessStartInfo(EngineExecutable)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = EngineDirectory,
            };
            info.ArgumentList.Add("--no-browser");
            using var process = Process.Start(info);
            if (process is null)
                return (false, "Windows could not launch the local engine.");
            // A second launch may exit immediately when the tray already owns
            // the server. The verified metadata and health APIs decide readiness.
            return (true, "Engine launch requested. Refresh status to verify readiness.");
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException
            or System.ComponentModel.Win32Exception)
        {
            return (false,
                "The local engine could not be started. Check its installation and permissions.");
        }
    }
}
