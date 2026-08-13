"""Prompt-based tool/function calling for models that lack native tool support.

Strategy: inject a system instruction describing the available tools and the
expected JSON output format, then parse JSON tool calls back out of the model's
free-form text. This keeps the OpenAI tool-calling API working across any model
the server can run. Pure logic — fully unit-testable without OpenVINO.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.openai_api import FunctionCall, ToolCall, ToolDefinition

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def format_tools_for_prompt(
    tools: list[ToolDefinition] | None,
    tool_choice: Any = None,
) -> str:
    """Render a system-prompt section instructing the model how to call tools.

    Returns an empty string when tools are disabled (``tool_choice == "none"``).
    Raises ``ValueError`` when the requested tool policy cannot be satisfied by the
    supplied definitions instead of silently constructing an impossible prompt.
    """

    if tool_choice == "none":
        return ""
    if not tools:
        if tool_choice == "required" or isinstance(tool_choice, dict):
            raise ValueError("tool_choice requires at least one matching function tool.")
        return ""

    tools_json = [
        {
            "type": tool.type,
            "function": {
                "name": tool.function.name,
                "description": tool.function.description or "",
                "parameters": tool.function.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]

    forced_tool: str | None = None
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        forced_tool = tool_choice.get("function", {}).get("name")
        if forced_tool:
            matching_tools = [
                tool for tool in tools_json if tool["function"]["name"] == forced_tool
            ]
            if not matching_tools:
                raise ValueError(f"tool_choice references unknown function '{forced_tool}'.")
            tools_json = matching_tools

    if forced_tool:
        instruction = (
            f"You MUST call the '{forced_tool}' function. Do not respond with anything else."
        )
    elif tool_choice == "required":
        instruction = (
            "You MUST call at least one of the available tools. "
            "Do not respond without calling a tool."
        )
    else:
        instruction = "Use the tools when needed. If you don't need a tool, respond normally."

    tools_str = json.dumps(tools_json, indent=2)
    return f"""You are a helpful assistant with access to the following tools. {instruction}

# Available Tools

{tools_str}

# Tool Call Format

When you need to call a tool, respond with a JSON object in this EXACT format:
{{"name": "function_name", "arguments": {{"arg1": "value1"}}}}

For multiple tool calls, use a JSON array:
[{{"name": "func1", "arguments": {{}}}}, {{"name": "func2", "arguments": {{}}}}]

IMPORTANT: Output ONLY the JSON when calling tools, no other text."""


def _arguments_to_json_string(arguments: Any) -> str:
    """Return OpenAI-compatible JSON text for a tool call's arguments field."""

    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        text = arguments.strip()
        if text:
            try:
                json.loads(text)
                return text
            except json.JSONDecodeError:
                pass
        return json.dumps(arguments, separators=(",", ":"))
    return json.dumps(arguments, separators=(",", ":"))


def _tool_items_from_json(value: Any) -> list[tuple[str, str]]:
    """Extract ``(name, arguments_json)`` pairs from parsed JSON values."""

    if isinstance(value, list):
        items: list[tuple[str, str]] = []
        for item in value:
            items.extend(_tool_items_from_json(item))
        return items

    if not isinstance(value, dict):
        return []

    if "name" in value:
        return [(str(value["name"]), _arguments_to_json_string(value.get("arguments", {})))]

    # Tolerate OpenAI-shaped objects if a model emits them verbatim.
    function = value.get("function")
    if isinstance(function, dict) and "name" in function:
        arguments = function.get("arguments", value.get("arguments", {}))
        return [(str(function["name"]), _arguments_to_json_string(arguments))]

    return []


def _json_candidates(text: str) -> list[tuple[int, int, Any]]:
    """Return parsed JSON candidates with their spans in the original text."""

    candidates: list[tuple[int, int, Any]] = []

    for match in _JSON_FENCE_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            candidates.append((match.start(), match.end(), json.loads(raw)))
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            parsed, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        candidates.append((idx, end, parsed))

    # Earlier spans first; for the same start, prefer the outermost JSON object.
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return candidates


def _span_is_contained(span: tuple[int, int], accepted: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(
        start >= accepted_start and end <= accepted_end for accepted_start, accepted_end in accepted
    )


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    chunks: list[str] = []
    cursor = 0
    for start, end in merged:
        chunks.append(text[cursor:start])
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks)


def parse_tool_calls(
    text: str,
    available_tools: list[ToolDefinition] | None = None,
) -> tuple[str, list[ToolCall]]:
    """Extract tool calls from model output.

    Handles JSON objects, JSON arrays, OpenAI-shaped tool call objects, and fenced
    code blocks. Parsing uses ``json.JSONDecoder`` instead of regex so nested
    argument objects/arrays remain valid.
    """

    tool_calls: list[ToolCall] = []
    seen: set[str] = set()
    accepted_spans: list[tuple[int, int]] = []

    valid_names = {t.function.name for t in available_tools} if available_tools else set()

    def add(name: str, arguments: str) -> bool:
        if valid_names and name not in valid_names:
            return False
        key = f"{name}:{arguments}"
        if key in seen:
            return False
        seen.add(key)
        tool_calls.append(
            ToolCall(
                id=f"call-{uuid.uuid4().hex[:12]}",
                type="function",
                function=FunctionCall(name=name, arguments=arguments),
            )
        )
        return True

    for start, end, parsed in _json_candidates(text):
        if _span_is_contained((start, end), accepted_spans):
            continue

        added = False
        for name, arguments in _tool_items_from_json(parsed):
            if add(name, arguments):
                added = True
        if added:
            accepted_spans.append((start, end))

    remaining_text = _remove_spans(text, accepted_spans)
    remaining_text = re.sub(r"\s+", " ", remaining_text).strip()
    return remaining_text, tool_calls


def detect_incomplete_tool_call(text: str) -> bool:
    """Heuristic: True if the output looks like a truncated tool-call JSON."""

    if '{"name"' in text or "[{" in text:
        if (text.count("{") - text.count("}")) > 0 or (text.count("[") - text.count("]")) > 0:
            return True
    return False


def get_retry_prompt() -> str:
    return (
        "Your previous response contained a malformed tool call. Please try again.\n"
        "Output ONLY valid JSON in this format:\n"
        '{"name": "function_name", "arguments": {"param": "value"}}'
    )
