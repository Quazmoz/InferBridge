"""Exercise real conversion cancellation and retry without retaining model files."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.model_manager import ModelManager
from runtime.model_artifacts import validate_openvino_model_dir


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _wait_for_phase(manager: ModelManager, model_id: str, phase: str, timeout: int) -> None:
    for _ in range(timeout * 4):
        if manager.progress.get(model_id, {}).get("phase") == phase:
            return
        task = manager.convert_tasks.get(model_id)
        if task is not None and task.done():
            break
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Conversion did not reach phase '{phase}' within {timeout} seconds.")


async def _run(args: argparse.Namespace) -> int:
    root = args.workspace.resolve()
    catalog_path = root / "models.json"
    model_dir = root / "models" / args.model_id
    cache_dir = root / "compiled-cache"
    catalog = {
        args.model_id: {
            "name": "Conversion lifecycle certification model",
            "description": "Temporary certification-only conversion.",
            "backend": "openvino-genai",
            "model_path": str(model_dir),
            "source_model": args.source_model,
            "weight_format": args.weight_format,
            "recommended_device": "CPU",
            "max_context_len": args.max_context,
            "max_output_tokens": args.max_output,
        }
    }
    root.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    old_models_file = os.environ.get("OV_LLM_MODELS_FILE")
    old_models_dir = os.environ.get("OV_LLM_MODELS_DIR")
    os.environ["OV_LLM_MODELS_FILE"] = str(catalog_path)
    os.environ["OV_LLM_MODELS_DIR"] = str(root / "models")
    report: dict[str, object] = {
        "schema_version": 1,
        "generated_at": _now(),
        "model_id": args.model_id,
        "source_model": args.source_model,
        "weight_format": args.weight_format,
    }
    try:
        settings = Settings.from_env().replace(
            models_file=catalog_path,
            models_dir=root / "models",
            cache_dir=cache_dir,
            default_model=None,
            force_mock=False,
        )
        manager = ModelManager(settings)
        first = manager.schedule_convert(
            args.model_id,
            "CPU",
            load_after=False,
            weight_format=args.weight_format,
        )
        if first is None:
            raise RuntimeError("First conversion was not scheduled.")
        await _wait_for_phase(manager, args.model_id, "downloading", args.start_timeout)
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        cancelled = first.cancelled() and args.model_id not in manager.convert_tasks
        report["cancellation"] = {
            "requested": True,
            "task_cancelled": first.cancelled(),
            "task_removed": args.model_id not in manager.convert_tasks,
            "passed": cancelled,
        }

        retry = manager.schedule_convert(
            args.model_id,
            "CPU",
            load_after=False,
            weight_format=args.weight_format,
        )
        if retry is None:
            raise RuntimeError("Retry conversion was not scheduled.")
        await retry
        validation = validate_openvino_model_dir(model_dir)
        ir_present = (model_dir / "openvino_model.xml").is_file() and (
            model_dir / "openvino_model.bin"
        ).is_file()
        report["retry"] = {
            "scheduled": True,
            "converted_ir_present": ir_present,
            "runnable_artifact_present": validation.ready,
            "artifact_validation": validation.reason,
            "output_bytes": sum(
                path.stat().st_size for path in model_dir.rglob("*") if path.is_file()
            ),
            "passed": validation.ready,
        }
        report["passed"] = bool(cancelled and validation.ready)
        return_code = 0 if report["passed"] else 1
        await manager.shutdown()
    except Exception as exc:  # noqa: BLE001 - retained report records failed lifecycle
        report["passed"] = False
        report["error"] = str(exc)
        return_code = 1
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(model_dir, ignore_errors=True)
        if old_models_file is None:
            os.environ.pop("OV_LLM_MODELS_FILE", None)
        else:
            os.environ["OV_LLM_MODELS_FILE"] = old_models_file
        if old_models_dir is None:
            os.environ.pop("OV_LLM_MODELS_DIR", None)
        else:
            os.environ["OV_LLM_MODELS_DIR"] = old_models_dir
    return return_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-id", default="cert-smollm2-135m-int4")
    parser.add_argument("--source-model", default="HuggingFaceTB/SmolLM2-135M-Instruct")
    parser.add_argument("--weight-format", choices=("int4", "int8", "fp16"), default="int4")
    parser.add_argument("--max-context", type=int, default=2048)
    parser.add_argument("--max-output", type=int, default=512)
    parser.add_argument("--start-timeout", type=int, default=120)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
