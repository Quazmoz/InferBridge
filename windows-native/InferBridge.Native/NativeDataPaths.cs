using System.Runtime.InteropServices;

namespace InferBridge.Native;

/// <summary>
/// Matches the Python tray's installed, Store and portable data-root contract.
/// Native code reads session metadata only; it never owns or migrates model data.
/// </summary>
internal static class NativeDataPaths
{
    private const int ErrorInsufficientBuffer = 122;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetCurrentPackageFullName(
        ref uint packageFullNameLength, char[]? packageFullName);

    private static bool HasPackageIdentity()
    {
        uint length = 0;
        return OperatingSystem.IsWindows()
            && GetCurrentPackageFullName(ref length, null) == ErrorInsufficientBuffer;
    }

    internal static string ResolveMetadataFile()
    {
        string profile = Environment.GetEnvironmentVariable("USERPROFILE")
            ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string local = Environment.GetEnvironmentVariable("LOCALAPPDATA")
            ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        string dataRoot = ResolveDataRoot(
            AppContext.BaseDirectory, HasPackageIdentity(),
            Environment.GetEnvironmentVariable("INFERBRIDGE_NATIVE_DATA_DIR"),
            Environment.GetEnvironmentVariable("OV_LLM_DATA_DIR"), profile, local);
        return Path.Combine(dataRoot, "desktop-instance.json");
    }

    internal static string ResolveDataRoot(
        string nativeDirectory, bool hasPackageIdentity,
        string? nativeOverride, string? backendOverride,
        string userProfile, string localAppData)
    {
        string? requested = !string.IsNullOrWhiteSpace(nativeOverride)
            ? nativeOverride : backendOverride;
        if (requested is not null)
        {
            // Relative overrides resolve against different working directories when
            // launched by the Start menu and Python tray; fail closed instead.
            if (!Path.IsPathFullyQualified(requested))
                throw new ArgumentException("The native data-root override must be absolute.");
            return Path.GetFullPath(requested);
        }

        string bundledRoot = Path.GetFullPath(Path.Combine(nativeDirectory, ".."));
        if (File.Exists(Path.Combine(bundledRoot, "portable.flag")))
            return Path.Combine(bundledRoot, "data");

        if (hasPackageIdentity)
            return Path.GetFullPath(Path.Combine(userProfile, "InferBridgeStoreData"));

        // Python retains its original legacy data root when it exists. Never
        // silently redirect to an empty renamed directory during an upgrade.
        string current = Path.GetFullPath(Path.Combine(localAppData, "InferBridge"));
        if (Path.Exists(current))
            return current;
        string legacy = Path.GetFullPath(Path.Combine(localAppData, "OpenVINOWindowsLLM"));
        return Path.Exists(legacy) ? legacy : current;
    }
}
