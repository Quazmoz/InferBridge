using System.Diagnostics;

namespace InferBridge.Native;

internal static class EngineLauncher
{
    public static (bool Started, string Message) Start()
    {
        // In a combined desktop distribution, the bundled Python executable
        // lives beside the native/ directory. Never execute PATH or user input.
        string directory = Path.GetFullPath(
            Path.Combine(AppContext.BaseDirectory, ".."));
        string executable = Path.Combine(directory, "InferBridge.exe");
        if (!File.Exists(executable))
            return (false,
                "The packaged InferBridge engine was not found beside this native app. " +
                "For development, start the Python desktop engine separately.");

        try
        {
            var info = new ProcessStartInfo(executable)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = directory,
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
