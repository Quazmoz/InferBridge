from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_runtime_hook_registers_only_the_installed_primary_tray_for_update_restart():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")

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
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")

    assert "_validate_windows_native_runtime" in hook
    assert "import psutil" in hook
    assert "psutil.Process(os.getpid()).create_time()" in hook
    assert "files from two versions were mixed" in hook
    assert "preserving downloaded models, settings" in hook
    assert "MessageBoxW" in hook
    assert "startup-runtime-error.log" in hook
    assert "os._exit(_RUNTIME_FAILURE_EXIT_CODE)" in hook


def test_runtime_hook_sanitizes_failure_details_and_has_a_stderr_fallback():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")

    assert "_WINDOWS_PATH_RE" in hook
    assert "_POSIX_HOME_RE" in hook
    assert '_WINDOWS_PATH_RE.sub(lambda _match: "...\\\\", detail)' in hook
    assert '_POSIX_HOME_RE.sub(".../", detail)' in hook
    assert 'sys.stderr.write(message + "\\n")' in hook
    assert "sys.stderr.flush()" in hook


def test_runtime_hook_keeps_restart_registration_non_fatal():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")

    assert "except (AttributeError, OSError):" in hook
    assert "must never block local inference" in hook
