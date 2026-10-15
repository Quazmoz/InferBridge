using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace InferBridge.Native;

internal static class Program
{
    // WinUI must start on an STA thread. Main stays synchronous: an async Main
    // runs inside a compiler-generated entry point that drops [STAThread].
    [STAThread]
    private static int Main(string[] args)
    {
        // Instance selection precedes WinUI initialization. The Python tray
        // remains the only process owner of the inference engine.
        WinRT.ComWrappersSupport.InitializeComWrappers();
        var activation = AppInstance.GetCurrent().GetActivatedEventArgs();
        // Separate installed/portable evaluation layouts remain independent while
        // launches from the same native installation share one window.
        string directory = Path.GetFullPath(AppContext.BaseDirectory).TrimEnd('\\', '/');
        string scope = Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(directory))[..12]);
        var primary = AppInstance.FindOrRegisterForKey("InferBridge.Native." + scope);
        if (!primary.IsCurrent)
        {
            // This launch holds the user's foreground rights; pass them on so the
            // existing window can come to the front instead of only flashing.
            _ = AllowSetForegroundWindow(primary.ProcessId);
            // Redirect off the STA thread; the blocking wait still pumps COM.
            Task.Run(() => primary.RedirectActivationToAsync(activation).AsTask()).Wait();
            return 0;
        }

        primary.Activated += (_, _) =>
        {
            if (Application.Current is App app)
                app.ActivateExistingWindow();
        };

        Application.Start(_ =>
        {
            SynchronizationContext.SetSynchronizationContext(
                new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread()));
            new App();
        });
        return 0;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AllowSetForegroundWindow(uint processId);
}
