"""POST /v1/rerank: Cohere/Jina/vLLM request shape, validation, ordering and engines."""

from __future__ import annotations

import sys
import threading
import time
import types
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import model_registry, multimodal, rerank_api
from app.config import BASE_DIR, Settings
from app.context_configuration import is_configurable
from app.model_roles import is_generation_backend
from app.server import create_app
from runtime import model_converter
from runtime.openvino_engine import MockRerankEngine, OpenVINORerankEngine, create_engine

RERANK = "bge-reranker-base"
EMBED = "bge-small-en-v1.5"
DOCS = ["Berlin is in Germany", {"text": "Paris is the capital of France"}, "Bananas are yellow"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "app.hardware_advisor.snapshot.memory_stats",
        lambda: {"total_gb": 64.0, "available_gb": 48.0, "used_percent": 25.0},
    )  # deterministic host RAM so slot memory checks never depend on the runner
    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight",
        AsyncMock(return_value={"code": "hf_access_granted"}),
    )
    settings = Settings(
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
        device="CPU",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _load(client, model_id=RERANK):
    assert client.post("/v1/models/load", json={"model": model_id}).status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline:
        if model_id in client.get("/v1/models/status").json()["models"]["loaded"]:
            return
        time.sleep(0.03)
    raise AssertionError("reranker did not load")


def _rerank(client, path="/v1/rerank", **body):
    payload = {"model": RERANK, "query": "capital of France", "documents": DOCS}
    payload.update(body)
    return client.post(path, json=payload)


def test_rerank_orders_by_relevance_with_documents_and_usage(client):
    _load(client)
    response = _rerank(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == RERANK and body["id"].startswith("rerank-")
    results = body["results"]
    assert [item["index"] for item in results][0] == 1
    assert len(results) == 3
    scores = [item["relevance_score"] for item in results]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)
    assert results[0]["document"] == {"text": "Paris is the capital of France"}
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] > 0


def test_top_n_and_return_documents(client):
    _load(client)
    body = _rerank(client, top_n=1, return_documents=False).json()
    assert body["results"] == [
        {"index": 1, "relevance_score": body["results"][0]["relevance_score"]}
    ]
    clamped = _rerank(client, top_n=50).json()
    assert len(clamped["results"]) == 3


@pytest.mark.parametrize("path", ["/rerank", "/v2/rerank"])
def test_aliases_share_the_contract(client, path):
    _load(client)
    response = _rerank(client, path=path)
    assert response.status_code == 200
    assert response.json()["results"][0]["index"] == 1


def test_model_can_be_omitted_to_use_the_resident_reranker(client):
    missing = client.post("/v1/rerank", json={"query": "q", "documents": ["a"]})
    assert missing.status_code == 503
    assert "No reranker model is loaded" in missing.json()["detail"]
    _load(client)
    response = client.post("/v1/rerank", json={"query": "capital France", "documents": DOCS})
    assert response.status_code == 200
    assert response.json()["model"] == RERANK


def test_unknown_unloaded_and_wrong_role_models(client):
    assert _rerank(client, model="nope").status_code == 404
    assert _rerank(client).status_code == 409  # cataloged but not loaded
    _load(client, EMBED)
    wrong = _rerank(client, model=EMBED)
    assert wrong.status_code == 400
    assert "not a reranker" in wrong.json()["detail"]


@pytest.mark.parametrize(
    "override",
    [
        {"documents": []},
        {"documents": [""]},
        {"documents": [{"text": "   "}]},
        {"documents": [{"title": "missing text"}]},
        {"documents": ["x"] * (rerank_api.MAX_RERANK_DOCUMENTS + 1)},
        {"documents": ["x" * (rerank_api.MAX_RERANK_DOCUMENT_CHARS + 1)]},
        {"query": ""},
        {"query": "   "},
        {"query": "q" * (rerank_api.MAX_RERANK_QUERY_CHARS + 1)},
        {"top_n": 0},
        {"top_n": -1},
    ],
)
def test_invalid_requests_are_rejected_before_inference(client, override):
    _load(client)
    response = _rerank(client, **override)
    assert response.status_code == 422, response.text


