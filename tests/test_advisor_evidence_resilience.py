from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from app.hardware_advisor.evidence import EvidenceMixin


class _Evidence(EvidenceMixin):
    def __init__(self, path) -> None:
        self.settings = SimpleNamespace(benchmark_results_file=path)
        self._store_lock = threading.RLock()
        self._benchmark_cache = []
        self._benchmark_cache_at = 0.0
        self._benchmark_cache_mtime_ns = -1
        self._size_cache = {}
        self.catalog = {}


def _matching_evidence(path) -> _Evidence:
    evidence = _Evidence(path)
    evidence.catalog = {
        "model": SimpleNamespace(
            source_model="example/model",
            backend="openvino-genai",
            weight_format="int4",
        )
    }
    evidence.hardware_snapshot = lambda: {"fingerprint": "current-hardware"}
    return evidence


def test_string_false_success_is_not_advisor_evidence(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "automatic": "false",
                        "results": [
                            {"model_id": "model", "success": "false"},
                            {"model_id": "real", "success": True},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = _Evidence(path)._benchmark_rows()

    assert [row["model_id"] for row in rows] == ["real"]
    assert rows[0]["automatic"] is False


def test_future_benchmark_schema_is_not_used_as_recommendation_evidence(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "runs": [
                    {
                        "results": [
                            {
                                "model_id": "future-model",
                                "success": True,
                                "tokens_sec": 999999,
                            }
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _Evidence(path)._benchmark_rows() == []


def test_nonfinite_schema_does_not_break_evidence_reader(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        '{"schema_version":Infinity,"runs":[{"results":[{"success":true}]}]}',
        encoding="utf-8",
    )

    assert _Evidence(path)._benchmark_rows() == []


def test_malformed_run_results_container_is_ignored(tmp_path):
    path = tmp_path / "benchmarks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {"results": "not-a-list"},
                    {"results": [{"model_id": "ok", "success": True}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert [row["model_id"] for row in _Evidence(path)._benchmark_rows()] == ["ok"]


def test_synthetic_benchmark_never_becomes_current_hardware_evidence(tmp_path):
    path = tmp_path / "benchmarks.json"
    identity = {
        "model_id": "model",
        "source_model": "example/model",
        "backend": "openvino-genai",
        "weight_format": "int4",
        "requested_device": "CPU",
        "actual_device": "CPU",
        "success": True,
        "decode_tokens_sec": 9999.0,
        "samples": [{"decode_tokens_sec": 9999.0}],
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "created_at": "2026-08-24T10:00:00Z",
                        "hardware_fingerprint": "current-hardware",
                        "mock": True,
                        "synthetic": True,
                        "results": [identity],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = _matching_evidence(path)

    assert evidence._benchmark_rows()[0]["synthetic"] is True
    assert evidence._latest_benchmark("model", "CPU") is None


def test_manual_multi_run_evidence_outranks_later_automatic_sample(tmp_path):
    path = tmp_path / "benchmarks.json"
    identity = {
        "model_id": "model",
        "source_model": "example/model",
        "backend": "openvino-genai",
        "weight_format": "int4",
        "requested_device": "CPU",
        "actual_device": "CPU",
        "success": True,
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "created_at": "2026-08-24T10:00:00Z",
                        "hardware_fingerprint": "current-hardware",
                        "methodology_version": 2,
                        "automatic": False,
                        "results": [
                            {
                                **identity,
                                "runs": 5,
                                "warmup_runs": 1,
                                "decode_tokens_sec": 24.0,
                                "stability": {"status": "stable", "cv_percent": 2.0},
                            }
                        ],
                    },
                    {
                        "created_at": "2026-08-24T11:00:00Z",
                        "hardware_fingerprint": "current-hardware",
                        "methodology_version": 1,
                        "automatic": True,
                        "results": [
                            {
                                **identity,
                                "runs": 1,
                                "decode_tokens_sec": 12.0,
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    selected = _matching_evidence(path)._latest_benchmark("model", "CPU")

    assert selected is not None
    assert selected["automatic"] is False
    assert selected["runs"] == 5
    assert selected["decode_tokens_sec"] == 24.0
    assert selected["stability"]["status"] == "stable"


def test_new_conversion_marker_invalidates_older_benchmark_evidence(tmp_path):
    path = tmp_path / "benchmarks.json"
    model_dir = tmp_path / "converted-model"
    model_dir.mkdir()
    marker = model_dir / ".ovllm-conversion.json"
    marker.write_text(json.dumps({"recorded_at": "2026-08-24T09:00:00Z"}), encoding="utf-8")

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    {
                        "created_at": "2026-08-24T10:00:00Z",
                        "hardware_fingerprint": "current-hardware",
                        "methodology_version": 2,
                        "automatic": False,
                        "results": [
                            {
                                "model_id": "model",
                                "source_model": "example/model",
                                "backend": "openvino-genai",
                                "weight_format": "int4",
                                "requested_device": "CPU",
                                "actual_device": "CPU",
                                "success": True,
                                "runs": 5,
                                "decode_tokens_sec": 20.0,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = _Evidence(path)
    evidence.catalog = {
        "model": SimpleNamespace(
            source_model="example/model",
            backend="openvino-genai",
            weight_format="int4",
            abs_path=lambda _base: model_dir,
        )
    }
    evidence.hardware_snapshot = lambda: {"fingerprint": "current-hardware"}

    assert evidence._latest_benchmark("model", "CPU") is not None

    marker.write_text(json.dumps({"recorded_at": "2026-08-24T11:00:00Z"}), encoding="utf-8")

    assert evidence._latest_benchmark("model", "CPU") is None
