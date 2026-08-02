"""Exact, privacy-preserving context budget preflight for chat requests.

The endpoint uses the loaded model's tokenizer and the same whole-turn retention
algorithm as generation. It returns only bounded summaries and omission previews;
request content is never persisted or logged by this module.
"""

from __future__ import annotations

import asyncio
import functools
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import Field

from app import chat_format, multimodal, tools
from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import require_safe_browser_origin
from app.openai_api import ChatCompletionRequest

_ROUTE_INSTALL_FLAG = "_ovllm_context_budget_routes_installed"
_PREVIEW_LIMIT = 180
_MAX_PREVIEW_MESSAGES = 12
_SAFETY_TOKENS = 8

ApplyTemplate = Callable[[list[dict[str, Any]]], str]
CountTokens = Callable[[str], int]
IndexedMessage = tuple[int, dict[str, Any]]


class ContextBudgetRequest(ChatCompletionRequest):
    """Chat request plus pending image count not yet embedded in browser messages."""

    image_count: int = Field(default=0, ge=0, le=multimodal.MAX_IMAGES_PER_REQUEST)


@dataclass(frozen=True, slots=True)
class PromptBudgetAnalysis:
    prompt: str
    prompt_tokens: int
    retained_indexes: tuple[int, ...]
    dropped_indexes: tuple[int, ...]
    dropped_turns: int


def _render_and_count(
    messages: list[dict[str, Any]],
    apply_template: ApplyTemplate,
    count_tokens: CountTokens,
) -> tuple[str, int]:
    prompt = apply_template(messages)
    try:
        return prompt, count_tokens(prompt)
    except BaseException:
        multimodal.discard_prompt_context(prompt)
        raise


def _split_indexed_turns(
    messages: list[dict[str, Any]],
) -> tuple[list[IndexedMessage], list[list[IndexedMessage]]]:
    """Mirror chat_format's whole-turn grouping while retaining source indexes."""

    prefix: list[IndexedMessage] = []
    cursor = 0
    while cursor < len(messages) and messages[cursor].get("role") == "system":
        prefix.append((cursor, messages[cursor]))
        cursor += 1

    turns: list[list[IndexedMessage]] = []
    current: list[IndexedMessage] = []
    for index, message in enumerate(messages[cursor:], start=cursor):
        starts_new_turn = message.get("role") == "user" and not (
            current and chat_format._is_tool_result_message(message)
        )
        if starts_new_turn:
            if current and current[0][1].get("role") == "user":
                turns.append(current)
            current = [(index, message)]
        elif current:
            current.append((index, message))
        elif message.get("role") == "user":
            current = [(index, message)]

    if current and current[0][1].get("role") == "user":
        turns.append(current)

    if not turns:
        remainder = list(enumerate(messages[cursor:], start=cursor))
        if remainder:
            turns = [[remainder[-1]]]

    return prefix, turns


def _flatten_indexed(turns: list[list[IndexedMessage]]) -> list[IndexedMessage]:
    return [message for turn in turns for message in turn]


def analyze_prompt_budget(
    messages: list[dict[str, Any]],
    apply_template: ApplyTemplate,
    count_tokens: CountTokens,
    max_prompt_len: int,
) -> PromptBudgetAnalysis:
    """Render the exact retained prompt and report which messages were omitted."""

    indexed_all = list(enumerate(messages))
    if not messages:
        prompt, tokens = _render_and_count([], apply_template, count_tokens)
        return PromptBudgetAnalysis(prompt, tokens, (), (), 0)

    full_prompt, full_tokens = _render_and_count(messages, apply_template, count_tokens)
    if full_tokens <= max_prompt_len:
        return PromptBudgetAnalysis(
            full_prompt,
            full_tokens,
            tuple(index for index, _ in indexed_all),
            (),
            0,
        )
    multimodal.discard_prompt_context(full_prompt)

    system, turns = _split_indexed_turns(messages)
    if not turns:
        system_messages = [message for _, message in system]
        prompt, tokens = _render_and_count(system_messages, apply_template, count_tokens)
        retained = tuple(index for index, _ in system)
        retained_set = set(retained)
        dropped = tuple(index for index, _ in indexed_all if index not in retained_set)
        return PromptBudgetAnalysis(prompt, tokens, retained, dropped, 0)

    low = 1
    high = len(turns)
    best_k = 1
    while low <= high:
        mid = (low + high) // 2
        candidate_indexed = system + _flatten_indexed(turns[-mid:])
        candidate_messages = [message for _, message in candidate_indexed]
        candidate, candidate_tokens = _render_and_count(
            candidate_messages,
            apply_template,
            count_tokens,
        )
        multimodal.discard_prompt_context(candidate)
        if candidate_tokens <= max_prompt_len:
            best_k = mid
            low = mid + 1
        else:
            high = mid - 1

    retained_indexed = system + _flatten_indexed(turns[-best_k:])
    retained_messages = [message for _, message in retained_indexed]
    prompt, tokens = _render_and_count(retained_messages, apply_template, count_tokens)
    retained = tuple(index for index, _ in retained_indexed)
    retained_set = set(retained)
    dropped = tuple(index for index, _ in indexed_all if index not in retained_set)
    return PromptBudgetAnalysis(
        prompt,
        tokens,
        retained,
        dropped,
        max(0, len(turns) - best_k),
    )


def _actual_image_count(messages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for message in messages
        for _ in multimodal.iter_image_payloads(message.get("content"))
    )


