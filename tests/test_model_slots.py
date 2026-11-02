"""Role-based resident slots (chat + embedding + rerank) on the mock engine."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import model_slots
from app.config import BASE_DIR, Settings
from app.engine_handoff_safety import ModelBusyError
from app.model_roles import role_for_backend
from app.server import create_app

CHAT = "tinyllama-1.1b-chat-fp16"
OTHER_CHAT = "qwen2.5-0.5b-fp16"
EMBED = "bge-small-en-v1.5"


@pytest.fixture()
def client(monkeypatch):
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
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _loaded(client) -> list[str]:
    return client.get("/v1/models/status").json()["models"]["loaded"]


def _load(client, model_id, timeout=10.0, **extra):
    response = client.post("/v1/models/load", json={"model": model_id, **extra})
    assert response.status_code == 200, response.text
    deadline = time.time() + timeout
    while time.time() < deadline:
        if model_id in _loaded(client):
            return response.json()
        time.sleep(0.03)
    raise AssertionError(f"{model_id} did not load in time")


def _load_all(client):
    for model_id in (CHAT, EMBED):
        _load(client, model_id)


def _slots(client) -> dict[str, dict]:
    rows = client.get("/v1/models/slots").json()["slots"]
    return {row["role"]: row for row in rows}


def _chat(client, model=CHAT):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
    )


def _manager(client):
    for route in client.app.routes:
        endpoint = getattr(route, "endpoint", None)
        closure = getattr(endpoint, "__closure__", None) or ()
        for cell in closure:
            value = cell.cell_contents
            if hasattr(value, "slot_snapshot") and hasattr(value, "engines"):
                return value
    raise AssertionError("manager not found")


# --- roles -----------------------------------------------------------------


def test_roles_follow_the_catalog_backend():
    assert role_for_backend("openvino-genai") == "chat"
    assert role_for_backend("openvino-vlm") == "chat"
    assert role_for_backend("mock") == "chat"
    assert role_for_backend("openvino-embeddings") == "embedding"
    assert role_for_backend("mock-embeddings") == "embedding"
    assert role_for_backend("openvino-rerank") == "rerank"
    assert role_for_backend("mock-rerank") == "rerank"


# --- slot routing ------------------------------------------------------------


def test_chat_and_embedding_stay_resident_and_route_by_model(client):
    _load_all(client)
    assert set(_loaded(client)) == {CHAT, EMBED}

    slots = _slots(client)
    assert slots["chat"]["model"] == CHAT and slots["chat"]["status"] == "loaded"
    assert slots["embedding"]["model"] == EMBED
    assert slots["rerank"]["model"] is None and slots["rerank"]["status"] == "empty"
    for role in ("chat", "embedding"):
        assert slots[role]["device"] and slots[role]["can_unload"] is True
        estimate = slots[role]["memory_estimate_gib"]
        assert estimate is None or estimate > 0

    assert _chat(client).status_code == 200
    embeddings = client.post("/v1/embeddings", json={"model": EMBED, "input": ["a", "b"]})
    assert embeddings.status_code == 200 and len(embeddings.json()["data"]) == 2

    listing = client.get("/v1/models").json()
    rows = {row["id"]: row for row in listing["data"]}
    assert rows[CHAT]["role"] == "chat" and rows[CHAT]["loaded"] is True
    assert rows[EMBED]["role"] == "embedding" and rows[EMBED]["device"]
    assert rows[OTHER_CHAT]["role"] == "chat" and rows[OTHER_CHAT]["loaded"] is False
    assert {row["role"] for row in listing["slots"]} == {"chat", "embedding", "rerank"}

    assert client.get("/health").json()["slots"] == {"chat": CHAT, "embedding": EMBED}
    status = client.get("/v1/system/status").json()
    assert {row["role"] for row in status["models"]["slots"]} == {"chat", "embedding", "rerank"}
    entry = next(item for item in status["models"]["available"] if item["id"] == EMBED)
    assert entry["role"] == "embedding"
    split = client.get("/v1/models/status").json()["models"]["slots"]
    assert [row["role"] for row in split] == ["chat", "embedding", "rerank"]


def test_loading_a_new_chat_model_replaces_only_the_chat_slot(client):
    _load_all(client)
    body = _load(client, OTHER_CHAT)
    assert body["slot"] == "chat"
    assert body["replaced"] == CHAT
    assert set(_loaded(client)) == {OTHER_CHAT, EMBED}
    events = client.get("/v1/system/status").json()["events"]
    assert any(
        "Replaced" in event["message"] and "chat slot" in event["message"] for event in events
    )
    # The replaced model now reports a clean not-loaded state.
    assert _chat(client, CHAT).status_code == 409
    assert _chat(client, OTHER_CHAT).status_code == 200


def test_routes_still_check_the_model_role(client):
    _load_all(client)
    response = _chat(client, EMBED)
    assert response.status_code == 400
    assert "embedding model" in response.json()["detail"]
    assert client.post("/v1/embeddings", json={"model": CHAT, "input": "hi"}).status_code == 400


def test_concurrent_chat_and_embedding_requests_all_succeed(client):
    _load_all(client)

    def call(kind: int):
        if kind == 0:
            return _chat(client).status_code
        return client.post("/v1/embeddings", json={"model": EMBED, "input": ["x", "y"]}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(call, [i % 2 for i in range(16)]))
    assert codes == [200] * 16
    assert set(_loaded(client)) == {CHAT, EMBED}


# --- per-slot unload -------------------------------------------------------------


def test_unload_by_slot_releases_only_that_slot(client):
    _load_all(client)
    response = client.post("/v1/models/unload", json={"slot": "embedding"})
    assert response.status_code == 200, response.text
    assert response.json()["slot"] == "embedding"
    assert _loaded(client) == [CHAT]
    assert _slots(client)["embedding"]["status"] == "empty"

    empty = client.post("/v1/models/unload", json={"slot": "embedding"})
    assert empty.status_code == 409
    assert "No model is loaded in the embeddings slot" in empty.json()["detail"]

    mismatch = client.post("/v1/models/unload", json={"slot": "embedding", "model": CHAT})
    assert mismatch.status_code == 400
    assert client.post("/v1/models/unload", json={}).status_code == 422

    # Legacy by-model unload keeps working for the chat slot.
    legacy = client.post("/v1/models/unload", json={"model": CHAT})
    assert legacy.status_code == 200
    assert _loaded(client) == []


def test_load_slot_must_match_the_model_role(client):
    response = client.post("/v1/models/load", json={"model": EMBED, "slot": "chat"})
    assert response.status_code == 400
    assert "embeddings model" in response.json()["detail"]
    ok = _load(client, EMBED, slot="embedding")
    assert ok["slot"] == "embedding"


# --- backward compatibility ---------------------------------------------------------


def test_single_chat_flows_are_unchanged(client):
    first = _load(client, CHAT)
    assert first["slot"] == "chat" and "replaced" not in first
    again = client.post("/v1/models/load", json={"model": CHAT})
    assert again.status_code == 200 and again.json()["status"] == "loaded"
    # A bad draft model is rejected before the resident chat model is released.
    bad = client.post("/v1/models/load", json={"model": OTHER_CHAT, "draft_model": EMBED})
    assert bad.status_code == 400
    assert _loaded(client) == [CHAT]
    # Context changes on the loaded chat model still go through the context route.
    conflict = client.post("/v1/models/load", json={"model": CHAT, "context_tokens": 1024})
    assert conflict.status_code == 409
    assert _chat(client).status_code == 200


def test_device_switch_of_the_resident_chat_model_is_not_a_replacement(client, monkeypatch):
    _load(client, CHAT)
    _load(client, EMBED)
    # The switch frees the model's own current footprint, so it fits even when the
    # free RAM alone would not hold a second copy (estimate 3 GB, 2 GB free).
    _fake_memory(monkeypatch, client, available_gb=2.0, estimate_gb=3.0)
    response = client.post("/v1/models/load", json={"model": CHAT, "device": "GPU"})
    assert response.status_code == 200
    assert "replaced" not in response.json()
    deadline = time.time() + 10
    while time.time() < deadline:
        slot = _slots(client)["chat"]
        if slot["device"] == "GPU" and slot["status"] == "loaded":
            break
        time.sleep(0.03)
    assert _slots(client)["chat"]["model"] == CHAT
    assert set(_loaded(client)) == {CHAT, EMBED}


# --- memory policy ---------------------------------------------------------


def _fake_memory(monkeypatch, client, *, available_gb, estimate_gb, gpu_total=None):
    manager = _manager(client)
    snapshot = {"memory": {"available_gb": available_gb}}
    if gpu_total is not None:
        snapshot["gpu"] = {"total_memory_gb": gpu_total}
    monkeypatch.setattr(manager.advisor, "hardware_snapshot", lambda *a, **k: snapshot)
    real_estimate = manager.advisor.estimate_model
    monkeypatch.setattr(
        manager.advisor,
        "estimate_model",
        lambda cfg, device=None: {
            **real_estimate(cfg, device=device),
            "runtime_memory_gb": estimate_gb,
        },
    )
    return manager


def test_second_model_is_refused_when_combined_memory_cannot_fit(client, monkeypatch):
    _fake_memory(monkeypatch, client, available_gb=3.0, estimate_gb=5.0)
    # The first model alone keeps the single-model behavior (no refusal).
    first = _load(client, CHAT)
    assert first["memory"]["verdict"] == "ok"

    refused = client.post("/v1/models/load", json={"model": EMBED})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "Not enough memory" in detail and "5.0 GB" in detail
    assert _loaded(client) == [CHAT]

    plan = client.get("/v1/models/slots", params={"model": EMBED, "device": "CPU"}).json()["plan"]
    assert plan["verdict"] == "refuse"
    assert plan["combined_estimate_gib"] == 10.0
    assert plan["resident"][0]["model"] == CHAT

    forced = _load(client, EMBED, allow_resource_risk=True)
    assert forced["memory"]["overridden"] is True
    assert set(_loaded(client)) == {CHAT, EMBED}


def test_replacing_a_slot_credits_the_memory_it_frees(client, monkeypatch):
    _load(client, CHAT)
    _load(client, EMBED)
    # 3 GB free plus the 5 GB the replaced chat model releases fits a 5 GB model.
    _fake_memory(monkeypatch, client, available_gb=3.0, estimate_gb=5.0)
    response = _load(client, OTHER_CHAT)
    assert response["memory"]["freed_by_replacement_gib"] == 5.0
    assert response["memory"]["verdict"] in {"ok", "warning"}


def test_tight_memory_warns_but_loads(client, monkeypatch):
    _fake_memory(monkeypatch, client, available_gb=6.0, estimate_gb=5.0)
    _load(client, CHAT)
    body = _load(client, EMBED)
    assert body["memory"]["verdict"] == "warning"
    assert "Memory will be tight" in body["message"]


def test_gpu_memory_overcommit_warns(client, monkeypatch):
    _fake_memory(monkeypatch, client, available_gb=64.0, estimate_gb=3.0, gpu_total=4.0)
    _load(client, CHAT, device="GPU")
    body = _load(client, EMBED, device="GPU")
    assert body["memory"]["verdict"] == "warning"
    assert "GPU" in body["memory"]["message"]


def test_two_npu_residents_are_refused_conservatively(client, monkeypatch):
    _fake_memory(monkeypatch, client, available_gb=64.0, estimate_gb=1.0)
    # Mock engines never touch the NPU; model real hardware for the policy check.
    monkeypatch.setattr(model_slots, "_simulated", lambda manager: False)
    _load(client, CHAT, device="NPU")
    refused = client.post("/v1/models/load", json={"model": EMBED, "device": "NPU"})
    assert refused.status_code == 409
    assert "NPU" in refused.json()["detail"]
    # Embeddings on CPU beside an NPU chat model is the supported mix.
    body = _load(client, EMBED, device="CPU")
    assert body["memory"]["verdict"] == "ok"


# --- eviction guards (unit) ----------------------------------------------------------


class _FakeManager:
    def __init__(self, *, busy=False, loading=None):
        self.catalog = {
            "a": SimpleNamespace(name="A", backend="openvino-genai"),
            "b": SimpleNamespace(name="B", backend="openvino-genai"),
            "e": SimpleNamespace(name="E", backend="openvino-embeddings"),
        }
        self.engines = {"a": object(), "e": object()}
        self.devices = {"a": "CPU", "e": "CPU"}
        self.locks = {}
        self.load_tasks = {}
        self._context_changes = set()
        self.unloaded = []
        self.events = []
        self.busy = busy
        if loading:
            self.load_tasks[loading] = SimpleNamespace(done=lambda: False)

    def unload(self, model_id):
        if self.busy:
            raise ModelBusyError("busy")
        self.unloaded.append(model_id)
        self.engines.pop(model_id)

    def emit_event(self, level, message):
        self.events.append(message)


def test_eviction_replaces_only_the_same_role_model():
    manager = _FakeManager()
    model_slots._evict_same_role_peers(manager, "b")
    assert manager.unloaded == ["a"] and "e" in manager.engines
    assert "chat slot" in manager.events[0]


def test_busy_resident_model_blocks_replacement():
    manager = _FakeManager(busy=True)
    with pytest.raises(model_slots.SlotConflictError, match="busy"):
        model_slots._evict_same_role_peers(manager, "b")
    assert "a" in manager.engines


def test_queued_same_role_loads_end_with_only_the_newest_resident(monkeypatch):
    import asyncio

    from app import model_load_target
    from app.model_manager import ModelManager

    async def scenario() -> None:
        monkeypatch.setattr(model_load_target, "_LOAD_WAIT_UPDATE_SECONDS", 0.02)
        manager = ModelManager(
            Settings(
                models_file=BASE_DIR / "models.json",
                models_dir=BASE_DIR / "models" / "openvino",
                default_model=None,
                force_mock=True,
                device="CPU",
            )
        )
        await manager._load_lock.acquire()
        first = manager.schedule_load(CHAT, "CPU")
        second = manager.schedule_load(OTHER_CHAT, "CPU")
        embed = manager.schedule_load(EMBED, "CPU")
        manager._load_lock.release()
        await asyncio.wait_for(asyncio.gather(first, second, embed), timeout=5)
        for _ in range(100):
            if CHAT not in manager.engines:
                break
            await asyncio.sleep(0.02)
        assert set(manager.engines) == {OTHER_CHAT, EMBED}
        await manager.shutdown()

    asyncio.run(scenario())


def test_context_change_blocks_replacement():
    manager = _FakeManager()
    manager._context_changes.add("a")
    with pytest.raises(model_slots.SlotConflictError, match="context change"):
        model_slots._evict_same_role_peers(manager, "b")


def test_npu_rule_only_advises_in_mock_mode(client, monkeypatch):
    _fake_memory(monkeypatch, client, available_gb=64.0, estimate_gb=1.0)
    _load(client, CHAT, device="NPU")
    body = _load(client, EMBED, device="NPU")
    assert body["memory"]["verdict"] == "warning"
    assert body["memory"]["npu_conflict"] == CHAT


def test_shutdown_drains_and_unloads_every_slot():
    settings = Settings(
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
        device="CPU",
    )
    with TestClient(create_app(settings)) as test_client:
        _load_all(test_client)
        manager = _manager(test_client)
        engines = list(manager.engines.values())
        assert len(engines) == 2
    assert manager.engines == {}
    assert all(getattr(engine, "_closed", True) for engine in engines)
