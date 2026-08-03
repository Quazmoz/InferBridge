import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_HOOK = ROOT / "packaging" / "runtime_hook.py"


def test_runtime_hook_registers_only_the_installed_primary_tray_for_update_restart():
    hook = RUNTIME_HOOK.read_text(encoding="utf-8")

    assert "RegisterApplicationRestart" in hook
    assert 'register("--no-browser", 0x1 | 0x2)' in hook
    assert '"--server-child"' in hook
    assert '"--convert-model"' in hook
    assert '"--diagnostic"' in hook
    assert '"--headless"' in hook
    assert '"portable.flag"' in hook
    assert 'os.name != "nt"' in hook
    assert 'getattr(sys, "frozen", False)' in hook


def test_runtime_hook_replaces_native_dependency_traceback_with_repair_guidance():
    hook = RUNTIME_HOOK.read_text(encoding="utf-8")

    assert "_validate_windows_native_runtime" in hook
    assert "import psutil" in hook
    assert "psutil.Process(os.getpid()).create_time()" in hook
    assert "files from two versions were mixed" in hook
    assert "preserving downloaded models, settings" in hook
    assert "MessageBoxW" in hook
    assert "startup-runtime-error.log" in hook
    assert "os._exit(_RUNTIME_FAILURE_EXIT_CODE)" in hook


def test_runtime_hook_validates_optimum_openvino_namespace_discovery():
    hook = RUNTIME_HOOK.read_text(encoding="utf-8")

    assert "_validate_packaged_optimum_cli" in hook
    assert "load_optimum_namespace_cli_commands" in hook
    assert 'if "openvino" not in names' in hook
    assert "without starting\n    a model download" in hook
    assert "_validate_packaged_optimum_cli()" in hook


def test_runtime_hook_native_smoke_executes_optimum_namespace_discovery(monkeypatch):
    namespace = runpy.run_path(str(RUNTIME_HOOK))
    validate = namespace["_validate_packaged_optimum_cli"]
    calls = 0

    optimum = ModuleType("optimum")
    commands = ModuleType("optimum.commands")
    optimum_cli = ModuleType("optimum.commands.optimum_cli")

    class FakeOpenVinoCommand:
        COMMAND = SimpleNamespace(name="openvino")

    def load_commands():
        nonlocal calls
        calls += 1
        return [(FakeOpenVinoCommand, object)]

    optimum_cli.load_optimum_namespace_cli_commands = load_commands
    commands.optimum_cli = optimum_cli
    optimum.commands = commands
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.commands", commands)
    monkeypatch.setitem(sys.modules, "optimum.commands.optimum_cli", optimum_cli)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["InferBridge.exe", "--native-smoke"])

    validate()

    assert calls == 1


def test_runtime_hook_sanitizes_failure_details_and_has_a_stderr_fallback():
    hook = RUNTIME_HOOK.read_text(encoding="utf-8")

    assert '_PATH_REDACTION = "[redacted path]"' in hook
    assert "_QUOTED_WINDOWS_PATH_RE" in hook
    assert "_WINDOWS_PATH_RE" in hook
    assert "_POSIX_HOME_RE" in hook
    assert "_WINDOWS_PATH_RE.sub(_PATH_REDACTION, detail)" in hook
    assert "_POSIX_HOME_RE.sub(_PATH_REDACTION, detail)" in hook
    assert 'sys.stderr.write(message + "\\n")' in hook
    assert "sys.stderr.flush()" in hook


def test_runtime_hook_redacts_quoted_and_unquoted_user_paths_with_spaces():
    namespace = runpy.run_path(str(RUNTIME_HOOK))
    sanitize = namespace["_safe_error_detail"]

    detail = sanitize(
        RuntimeError(
            'failed to load "C:\\Users\\Quinn Favo\\AppData\\Local\\OpenVINO\\_psutil_windows.pyd": '
            "fallback at /home/quinn/private/runtime"
        )
    )
    assert "Quinn" not in detail
    assert "quinn" not in detail
    assert "AppData" not in detail
    assert detail.count("[redacted path]") == 2

    unquoted = sanitize(
        RuntimeError(
            "load failed at C:\\Users\\Quinn Favo\\AppData\\Local\\OpenVINO\\_psutil_windows.pyd: incompatible"
        )
    )
    assert "Quinn" not in unquoted
    assert "AppData" not in unquoted
    assert "[redacted path]" in unquoted
    assert unquoted.endswith("incompatible")


def test_runtime_hook_keeps_restart_registration_non_fatal():
    hook = RUNTIME_HOOK.read_text(encoding="utf-8")

    assert "except (AttributeError, OSError):" in hook
    assert "must never block local inference" in hook
