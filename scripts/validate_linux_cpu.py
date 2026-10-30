"""Bounded real CPU probe using the downloaded bundle and an untrained test model.

Run with the bundle's private interpreter. This checks conversion, direct CPU
generation, streaming and cleanup, not language quality or GPU/NPU compatibility.
No model files enter the distribution artifact.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import resource
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

MODEL = "trl-internal-testing/tiny-random-LlamaForCausalLM"
REVISION = "2c542e4731d42d3bb68324c4eca16df9f93b8b33"
MAX_DOWNLOAD = 16 * 1024 * 1024
MODEL_FILES = frozenset(
    {
        "config.json",
        "generation_config.json",
        "model.safetensors",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer.model",
        "tokenizer_config.json",
    }
)


def selected_files(siblings) -> list[str]:
    files = [item for item in siblings if item.rfilename in MODEL_FILES]
    if any(item.size is None or item.size < 0 for item in files):
        raise RuntimeError("The model service did not provide bounded file sizes.")
    if not files or sum(item.size for item in files) > MAX_DOWNLOAD:
        raise RuntimeError("The CPU test model exceeds its 16 MiB download budget.")
    if "model.safetensors" not in {item.rfilename for item in files}:
        raise RuntimeError("The test requires safetensors; pickle weights are excluded.")
    return [item.rfilename for item in files]


def probe(bundle: Path, output: Path) -> None:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("This probe requires native Linux x86_64.")
    if not Path(sys.executable).resolve().is_relative_to(bundle / "python"):
        raise RuntimeError("Run this probe with the bundle's private Python, not host Python.")
    sys.path.insert(0, str(bundle / "resources"))
    from huggingface_hub import HfApi, snapshot_download
    from openvino import Core

    import runtime
    from runtime.model_converter import export_model
    from runtime.openvino_engine import GenParams, OpenVINOEngine, create_engine

    assert Path(runtime.__file__).resolve().is_relative_to(bundle)
    assert "CPU" in Core().available_devices
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="inferbridge-real-cpu-") as directory:
        work = Path(directory)
        os.environ["HF_HOME"] = str(work / "hf")
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["DO_NOT_TRACK"] = "1"
        os.environ["PATH"] = str(bundle / "python/bin") + os.pathsep + os.environ.get("PATH", "")
        os.environ["PYTHONPATH"] = str(bundle / "resources")
        os.environ["PYTHONSAFEPATH"] = "1"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "30"
        info = HfApi(token=False).model_info(
            MODEL, revision=REVISION, files_metadata=True, timeout=30
        )
        files = selected_files(info.siblings)
        source = snapshot_download(
            MODEL,
            revision=REVISION,
            allow_patterns=files,
            local_dir=work / "source",
            token=False,
            max_workers=2,
            etag_timeout=30,
        )
        model = export_model(
            source,
            work / "openvino",
            "fp16",
            task="text-generation-with-past",
            trust_remote_code=False,
        )
        engine = create_engine(
            model_id="linux-cpu-probe",
            model_path=str(model),
            device="CPU",
            force_mock=False,
            cache_dir=work / "compiled",
        )
        assert isinstance(engine, OpenVINOEngine), "A mock engine cannot qualify real inference"
        try:
            params = GenParams(max_new_tokens=8, do_sample=False)
            result = engine.generate("Hello", params)
            assert result.text and result.completion_tokens > 0
            stream = engine.stream("Hello", params)
            parts = []
            while (chunk := stream.next_chunk()) is not None:
                parts.append(chunk)
            assert stream.wait_closed(timeout=30) and stream.error is None
            assert "".join(parts)
        finally:
            engine.close()
        assert engine._closed and engine._pipe is None
        cpu = {}
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                if key in {"model name", "vendor_id"} and key not in cpu:
                    cpu[key] = value
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "commit": json.loads((bundle / "resources/linux-build.json").read_text())["commit"],
            "os": platform.freedesktop_os_release(),
            "kernel": platform.release(),
            "cpu": cpu,
            "openvino_cpu_name": Core().get_property("CPU", "FULL_DEVICE_NAME"),
            "versions": {
                name: importlib.metadata.version(name)
                for name in (
                    "openvino",
                    "openvino-genai",
                    "openvino-tokenizers",
                    "optimum-intel",
                    "torch",
                )
            },
            "model": MODEL,
            "revision": REVISION,
            "precision": "fp16",
            "download_bytes": sum(item.size for item in info.siblings if item.rfilename in files),
            "requested_device": "CPU",
            "execution_evidence": "Successful OpenVINO GenAI LLMPipeline generation compiled for the direct CPU target; no AUTO or fallback routing",
            "completion_tokens": result.completion_tokens,
            "stream_chunks": len(parts),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "peak_process_rss_kib": max(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            ),
            "memory_scope": "Maximum process high-water RSS of probe or child converter, not simultaneous process-tree RSS",
            "mock": False,
            "passed": True,
            "limits": "Untrained random test weights; no language-quality, full application CPU API, GPU or NPU certification",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe(args.bundle.resolve(), args.output.resolve())
