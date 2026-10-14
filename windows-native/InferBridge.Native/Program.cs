using System.Security.Cryptography;
using System.Text;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace InferBridge.Native;

internal static class Program
{
    [STAThread]
    private static async Task Main(string[] args)
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
            await primary.RedirectActivationToAsync(activation);
            return;
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
    }
}
