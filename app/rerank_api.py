"""Cohere/Jina/vLLM-compatible document reranking: ``POST /v1/rerank``.

RAG clients (Open WebUI external reranker, LlamaIndex, Dify, LangChain) send a query
and candidate documents and expect them back ordered by relevance:

    {"model": "...", "query": "...", "documents": ["..." | {"text": "..."}],
     "top_n": 3, "return_documents": true}
    -> {"id": "...", "model": "...", "results": [{"index": 0, "relevance_score": 0.98,
        "document": {"text": "..."}}], "usage": {...}}

Scoring runs on the resident reranker slot (``openvino-rerank`` backend, OpenVINO GenAI
``TextRerankPipeline``). Like embeddings, calls are serialized per engine and are
lifecycle-safe: unload waits for (or refuses during) an in-flight request.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import multimodal
from app.model_roles import ROLE_RERANK, role_for_backend

logger = logging.getLogger("ov-llm.rerank")

MAX_RERANK_DOCUMENTS = 512
MAX_RERANK_QUERY_CHARS = 32_000
MAX_RERANK_DOCUMENT_CHARS = 100_000
RERANK_PATHS = ("/v1/rerank", "/rerank", "/v2/rerank")


class RerankDocument(BaseModel):
    text: str


class RerankRequest(BaseModel):
    # Optional: when omitted the resident reranker slot is used.
    model: str | None = Field(default=None, max_length=160)
    query: str = Field(min_length=1, max_length=MAX_RERANK_QUERY_CHARS)
    documents: list[str | RerankDocument] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)
    return_documents: bool = True
    user: str | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Rerank query cannot be blank.")
        return value

    @field_validator("documents")
    @classmethod
    def validate_documents(cls, value: list[str | RerankDocument]) -> list[str | RerankDocument]:
        if len(value) > MAX_RERANK_DOCUMENTS:
            raise ValueError(
                f"Rerank requests may contain at most {MAX_RERANK_DOCUMENTS} documents."
            )
        texts = [item if isinstance(item, str) else item.text for item in value]
        if any(not text.strip() for text in texts):
            raise ValueError("Rerank documents cannot be empty.")
        if any(len(text) > MAX_RERANK_DOCUMENT_CHARS for text in texts):
            raise ValueError(
                f"Each rerank document may contain at most {MAX_RERANK_DOCUMENT_CHARS:,} characters."
            )
        if sum(len(text) for text in texts) > multimodal.MAX_REQUEST_TEXT_CHARS:
            raise ValueError(
                f"Rerank documents may contain at most {multimodal.MAX_REQUEST_TEXT_CHARS:,} "
                "characters in total."
            )
        return value

    def texts(self) -> list[str]:
        return [item if isinstance(item, str) else item.text for item in self.documents]


def order_results(
    scored: list[tuple[int, float]], texts: list[str], top_n: int | None, return_documents: bool
) -> list[dict[str, Any]]:
    """Sort by score (stable by original index) and apply ``top_n``."""

    seen: set[int] = set()
    rows: list[tuple[int, float]] = []
    for index, score in scored:
        index = int(index)
        if not 0 <= index < len(texts) or index in seen:
            raise RuntimeError("The reranker returned an invalid document index.")
        seen.add(index)
        rows.append((index, float(score)))
    if len(rows) != len(texts):
        raise RuntimeError("The reranker did not score every document.")
    rows.sort(key=lambda item: (-item[1], item[0]))
    limit = min(top_n, len(rows)) if top_n else len(rows)
    results = []
    for index, score in rows[:limit]:
        item: dict[str, Any] = {"index": index, "relevance_score": round(score, 6)}
        if return_documents:
            item["document"] = {"text": texts[index]}
        results.append(item)
    return results


def install_rerank_api(
    app: FastAPI,
    *,
    manager: Any,
    dependencies: list[Any],
    resolve_engine: Callable[[str], Any],
    record_key_metrics: Callable[[int, int, float], None],
) -> None:
    """Register ``/v1/rerank`` and its ``/rerank`` and ``/v2/rerank`` aliases."""

    from app.engine_handoff_safety import current_engine_lease
    from app.model_slots import resident_for_role

    async def create_rerank(request: RerankRequest) -> dict[str, Any]:
        model_id = (request.model or "").strip()
        if not model_id:
            resident = resident_for_role(manager, ROLE_RERANK)
            if resident is None:
                raise HTTPException(
                    status_code=503,
                    detail="No reranker model is loaded. Load one as the reranker first.",
                )
            model_id = resident
        engine = resolve_engine(model_id)
        if role_for_backend(getattr(engine, "backend", "")) != ROLE_RERANK:
            raise HTTPException(
                status_code=400, detail=f"Model '{model_id}' is not a reranker model."
            )

        texts = request.texts()
        start = time.perf_counter()
        async with manager._track_generation():
            async with current_engine_lease(manager, engine) as active_engine:

                def rerank_and_count():
                    scored = active_engine.rerank(request.query, texts)
                    tokens = sum(active_engine.count_tokens(request.query + text) for text in texts)
                    return scored, tokens

                try:
                    worker = asyncio.create_task(asyncio.to_thread(rerank_and_count))
                    result, cancellation = await manager._await_resilient_future(worker)
                    if cancellation is not None:
                        raise cancellation
                    scored, prompt_tokens = result
                    results = order_results(scored, texts, request.top_n, request.return_documents)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - native errors are untrusted
                    logger.exception("Rerank inference failed: %s", exc)
                    raise HTTPException(
                        status_code=500,
                        detail="Rerank inference failed; see server logs for the request ID.",
                    ) from exc

        latency = time.perf_counter() - start
        manager.record_request(engine.model_id, prompt_tokens, 0, latency)
        record_key_metrics(prompt_tokens, 0, latency)
        return {
            "id": f"rerank-{secrets.token_hex(12)}",
            "object": "rerank",
            "model": model_id,
            "results": results,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }

    for index, path in enumerate(RERANK_PATHS):
        app.add_api_route(
            path,
            create_rerank,
            methods=["POST"],
            dependencies=dependencies,
            include_in_schema=index == 0,
            name="create_rerank" if index == 0 else f"create_rerank_alias_{index}",
        )


__all__ = ["RerankRequest", "install_rerank_api", "order_results"]
