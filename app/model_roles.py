"""Model roles: which resident slot a catalog backend occupies.

InferBridge keeps at most one resident model per role so memory stays predictable:
one chat (text-generation or vision) model, one embedding model and one reranker.
The role is derived from the catalog ``backend`` and is never chosen by the client.
"""

from __future__ import annotations

ROLE_CHAT = "chat"
ROLE_EMBEDDING = "embedding"
ROLE_RERANK = "rerank"
ROLES = (ROLE_CHAT, ROLE_EMBEDDING, ROLE_RERANK)

RERANK_BACKEND = "openvino-rerank"
SUPPORTED_BACKENDS = ("openvino-genai", "openvino-embeddings", "openvino-vlm", RERANK_BACKEND)
# Shared by every Pydantic schema that accepts a catalog backend.
BACKEND_PATTERN = r"^(openvino-genai|openvino-embeddings|openvino-vlm|openvino-rerank)$"

ROLE_LABELS = {ROLE_CHAT: "Chat", ROLE_EMBEDDING: "Embeddings", ROLE_RERANK: "Reranker"}


def role_for_backend(backend: object) -> str:
    """Return the slot role for a catalog or engine backend string (mock included)."""

    value = str(backend or "").lower()
    if "rerank" in value:
        return ROLE_RERANK
    if "embedding" in value:
        return ROLE_EMBEDDING
    return ROLE_CHAT


def is_generation_backend(backend: object) -> bool:
    """True for text-generation and vision backends (the chat slot)."""

    return role_for_backend(backend) == ROLE_CHAT


def uses_output_budget(backend: object) -> bool:
    """Embedding and rerank models score input only and must use max_output_tokens=0."""

    return not is_generation_backend(backend)


def kind_phrase(backend: object) -> str:
    """Human phrase for a non-chat model in error copy ("an embedding model")."""

    return "a reranker model" if role_for_backend(backend) == ROLE_RERANK else "an embedding model"