def _preview_message(index: int, message: dict[str, Any]) -> dict[str, Any]:
    try:
        text = multimodal.plain_text(message.get("content"), image_placeholder="[Image]")
    except (TypeError, ValueError):
        text = ""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > _PREVIEW_LIMIT:
        text = text[: _PREVIEW_LIMIT - 1].rstrip() + "…"
    return {
        "index": index,
        "role": str(message.get("role") or "unknown")[:32],
        "preview": text or "(empty message)",
    }


async def _require_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    require_safe_browser_origin(request)
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    supplied = authorization.removeprefix("Bearer ")
    if not any(secrets.compare_digest(supplied, key) for key in configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _resolve_engine(request: Request, model_id: str):
    from app import model_manager

    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Model manager is unavailable.")
    try:
        return manager, manager.resolve_engine(model_id)
    except model_manager.UnknownModel as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except model_manager.ModelLoading as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_manager.ModelNotLoaded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_manager.NoModelsLoaded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def register_context_budget_routes(app: FastAPI) -> None:
    if getattr(app.state, "context_budget_routes_registered", False):
        return

    router = APIRouter(
        prefix="/v1/chat",
        tags=["chat"],
        dependencies=[Depends(_require_access)],
    )

    @router.post("/context-budget")
    async def inspect_context_budget(request: Request, body: ContextBudgetRequest):
        manager, engine = _resolve_engine(request, body.model)
        if "embedding" in getattr(engine, "backend", "").lower():
            raise HTTPException(
                status_code=400,
                detail=f"Model '{body.model}' is an embedding model and has no chat context budget.",
            )
        if multimodal.contents_have_images(message.content for message in body.messages) and not getattr(
            engine, "supports_vision", False
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Model '{body.model}' is not vision-capable and cannot accept image input.",
            )

        cfg = manager.config_for(engine.model_id)
        max_context_len = cfg.max_context_len if cfg else 2048
        max_prompt_len = cfg.max_prompt_len if cfg else 1536
        model_output_reserve = max(0, max_context_len - max_prompt_len)

        use_tools = bool(body.tools) and body.tool_choice != "none"
        system_override = tools.format_tools_for_prompt(body.tools, body.tool_choice) if use_tools else ""
        if system_override:
            multimodal.preflight_request_contents([system_override])

        normalized = await asyncio.to_thread(
            chat_format.normalize_messages,
            body.messages,
            system_override,
        )
        actual_images = _actual_image_count(normalized)
        synthetic_images = max(0, body.image_count - actual_images)
        image_reserve = int(getattr(multimodal, "_IMAGE_TOKEN_RESERVE", 512))

        def count_with_pending_images(prompt: str) -> int:
            return engine.count_tokens(prompt) + synthetic_images * image_reserve

        analysis = await asyncio.to_thread(
            analyze_prompt_budget,
            normalized,
            engine.apply_chat_template,
            count_with_pending_images,
            max_prompt_len,
        )
        try:
            requested_output = int(body.max_tokens or 512)
            available_output = max(0, max_context_len - analysis.prompt_tokens - _SAFETY_TOKENS)
            effective_output = min(requested_output, available_output)
            retained_set = set(analysis.retained_indexes)
            dropped_previews = [
                _preview_message(index, message)
                for index, message in enumerate(normalized)
                if index not in retained_set
            ][:_MAX_PREVIEW_MESSAGES]
            attachment_count = max(actual_images, body.image_count)
            attachment_tokens = attachment_count * image_reserve
            context_usage = min(
                max_context_len,
                analysis.prompt_tokens + effective_output + _SAFETY_TOKENS,
            )
            return {
                "model": body.model,
                "model_name": cfg.name if cfg else body.model,
                "prompt_tokens": analysis.prompt_tokens,
                "max_prompt_tokens": max_prompt_len,
                "prompt_budget_percent": round(
                    analysis.prompt_tokens / max(max_prompt_len, 1) * 100,
                    1,
                ),
                "max_context_tokens": max_context_len,
                "model_output_reserve_tokens": model_output_reserve,
                "requested_output_tokens": requested_output,
                "available_output_tokens": available_output,
                "effective_output_tokens": effective_output,
                "output_limited": effective_output < requested_output,
                "safety_tokens": _SAFETY_TOKENS,
                "context_usage_tokens": context_usage,
                "context_usage_percent": round(
                    context_usage / max(max_context_len, 1) * 100,
                    1,
                ),
                "message_count": len(normalized),
                "retained_message_count": len(analysis.retained_indexes),
                "dropped_message_count": len(analysis.dropped_indexes),
                "dropped_turn_count": analysis.dropped_turns,
                "dropped_messages": dropped_previews,
                "dropped_preview_truncated": len(analysis.dropped_indexes) > len(dropped_previews),
                "will_truncate": bool(analysis.dropped_indexes),
                "prompt_over_budget": analysis.prompt_tokens > max_prompt_len,
                "blocked": available_output < 1,
                "system_instructions_retained": bool(
                    normalized and normalized[0].get("role") == "system"
                ),
                "attachment_count": attachment_count,
                "attachment_token_estimate": attachment_tokens,
                "attachment_estimate_per_image": image_reserve,
            }
        finally:
            multimodal.discard_prompt_context(analysis.prompt)

    app.include_router(router)
    app.state.context_budget_routes_registered = True


def install_context_budget_routes_extension() -> None:
    """Register the context preflight route on InferBridge FastAPI applications."""

    if getattr(FastAPI, _ROUTE_INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_context_budget(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_context_budget_routes(self)

    FastAPI.__init__ = init_with_context_budget  # type: ignore[method-assign]
    setattr(FastAPI, _ROUTE_INSTALL_FLAG, True)


__all__ = [
    "ContextBudgetRequest",
    "PromptBudgetAnalysis",
    "analyze_prompt_budget",
    "install_context_budget_routes_extension",
    "register_context_budget_routes",
]
