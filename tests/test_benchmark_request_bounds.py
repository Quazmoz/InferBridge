from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.openai_api import BenchmarkRunRequest


def test_benchmark_request_keeps_existing_single_model_defaults():
    request = BenchmarkRunRequest(model="tinyllama-1.1b-chat-fp16")

    assert request.devices == ["CPU", "GPU", "NPU", "AUTO"]
    assert request.runs == 1
    assert request.max_tokens == 64


def test_benchmark_request_rejects_excessive_model_device_matrix():
    with pytest.raises(ValidationError, match="at most 32 model/device combinations"):
        BenchmarkRunRequest(
            models=[f"model-{index}" for index in range(9)],
            devices=["CPU", "GPU", "NPU", "AUTO"],
        )


def test_benchmark_request_counts_unique_matrix_entries():
    request = BenchmarkRunRequest(
        model="model-a",
        models=["model-a", "model-b", "model-b"],
        devices=["CPU", "CPU", "GPU"],
    )

    assert request.model == "model-a"
    assert request.models == ["model-a", "model-b", "model-b"]


def test_benchmark_request_bounds_list_sizes_and_prompt():
    with pytest.raises(ValidationError):
        BenchmarkRunRequest(models=[f"model-{index}" for index in range(17)])

    with pytest.raises(ValidationError):
        BenchmarkRunRequest(model="model", devices=["CPU"] * 17)

    with pytest.raises(ValidationError):
        BenchmarkRunRequest(model="model", prompt="x" * 16_385)
