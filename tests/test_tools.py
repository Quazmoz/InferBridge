import pytest

from app.openai_api import FunctionDefinition, ToolDefinition
from app.tools import (
    detect_incomplete_tool_call,
    format_tools_for_prompt,
    parse_tool_calls,
)


def _weather_tool():
    return ToolDefinition(
        function=FunctionDefinition(
            name="get_weather",
            description="Get weather",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
    )


def test_format_tools_disabled_with_none():
    assert format_tools_for_prompt([_weather_tool()], tool_choice="none") == ""


def test_format_tools_includes_name_and_format():
    prompt = format_tools_for_prompt([_weather_tool()], tool_choice="auto")
    assert "get_weather" in prompt
    assert "Tool Call Format" in prompt


def test_format_tools_forced_tool():
    prompt = format_tools_for_prompt(
        [_weather_tool()], tool_choice={"type": "function", "function": {"name": "get_weather"}}
    )
    assert "MUST call the 'get_weather'" in prompt


def test_format_tools_rejects_required_without_tools():
    with pytest.raises(ValueError, match="requires at least one matching function tool"):
        format_tools_for_prompt([], tool_choice="required")


def test_format_tools_rejects_unknown_forced_tool():
    with pytest.raises(ValueError, match="unknown function 'missing'"):
        format_tools_for_prompt(
            [_weather_tool()],
            tool_choice={"type": "function", "function": {"name": "missing"}},
        )


def test_parse_single_object():
    text = 'Sure. {"name": "get_weather", "arguments": {"city": "Paris"}}'
    remaining, calls = parse_tool_calls(text, [_weather_tool()])
    assert len(calls) == 1
    assert calls[0].function.name == "get_weather"
    assert "Paris" in calls[0].function.arguments
    assert "get_weather" not in remaining


def test_parse_array_of_calls():
    text = '[{"name": "get_weather", "arguments": {"city": "A"}}, {"name": "get_weather", "arguments": {"city": "B"}}]'
    _, calls = parse_tool_calls(text, [_weather_tool()])
    assert len(calls) == 2


def test_parse_dedupes_identical_calls():
    text = '{"name": "get_weather", "arguments": {"city": "X"}} {"name": "get_weather", "arguments": {"city": "X"}}'
    _, calls = parse_tool_calls(text, [_weather_tool()])
    assert len(calls) == 1


def test_parse_validates_against_known_tools():
    text = '{"name": "unknown_fn", "arguments": {}}'
    _, calls = parse_tool_calls(text, [_weather_tool()])
    assert calls == []


def test_parse_code_block():
    text = '```json\n{"name": "get_weather", "arguments": {"city": "Z"}}\n```'
    _, calls = parse_tool_calls(text, [_weather_tool()])
    assert len(calls) == 1


def test_detect_incomplete_tool_call():
    assert detect_incomplete_tool_call('{"name": "get_weather", "arguments": {"city": "')
    assert not detect_incomplete_tool_call("just a normal sentence")
