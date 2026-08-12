"""OpenAI Responses API compatibility layer.

InferBridge uses the same local OpenVINO generation engine for Chat Completions and
Responses. This module keeps Responses-specific request, output, streaming, tool, and
error semantics out of the main server module while reusing the existing prompt budget,
model locks, cancellation, structured output, and tool-call parser.

Only local function tools are supported. Hosted OpenAI tools are deliberately rejected
by the request schema because InferBridge does not provide those services.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app import chat_format, multimodal, tools
from app.openai_api import (
    ResponseFunctionCall,
    ResponseObject,
    ResponseOutputMessage,
    ResponseRequest,
    ResponseUsage,
)
from runtime.openvino_engine import BaseEngine, GenParams

logger = logging.getLogger("ov-llm.responses")


def _response_input_contents(request_input: Any) -> tuple[list[Any], list[str]]:
    """Return bounded content/role inputs for multimodal and text preflight.

    Responses input may contain normal messages plus function-call history and
    function-call outputs. Function arguments and outputs are still user-controlled
    text and must pass the same request-size validation as message content.
    """

    if isinstance(request_input, str):
        return [request_input], ["user"]
    if not isinstance(request_input, list):
        return [], []

    contents: list[Any] = []
    roles: list[str] = []
    for item in request_input:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "function_call_output":
            contents.append(item.get("output", ""))
            roles.append("user")
        elif item_type == "function_call":
            contents.append(item.get("arguments", ""))
            roles.append("assistant")
        else:
            contents.append(item.get("content", ""))
            role = str(item.get("role", "user"))
            roles.append("system" if role == "developer" else role)
    return contents, roles


def _normalize_and_build_response_prompt(
    engine: BaseEngine,
    request_input: Any,
    instructions: str | None,
    max_prompt_len: int,
    request_tools: Any,
    tool_choice: Any,
    use_tools: bool,
) -> tuple[list[dict[str, Any]], str, int]:
    contents, roles = _response_input_contents(request_input)
    multimodal.preflight_request_contents(contents, roles=roles)

    tool_instructions = (
        tools.format_tools_for_prompt(request_tools, tool_choice) if use_tools else ""
    )
    if tool_instructions:
        multimodal.preflight_request_contents([tool_instructions])

    combined_instructions = (instructions or "").strip()
    if tool_instructions:
        combined_instructions = (
            f"{combined_instructions}\n\n{tool_instructions}"
            if combined_instructions
            else tool_instructions
        )

    messages = chat_format.responses_input_to_messages(
        request_input,
        combined_instructions or None,
    )
    prompt, prompt_tokens = chat_format.build_prompt_within_budget(
        messages,
        engine.apply_chat_template,
        engine.count_tokens,
        max_prompt_len,
    )
    return messages, prompt, prompt_tokens


def _build_normalized_response_prompt(
    engine: BaseEngine,
    messages: list[dict[str, Any]],
    max_prompt_len: int,
) -> tuple[str, int]:
    return chat_format.build_prompt_within_budget(
        messages,
        engine.apply_chat_template,
        engine.count_tokens,
        max_prompt_len,
    )


def _usage(input_tokens: int, output_tokens: int) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details={"cached_tokens": 0},
        output_tokens=output_tokens,
        output_tokens_details={"reasoning_tokens": 0},
        total_tokens=input_tokens + output_tokens,
    )


def _message_item(
    item_id: str,
    text: str,
    *,
    status: str = "completed",
) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=item_id,
        status=status,
        content=[{"type": "output_text", "text": text, "annotations": []}],
    )


def _function_item(tool_call: Any, *, status: str = "completed") -> ResponseFunctionCall:
    return ResponseFunctionCall(
        id=f"fc-{uuid.uuid4().hex}",
        call_id=tool_call.id,
        name=tool_call.function.name,
        arguments=tool_call.function.arguments,
        status=status,
    )


def _response_object(
    request: ResponseRequest,
    *,
    response_id: str,
    created_at: int,
    status: str,
    output: list[ResponseOutputMessage | ResponseFunctionCall],
    usage: ResponseUsage | None,
    completed_at: int | None = None,
    error: dict[str, Any] | None = None,
) -> ResponseObject:
    return ResponseObject(
        id=response_id,
        created_at=created_at,
        completed_at=completed_at,
        model=request.model,
        status=status,
        output=output,
        usage=usage,
        error=error,
        instructions=request.instructions,
        max_output_tokens=request.max_output_tokens,
        temperature=request.temperature,
        top_p=request.top_p,
        tool_choice=request.tool_choice if request.tool_choice is not None else "auto",
        tools=[tool.model_dump(exclude_none=True) for tool in request.tools or []],
        parallel_tool_calls=request.parallel_tool_calls,
        text=request.response_text_payload(),
    )


def install_responses_api(
    app: FastAPI,
    *,
    manager: Any,
    dependencies: list[Any],
    resolve_engine: Callable[[str], BaseEngine],
    validate_generation_request: Callable[[BaseEngine, str, Any, str | None], None],
    build_prompt_off_thread: Callable[..., Any],
    params_for: Callable[..., GenParams],
    record_key_metrics: Callable[[int, int, float], None],
    request_id_var: Any,
) -> None:
    """Register the Responses endpoint using the server's shared generation helpers."""

    if getattr(app.state, "responses_api_parity_installed", False):
        return
    app.state.responses_api_parity_installed = True

    async def complete_response(
        engine: BaseEngine,
        request: ResponseRequest,
        prompt: str,
        prompt_tokens: int,
        params: GenParams,
        *,
        use_tools: bool,
        response_tools: Any,
        normalized_messages: list[dict[str, Any]],
        max_prompt_len: int,
        max_context_len: int,
        response_id: str,
        message_id: str,
        created_at: int,
    ) -> ResponseObject:
        current_prompt = prompt
        current_prompt_tokens = prompt_tokens
        current_messages = normalized_messages
        current_params = params
        started = time.perf_counter()

        try:
            text = ""
            completion_tokens = 0
            for attempt in range(3):
                try:
                    result = await manager.generate(engine, current_prompt, current_params)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - sanitize native/runtime failures
                    logger.exception("Responses generation failed: %s", exc)
                    raise HTTPException(
                        status_code=500,
                        detail="Response generation failed; see server logs for the request ID.",
                    ) from exc

                text = result.text
                completion_tokens = result.completion_tokens
                if not (use_tools and tools.detect_incomplete_tool_call(text) and attempt < 2):
                    break

                logger.warning("Malformed Responses tool call; retry %d/2", attempt + 1)
                retry_messages = list(current_messages) + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": tools.get_retry_prompt()},
                ]
                new_prompt, new_prompt_tokens = await build_prompt_off_thread(
                    _build_normalized_response_prompt,
                    engine,
                    retry_messages,
                    max_prompt_len,
                )
                multimodal.discard_prompt_context(current_prompt)
                current_prompt = new_prompt
                current_prompt_tokens = new_prompt_tokens
                current_messages = retry_messages
                current_params = params_for(
                    request.max_output_tokens,
                    request.temperature,
                    request.top_p,
                    current_prompt_tokens,
                    max_context_len,
                    stop=chat_format.normalize_stop(request.stop),
                    seed=request.seed,
                    response_format=request.generation_response_format(),
                    lora_path=request.lora_path,
                    lora_alpha=request.lora_alpha,
                )

            if current_params.stop:
                visible_text, hit_stop = chat_format.truncate_at_stop(
                    text,
                    current_params.stop,
                )
                if hit_stop:
                    text = visible_text
                    completion_tokens = await asyncio.to_thread(
                        engine.count_tokens,
                        text,
                    )

            output: list[ResponseOutputMessage | ResponseFunctionCall] = []
            if use_tools:
                remaining, parsed = tools.parse_tool_calls(text, response_tools)
                if remaining:
                    output.append(_message_item(message_id, remaining))
                output.extend(_function_item(call) for call in parsed)
                if not parsed and not remaining:
                    output.append(_message_item(message_id, text))
            else:
                output.append(_message_item(message_id, text))

            latency = time.perf_counter() - started
            manager.record_request(
                engine.model_id,
                current_prompt_tokens,
                completion_tokens,
                latency,
            )
            record_key_metrics(current_prompt_tokens, completion_tokens, latency)
            return _response_object(
                request,
                response_id=response_id,
                created_at=created_at,
                completed_at=int(time.time()),
                status="completed",
                output=output,
                usage=_usage(current_prompt_tokens, completion_tokens),
            )
        finally:
            multimodal.discard_prompt_context(current_prompt)

    async def stream_response(
        engine: BaseEngine,
        request: ResponseRequest,
        prompt: str,
        prompt_tokens: int,
        params: GenParams,
        *,
        use_tools: bool,
        response_tools: Any,
        response_id: str,
        message_id: str,
        created_at: int,
        request_id: str,
    ):
        token = request_id_var.set(request_id)
        sequence_number = 0
        started = time.perf_counter()

        def event(type_name: str, payload: dict[str, Any]) -> str:
            nonlocal sequence_number
            sequence_number += 1
            body = {"type": type_name, **payload, "sequence_number": sequence_number}
            return f"event: {type_name}\ndata: {json.dumps(body)}\n\n"

        def message_added(output_index: int) -> str:
            return event(
                "response.output_item.added",
                {
                    "output_index": output_index,
                    "item": {
                        "id": message_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                },
            )

        def content_added(output_index: int) -> str:
            return event(
                "response.content_part.added",
                {
                    "item_id": message_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": "",
                        "annotations": [],
                    },
                },
            )

        def message_done(output_index: int, text: str) -> list[str]:
            part = {"type": "output_text", "text": text, "annotations": []}
            item = _message_item(message_id, text).model_dump()
            return [
                event(
                    "response.output_text.done",
                    {
                        "item_id": message_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "text": text,
                    },
                ),
                event(
                    "response.content_part.done",
                    {
                        "item_id": message_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "part": part,
                    },
                ),
                event(
                    "response.output_item.done",
                    {"output_index": output_index, "item": item},
                ),
            ]

        try:
            created_response = _response_object(
                request,
                response_id=response_id,
                created_at=created_at,
                status="in_progress",
                output=[],
                usage=None,
            )
            yield event(
                "response.created",
                {"response": created_response.model_dump()},
            )

            full_text = ""
            output: list[ResponseOutputMessage | ResponseFunctionCall] = []
            generation_failed = False
            stream_gen = manager.stream(engine, prompt, params)
            try:
                if use_tools:
                    async for piece in stream_gen:
                        full_text += piece
                    if params.stop:
                        full_text, _ = chat_format.truncate_at_stop(
                            full_text,
                            params.stop,
                        )

                    remaining, parsed = tools.parse_tool_calls(full_text, response_tools)
                    output_index = 0
                    if remaining or not parsed:
                        visible_text = remaining or full_text
                        output.append(_message_item(message_id, visible_text))
                        yield message_added(output_index)
                        yield content_added(output_index)
                        yield event(
                            "response.output_text.delta",
                            {
                                "item_id": message_id,
                                "output_index": output_index,
                                "content_index": 0,
                                "delta": visible_text,
                            },
                        )
                        for payload in message_done(output_index, visible_text):
                            yield payload
                        output_index += 1

                    for parsed_call in parsed:
                        call_item = _function_item(parsed_call)
                        output.append(call_item)
                        in_progress = call_item.model_copy(
                            update={"arguments": "", "status": "in_progress"}
                        )
                        yield event(
                            "response.output_item.added",
                            {
                                "output_index": output_index,
                                "item": in_progress.model_dump(),
                            },
                        )
                        yield event(
                            "response.function_call_arguments.delta",
                            {
                                "item_id": call_item.id,
                                "output_index": output_index,
                                "delta": call_item.arguments,
                            },
                        )
                        yield event(
                            "response.function_call_arguments.done",
                            {
                                "item_id": call_item.id,
                                "output_index": output_index,
                                "name": call_item.name,
                                "arguments": call_item.arguments,
                            },
                        )
                        yield event(
                            "response.output_item.done",
                            {
                                "output_index": output_index,
                                "item": call_item.model_dump(),
                            },
                        )
                        output_index += 1
                else:
                    yield message_added(0)
                    yield content_added(0)
                    stopper = chat_format.StopStreamer(params.stop or [])
                    async for piece in stream_gen:
                        emitted = stopper.feed(piece)
                        if emitted:
                            full_text += emitted
                            yield event(
                                "response.output_text.delta",
                                {
                                    "item_id": message_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "delta": emitted,
                                },
                            )
                        if stopper.stopped:
                            break
                    tail = stopper.flush()
                    if tail:
                        full_text += tail
                        yield event(
                            "response.output_text.delta",
                            {
                                "item_id": message_id,
                                "output_index": 0,
                                "content_index": 0,
                                "delta": tail,
                            },
                        )
                    output.append(_message_item(message_id, full_text))
                    for payload in message_done(0, full_text):
                        yield payload
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - SSE must not expose native details
                generation_failed = True
                logger.exception("Responses streaming generation failed: %s", exc)
                error = {
                    "code": "generation_error",
                    "message": "Response generation failed; see server logs for the request ID.",
                }
                yield event(
                    "error",
                    {
                        "code": error["code"],
                        "message": error["message"],
                        "param": None,
                    },
                )
                failed_response = _response_object(
                    request,
                    response_id=response_id,
                    created_at=created_at,
                    completed_at=int(time.time()),
                    status="failed",
                    output=output,
                    usage=None,
                    error=error,
                )
                yield event(
                    "response.failed",
                    {"response": failed_response.model_dump()},
                )
            finally:
                await stream_gen.aclose()

            if generation_failed:
                yield "data: [DONE]\n\n"
                return

            completion_tokens = await asyncio.to_thread(
                engine.count_tokens,
                full_text,
            )
            latency = time.perf_counter() - started
            manager.record_request(
                engine.model_id,
                prompt_tokens,
                completion_tokens,
                latency,
            )
            record_key_metrics(prompt_tokens, completion_tokens, latency)
            completed_response = _response_object(
                request,
                response_id=response_id,
                created_at=created_at,
                completed_at=int(time.time()),
                status="completed",
                output=output,
                usage=_usage(prompt_tokens, completion_tokens),
            )
            yield event(
                "response.completed",
                {"response": completed_response.model_dump()},
            )
            yield "data: [DONE]\n\n"
        finally:
            multimodal.discard_prompt_context(prompt)
            request_id_var.reset(token)

    async def create_response(request: ResponseRequest):
        engine = resolve_engine(request.model)
        if "embedding" in getattr(engine, "backend", "").lower():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model '{request.model}' is an embedding model and cannot be used "
                    "for response generation."
                ),
            )

        response_contents, _response_roles = _response_input_contents(request.input)
        validate_generation_request(
            engine,
            request.model,
            response_contents,
            request.lora_path,
        )
        cfg = manager.config_for(engine.model_id)
        max_context_len = cfg.max_context_len if cfg else 2048
        max_prompt_len = cfg.max_prompt_len if cfg else 1536

        response_tools = request.chat_tools()
        tool_choice = request.chat_tool_choice()
        use_tools = bool(response_tools) and tool_choice != "none"
        normalized_messages, prompt, prompt_tokens = await build_prompt_off_thread(
            _normalize_and_build_response_prompt,
            engine,
            request.input,
            request.instructions,
            max_prompt_len,
            response_tools,
            tool_choice,
            use_tools,
        )
        try:
            params = params_for(
                request.max_output_tokens,
                request.temperature,
                request.top_p,
                prompt_tokens,
                max_context_len,
                stop=chat_format.normalize_stop(request.stop),
                seed=request.seed,
                response_format=request.generation_response_format(),
                lora_path=request.lora_path,
                lora_alpha=request.lora_alpha,
            )
        except BaseException:
            multimodal.discard_prompt_context(prompt)
            raise

        response_id = f"resp-{uuid.uuid4().hex}"
        message_id = f"msg-{uuid.uuid4().hex}"
        created_at = int(time.time())

        if request.stream:
            request_id = request_id_var.get()
            return StreamingResponse(
                stream_response(
                    engine,
                    request,
                    prompt,
                    prompt_tokens,
                    params,
                    use_tools=use_tools,
                    response_tools=response_tools,
                    response_id=response_id,
                    message_id=message_id,
                    created_at=created_at,
                    request_id=request_id,
                ),
                media_type="text/event-stream",
                background=BackgroundTask(multimodal.discard_prompt_context, prompt),
            )

        return await complete_response(
            engine,
            request,
            prompt,
            prompt_tokens,
            params,
            use_tools=use_tools,
            response_tools=response_tools,
            normalized_messages=normalized_messages,
            max_prompt_len=max_prompt_len,
            max_context_len=max_context_len,
            response_id=response_id,
            message_id=message_id,
            created_at=created_at,
        )

    app.post("/v1/responses", dependencies=dependencies)(create_response)
