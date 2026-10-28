"""Issue #65: Phi-4 Mini INT4 on NPU (Meteor Lake 3720) and its recovery paths.

Covers the channel-wise INT4 policy for NPU-targeted conversions, the classified
ZE_RESULT_ERROR_INVALID_ARGUMENT message with one-click recoveries, the Phi-4 Mini
NPU caveat, the NPU driver version in diagnostics, and the clipboard fallback.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import desktop_shell, errors, model_load_safety, quantization_policy
from app.config import Settings
from app.diagnostics import DiagnosticsCollector
from app.model_registry import ModelConfig
from app.server import create_app
from app.tray_diagnostics_actions import TrayDiagnosticsActionsMixin

# The exact native text reported in #65 (InferBridge 1.0.0, Core Ultra 7 155H).
ISSUE_65 = (
    "Exception from src\\inference\\src\\cpp\\core.cpp:112:\n"
    "Exception from src\\inference\\src\\dev\\plugin.cpp:53:\n"
    "Exception from src\\plugins\\intel_npu\\src\\plugin\\src\\plugin.cpp:673:\n"
    "Exception from src\\plugins\\intel_npu\\src\\compiler_adapter\\src\\"
    "ze_graph_ext_wrappers.cpp:433:\n"
    "Compilation failed. Level0 pfnCreate2 result: ZE_RESULT_ERROR_INVALID_ARGUMENT, "
    "code 0x78000004 - generic error code for invalid arguments . C:\\Users\\Private\\models"
)
PHI4 = "microsoft/phi-4-mini-instruct"


def _cfg(source: str = PHI4, weight_format: str = "int4") -> ModelConfig:
    return ModelConfig(
        id="phi-4-mini-int4",
        name="Phi-4 Mini Instruct INT4",
        description="",
        backend="openvino-genai",
        model_path="models/openvino/phi-4-mini-int4",
        source_model=source,
        weight_format=weight_format,
        recommended_device="NPU",
        max_context_len=4096,
        max_output_tokens=1024,
    )


# --- channel-wise INT4 policy -------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "arch", "expected"),
    [
        (PHI4, None, -1),
        ("microsoft/Phi-4-mini-instruct", "4000", -1),
        ("Qwen/Qwen2.5-7B-Instruct", None, -1),
        ("Qwen/Qwen3-30B-A3B", None, -1),
        ("Qwen/Qwen2.5-3B-Instruct", None, 128),
        ("Qwen/Qwen2.5-3B-Instruct", "3720", -1),
        ("meta-llama/Llama-3.2-1B-Instruct", "3720", 128),
        ("HuggingFaceTB/SmolLM2-135M-Instruct", "3720", 128),
        ("Qwen/Qwen2.5-0.5B-Instruct", None, 128),
    ],
)
def test_npu_int4_group_size_follows_intel_guidance(source, arch, expected) -> None:
    assert quantization_policy.npu_int4_group_size(source, npu_architecture=arch) == expected


def test_npu_recommendation_is_channel_wise_for_phi4_mini() -> None:
    rec = quantization_policy.recommend_quantization(
        backend="openvino-genai", device="NPU", source_model=PHI4
    )
    assert (rec.weight_format, rec.group_size, rec.ratio, rec.sym) == ("int4", -1, 1.0, True)
    assert "channel-wise" in rec.reason
    small = quantization_policy.recommend_quantization(
        backend="openvino-genai", device="NPU", source_model="Qwen/Qwen2.5-1.5B-Instruct"
    )
    assert small.group_size == 128


def test_conversion_profile_uses_channel_wise_only_for_npu_targets() -> None:
    resolve = model_load_safety.resolve_conversion_profile
    args = {"weight_format": None, "group_size": None, "ratio": None, "sym": None}
    assert resolve(_cfg(), device="NPU", **args) == ("int4", -1, 1.0, True)
    assert resolve(_cfg(), device="AUTO:NPU,GPU", **args)[1] == -1
    assert resolve(_cfg(), device="GPU", **args) == ("int4", 128, 1.0, True)
    assert resolve(_cfg(), device=None, **args)[1] == 128
    # An explicit group size always wins.
    assert resolve(_cfg(), device="NPU", **{**args, "group_size": 128})[1] == 128
    qwen3b = _cfg("Qwen/Qwen2.5-3B-Instruct")
    assert resolve(qwen3b, device="NPU", npu_architecture="3720", **args)[1] == -1
    assert resolve(qwen3b, device="NPU", npu_architecture="4000", **args)[1] == 128
    # Channel-wise artifacts stay NPU-eligible.
    assert model_load_safety.is_npu_compatible_int4_profile(
        weight_format="int4", group_size=-1, ratio=1.0, sym=True
    )


def test_phi4_mini_is_marked_as_an_npu_known_issue() -> None:
    note = quantization_policy.npu_known_issue("Microsoft/Phi-4-mini-instruct")
    assert note and "NPU" in note and "group size -1" in note
    assert quantization_policy.npu_known_issue("Qwen/Qwen2.5-1.5B-Instruct") is None


# --- classified error and recovery actions ------------------------------------------


def test_issue_65_error_is_actionable_and_private() -> None:
    message = errors.format_model_load_error(RuntimeError(ISSUE_65), device="NPU")
    assert errors.NPU_DRIVER_DOWNLOAD_URL in message
    assert "GPU instead" in message and "channel-wise INT4 (group size -1)" in message
    assert "has not switched devices" in message
    assert "Private" not in message and "plugin.cpp" not in message
    assert errors.model_load_error_code(RuntimeError(ISSUE_65), device="NPU") == (
        errors.NPU_COMPILE_REJECTED
    )
    npuw = RuntimeError(
        "just_sync_infer_request.cpp:659: Failed to compile. No more devices are left!"
    )
    assert errors.model_load_error_code(npuw, device="NPU.0") == errors.NPU_COMPILE_REJECTED
    assert errors.model_load_error_code(RuntimeError(ISSUE_65), device="GPU") is None


def test_recovery_actions_depend_on_gpu_and_format() -> None:
    actions = errors.load_recovery_actions(
        errors.NPU_COMPILE_REJECTED,
        weight_format="int4",
        has_source_model=True,
        available_devices=["CPU", "GPU.0", "NPU"],
    )
    assert [row["id"] for row in actions] == ["update_npu_driver", "load_gpu", "reconvert_npu"]
    assert actions[0]["url"] == errors.NPU_DRIVER_DOWNLOAD_URL
    assert actions[2]["convert"] == {
        "device": "NPU",
        "weight_format": "int4",
        "group_size": -1,
        "ratio": 1.0,
        "sym": True,
    }
    no_gpu_fp16 = errors.load_recovery_actions(
        errors.NPU_COMPILE_REJECTED,
        weight_format="fp16",
        has_source_model=True,
        available_devices=["CPU", "NPU"],
    )
    assert [row["id"] for row in no_gpu_fp16] == ["update_npu_driver"]
    assert (
        errors.load_recovery_actions(
            None, weight_format="int4", has_source_model=True, available_devices=["GPU"]
        )
        == []
    )


def _settings(tmp_path: Path) -> Settings:
    model_dir = tmp_path / "models" / "phi-4-mini-int4"
    model_dir.mkdir(parents=True)
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                "phi-4-mini-int4": {
                    "name": "Phi-4 Mini Instruct INT4",
                    "model_path": str(model_dir),
                    "source_model": PHI4,
                    "backend": "openvino-genai",
                    "weight_format": "int4",
                    "recommended_device": "NPU",
                    "max_context_len": 4096,
                    "max_output_tokens": 1024,
                }
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        device="NPU",
        force_mock=True,
    )


def test_failed_npu_load_exposes_one_click_recoveries(tmp_path, monkeypatch) -> None:
    def reject(**_kwargs):
        raise RuntimeError(ISSUE_65)

    monkeypatch.setattr("app.model_manager_core.create_engine", reject)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        started = client.post("/v1/models/load", json={"model": "phi-4-mini-int4", "device": "NPU"})
        assert started.status_code == 200, started.text
        entry = {}
        for _ in range(100):
            entry = app.state.manager.catalog_entry("phi-4-mini-int4")
            if entry["status"] == "error":
                break
            time.sleep(0.05)
        assert entry["status"] == "error"
        assert entry["error_code"] == errors.NPU_COMPILE_REJECTED
        assert [row["id"] for row in entry["recovery_actions"]] == [
            "update_npu_driver",
            "load_gpu",
            "reconvert_npu",
        ]
        assert "known issue" not in entry["error"]
        assert entry["npu_note"] and "Phi-4 Mini" in entry["npu_note"]
        row = next(
            item
            for item in client.get("/v1/models").json()["data"]
            if item["id"] == "phi-4-mini-int4"
        )
        assert row["error_code"] == errors.NPU_COMPILE_REJECTED
        assert row["recovery_actions"] == ["load_gpu", "reconvert_npu"]
        assert "ZE_RESULT_ERROR_INVALID_ARGUMENT" in row["error"]


def test_model_rows_without_errors_keep_their_shape(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        row = client.get("/v1/models").json()["data"][0]
        assert "error_code" not in row and "recovery_actions" not in row
        entry = app.state.manager.catalog_entry("phi-4-mini-int4")
        assert entry["error_code"] is None and entry["recovery_actions"] == []


# --- diagnostics: NPU driver version -------------------------------------------------


def test_diagnostics_summary_reports_npu_driver_and_architecture(tmp_path) -> None:
    from tests.test_support_workflow import _paths

    collector = DiagnosticsCollector(
        paths=_paths(tmp_path, packaged=False),
        runtime_snapshot={"mock": True},
        hardware_snapshot={
            "os": {"system": "Windows"},
            "available_devices": ["CPU", "NPU"],
            "devices": [
                {"device": "CPU", "full_name": "Intel CPU"},
                {
                    "device": "NPU",
                    "full_name": "Intel(R) AI Boost",
                    "architecture": "3720",
                    "npu_driver_version": 1688,
                },
            ],
        },
    )
    summary = collector.support_summary()
    assert "NPU driver: 1688" in summary
    assert "NPU architecture: 3720" in summary
    bare = DiagnosticsCollector(
        paths=_paths(tmp_path / "bare", packaged=False),
        runtime_snapshot={"mock": True},
        hardware_snapshot={"devices": [{"device": "CPU"}], "available_devices": ["CPU"]},
    ).support_summary()
    assert "NPU driver: unavailable" in bare


def test_hardware_scan_reads_npu_driver_version(monkeypatch) -> None:
    from app.hardware_advisor import hardware

    class Core:
        def get_property(self, device, prop):
            values = {
                ("NPU", "FULL_DEVICE_NAME"): "Intel(R) AI Boost",
                ("NPU", "DEVICE_ARCHITECTURE"): "3720",
                ("NPU", "NPU_DRIVER_VERSION"): 1688,
            }
            if (device, prop) not in values:
                raise RuntimeError("unsupported property")
            return values[(device, prop)]

    monkeypatch.setattr(hardware.device_check, "available_devices", lambda: ["NPU"])
    monkeypatch.setattr(hardware.device_check, "get_core", lambda: Core())
    hardware.device_details.cache_clear()
    try:
        (npu,) = hardware.device_details()
    finally:
        hardware.device_details.cache_clear()
    assert npu["npu_driver_version"] == 1688
    assert npu["driver_version"] == "1688"
    assert npu["architecture"] == "3720"


# --- clipboard robustness ------------------------------------------------------------


def test_windows_clipboard_falls_back_to_clip_exe(monkeypatch) -> None:
    piped: list[str] = []

    def broken(_text):
        raise RuntimeError("Windows could not lock clipboard memory.")

    monkeypatch.setattr(desktop_shell, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(desktop_shell, "_copy_with_win32", broken)
    monkeypatch.setattr(
        desktop_shell, "_copy_with_clip_exe", lambda text: piped.append(text) or True
    )
    desktop_shell.copy_to_clipboard("safe diagnostics")
    assert piped == ["safe diagnostics"]

    monkeypatch.setattr(desktop_shell, "_copy_with_clip_exe", lambda _text: False)
    with pytest.raises(RuntimeError, match="lock clipboard memory"):
        desktop_shell.copy_to_clipboard("safe diagnostics")


def test_copy_diagnostics_saves_a_file_and_shows_the_path(tmp_path, monkeypatch) -> None:
    from tests.test_support_workflow import _paths

    dialogs: list[str] = []
    opened: list[Path] = []
    collector = DiagnosticsCollector(
        paths=_paths(tmp_path, packaged=False),
        runtime_snapshot={"mock": True},
        hardware_snapshot={"devices": [], "available_devices": []},
    )

    class Tray(TrayDiagnosticsActionsMixin):
        last_diagnostics_path = None

        def _diagnostics_collector(self):
            return collector

    def busy(_text):
        raise RuntimeError("Windows could not lock clipboard memory.")

    monkeypatch.setattr("app.tray_diagnostics_actions.copy_to_clipboard", busy)
    monkeypatch.setattr("app.tray_diagnostics_actions.open_path", lambda path: opened.append(path))
    monkeypatch.setattr(
        "app.tray_diagnostics_actions.show_dialog",
        lambda _title, message, error=False: dialogs.append(message),
    )
    tray = Tray()
    tray.copy_diagnostics()

    saved = tray.last_diagnostics_path
    assert saved is not None and saved.is_file() and saved.suffix == ".txt"
    assert saved.read_text(encoding="utf-8").startswith("InferBridge Diagnostics")
    assert opened == [saved.parent]
    assert str(saved) in dialogs[-1] and "Nothing was uploaded" in dialogs[-1]


# --- source contracts: native and web one-click recoveries --------------------------

ROOT = Path(__file__).resolve().parents[1]


def test_native_model_cards_offer_npu_recoveries() -> None:
    cards = (ROOT / "windows-native" / "InferBridge.Native" / "MainWindow.ModelCards.cs").read_text(
        encoding="utf-8"
    )
    assert 'Add("load_gpu", "\\uE7F4", "Load on GPU instead"' in cards
    assert '"Reconvert for NPU (channel-wise INT4)"' in cards
    assert "_backend.RecoverModelAsync(model, action" in cards
    assert 'model.Status == "error" && model.Error is { Length: > 0 } failure' in cards
    program = (
        ROOT / "windows-native" / "InferBridge.Native.ContractTests" / "Program.cs"
    ).read_text(encoding="utf-8")
    assert "await NpuRecoveryContractTests.RunAsync(root);" in program


def test_web_error_card_offers_npu_recoveries() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "data-recovery-action" in html
    assert "async function runModelRecovery(modelId, actionId)" in html
    assert "group_size: convert.group_size" in html
    assert 'rel="noopener noreferrer"' in html
