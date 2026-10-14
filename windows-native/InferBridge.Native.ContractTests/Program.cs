using InferBridge.Native;

// Executable native-path contract tests, deliberately package/test-framework free.
// The fixture only uses disposable temporary directories and simulates package
// identity; signed MSIX installation must still be qualified separately.
string root = Path.Combine(Path.GetTempPath(), "inferbridge-native-paths-" + Guid.NewGuid().ToString("N"));
try
{
    string app = Path.Combine(root, "app");
    string native = Path.Combine(app, "native");
    string profile = Path.Combine(root, "profile");
    string local = Path.Combine(profile, "AppData", "Local");
    Directory.CreateDirectory(native);
    Directory.CreateDirectory(local);

    string Resolve(bool packaged = false, string? nativeOverride = null, string? backendOverride = null)
        => NativeDataPaths.ResolveDataRoot(
            native, packaged, nativeOverride, backendOverride, profile, local);

    void Expect(string label, string actual, string expected)
    {
        if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException(label + ": unexpected data root.");
    }

    Expect("Standalone defaults to current data",
        Resolve(), Path.Combine(local, "InferBridge"));

    string legacy = Path.Combine(local, "OpenVINOWindowsLLM");
    Directory.CreateDirectory(legacy);
    Expect("Standalone retains legacy data after upgrade", Resolve(), legacy);
    string current = Path.Combine(local, "InferBridge");
    Directory.CreateDirectory(current);
    Expect("Current root takes precedence", Resolve(), current);

    Expect("MSIX uses non-virtualized per-user storage", Resolve(packaged: true),
        Path.Combine(profile, "InferBridgeStoreData"));

    string overrideData = Path.Combine(root, "custom");
    Expect("Python override matches tray", Resolve(backendOverride: overrideData), overrideData);
    string nativeData = Path.Combine(root, "native-override");
    Expect("Native metadata override takes precedence",
        Resolve(nativeOverride: nativeData, backendOverride: overrideData), nativeData);

    File.WriteAllText(Path.Combine(app, "portable.flag"), "");
    Expect("Portable flag wins over packaged and standalone defaults",
        Resolve(packaged: true), Path.Combine(app, "data"));
    Expect("Python explicit data root wins over portable mode",
        Resolve(backendOverride: overrideData), overrideData);

    foreach (string badOverride in new[] { "relative-root", @".\data" })
    {
        bool rejected = false;
        try
        {
            _ = Resolve(nativeOverride: badOverride);
        }
        catch (ArgumentException)
        {
            rejected = true;
        }
        if (!rejected)
            throw new InvalidOperationException("Relative metadata override was accepted.");
    }

    Console.WriteLine("Native data-root contract: all cases passed.");
    return 0;
}
finally
{
    if (Directory.Exists(root))
        Directory.Delete(root, recursive: true);
}
