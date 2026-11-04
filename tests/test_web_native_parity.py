"""Browser-side additions that match native WinUI capabilities."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.advisor_ui import ADVISOR_EXTENSION_JS
from app.config import BASE_DIR, Settings
from app.server import create_app
from app.ui_quality import UI_QUALITY_JS

INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_web_exposes_top_p_like_native_settings():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="settings-top-p"' in html
    assert 'min="0" max="1" step="0.05"' in html
    assert "top_p: Number.isFinite(topP) ? topP : 1.0" in html
    assert "localStorage.setItem('ovllm.top_p.v1'" in html


def test_web_and_native_render_the_same_heading_levels():
    html = INDEX.read_text(encoding="utf-8")
    native = (
        INDEX.parents[1] / "windows-native" / "InferBridge.Native" / "ChatMarkdown.cs"
    ).read_text(encoding="utf-8")
    assert "line.match(/^(#{1,6})\\s+(.+)$/)" in html
    assert '@"^(#{1,6})\\s+(.+)$"' in native


# ── Benchmark Lab and chat device routing (native BenchmarkLabClient / GetDevicesAsync) ──


NATIVE = INDEX.parents[1] / "windows-native" / "InferBridge.Native"


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(source.index(") {", start) + 2, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start : index + 1]
    raise AssertionError(f"unterminated {name}")


def _run_node(script: str, payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _benchmark_helpers() -> str:
    start = ADVISOR_EXTENSION_JS.index("// benchmark-parity:start")
    end = ADVISOR_EXTENSION_JS.index("// benchmark-parity:end")
    return ADVISOR_EXTENSION_JS[start:end]


def test_web_benchmark_lab_uses_native_bounds_and_correlation():
    native = (NATIVE / "BenchmarkLabClient.cs").read_text(encoding="utf-8")
    assert "MaximumCombinations = 8" in native
    assert "const BENCHMARK_MAX_COMBINATIONS = 8;" in ADVISOR_EXTENSION_JS
    assert "client_run_id: clientRunId," in ADVISOR_EXTENSION_JS
    assert "Benchmark Lab · run ${clientRunId} · combination " in ADVISOR_EXTENSION_JS
    # Every native preset is offered with identical workloads.
    for runs, warmups, tokens in ((3, 1, 32), (5, 1, 64), (8, 2, 128)):
        assert f"({runs}, {warmups}, {tokens})" in native
        assert f"runs: {runs}, warmups: {warmups}, maxTokens: {tokens}" in ADVISOR_EXTENSION_JS
    # The legacy timestamp/prefix correlation is gone.
    assert "startsWith('Benchmark Lab ·')" not in ADVISOR_EXTENSION_JS


def test_web_benchmark_lab_treats_lost_responses_as_uncertain():
    js = ADVISOR_EXTENSION_JS
    assert "benchmarkUncertainRunId = clientRunId;" in js
    assert "if (benchmarkRunning || benchmarkUncertainRunId) return;" in js
    assert "Unable to confirm benchmark outcome" in js
    assert "No request was retried." in js
    assert "run?.client_run_id === benchmarkUncertainRunId" in js
    assert "Absence does not prove it was rejected." in js
    # Explicit 4xx (including 409 busy) is a confirmed refusal, never queued or retried.
    assert "response.status >= 400 && response.status < 500" in js
    assert "Nothing was queued." in js
    assert "no benchmark cancellation" in js
    assert 'id="benchmark-refresh-history"' in js
    assert ".slice(0, 100)" in js


def test_web_benchmark_history_comparability_matches_native():
    js = ADVISOR_EXTENSION_JS
    assert "Boolean(a.synthetic || a.mock) === Boolean(b.synthetic || b.mock)" in js
    assert "previous.configured_context_tokens === row.configured_context_tokens" in js
    assert "previous.reserved_output_tokens === row.reserved_output_tokens" in js
    assert "previous.actual_device === row.actual_device" in js


def test_web_benchmark_helpers_accept_real_engine_output(tmp_path):
    client_run_id = "ab" * 16
    other_run_id = "cd" * 16
    models = ["tinyllama-1.1b-chat-fp16", "tinyllama-1.1b-chat-int4"]
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        force_mock=True,
        benchmark_results_file=tmp_path / "benchmarks.json",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/benchmarks/run",
            json={
                "models": models,
                "devices": ["CPU"],
                "runs": 3,
                "max_tokens": 32,
                "client_run_id": client_run_id,
            },
        )
        assert response.status_code == 200, response.text
        events = client.get("/v1/system/status").json()["events"]
        history = client.get("/v1/benchmarks").json()["data"]
    run = response.json()
    assert history[0]["client_run_id"] == client_run_id
    foreign = [
        {"message": f"Benchmark Lab · run {other_run_id} · combination 2/2 · x · CPU · complete"},
        {"message": "Benchmark Lab · combination 2/2 · x · CPU · complete"},
    ]
    tampered = json.loads(json.dumps(run))
    tampered["results"] = tampered["results"][:1]
    mislabeled = json.loads(json.dumps(run))
    mislabeled["synthetic"] = False
    mislabeled["mock"] = True
    script = (
        "globalThis.window = { crypto: require('crypto').webcrypto };"
        + _benchmark_helpers()
        + """
        const p = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        const ids = [newBenchmarkClientRunId(), newBenchmarkClientRunId()];
        process.stdout.write(JSON.stringify({
            ids,
            real: readBenchmarkActivity(p.events, p.id, 2),
            foreignOnly: readBenchmarkActivity(p.foreign, p.id, 2),
            wrongTotal: readBenchmarkActivity(p.events, p.id, 3),
            backwards: readBenchmarkActivity([
                {message: `Benchmark Lab · run ${p.id} · combination 2/2 · m · CPU · failed`},
                {message: `Benchmark Lab · run ${p.id} · combination 1/2 · m · CPU`},
            ], p.id, 2),
            ok: benchmarkCompletionError(p.run, p.id, p.models, ['CPU']),
            otherRun: benchmarkCompletionError(p.run, p.other, p.models, ['CPU']),
            missing: benchmarkCompletionError(p.tampered, p.id, p.models, ['CPU']),
            mislabeled: benchmarkCompletionError(p.mislabeled, p.id, p.models, ['CPU']),
        }));
        """
    )
    out = _run_node(
        script,
        {
            "events": events,
            "foreign": foreign,
            "id": client_run_id,
            "other": other_run_id,
            "run": run,
            "tampered": tampered,
            "mislabeled": mislabeled,
            "models": models,
        },
    )
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in out["ids"])
    assert out["ids"][0] != out["ids"][1]
    assert out["real"]["completed"] == 2 and out["real"]["total"] == 2
    assert client_run_id not in out["real"]["message"]
    assert out["foreignOnly"] is None
    assert out["wrongTotal"] is None
    assert out["backwards"]["completed"] == 2
    assert out["ok"] == ""
    assert out["otherRun"] == "The engine returned a different benchmark operation."
    assert out["missing"] == "The engine did not confirm the benchmark matrix."
    assert out["mislabeled"] == "Mock benchmark cannot be labeled as real."


def _device_helpers(html: str) -> str:
    names = (
        "normalizeUiDevice",
        "deviceSuggestionValue",
        "isSupportedRoutingTarget",
        "deviceRoutingOptions",
    )
    pattern = re.search(r"const DIRECT_DEVICE_PATTERN = (/.+/);", html)
    assert pattern is not None
    return f"const DIRECT_DEVICE_PATTERN = {pattern.group(1)};\n" + "\n".join(
        _function_source(html, name) for name in names
    )


def test_web_chat_device_selector_mirrors_native_get_devices():
    html = INDEX.read_text(encoding="utf-8")
    backend = (NATIVE / "BackendClient.cs").read_text(encoding="utf-8")
    assert "GetDevicesAsync" in backend
    # No hardcoded composite targets: only backend suggestions are offered.
    assert "DEFAULT_ADVANCED_DEVICE_OPTIONS" not in html
    assert 'id="device-routing-summary" role="status" aria-live="polite"' in html
    assert "Next load target: ${target} · Reported active device: ${active}" in html
    # Selection alone never unloads or switches a running model.
    assert "await requestModelUnload(model.id);\n                await requestModelLoad" not in html
    assert "deviceSelect.replaceChildren();" in html
    assert "loadButton?.addEventListener('click', async event =>" in UI_QUALITY_JS
    assert "deviceSelectElement?.addEventListener('change', async" not in UI_QUALITY_JS

    out = _run_node(
        _device_helpers(html)
        + """
        const p = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        const pack = dev => { const r = deviceRoutingOptions(dev); return { direct: r.direct, advanced: [...r.advanced.keys()] }; };
        process.stdout.write(JSON.stringify(p.cases.map(pack)));
        """,
        {
            "cases": [
                {
                    "available": ["CPU", "GPU.0", "bogus", "NPU.1x"],
                    "suggestions": [
                        {"device": "AUTO:GPU,CPU", "experimental": False},
                        {"device": "MULTI:GPU,CPU", "experimental": True},
                        {"device": "HETERO:GPU,GPU"},
                        {"device": "AUTO"},
                        {"device": "AUTO:GPU,CPU"},
                        {"device": "<img src=x>"},
                    ],
                },
                {"available": [], "mock": True, "suggestions": []},
                {"available": []},
            ]
        },
    )
    assert out[0] == {"direct": ["GPU", "CPU"], "advanced": ["AUTO:GPU,CPU", "MULTI:GPU,CPU"]}
    assert out[1] == {"direct": ["NPU", "GPU", "CPU"], "advanced": []}
    assert out[2] == {"direct": [], "advanced": []}


def test_combined_device_builder_consumes_the_engine_on_both_sides():
    html = INDEX.read_text(encoding="utf-8")
    ui_quality = (INDEX.parents[1] / "app" / "ui_quality.py").read_text(encoding="utf-8")
    native = (NATIVE / "MainWindow.CombinedDevices.cs").read_text(encoding="utf-8")
    client = (NATIVE / "DeviceModesClient.cs").read_text(encoding="utf-8")
    xaml = (NATIVE / "MainWindow.xaml").read_text(encoding="utf-8")
    main = (NATIVE / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    # Web: compact builder beside the picker, engine-classified modes, keyboard ordering.
    assert 'id="device-combine"' in html and "device-combine-badge" in html
    assert "fetch(`/v1/devices/modes${query}`" in html
    assert "Move ${device} ${verb}" in html and "Use ${device}" in html
    assert "input.disabled = verdict.support === 'unsupported'" in html
    assert "...modelLoadOptions(modelLoadDevice())" in html
    assert "modelLoadOptions(nextDevice)" in ui_quality  # in-place device switch
    # Native: same endpoint, same four modes, up/down buttons, disabled unsupported radios.
    assert '"/v1/devices/modes" + query' in client and '"?members="' in client
    assert "GetCombinedModesAsync" in native and 'Ui.Pill("Experimental", "warning")' in native
    assert '"Move " + device + " " + verb' in native
    assert "IsEnabled = !support.Unsupported" in native
    assert "AutomationEvents.LiveRegionChanged" in native
    assert "performanceHint: hint, modelDistribution: distribution" in main
    assert 'x:Name="ChatCombineDevicesButton"' in xaml
    assert 'x:Name="LibraryCombineDevicesButton"' in xaml
    assert 'x:Name="BenchmarkCombinedPresetButton"' in xaml
    # Rules stay in app/device_modes.py: neither client hard-codes NPU/HETERO verdicts.
    for source in (native, client):
        assert "cannot share" not in source and "pipeline_parallel needs" not in source


def test_native_rerank_panel_matches_the_browser_connection_hub():
    hub = (INDEX.parents[1] / "app" / "connection_hub_ui.py").read_text(encoding="utf-8")
    panel = (NATIVE / "MainWindow.Rerank.cs").read_text(encoding="utf-8")
    client = (NATIVE / "RerankClient.cs").read_text(encoding="utf-8")
    xaml = (NATIVE / "MainWindow.xaml").read_text(encoding="utf-8")
    # Same heading, endpoint, request body and limits as the browser form.
    assert "Rerank for RAG" in hub and 'Text="Rerank for RAG"' in xaml
    assert "return_documents: true" in hub and "return_documents = true" in client
    assert ".slice(0, 50)" in hub and "MaximumDocuments = 50" in client
    assert 'maxlength="2000"' in hub and 'MaxLength="2000"' in xaml
    assert '"/v1/rerank"' in client and '"/v1/models/slots"' in client
    # Disabled with guidance while no reranker is resident; WinUI "\r" lines are split.
    assert "_rerankSlot is not null" in panel and "Load as reranker" in panel
    assert "'\\r', '\\n'" in client
    assert 'x:Name="RerankResults"' in xaml and "Documents ordered by relevance" in xaml
    assert "AutomationEvents.LiveRegionChanged" in panel


def test_vram_split_suggestion_is_shown_never_selected():
    html = INDEX.read_text(encoding="utf-8")
    native = (NATIVE / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    client = (NATIVE / "DeviceModesClient.cs").read_text(encoding="utf-8")
    assert "model?.advisor?.device_suggestion" in html and "Suggested: ${split.device}" in html
    assert '"device_suggestion"' in client and '" · Suggested: "' in native
    # Neither client assigns the suggestion to the selection.
    assert "selectedDevice = split" not in html and "_combinedTarget = split" not in native
