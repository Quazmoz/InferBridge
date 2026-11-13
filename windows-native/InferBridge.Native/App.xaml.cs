using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinRT.Interop;

namespace InferBridge.Native;

public partial class App : Application
{
    private Window? _mainWindow;

    public App()
    {
        // In the Store package MRT Core's default index is the package-root resources.pri,
        // whose file resources (acrylic noise) resolve against the package root, not native\.
        // Give XAML this app's own index so they resolve beside the executable, as they
        // do unpackaged, where [exe name].pri is already the default.
        string resources = Path.ChangeExtension(Environment.ProcessPath ?? "", ".pri");
        if (NativeDataPaths.HasPackageIdentity() && File.Exists(resources))
            ResourceManagerRequested += (_, args) => args.CustomResourceManager =
                new Microsoft.Windows.ApplicationModel.Resources.ResourceManager(resources);
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _mainWindow = new MainWindow();
        _mainWindow.Activate();
    }

    public void ActivateExistingWindow()
    {
        var window = _mainWindow;
        if (window is null) return; // OnLaunched will activate a window if still starting.

        _ = window.DispatcherQueue.TryEnqueue(() =>
        {
            // Secondary launches must focus the existing native window, never
            // create another UI or attempt to start the authoritative engine.
            // Restore only when minimized; a plain restore would un-maximize it.
            if (window.AppWindow.Presenter is OverlappedPresenter
                { State: OverlappedPresenterState.Minimized } presenter)
                presenter.Restore();
            window.Activate();
            nint handle = WindowNative.GetWindowHandle(window);
            if (handle != 0)
                _ = SetForegroundWindow(handle);
        });
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(nint hWnd);
}