def test_total_character_budget(client, monkeypatch):
    _load(client)
    monkeypatch.setattr(multimodal, "MAX_REQUEST_TEXT_CHARS", 10)
    response = _rerank(client, documents=["aaaaaa", "bbbbbb"])
    assert response.status_code == 422


def test_engine_failure_is_sanitized(client, monkeypatch):
    _load(client)

    def boom(self, query, documents):
        raise RuntimeError("C:\\secret\\path native failure")

    monkeypatch.setattr(MockRerankEngine, "rerank", boom)
    response = _rerank(client)
    assert response.status_code == 500
    assert "secret" not in response.text


def test_rerank_records_request_metrics(client):
    _load(client)
    _rerank(client)
    metrics = client.get("/v1/system/status").json()["metrics"]["per_model"][RERANK]
    assert metrics["requests"] == 1 and metrics["completion_tokens"] == 0


def test_unload_is_refused_while_a_rerank_request_holds_the_slot(client, monkeypatch):
    _load(client)
    entered = threading.Event()
    release = threading.Event()
    original = MockRerankEngine.rerank

    def slow(self, query, documents):
        entered.set()
        release.wait(5)
        return original(self, query, documents)

    monkeypatch.setattr(MockRerankEngine, "rerank", slow)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("r", _rerank(client)))
    worker.start()
    assert entered.wait(5)
    busy = client.post("/v1/models/unload", json={"slot": "rerank"})
    release.set()
    worker.join(5)
    assert busy.status_code == 409
    assert result["r"].status_code == 200
    assert client.post("/v1/models/unload", json={"slot": "rerank"}).status_code == 200


# --- ordering helper -----------------------------------------------------------------


def test_order_results_is_stable_and_validates_indices():
    texts = ["a", "b", "c"]
    ordered = rerank_api.order_results([(2, 0.5), (0, 0.5), (1, 0.9)], texts, None, True)
    assert [item["index"] for item in ordered] == [1, 0, 2]
    with pytest.raises(RuntimeError):
        rerank_api.order_results([(0, 0.1), (0, 0.2), (1, 0.3)], texts, None, False)
    with pytest.raises(RuntimeError):
        rerank_api.order_results([(0, 0.1), (5, 0.2), (1, 0.3)], texts, None, False)
    with pytest.raises(RuntimeError):
        rerank_api.order_results([(0, 0.1)], texts, None, False)


# --- engines, catalog and conversion ---------------------------------------------------


def test_mock_engine_is_created_for_the_rerank_backend():
    engine = create_engine(
        model_id="r", model_path="", device="CPU", force_mock=True, backend="openvino-rerank"
    )
    assert isinstance(engine, MockRerankEngine)
    scored = engine.rerank("red apple", ["green pear", "red apple pie", "apple"])
    assert scored[0][0] == 1 and len(scored) == 3
    engine.close()
    with pytest.raises(RuntimeError):
        engine.rerank("q", ["d"])


def test_openvino_engine_batches_and_requests_every_score(monkeypatch):
    calls = []

    class RerankConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Pipeline:
        Config = RerankConfig

        def __init__(self, path, device, config, **plugin):
            calls.append(("init", path, device, config.kwargs, plugin))

        def rerank(self, query, texts):
            calls.append(("rerank", len(texts)))
            # Highest score first, as TextRerankPipeline returns (index, score) pairs.
            return sorted(((i, float(len(t))) for i, t in enumerate(texts)), key=lambda x: -x[1])

    fake = types.ModuleType("openvino_genai")
    fake.TextRerankPipeline = Pipeline
    monkeypatch.setitem(sys.modules, "openvino_genai", fake)
    engine = OpenVINORerankEngine("r", "/m", "CPU", {"CACHE_DIR": "/c"}, max_length=512)
    docs = ["x" * (i % 7 + 1) for i in range(70)]
    scored = engine.rerank("q", docs)
    assert calls[0] == ("init", "/m", "CPU", {"top_n": 32, "max_length": 512}, {"CACHE_DIR": "/c"})
    assert [call[1] for call in calls[1:]] == [32, 32, 6]
    assert sorted(index for index, _ in scored) == list(range(70))
    assert scored[0][1] == 7.0
    assert engine.count_tokens("y" * 10_000) == 512
    engine.close()
    with pytest.raises(RuntimeError):
        engine.rerank("q", ["d"])


