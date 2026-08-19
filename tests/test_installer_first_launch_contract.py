import sys
from pathlib import Path
from types import SimpleNamespace

from app import frozen_entrypoint

ROOT = Path(__file__).resolve().parent.parent


def test_pyinstaller_uses_hardened_entrypoint_and_bundles_setuptools_data():
    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")

    assert 'root / "app" / "frozen_entrypoint.py"' in spec
    assert 'collect_data_files("setuptools", include_py_files=False)' in spec
    assert "Lorem ipsum.txt" in spec


def test_bootstrap_smoke_exits_before_desktop_launcher_import(monkeypatch):
    # If this sentinel module were imported, attribute access would fail. The bootstrap
    # probe must stop before application imports so the installer can cheaply validate the
    # frozen interpreter and PyInstaller runtime hooks from the installed directory.
    monkeypatch.setitem(sys.modules, "app.desktop_launcher", None)

    assert frozen_entrypoint.run(["--bootstrap-smoke"]) == 0


def test_frozen_boundary_contains_unexpected_launcher_exception(monkeypatch):
    shown = {}

    def failing_main(_arguments):
        raise FileNotFoundError("missing packaged runtime data")

    def show_dialog(title, message, *, error=False):
        shown["title"] = title
        shown["message"] = message
        shown["error"] = error

    monkeypatch.setitem(sys.modules, "app.desktop_launcher", SimpleNamespace(main=failing_main))
    monkeypatch.setitem(sys.modules, "app.desktop_shell", SimpleNamespace(show_dialog=show_dialog))

    assert frozen_entrypoint.run([]) == 2
    assert shown["error"] is True
    assert "FileNotFoundError" in shown["message"]
    assert "missing packaged runtime data" not in shown["message"]


def test_installer_runs_bootstrap_probe_before_offering_postinstall_launch():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert "function VerifyInstalledRuntime(): Boolean;" in script
    assert "--bootstrap-smoke" in script
    assert "ewWaitUntilTerminated" in script
    assert "RuntimeCheckAttempts = 3" in script
    assert "procedure CurStepChanged(CurStep: TSetupStep);" in script
    assert "InstalledRuntimeReady := VerifyInstalledRuntime();" in script
    assert "Check: CanLaunchInstalledRuntime" in script
    assert "Setup will not launch the application automatically" in script


def test_installer_bootstrap_probe_is_noninteractive_and_never_restart_registered():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")
    failure_guard = hook.split("def _show_runtime_failure", 1)[1].split(
        "def _fail_runtime_validation", 1
    )[0]
    restart_guard = hook.split("def _register_for_update_restart", 1)[1].split(
        '_restore_output("stdout", 1)', 1
    )[0]

    assert '"--native-smoke"' in failure_guard
    assert '"--bootstrap-smoke"' in failure_guard
    assert "set(sys.argv[1:]) & helper_modes" in failure_guard
    assert '"--native-smoke"' in restart_guard
    assert '"--bootstrap-smoke"' in restart_guard
    assert "arguments & helper_modes" in restart_guard
