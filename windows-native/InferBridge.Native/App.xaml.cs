using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using WinRT.Interop;

namespace InferBridge.Native;

public partial class App : Application
{
    private Window? _mainWindow;

    public App()
    {
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
            window.Activate();
            nint handle = WindowNative.GetWindowHandle(window);
            if (handle != 0)
            {
                _ = ShowWindow(handle, 9); // SW_RESTORE
                _ = SetForegroundWindow(handle);
            }
        });
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindow(nint hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(nint hWnd);
}
