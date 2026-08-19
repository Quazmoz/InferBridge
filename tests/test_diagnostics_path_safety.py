from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import diagnostics, diagnostics_sections
from app.diagnostics import DiagnosticsCollector
from app.paths import RuntimePaths


def _paths(tmp_path: Path) -> RuntimePaths:
    data = tmp_path / "data"
    return RuntimePaths(
        resource_root=tmp_path / "resources",
        data_root=data,
        config_dir=data / "config",
        logs_dir=data / "logs",
        models_dir=data / "models",
        huggingface_cache_dir=data / "cache" / "huggingface",
        compiled_cache_dir=data / "cache" / "openvino",
        benchmarks_dir=data / "benchmarks",
        diagnostics_dir=data / "diagnostics",
        onboarding_dir=data / "onboarding",
        models_file=data / "config" / "models.json",
        portable=False,
        packaged=True,
    )


def test_diagnostics_export_rejects_reparse_point_output_root(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.diagnostics_dir.mkdir(parents=True)
    monkeypatch.setattr(
        diagnostics,
        "is_reparse_point",
        lambda path: Path(path) == paths.diagnostics_dir,
    )

    with pytest.raises(RuntimeError, match="symbolic link or Windows junction"):
        DiagnosticsCollector(paths=paths).export()


def test_log_collection_rejects_reparse_point_logs_root(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.logs_dir.mkdir(parents=True)
    (paths.logs_dir / "desktop.log").write_text("must-not-be-collected\n", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics_sections,
        "is_reparse_point",
        lambda path: Path(path) == paths.logs_dir,
    )
    collector = DiagnosticsCollector(paths=paths)
    files: dict[str, bytes] = {}
    categories: list[str] = []

    collector._collect_logs(files, categories)

    assert files == {}
    assert categories == []
    assert any("junction" in error for error in collector.collection_errors)


def test_log_collection_skips_reparse_point_log_file(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.logs_dir.mkdir(parents=True)
    log = paths.logs_dir / "desktop.log"
    log.write_text("must-not-be-collected\n", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics_sections,
        "is_reparse_point",
        lambda path: Path(path) == log,
    )
    collector = DiagnosticsCollector(paths=paths)
    files: dict[str, bytes] = {}
    categories: list[str] = []

    collector._collect_logs(files, categories)

    assert files == {}
    assert categories == []


def test_repeated_exports_at_same_timestamp_use_unique_paths(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    fixed = datetime(2026, 8, 19, 6, 50, tzinfo=UTC)
    suffixes = iter(("11111111", "22222222", "33333333"))
    monkeypatch.setattr(diagnostics.secrets, "token_hex", lambda _length: next(suffixes))
    hardware = {
        "fingerprint": "test",
        "os": {},
        "cpu": {},
        "memory": {},
        "disk": {},
        "runtime": {},
        "devices": [],
        "available_devices": [],
    }

    outputs = [
        DiagnosticsCollector(
            paths=paths,
            now=lambda: fixed,
            hardware_snapshot=hardware,
            npu_readiness={"state": "unknown"},
        ).export().path
        for _ in range(3)
    ]

    assert len(set(outputs)) == 3
    assert all(path.is_file() for path in outputs)
    assert not list(paths.diagnostics_dir.glob("*.tmp"))