def test_rerank_backend_is_a_scoring_only_catalog_role(tmp_path):
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        '{"good": {"backend": "openvino-rerank", "max_context_len": 512, '
        '"max_output_tokens": 0, "source_model": "BAAI/bge-reranker-base"},'
        '"bad": {"backend": "openvino-rerank", "max_context_len": 512, "max_output_tokens": 16}}',
        encoding="utf-8",
    )
    catalog = model_registry.load_catalog(catalog_file)
    assert set(catalog) == {"good"}
    cfg = catalog["good"]
    assert cfg.capabilities == ("rerank",)
    assert not is_generation_backend(cfg.backend)
    assert not is_configurable(cfg)


def test_reranker_conversion_uses_the_text_classification_task(monkeypatch, tmp_path):
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        '{"rr": {"backend": "openvino-rerank", "max_context_len": 512, '
        '"max_output_tokens": 0, "source_model": "BAAI/bge-reranker-base"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OV_LLM_MODELS_FILE", str(catalog_file))
    *_, task, trust = model_converter._resolve_from_catalog("rr", include_task=True)
    assert task == "text-classification" and trust is False


def test_rerank_engines_share_the_embedding_lifecycle_guard():
    from app.embedding_lifecycle_safety import _ENGINE_INSTALL_FLAG

    assert MockRerankEngine.__dict__.get(_ENGINE_INSTALL_FLAG) is True
    assert OpenVINORerankEngine.__dict__.get(_ENGINE_INSTALL_FLAG) is True
    engine = MockRerankEngine("r")
    engine.close()
    with pytest.raises(RuntimeError, match="unloading|closed"):
        engine.rerank("q", ["d"])


# --- three resident slots ------------------------------------------------------------

CHAT = "tinyllama-1.1b-chat-fp16"


def test_chat_embedding_and_reranker_stay_resident_together(client):
    for model_id in (CHAT, EMBED, RERANK):
        _load(client, model_id)
    slots = {row["role"]: row for row in client.get("/v1/models/slots").json()["slots"]}
    assert {role: row["model"] for role, row in slots.items()} == {
        "chat": CHAT,
        "embedding": EMBED,
        "rerank": RERANK,
    }
    listing = {row["id"]: row for row in client.get("/v1/models").json()["data"]}
    assert listing[RERANK]["role"] == "rerank" and listing[RERANK]["loaded"] is True
    assert client.get("/health").json()["slots"]["rerank"] == RERANK
    entry = next(
        item
        for item in client.get("/v1/models/status").json()["models"]["available"]
        if item["id"] == RERANK
    )
    assert entry["capabilities"] == ["rerank"] and entry["role"] == "rerank"


def test_generation_routes_reject_reranker_models(client):
    _load(client)
    chat = client.post(
        "/v1/chat/completions",
        json={"model": RERANK, "messages": [{"role": "user", "content": "x"}]},
    )
    assert chat.status_code == 400 and "reranker model" in chat.json()["detail"]
    assert client.post("/v1/responses", json={"model": RERANK, "input": "x"}).status_code == 400
    assert client.post("/v1/embeddings", json={"model": RERANK, "input": "x"}).status_code == 400


def test_concurrent_chat_embedding_and_rerank_requests(client):
    from concurrent.futures import ThreadPoolExecutor

    for model_id in (CHAT, EMBED, RERANK):
        _load(client, model_id)

    def call(kind: int) -> int:
        if kind == 0:
            return client.post(
                "/v1/chat/completions",
                json={"model": CHAT, "messages": [{"role": "user", "content": "hi"}]},
            ).status_code
        if kind == 1:
            return client.post("/v1/embeddings", json={"model": EMBED, "input": ["a"]}).status_code
        return _rerank(client).status_code

    with ThreadPoolExecutor(max_workers=9) as pool:
        codes = list(pool.map(call, [i % 3 for i in range(18)]))
    assert codes == [200] * 18
    loaded = client.get("/v1/models/status").json()["models"]["loaded"]
    assert set(loaded) == {CHAT, EMBED, RERANK}
