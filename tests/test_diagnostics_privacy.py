"""Privacy contract for the local diagnostics support bundle.

A diagnostics ZIP is the one artefact a user is invited to hand to a stranger, so the
redaction rules and the archive-path guard are the security boundary. These tests pin
what must never reach the bundle and what the bundle promises to contain.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest

from app.config import BASE_DIR
from app.diagnostics import DiagnosticsCollector
from app.diagnostics_privacy import (
    MAX_LOG_LINES,
    benchmark_summary,
    bounded_log_text,
    certification_summary,
    json_bytes,
    redact_path,
    safe_archive_name,
    safe_disk_payload,
    sanitize_text,
    sanitize_value,
)
from app.paths import RuntimePaths


@pytest.fixture
def paths(tmp_path) -> RuntimePaths:
    for name in ("config", "logs", "models", "benchmarks", "diagnostics", "onboarding"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        resource_root=BASE_DIR,
        data_root=tmp_path,
        config_dir=tmp_path / "config",
        logs_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
        huggingface_cache_dir=tmp_path / "cache" / "huggingface",
        compiled_cache_dir=tmp_path / "cache" / "openvino",
        benchmarks_dir=tmp_path / "benchmarks",
        diagnostics_dir=tmp_path / "diagnostics",
        onboarding_dir=tmp_path / "onboarding",
        models_file=tmp_path / "config" / "models.json",
        portable=True,
        packaged=False,
    )


# --- sanitize_text ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer sk-abc123def456ghi",
        "hf_ABCDEFGHIJKLMNOPqrstuvwx1234",
        "api_key=supersecret123",
        "API-KEY: supersecret123",
        "token = abc.def-ghi",
        "password: hunter2",
        "secret=classified",
    ],
)
def test_credential_shapes_never_survive_sanitization(raw) -> None:
    cleaned = sanitize_text(raw)

    assert "[redacted-secret]" in cleaned
    for leak in ("sk-abc123def456ghi", "supersecret123", "hunter2", "classified", "abc.def-ghi"):
        assert leak not in cleaned
    assert "hf_ABCDEFGHIJKLMNOP" not in cleaned


def test_email_addresses_are_replaced() -> None:
    assert sanitize_text("owner user@example.com filed this") == (
        "owner [redacted-email] filed this"
    )


def test_home_directory_names_are_replaced_on_both_platforms() -> None:
    assert sanitize_text(r"C:\Users\alice\AppData\model.bin") == (
        r"C:\Users\<redacted-user>\AppData\model.bin"
    )
    assert sanitize_text("/home/deploy/.cache/hf") == "/home/<redacted-user>/.cache/hf"
    assert sanitize_text("/Users/alice/Documents") == "/home/<redacted-user>/Documents"


def test_a_bare_users_path_segment_is_not_mistaken_for_a_home_directory() -> None:
    """The POSIX home pattern requires a real path boundary before ``/Users``."""

    assert sanitize_text("api/Users/list") == "api/Users/list"


def test_control_characters_are_stripped_but_newlines_survive() -> None:
    cleaned = sanitize_text("line one\x00\x07\nline two\ttabbed")

    assert cleaned == "line one\nline two\ttabbed"


def test_sanitization_reports_which_rules_fired() -> None:
    redactions: set[str] = set()
    sanitize_text(
        r"user@example.com hf_ABCDEFGHIJKL1234 C:\Users\bob\x /home/carol/y",
        redactions=redactions,
    )

    assert redactions == {
        "secret patterns",
        "email addresses",
        "Windows user directory names",
        "home directory names",
    }


def test_sanitization_reports_nothing_for_clean_text() -> None:
    redactions: set[str] = set()
    assert sanitize_text("CPU load 42 percent", redactions=redactions) == "CPU load 42 percent"
    assert redactions == set()


def test_sanitization_enforces_the_length_limit() -> None:
    assert len(sanitize_text("a" * 5000, limit=100)) == 100


def test_sanitization_accepts_non_string_and_missing_values() -> None:
    assert sanitize_text(None) == ""
    assert sanitize_text(0) == ""  # falsy values collapse to the empty string
    assert sanitize_text(1234) == "1234"
    assert sanitize_text(ValueError("api_key=leaked")) == "[redacted-secret]"


# --- sanitize_value --------------------------------------------------------------


def test_secret_named_fields_are_redacted_at_any_depth() -> None:
    payload = {
        "api_key": "leaked",
        "hf_token": "leaked",
        "Authorization": "leaked",
        "private_key": "leaked",
        "certificate": "leaked",
        "nested": {"list": [{"password": "leaked"}]},
    }

    cleaned = sanitize_value(payload)

    assert "leaked" not in json.dumps(cleaned)
    assert cleaned["nested"]["list"][0]["password"] == "[redacted-secret]"


def test_the_api_key_configured_flag_is_kept_because_it_holds_no_secret() -> None:
    cleaned = sanitize_value({"api_key_configured": True, "api_key": "leaked"})

    assert cleaned == {"api_key_configured": True, "api_key": "[redacted-secret]"}


def test_scalar_types_are_preserved_for_machine_readable_diagnostics() -> None:
    cleaned = sanitize_value({"port": 8000, "ratio": 1.5, "mock": False, "missing": None})

    assert cleaned == {"port": 8000, "ratio": 1.5, "mock": False, "missing": None}


def test_paths_inside_structures_are_redacted() -> None:
    cleaned = sanitize_value({"models_dir": PurePosixPath("/home/dana/models")})

    assert cleaned == {"models_dir": "/home/<redacted-user>/models"}


def test_sequences_are_normalized_to_lists_and_bounded() -> None:
    cleaned = sanitize_value({"tuple": ("a", "b"), "long": list(range(600))})

    assert cleaned["tuple"] == ["a", "b"]
    assert len(cleaned["long"]) == 500


def test_non_string_mapping_keys_are_stringified_safely() -> None:
    cleaned = sanitize_value({1: "one", None: "none"})

    assert cleaned == {"1": "one", "": "none"}


# --- archive names ---------------------------------------------------------------


def test_safe_archive_names_keep_readable_nested_paths() -> None:
    assert safe_archive_name("logs/desktop.log.txt") == "logs/desktop.log.txt"
    assert safe_archive_name("logs\\desktop.txt") == "logs/desktop.txt"
    assert safe_archive_name("./certification/report.json") == "certification/report.json"
    assert safe_archive_name("odd name!.json") == "odd_name_.json"


@pytest.mark.parametrize(
    "name",
    [
        "../escape.json",
        "logs/../../escape.json",
        "/absolute.json",
        "C:/absolute.json",
        "C:\\absolute.json",
        "",
        "/",
        ".",
        "..",
    ],
)
def test_archive_paths_that_could_escape_the_bundle_are_rejected(name) -> None:
    with pytest.raises(ValueError, match="Unsafe diagnostics archive path."):
        safe_archive_name(name)


def test_archive_name_segments_are_length_bounded() -> None:
    assert len(safe_archive_name("a" * 400)) == 120


# --- bounded log reading ---------------------------------------------------------


def test_log_reading_keeps_only_the_tail(tmp_path) -> None:
    log = tmp_path / "desktop.log"
    log.write_text("\n".join(f"line {n}" for n in range(1000)) + "\n", encoding="utf-8")

    text = bounded_log_text(log)

    lines = text.splitlines()
    assert len(lines) == MAX_LOG_LINES
    assert lines[-1] == "line 999"
    assert "line 0\n" not in text


def test_log_lines_that_could_hold_conversation_content_are_dropped(tmp_path) -> None:
    log = tmp_path / "desktop.log"
    log.write_text(
        "\n".join(
            [
                "startup complete",
                "raw request: {'user': 'hello'}",
                "prompt_text: summarize this",
                "messages = [{'role': 'user'}]",
                "chat history restored",
                "source image decoded",
                "shutdown complete",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    text = bounded_log_text(log)

    assert text.count("[redacted-content-line]") == 5
    assert "hello" not in text
    assert "summarize this" not in text
    assert "startup complete" in text
    assert "shutdown complete" in text


def test_an_empty_log_reads_as_empty(tmp_path) -> None:
    log = tmp_path / "desktop.log"
    log.write_text("", encoding="utf-8")

    assert bounded_log_text(log) == ""


def test_undecodable_log_bytes_do_not_raise(tmp_path) -> None:
    log = tmp_path / "desktop.log"
    log.write_bytes(b"valid\n\xff\xfe invalid bytes\n")

    assert "valid" in bounded_log_text(log)


# --- summaries -------------------------------------------------------------------


def test_benchmark_summary_keeps_only_allowlisted_measurements() -> None:
    summary = benchmark_summary(
        {
            "run_id": "r1",
            "created_at": "2026-01-01T00:00:00Z",
            "prompt": "leaked prompt",
            "results": [
                {"model_id": "m", "tokens_sec": 12.5, "prompt_text": "leaked", "error": None},
            ],
        }
    )

    assert summary["run_id"] == "r1"
    assert "prompt" not in summary
    assert summary["results"] == [{"model_id": "m", "tokens_sec": 12.5, "error": None}]


def test_benchmark_summary_bounds_the_result_list() -> None:
    summary = benchmark_summary({"results": [{"model_id": str(n)} for n in range(80)]})

    assert len(summary["results"]) == 50


def test_certification_summary_rejects_a_non_object_report() -> None:
    assert certification_summary(["not", "an", "object"]) == {
        "status": "unavailable",
        "reason": "Certification report was not an object.",
    }


def test_certification_summary_redacts_content_bearing_failures() -> None:
    summary = certification_summary(
        {
            "status": "failed",
            "devices": [{"device": "NPU", "driver_version": "1.2", "serial": "leaked"}],
            "results": [{"test": "load", "success": False, "prompt_text": "leaked"}],
            "failures": ["raw request: leaked", "device timeout after 30s"],
            "warnings": ["api_key=leaked"],
        }
    )

    assert summary["devices"] == [{"device": "NPU", "driver_version": "1.2"}]
    assert summary["results"] == [{"test": "load", "success": False}]
    assert summary["failures"] == ["[redacted-content]", "device timeout after 30s"]
    assert summary["warnings"] == ["[redacted-secret]"]


def test_disk_payload_reports_real_capacity_for_a_missing_directory(tmp_path) -> None:
    payload = safe_disk_payload({}, tmp_path / "not-created-yet")

    assert payload["total_gb"] > 0
    assert payload["free_gb"] >= 0


def test_json_bytes_is_stable_and_newline_terminated() -> None:
    encoded = json_bytes({"b": 1, "a": 2})

    assert encoded == b'{\n  "a": 2,\n  "b": 1\n}\n'


def test_redact_path_accepts_both_paths_and_strings() -> None:
    assert redact_path(PurePosixPath("/home/erin/models")) == "/home/<redacted-user>/models"
    assert redact_path("/home/erin/models") == "/home/<redacted-user>/models"


# --- end-to-end export -----------------------------------------------------------


def _export(paths: RuntimePaths, **kwargs) -> tuple[zipfile.ZipFile, object]:
    collector = DiagnosticsCollector(
        paths=paths,
        now=lambda: datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC),
        **kwargs,
    )
    result = collector.export()
    return zipfile.ZipFile(result.path), result


def test_export_produces_a_named_archive_with_a_manifest(paths) -> None:
    archive, result = _export(paths)

    # A random suffix keeps a tray export and a server export in the same second from
    # colliding; the timestamped prefix is the part that stays contractual.
    assert result.path.name.startswith("inferbridge-diagnostics-20260304-050607-")
    assert result.path.suffix == ".zip"
    with archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 1
        assert manifest["installation_mode"] == "portable"
        # The manifest lists itself, so a recipient can detect a truncated bundle.
        assert "manifest.json" in manifest["files"]
        assert set(manifest["files"]) == set(archive.namelist())


def test_export_never_writes_the_excluded_categories(paths) -> None:
    archive, result = _export(paths)

    with archive:
        assert "prompts and chat history" in result.excluded_categories
        assert "API keys and Hugging Face tokens" in result.excluded_categories


def test_export_redacts_secrets_reaching_it_through_the_runtime_snapshot(paths) -> None:
    archive, _result = _export(
        paths,
        runtime_snapshot={
            "active_model": "tiny",
            "events": [
                "loaded /home/frank/models/tiny",
                "auth used api_key=leaked-value",
                "operator frank@example.com started a run",
            ],
        },
        effective_configuration={"api_key_configured": True, "port": 8000},
    )

    with archive:
        body = archive.read("events.json").decode("utf-8")
        assert "leaked-value" not in body
        assert "frank@example.com" not in body
        assert "/home/frank" not in body
        assert "[redacted-secret]" in body
        configuration = json.loads(archive.read("configuration.json"))
        assert configuration["api_key_configured"] is True


def test_export_records_which_redaction_rules_were_applied(paths) -> None:
    archive, _result = _export(
        paths,
        runtime_snapshot={"events": ["token = leaked", "reader@example.com"]},
    )

    with archive:
        manifest = json.loads(archive.read("manifest.json"))

    assert "secret patterns" in manifest["redactions_applied"]
    assert "email addresses" in manifest["redactions_applied"]


def test_export_only_collects_allowlisted_log_files(paths) -> None:
    (paths.logs_dir / "desktop.log").write_text("desktop started\n", encoding="utf-8")
    (paths.logs_dir / "conversation.log").write_text("user said hello\n", encoding="utf-8")

    archive, result = _export(paths)

    with archive:
        names = archive.namelist()
        assert "logs/desktop.log.txt" in names
        assert not any("conversation" in name for name in names)
        assert "hello" not in archive.read("logs/desktop.log.txt").decode("utf-8")
    assert "sanitized logs" in result.included_categories


def test_export_summarizes_certification_reports_without_copying_them(paths) -> None:
    (paths.diagnostics_dir / "npu-certification.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "hardware_fingerprint": "abc",
                "results": [{"test": "load", "success": True, "prompt_text": "leaked"}],
            }
        ),
        encoding="utf-8",
    )

    archive, result = _export(paths)

    with archive:
        summary = json.loads(archive.read("certification/npu-certification-summary.json"))
        assert summary["status"] == "passed"
        assert summary["results"] == [{"test": "load", "success": True}]
    assert "certification summaries" in result.included_categories


def test_export_skips_an_oversized_certification_report(paths) -> None:
    oversized = paths.diagnostics_dir / "huge-certification.json"
    oversized.write_text(json.dumps({"pad": "x" * (300 * 1024)}), encoding="utf-8")

    archive, result = _export(paths)

    with archive:
        assert not any("huge" in name for name in archive.namelist())
    assert any("exceeded size limit" in error for error in result.manifest["collection_errors"])


def test_export_records_a_collection_error_rather_than_failing_the_bundle(paths) -> None:
    (paths.diagnostics_dir / "broken-certification.json").write_text("{ not json", encoding="utf-8")

    archive, result = _export(paths)

    with archive:
        assert "manifest.json" in archive.namelist()
    assert any("broken-certification" in error for error in result.manifest["collection_errors"])


def test_two_exports_in_the_same_second_do_not_overwrite_each_other(paths) -> None:
    stamp = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    first = DiagnosticsCollector(paths=paths, now=lambda: stamp).export()
    second = DiagnosticsCollector(paths=paths, now=lambda: stamp).export()

    assert first.path != second.path
    assert first.path.exists() and second.path.exists()


def test_export_refuses_a_symlinked_diagnostics_directory(tmp_path, paths) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    linked = tmp_path / "linked-diagnostics"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable on this platform.")
    paths = RuntimePaths(**{**paths.__dict__, "diagnostics_dir": linked})

    with pytest.raises(RuntimeError, match="symbolic link"):
        DiagnosticsCollector(paths=paths).export()


def test_export_leaves_no_temporary_file_behind(paths) -> None:
    _archive, result = _export(paths)

    leftovers = list(paths.diagnostics_dir.glob("*.tmp"))
    assert leftovers == []
    assert result.path.suffix == ".zip"
