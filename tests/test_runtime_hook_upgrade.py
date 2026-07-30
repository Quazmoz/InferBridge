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
    assert "os.name != \"nt\"" in hook
    assert "getattr(sys, \"frozen\", False)" in hook


def test_runtime_hook_keeps_restart_registration_non_fatal():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")

    assert "except (AttributeError, OSError):" in hook
    assert "must never block local inference" in hook
