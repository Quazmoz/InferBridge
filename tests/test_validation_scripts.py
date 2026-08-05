from pathlib import Path

from scripts.validate_api_contract import Check, markdown, parse_sse, sanitize

ROOT = Path(__file__).resolve().parents[1]


def test_parse_sse_extracts_events_payloads_and_done():
    events, payloads, done = parse_sse(
        'event: response.created\ndata: {"type":"response.created"}\n\ndata: [DONE]\n\n'
    )
    assert events == ["response.created"]
    assert payloads == [{"type": "response.created"}]
    assert done is True


def test_sanitize_redacts_secrets_and_local_paths():
    text = sanitize(
        r"Bearer sk-secret hf_abcdefghijklmnopqrstuvwxyz token=abc C:\\Users\\Quinn\\model"
    )
    assert "sk-secret" not in text
    assert "hf_" not in text
    assert "Quinn" not in text
    assert "[redacted]" in text
    assert "[local-path]" in text


def test_markdown_contains_summary_and_checks():
    report = {
        "generated_at": "2026-07-16T12:00:00+00:00",
        "profile": "full",
        "model": "test-model",
        "requested_device": "CPU",
        "summary": {"pass": 1, "warn": 0, "skip": 0, "fail": 0},
        "checks": [
            {
                "name": "Health",
                "status": "pass",
                "duration_ms": 2.5,
                "detail": "mock=False",
            }
        ],
    }
    result = markdown(report)
    assert "# InferBridge API Validation" in result
    assert "1 passed" in result
    assert "test-model" in result
    assert "Health" in result


def test_check_dataclass_shape_is_report_safe():
    check = Check("Contract", "pass", 1.2, "ok")
    assert check.name == "Contract"
    assert check.status == "pass"


def test_windows_harness_forces_real_local_validation():
    script = (ROOT / "scripts" / "validate_windows.ps1").read_text(encoding="utf-8")
    assert '$env:OV_LLM_MOCK = ""' in script
    assert '"--expect-real"' in script
    # The harness must wait for the previous device's server to release the port
    # (rather than aborting the run on a transient conflict) and must do so inside
    # the per-device try so -ContinueOnFailure still covers the next device.
    assert "Wait-PortFree $Port" in script
    assert "Assert-PortFree" not in script
    assert "OV_LLM_API_KEY" in script
    assert '"--api-key"' not in script
    assert "certification/results" in script
    # Discovery helpers must not pass multi-line Python via `python -c "<code>"`:
    # Windows PowerShell 5.1 strips the embedded double quotes, corrupting the
    # JSON dictionary keys and aborting the whole run before any device is
    # certified. The snippet is written to a temp file and executed instead.
    assert "-c $Code" not in script
    assert "& $Python $scriptFile" in script
    # The documented `powershell.exe -File ... -Devices CPU,GPU,NPU,AUTO` invocation binds
    # the device list as one literal string under -File mode; the harness must split it so
    # each device is certified individually rather than as a bogus combined target.
    assert '-split "[,;]"' in script
    assert "certify_context_depth.py" in script
    assert "context_depth = $context" in script
    assert "schema_version = 2" in script
    assert "compiled_cache_reuse = $cacheReuse" in script
    assert "$cacheAfterFirstRestart.fingerprint -eq $cacheAfterSecondRestart.fingerprint" in script
