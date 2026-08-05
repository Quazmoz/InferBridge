import asyncio
import json

from app.config import Settings
from app.model_manager import ModelManager
from app.structured_progress import _CONVERTER_DIAGNOSTIC_TAIL_LINES


class _LineStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(line.encode("utf-8") for line in lines)

    async def readline(self) -> bytes:
        return next(self._lines, b"")


def _manager(tmp_path) -> ModelManager:
    model_dir = tmp_path / "models" / "model"
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                "model": {
                    "name": "Model",
                    "model_path": str(model_dir),
                    "source_model": "org/model",
                    "backend": "openvino-genai",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                    "max_context_len": 2048,
                    "max_output_tokens": 512,
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
    )
    return ModelManager(settings)


def test_converter_diagnostic_lines_are_bounded(tmp_path):
    manager = _manager(tmp_path)
    total_lines = _CONVERTER_DIAGNOSTIC_TAIL_LINES + 25
    stream = _LineStream([f"Downloading shard {index}\n" for index in range(total_lines)])

    retained = asyncio.run(
        manager._read_conversion_stream("model", manager.catalog["model"], stream)
    )

    assert len(retained) == _CONVERTER_DIAGNOSTIC_TAIL_LINES
    assert retained[0] == "Downloading shard 25"
    assert retained[-1] == f"Downloading shard {total_lines - 1}"
    assert manager.progress["model"]["log_tail"] == [
        f"Downloading shard {index}" for index in range(total_lines - 10, total_lines)
    ]
