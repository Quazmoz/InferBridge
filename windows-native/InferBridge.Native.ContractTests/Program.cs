using InferBridge.Native;

// Executable native data-path and HTTP client contracts, without a test framework.
// All metadata is temporary and HTTP responses are simulated. Signed MSIX
// installation and real inference must be qualified separately.
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
    var defaultStart = EngineLauncher.CreateStartInfo(null);
    if (!defaultStart.ArgumentList.SequenceEqual(new[] { "--no-browser" })
        || defaultStart.UseShellExecute || !defaultStart.CreateNoWindow)
        throw new InvalidOperationException("Default engine launch contract changed.");
    string engineData = Path.Combine(root, "custom engine data");
    var customStart = EngineLauncher.CreateStartInfo(engineData);
    if (!customStart.ArgumentList.SequenceEqual(new[] { "--no-browser", "--data-dir", engineData }))
        throw new InvalidOperationException("Engine data override was not passed as one argument.");
    foreach (string invalid in new[] { "", "relative-root", @".\data" })
    {
        try
        {
            _ = EngineLauncher.CreateStartInfo(invalid);
        }
        catch (ArgumentException)
        {
            continue;
        }
        throw new InvalidOperationException("Relative engine data override was accepted.");
    }
    Console.WriteLine("Native engine launch contract: all cases passed.");
    await BackendContractTests.RunAsync(root);
    await ConnectionHubContractTests.RunAsync(root);
    ToolSetupContractTests.Run(root);
    await BenchmarkLabContractTests.RunAsync(root);
    await HuggingFaceContractTests.RunAsync(root);
    await HuggingFaceDownloadContractTests.RunAsync(root);
    await ModelPreparationContractTests.RunAsync(root);
    NativeConversationContractTests.Run(root);
    ChatMarkdownContractTests.Run();
    PickerRefreshContractTests.Run();
    ChartMathContractTests.Run();
    await ChatRequestContractTests.RunAsync(root);
    await OperationsParityContractTests.RunAsync(root);
    await ModelLibraryContractTests.RunAsync(root);
    await ContextEditorContractTests.RunAsync(root);
    await ChatParityContractTests.RunAsync(root);
    return 0;
}
finally
{
    if (Directory.Exists(root))
        Directory.Delete(root, recursive: true);
}
