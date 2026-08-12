"""Regression coverage for the low-friction InferBridge support workflow."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.diagnostics import DiagnosticsCollector
from app.paths import RuntimePaths
from app.support import GITHUB_ISSUES_URL, SUPPORT_URL, validate_support_url
from app.tray_diagnostics_actions import TrayDiagnosticsActionsMixin
from app.tray_menu import TrayMenuMixin


def _paths(root: Path, *, packaged: bool = True, portable: bool = False) -> RuntimePaths:
    return RuntimePaths(
        resource_root=root,
        data_root=root / "data",
        config_dir=root / "data" / "config",
        logs_dir=root / "data" / "logs",
        models_dir=root / "data" / "models",
        huggingface_cache_dir=root / "data" / "cache" / "huggingface",
        compiled_cache_dir=root / "data" / "cache" / "openvino",
        benchmarks_dir=root / "data" / "benchmarks",
        diagnostics_dir=root / "data" / "diagnostics",
        onboarding_dir=root / "data" / "onboarding",
        models_file=root / "data" / "config" / "models.json",
        portable=portable,
        packaged=packaged,
    )


def test_support_destinations_are_https_and_keep_github_available() -> None:
    assert SUPPORT_URL == "https://consultant.quinnfavo.com/apps/inferbridge#feedback"
    assert GITHUB_ISSUES_URL == "https://github.com/Quazmoz/InferBridge/issues"
    assert validate_support_url(SUPPORT_URL) == SUPPORT_URL
    assert validate_support_url(GITHUB_ISSUES_URL) == GITHUB_ISSUES_URL


def test_support_url_validation_rejects_non_https() -> None:
    try:
        validate_support_url("http://example.test/support")
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Non-HTTPS support URL was accepted")


def test_copyable_diagnostics_include_available_hardware_without_secrets(tmp_path: Path) -> None:
    collector = DiagnosticsCollector(
        paths=_paths(tmp_path),
        runtime_snapshot={
            "device": {"default": "NPU"},
            "active_model": {
                "id": "qwen2.5-1.5b-instruct-fp16",
                "weight_format": "fp16",
                "model_path": r"C:\Users\PrivateUser\Models\qwen",
                "token": "hf_supersecretvalue",
            },
            "mock": False,
        },
        effective_configuration={
            "device": "NPU",
            "api_key_configured": True,
            "models_dir": r"C:\Users\PrivateUser\InferBridge\models",
        },
        hardware_snapshot={
            "os": {
                "system": "Windows",
                "edition": "Windows 11 Pro",
                "release": "11",
                "version": "10.0.26100",
                "architecture": "AMD64",
            },
            "cpu": {"name": "Intel Core Ultra Test CPU"},
            "memory": {"total_gb": 32.0},
            "runtime": {"openvino": "2026.2.1", "openvino_genai": "2026.2.1"},
            "available_devices": ["CPU", "GPU", "NPU"],
            "devices": [
                {"device": "CPU", "full_name": "Intel CPU"},
                {"device": "GPU", "full_name": "Intel Arc Test GPU"},
                {"device": "NPU", "full_name": "Intel AI Boost Test NPU"},
            ],
        },
        build_metadata={"build_id": "test-build", "artifact_kind": "installed"},
    )

    summary = collector.support_summary()

    assert "InferBridge Diagnostics" in summary
    assert "Windows 11 Pro" in summary
    assert "Intel Core Ultra Test CPU" in summary
    assert "Intel Arc Test GPU" in summary
    assert "Intel AI Boost Test NPU" in summary
    assert "OpenVINO: 2026.2.1" in summary
    assert "Selected device: NPU" in summary
    assert "qwen2.5-1.5b-instruct-fp16" in summary
    assert "hf_supersecretvalue" not in summary
    assert "PrivateUser" not in summary
    assert "model_path" not in summary
    assert "API" not in summary.split("Privacy:", maxsplit=1)[0]


def test_copyable_diagnostics_degrade_missing_runtime_information(tmp_path: Path) -> None:
    collector = DiagnosticsCollector(
        paths=_paths(tmp_path, packaged=False),
        runtime_snapshot={"mock": True},
        hardware_snapshot={
            "os": {"system": "Windows", "architecture": "AMD64"},
            "cpu": {},
            "memory": {},
            "runtime": {},
            "available_devices": ["CPU"],
            "devices": [{"device": "CPU", "full_name": "Intel CPU"}],
        },
    )

    summary = collector.support_summary()

    assert "Environment: development" in summary
    assert "GPU: not detected" in summary
    assert "NPU: not detected" in summary
    assert "OpenVINO: unavailable" in summary
    assert "OpenVINO GenAI: unavailable" in summary
    assert "Available devices: CPU" in summary
    assert "Mock mode: yes" in summary


class _SupportActionStub(TrayMenuMixin, TrayDiagnosticsActionsMixin):
    def __init__(self) -> None:
        self.refreshes = 0

    def _diagnostics_collector(self):
        return SimpleNamespace(support_summary=lambda: "safe diagnostics")

    def _refresh_icon(self) -> None:
        self.refreshes += 1


def test_tray_feedback_opens_authoritative_support_url(monkeypatch) -> None:
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.tray_menu.webbrowser.open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    _SupportActionStub().open_feedback()

    assert opened == [(SUPPORT_URL, 2)]


def test_clipboard_failure_is_presented_as_a_guarded_tray_error(monkeypatch) -> None:
    dialogs: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        "app.tray_diagnostics_actions.copy_to_clipboard",
        lambda _text: (_ for _ in ()).throw(RuntimeError("clipboard busy")),
    )
    monkeypatch.setattr(
        "app.tray_menu.show_dialog",
        lambda title, message, error=False: dialogs.append((title, message, error)),
    )
    stub = _SupportActionStub()

    stub._guarded_action(stub.copy_diagnostics)

    assert dialogs
    assert dialogs[-1][1] == "clipboard busy"
    assert dialogs[-1][2] is True
    assert stub.refreshes == 1
