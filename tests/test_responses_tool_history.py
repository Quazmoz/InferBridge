"""Responses tool-history normalization tests."""

import json

from app import chat_format


def test_responses_function_call_and_output_continue_as_one_tool_turn():
    messages = chat_format.responses_input_to_messages(
        [
            {"role": "user", "content": "What is the weather?"},
            {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": "get_weather",
                "arguments": '{"city":"London"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"temperature_c":22}',
            },
        ]
    )

    assert messages[0] == {"role": "user", "content": "What is the weather?"}
    assert messages[1]["role"] == "assistant"
    calls = json.loads(messages[1]["content"])
    assert calls == [
        {
            "name": "get_weather",
            "arguments": '{"city":"London"}',
        }
    ]
    assert messages[2] == {
        "role": "user",
        "content": '[tool result (call call-1)]\n{"temperature_c":22}',
    }


def test_responses_developer_role_maps_to_local_system_role():
    messages = chat_format.responses_input_to_messages(
        [
            {"role": "developer", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ]
    )

    assert messages == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]
