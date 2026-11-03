"""Combined-device resolver (AUTO, AUTO:<list>, MULTI, HETERO) and its API."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import device_modes, errors, model_slots
from app.config import BASE_DIR, Settings
from app.server import create_app
from runtime.openvino_engine import build_plugin_config, probe_execution_devices

ULTRA = ["CPU", "GPU.0", "NPU"]
CHAT = "tinyllama-1.1b-chat-fp16"
EMBED = "bge-small-en-v1.5"


def test_multi_migrates_to_cumulative_auto_once():
    device, hint, notice = device_modes.migrate("multi:gpu,cpu")
    assert (device, hint) == ("AUTO:GPU,CPU", "CUMULATIVE_THROUGHPUT")
    assert "deprecated" in notice
    events = []
    assert device_modes.notify_once(notice + " test", lambda *a: events.append(a)) is True
    assert device_modes.notify_once(notice + " test", lambda *a: events.append(a)) is False
    assert len(events) == 1
    assert device_modes.migrate("AUTO:GPU,CPU") == ("AUTO:GPU,CPU", None, None)


def test_plain_auto_never_includes_npu():
    info = device_modes.describe("AUTO", kind="chat", available=ULTRA)
    assert info["members"] == ["GPU", "CPU"]
    assert info["may_use_npu"] is False
    assert info["support"] == "supported"
    assert info["recommended_hints"] == {"PERFORMANCE_HINT": "LATENCY"}
    assert device_modes.may_use_npu("AUTO") is False
    assert device_modes.may_use_npu("AUTO:NPU,CPU") is True


@pytest.mark.parametrize(
    ("device", "kind", "support"),
    [
        ("GPU", "chat", "supported"),
        ("NPU", "chat", "supported"),
        ("NPU", "embedding", "experimental"),
        ("AUTO:GPU,CPU", "chat", "supported"),
        ("AUTO:NPU,GPU,CPU", "chat", "experimental"),
        ("AUTO:NPU,GPU,CPU", "vlm", "unsupported"),
        ("AUTO:NPU,GPU,CPU", "embedding", "supported"),
        ("MULTI:GPU,CPU", "chat", "experimental"),
        ("MULTI:NPU,GPU,CPU", "chat", "unsupported"),
        ("HETERO:GPU,CPU", "chat", "experimental"),
        ("HETERO:NPU,GPU,CPU", "chat", "unsupported"),
        ("HETERO:NPU,CPU", "rerank", "unsupported"),
    ],
)
def test_support_matrix(device, kind, support):
    assert device_modes.describe(device, kind=kind, available=ULTRA)["support"] == support


def test_missing_member_is_unsupported_and_400():
    info = device_modes.describe("HETERO:GPU,CPU", kind="chat", available=["CPU"])
    assert info["missing"] == ["GPU"] and info["support"] == "unsupported"
    with pytest.raises(device_modes.DeviceModeError, match="not available"):
        device_modes.resolve_or_400("HETERO:GPU,CPU", backend="openvino-genai", available=["CPU"])


def test_resolve_rejects_unsupported_and_misplaced_options():
    with pytest.raises(device_modes.DeviceModeError, match="not supported"):
        device_modes.resolve_or_400("HETERO:NPU,CPU", backend="openvino-genai", available=ULTRA)
    assert (
        device_modes.resolve_or_400(
            "HETERO:NPU,CPU", backend="openvino-genai", available=ULTRA, allow_unsupported=True
        )["support"]
        == "unsupported"
    )
    with pytest.raises(device_modes.DeviceModeError, match="only applies to AUTO"):
        device_modes.resolve_or_400(
            "GPU", backend="openvino-genai", available=ULTRA, performance_hint="LATENCY"
        )
    with pytest.raises(device_modes.DeviceModeError, match="pipeline_parallel"):
        device_modes.resolve_or_400(
            "AUTO:GPU,CPU",
            backend="openvino-genai",
            available=ULTRA,
            model_distribution="pipeline_parallel",
        )
    with pytest.raises(device_modes.DeviceModeError):
        device_modes.resolve_or_400("BOGUS", backend="openvino-genai", available=None)


def test_engine_properties():
    assert device_modes.engine_properties("AUTO", backend="openvino-genai")[1] == {
        "PERFORMANCE_HINT": "LATENCY"
    }
    assert device_modes.engine_properties("AUTO", backend="openvino-embeddings")[1] == {}
    device, props, notice = device_modes.engine_properties("MULTI:GPU,CPU", backend="x")
    assert device == "AUTO:GPU,CPU" and props == {"PERFORMANCE_HINT": "CUMULATIVE_THROUGHPUT"}
    assert notice
    assert device_modes.engine_properties(
        "HETERO:GPU,CPU",
        backend="openvino-genai",
        options={"model_distribution": "pipeline_parallel"},
    )[1] == {"MODEL_DISTRIBUTION_POLICY": "PIPELINE_PARALLEL"}
    assert device_modes.engine_properties(
        "AUTO:GPU,CPU", backend="openvino-genai", options={"performance_hint": "THROUGHPUT"}
    )[1] == {"PERFORMANCE_HINT": "THROUGHPUT"}
    config = build_plugin_config(
        "AUTO:GPU,CPU", None, kv_cache_precision="u8", extra_properties=props
    )
    assert config == {"PERFORMANCE_HINT": "CUMULATIVE_THROUGHPUT"}  # KV stays auto-only


def test_execution_devices_probe_and_placement():
    assert probe_execution_devices(object()) is None
    pipe = SimpleNamespace(get_property=lambda name: ["GPU.0"])
    assert probe_execution_devices(pipe) == ["GPU.0"]
    engine = SimpleNamespace(execution_devices=["GPU.0"], execution_devices_source="openvino")
    assert device_modes.placement("AUTO", engine)["resolved_device"] == "AUTO \u2192 GPU.0"
    assert device_modes.placement("GPU", engine)["resolved_device"] == "GPU"
    assert model_slots._uses_npu("AUTO:NPU,GPU", engine) is False
    assert model_slots._uses_npu("AUTO:NPU,GPU", None) is True


def test_combined_failure_offers_single_device_fallback():
    exc = RuntimeError("compile failed")
    assert errors.model_load_error_code(exc, device="HETERO:GPU,CPU") == "combined_device_failed"
    assert errors.model_load_error_code(exc, device="GPU") is None
    actions = errors.load_recovery_actions(
        "combined_device_failed",
        weight_format="int4",
        has_source_model=True,
        available_devices=ULTRA,
        device="AUTO:NPU,GPU,CPU",
    )
    assert [a["id"] for a in actions] == ["load_gpu"]
    actions = errors.load_recovery_actions(
        "combined_device_failed",
        weight_format="int4",
        has_source_model=True,
        available_devices=["CPU", "NPU"],
        device="AUTO:NPU,CPU",
    )
    assert [a["id"] for a in actions] == ["load_cpu"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "app.hardware_advisor.snapshot.memory_stats",
        lambda: {"total_gb": 64.0, "available_gb": 48.0, "used_percent": 25.0},
    )
    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight",
        AsyncMock(return_value={"code": "hf_access_granted"}),
    )
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_modes_endpoint_in_mock(client):
    body = client.get("/v1/devices/modes").json()
    devices = {row["device"]: row for row in body["modes"]}
    assert {"AUTO", "AUTO:NPU,GPU,CPU", "AUTO:GPU,CPU", "HETERO:GPU,CPU"} <= set(devices)
    hetero = devices["HETERO:GPU,CPU"]["backends"]["chat"]
    assert hetero["support"] == "experimental"
    assert hetero["options"]["model_distribution"] == ["pipeline_parallel"]
    assert devices["HETERO:NPU,GPU,CPU"]["backends"]["chat"]["support"] == "unsupported"
    assert devices["AUTO:GPU,CPU"]["cumulative_throughput"]["chat"]["support"] == "experimental"
    assert body["execution_devices_reported"] is False


def test_load_validates_and_reports_resolved_device(client):
    bad = client.post("/v1/models/load", json={"model": CHAT, "device": "HETERO:NPU,CPU"})
    assert bad.status_code == 400 and "not supported" in bad.json()["detail"]
    bad = client.post(
        "/v1/models/load", json={"model": CHAT, "device": "GPU", "performance_hint": "LATENCY"}
    )
    assert bad.status_code == 400
    ok = client.post("/v1/models/load", json={"model": CHAT, "device": "MULTI:GPU,CPU"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["device_mode"]["normalized"] == "AUTO:GPU,CPU"
    assert body["device_mode"]["migrated_from"] == "MULTI:GPU,CPU"
    deadline = time.time() + 10
    slot = None
    while time.time() < deadline:
        slot = next(
            s for s in client.get("/v1/models/slots").json()["slots"] if s["role"] == "chat"
        )
        if slot["status"] == "loaded":
            break
        time.sleep(0.03)
    assert slot["device"] == "AUTO:GPU,CPU"
    assert slot["execution_devices"] == ["GPU"] and slot["execution_devices_source"] == "mock"
    assert slot["resolved_device"] == "AUTO:GPU,CPU \u2192 GPU"
    model = next(m for m in client.get("/v1/models").json()["data"] if m["id"] == CHAT)
    assert model["device"] == "AUTO:GPU,CPU"
