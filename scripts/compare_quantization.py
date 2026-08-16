"""Compare INT4, INT8, and FP16 variants on the same local hardware.

This is an evidence tool, not a certification shortcut. It requires pre-converted
OpenVINO GenAI model variants and records deterministic smoke-quality results,
load/generation timings, converted size, and response similarity against a reference.

Example:
    python scripts/compare_quantization.py \
      qwen2.5-1.5b-int4 qwen2.5-1.5b-int8 qwen2.5-1.5b-fp16 \
      --device GPU --reference qwen2.5-1.5b-fp16 \
      --json benchmark-results/qwen-1.5b-quantization.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import model_registry  # noqa: E402
from app.config import BASE_DIR  # noqa: E402
from runtime.openvino_engine import build_plugin_config  # noqa: E402

QUALITY_CASES = (
    ("exact", "Reply with exactly INFERBRIDGE_OK and nothing else.", "INFERBRIDGE_OK"),
    ("arithmetic", "What is 17 multiplied by 23? Reply with only the integer.", "391"),
    ("instruction", "Reply with exactly three words: local inference works", "local inference works"),
)


@dataclass
class QualityCaseResult:
    case_id: str
    expected: str
    output: str
    passed: bool
    latency_s: float
    output_tokens: int
    tokens_per_s: float | None
    reference_similarity: float | None = None


@dataclass
class QuantizationResult:
    model_id: str
    source_model: str
    weight_format: str
    device: str
    success: bool
    model_path: str
    converted_size_bytes: int | None = None
    load_time_s: float | None = None
    smoke_pass_rate: float | None = None
    mean_latency_s: float | None = None
    mean_tokens_per_s: float | None = None
    cases: list[QualityCaseResult] = field(default_factory=list)
    error: str | None = None


def _normalize_answer(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _quality_pass(output: str, expected: str) -> bool:
    return _normalize_answer(output) == _normalize_answer(expected)


def _response_similarity(output: str, reference: str) -> float:
    return round(
        SequenceMatcher(None, _normalize_answer(output), _normalize_answer(reference)).ratio(),
        4,
    )


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _result_text(result: Any) -> str:
    texts = getattr(result, "texts", None)
    if texts:
        return str(texts[0])
    return str(result)


def _count_tokens(pipe: Any, text: str) -> int:
    try:
        ids = pipe.get_tokenizer().encode(text).input_ids
        try:
            return int(ids.get_shape()[-1])
        except Exception:
            return int(ids.shape[-1])
    except Exception:
        return max(1, len(text) // 4)


def _generation_config(ov_genai: Any, max_new_tokens: int) -> Any:
    cfg = ov_genai.GenerationConfig()
    cfg.max_new_tokens = int(max_new_tokens)
    if hasattr(cfg, "do_sample"):
        cfg.do_sample = False
    if hasattr(cfg, "temperature"):
        cfg.temperature = 0.0
    return cfg


def _generate(pipe: Any, ov_genai: Any, prompt: str, max_new_tokens: int) -> tuple[str, float, int]:
    config = _generation_config(ov_genai, max_new_tokens)
    started = time.perf_counter()
    result = pipe.generate(prompt, config)
    elapsed = time.perf_counter() - started
    text = _result_text(result).strip()
    return text, elapsed, _count_tokens(pipe, text)


def _validate_family(configs: list[model_registry.ModelConfig]) -> None:
    sources = {cfg.source_model for cfg in configs}
    if len(sources) != 1:
        raise SystemExit("All compared model IDs must use the same source_model.")
    formats = [cfg.weight_format for cfg in configs]
    if len(formats) != len(set(formats)):
        raise SystemExit("Provide at most one model variant for each weight format.")


def _benchmark_variant(
    *,
    cfg: model_registry.ModelConfig,
    device: str,
    ov_genai: Any,
    max_new_tokens: int,
    max_prompt_len: int,
    cache_dir: Path,
) -> QuantizationResult:
    model_path = cfg.abs_path(BASE_DIR)
    result = QuantizationResult(
        model_id=cfg.id,
        source_model=cfg.source_model,
        weight_format=cfg.weight_format,
        device=device,
        success=False,
        model_path=str(model_path),
    )
    try:
        if not model_registry.is_downloaded(cfg, BASE_DIR):
            raise RuntimeError("OpenVINO model files are not converted yet.")
        result.converted_size_bytes = _directory_size_bytes(model_path)
        plugin_config = build_plugin_config(device, max_prompt_len, cache_dir)
        started = time.perf_counter()
        pipe = (
            ov_genai.LLMPipeline(str(model_path), device, **plugin_config)
            if plugin_config
            else ov_genai.LLMPipeline(str(model_path), device)
        )
        result.load_time_s = time.perf_counter() - started

        cases: list[QualityCaseResult] = []
        for case_id, prompt, expected in QUALITY_CASES:
            output, latency, tokens = _generate(pipe, ov_genai, prompt, max_new_tokens)
            cases.append(
                QualityCaseResult(
                    case_id=case_id,
                    expected=expected,
                    output=output,
                    passed=_quality_pass(output, expected),
                    latency_s=latency,
                    output_tokens=tokens,
                    tokens_per_s=(tokens / latency) if latency > 0 else None,
                )
            )
        result.cases = cases
        result.smoke_pass_rate = sum(case.passed for case in cases) / len(cases)
        result.mean_latency_s = statistics.mean(case.latency_s for case in cases)
        rates = [case.tokens_per_s for case in cases if case.tokens_per_s is not None]
        result.mean_tokens_per_s = statistics.mean(rates) if rates else None
        result.success = True
        pipe = None
    except Exception as exc:  # noqa: BLE001 - comparison should retain per-variant failures
        result.error = str(exc)
    return result


def _attach_reference_similarity(
    results: list[QuantizationResult], reference_model_id: str | None
) -> None:
    if not reference_model_id:
        return
    reference = next(
        (result for result in results if result.model_id == reference_model_id and result.success),
        None,
    )
    if reference is None:
        return
    reference_cases = {case.case_id: case.output for case in reference.cases}
    for result in results:
        for case in result.cases:
            reference_output = reference_cases.get(case.case_id)
            if reference_output is not None:
                case.reference_similarity = _response_similarity(case.output, reference_output)


def _print_summary(results: list[QuantizationResult]) -> None:
    print("model                          format  status  size_gb  load_s  smoke  tok/s")
    print("-----------------------------  ------  ------  -------  ------  -----  -----")
    for result in results:
        size_gb = (
            f"{result.converted_size_bytes / (1024**3):.2f}"
            if result.converted_size_bytes is not None
            else "-"
        )
        load_s = f"{result.load_time_s:.2f}" if result.load_time_s is not None else "-"
        smoke = f"{result.smoke_pass_rate * 100:.0f}%" if result.smoke_pass_rate is not None else "-"
        rate = f"{result.mean_tokens_per_s:.2f}" if result.mean_tokens_per_s is not None else "-"
        status = "ok" if result.success else "fail"
        print(
            f"{result.model_id[:29]:29}  {result.weight_format:6}  {status:6}  "
            f"{size_gb:7}  {load_s:6}  {smoke:5}  {rate:5}"
        )
        if result.error:
            print(f"  error: {result.error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare quantized variants of one source model on local OpenVINO hardware."
    )
    parser.add_argument("models", nargs="+", help="Catalog IDs for the same source model")
    parser.add_argument("--device", default="CPU", help="One OpenVINO device target")
    parser.add_argument("--reference", help="Model ID used for response-similarity comparison")
    parser.add_argument("--max-new-tokens", type=int, default=32)
    parser.add_argument("--max-prompt-len", type=int, default=1024)
    parser.add_argument(
        "--models-file", type=Path, default=BASE_DIR / "models.json", help="Catalog JSON path"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=BASE_DIR / "models" / "cache", help="OpenVINO cache dir"
    )
    parser.add_argument("--json", type=Path, help="Optional evidence report path")
    args = parser.parse_args(argv)

    if args.max_new_tokens < 1:
        parser.error("--max-new-tokens must be at least 1")
    catalog = model_registry.load_catalog(args.models_file)
    missing = [model_id for model_id in args.models if model_id not in catalog]
    if missing:
        parser.error(f"Unknown model ID(s): {', '.join(missing)}")
    configs = [catalog[model_id] for model_id in args.models]
    _validate_family(configs)
    if args.reference and args.reference not in args.models:
        parser.error("--reference must be one of the compared model IDs")

    try:
        import openvino_genai as ov_genai
    except Exception as exc:  # noqa: BLE001
        print(f"openvino_genai import failed: {exc}", file=sys.stderr)
        return 2

    results = [
        _benchmark_variant(
            cfg=cfg,
            device=args.device,
            ov_genai=ov_genai,
            max_new_tokens=args.max_new_tokens,
            max_prompt_len=args.max_prompt_len,
            cache_dir=args.cache_dir,
        )
        for cfg in configs
    ]
    _attach_reference_similarity(results, args.reference)
    _print_summary(results)
    print("\nEvidence only: smoke prompts and timings are not hardware or quality certification.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "device": args.device,
            "reference_model_id": args.reference,
            "results": [asdict(result) for result in results],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote evidence report to {args.json}")

    return 0 if all(result.success for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
