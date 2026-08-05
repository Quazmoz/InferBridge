"""Export Hugging Face models to OpenVINO IR via Optimum Intel.

Conversion is a separate, heavier step than serving and requires the extra
``requirements-convert.txt`` dependencies. Catalog backends select the matching
Optimum task for text generation, embeddings, or vision-language models.

Stdout is a versioned JSON Lines progress channel. Human-readable Optimum output stays
on stderr so callers can consume reliable machine state without losing diagnostics.
"""

from __future__ import annotations

import argparse
import codecs
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO, TextIO

from runtime.model_output_transaction import staged_model_output
from runtime.progress_protocol import ProgressEventEmitter

_venv_bin = str(Path(sys.executable).parent)
if _venv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _venv_bin + os.pathsep + os.environ.get("PATH", "")

logger = logging.getLogger("ov-llm.convert")

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PERCENT_RE = re.compile(r"(?<!\d)(100(?:\.0+)?|[1-9]?\d(?:\.\d+)?)\s*%")
_COUNT_RE = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)")
_DOWNLOAD_PROGRESS_RE = re.compile(
    r"(?:fetching\s+\d+\s+files?|download|snapshot|cache|\.safetensors\b|\.bin\b|\.model\b|\.json\b)",
    re.IGNORECASE,
)
_CONVERT_PROGRESS_RE = re.compile(
    r"(?:quant|compress|weight|export|openvino|convert|compile|transform)", re.IGNORECASE
)
_FINALIZE_PROGRESS_RE = re.compile(r"(?:save|write|serializ|finaliz)", re.IGNORECASE)
_PHASE_RANK = {"resolving": 0, "downloading": 1, "converting": 2, "finalizing": 3}


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 stdio so forwarded progress glyphs never abort a conversion.

    ``optimum-cli``/``tqdm`` and the Transformers weight loader emit block-drawing
    characters such as U+258F (``▏``). When this process's stdout falls back to a
    legacy Windows code page, printing those glyphs can raise ``UnicodeEncodeError``.
    JSON protocol records are ASCII-escaped, while human stderr remains UTF-8.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream without utf-8 support
            pass


def build_export_command(
    source_model: str,
    output_dir: str | Path,
    weight_format: str = "int4",
    *,
    trust_remote_code: bool = False,
    task: str | None = None,
    group_size: int | None = None,
    ratio: float | None = None,
    sym: bool | None = None,
) -> list[str]:
    """Construct the ``optimum-cli export openvino`` command."""

    command = [
        "optimum-cli",
        "export",
        "openvino",
        "--model",
        source_model,
        "--weight-format",
        weight_format,
    ]
    if task:
        command += ["--task", task]
    if trust_remote_code:
        command.append("--trust-remote-code")
    if weight_format == "int4":
        if group_size is not None:
            command += ["--group-size", str(group_size)]
        if ratio is not None:
            command += ["--ratio", str(ratio)]
        if sym:
            command.append("--sym")
    command.append(str(output_dir))
    return command


def _clean_console_line(text: str) -> str:
    """Remove terminal formatting while preserving useful progress information."""

    return _ANSI_ESCAPE_RE.sub("", str(text or "")).replace("\x00", "").strip()


def _iter_console_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Yield terminal updates split on either newlines or carriage returns."""

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    for chunk in chunks:
        if not chunk:
            continue
        pending += decoder.decode(chunk)
        while True:
            match = re.search(r"[\r\n]", pending)
            if match is None:
                break
            line = _clean_console_line(pending[: match.start()])
            pending = pending[match.end() :]
            while pending.startswith(("\r", "\n")):
                pending = pending[1:]
            if line:
                yield line

    pending += decoder.decode(b"", final=True)
    line = _clean_console_line(pending)
    if line:
        yield line


def _read_process_chunks(stream: BinaryIO, chunk_size: int = 4096) -> Iterator[bytes]:
    """Read available child-process output in chunks on Windows and POSIX."""

    reader = getattr(stream, "read1", stream.read)
    while True:
        chunk = reader(chunk_size)
        if not chunk:
            break
        yield chunk


def _progress_key(line: str, match: re.Match[str]) -> str:
    prefix = line[: match.start()].strip(" :|")
    if prefix:
        return prefix[-120:]
    return line[:120]


def _structured_progress_from_line(
    line: str,
) -> tuple[str, str, float | None, int | None, int | None] | None:
    """Map unstable third-party console text into the stable converter protocol.

    This adapter is the only place that knows about Optimum/tqdm wording. The parent
    server never interprets those strings and only accepts validated protocol records.
    """

    percent_match = _PERCENT_RE.search(line)
    percent = float(percent_match.group(1)) if percent_match else None
    count_match = _COUNT_RE.search(line)
    completed = int(count_match.group(1)) if count_match else None
    total = int(count_match.group(2)) if count_match else None

    if _FINALIZE_PROGRESS_RE.search(line):
        return "finalizing", "Finalizing OpenVINO model files…", percent, completed, total
    if _DOWNLOAD_PROGRESS_RE.search(line):
        return "downloading", "Downloading model files…", percent, completed, total
    if _CONVERT_PROGRESS_RE.search(line) or percent is not None:
        return "converting", "Converting model to OpenVINO IR…", percent, completed, total
    return None


class _ProgressLineEmitter:
    """Throttle terminal redraw noise and optionally emit structured progress."""

    def __init__(
        self,
        protocol_emitter: ProgressEventEmitter | None = None,
        *,
        human_stream: TextIO | None = None,
    ) -> None:
        self._protocol_emitter = protocol_emitter
        self._human_stream = human_stream or sys.stdout
        self._last_line = ""
        self._last_line_at = 0.0
        self._percent_by_key: dict[str, int] = {}
        self._percent_at: dict[str, float] = {}
        self._phase = "resolving"
        self._last_protocol_state: tuple[object, ...] | None = None

    def emit(self, raw_line: str) -> None:
        import time

        line = _clean_console_line(raw_line)
        if not line:
            return

        percent_match = _PERCENT_RE.search(line)
        now = time.monotonic()
        if percent_match:
            percent = int(float(percent_match.group(1)))
            key = _progress_key(line, percent_match)
            previous = self._percent_by_key.get(key)
            last_at = self._percent_at.get(key, 0.0)
            if previous == percent and percent not in {0, 100} and now - last_at < 0.75:
                return
            self._percent_by_key[key] = percent
            self._percent_at[key] = now
            if _DOWNLOAD_PROGRESS_RE.search(line) and "download" not in line.lower():
                line = f"Downloading {line}"
        elif line == self._last_line and now - self._last_line_at < 1.0:
            return

        self._last_line = line
        self._last_line_at = now
        print(line, file=self._human_stream, flush=True)

        if self._protocol_emitter is None:
            return
        structured = _structured_progress_from_line(line)
        if structured is None:
            return
        phase, message, percent_value, completed, total = structured
        if _PHASE_RANK[phase] < _PHASE_RANK[self._phase]:
            phase = self._phase
            message = (
                "Finalizing OpenVINO model files…"
                if phase == "finalizing"
                else "Converting model to OpenVINO IR…"
            )
        else:
            self._phase = phase
        state = (phase, message, percent_value, completed, total)
        if state == self._last_protocol_state:
            return
        self._last_protocol_state = state
        self._protocol_emitter.emit(
            phase,
            message,
            percent=percent_value,
            completed=completed,
            total=total,
        )


def _run_streaming_command(
    command: list[str],
    *,
    progress_emitter: ProgressEventEmitter | None = None,
) -> None:
    """Run *command* into validated staging, then publish the model atomically."""

    if not command:
        raise ValueError("Conversion command cannot be empty.")

    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("COLUMNS", "120")

    final_output = Path(command[-1])
    with staged_model_output(final_output) as staging_output:
        staged_command = [*command[:-1], str(staging_output)]
        process = subprocess.Popen(
            staged_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            bufsize=-1,
        )
        if process.stdout is None:  # pragma: no cover - defensive subprocess contract
            process.kill()
            process.wait()
            raise RuntimeError("Could not capture optimum-cli output.")

        emitter = _ProgressLineEmitter(progress_emitter, human_stream=sys.stderr)
        try:
            for line in _iter_console_lines(_read_process_chunks(process.stdout)):
                emitter.emit(line)
            return_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()

        if return_code != 0:
            # Report the user-facing final output path rather than the private staging
            # path, while the transaction removes incomplete files automatically.
            raise subprocess.CalledProcessError(return_code, command)


def export_model(
    source_model: str,
    output_dir: str | Path,
    weight_format: str = "int4",
    *,
    trust_remote_code: bool = False,
    task: str | None = None,
    group_size: int | None = None,
    ratio: float | None = None,
    sym: bool | None = None,
    operation_id: str | None = None,
    model_id: str | None = None,
) -> Path:
    """Run an export and return its output directory."""

    _ensure_utf8_stdio()
    progress = ProgressEventEmitter(
        operation_id=operation_id,
        model_id=model_id or source_model,
    )
    progress.emit("resolving", "Resolving model metadata and conversion settings…", percent=0)

    if shutil.which("optimum-cli") is None:
        message = (
            "optimum-cli not found. Install conversion deps: "
            "pip install -r requirements-convert.txt"
        )
        progress.emit("error", message)
        raise RuntimeError(message)

    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if trust_remote_code:
        logger.warning(
            "Running with --trust-remote-code: model '%s' may execute arbitrary code from "
            "the Hugging Face repo during conversion. Only use this with models you trust.",
            source_model,
        )
    command = build_export_command(
        source_model,
        output_dir,
        weight_format,
        trust_remote_code=trust_remote_code,
        task=task,
        group_size=group_size,
        ratio=ratio,
        sym=sym,
    )
    logger.info("Running: %s", " ".join(command))
    print(
        f"Downloading model metadata and weights for {source_model}",
        file=sys.stderr,
        flush=True,
    )
    progress.emit("downloading", "Downloading model metadata and weights…", percent=0)
    try:
        _run_streaming_command(command, progress_emitter=progress)
    except BaseException as exc:
        progress.emit("error", f"Conversion failed: {exc}")
        raise

    print(f"Saving OpenVINO IR for {source_model}", file=sys.stderr, flush=True)
    progress.emit("finalizing", "Saving OpenVINO IR files…", percent=100)
    progress.emit("ready", f"Done. Model available at: {output_dir}", percent=100)
    logger.info("Exported %s -> %s", source_model, output_dir)
    return output_dir


def _resolve_from_catalog(
    model_id: str, *, include_task: bool = False
) -> tuple[str, Path, str] | tuple[str, Path, str, str | None, bool]:
    """Look up catalog conversion settings, optionally including the Optimum task."""

    from app.config import Settings
    from app.model_registry import load_catalog

    settings = Settings.from_env()
    catalog = load_catalog(settings.models_file)
    cfg = catalog.get(model_id)
    if cfg is None:
        raise SystemExit(
            f"Unknown model id '{model_id}'. Known ids: {', '.join(catalog) or '(none)'}"
        )
    if not cfg.source_model:
        raise SystemExit(f"Model '{model_id}' has no 'source_model' in models.json")

    from app.config import BASE_DIR

    result = (cfg.source_model, cfg.abs_path(BASE_DIR), cfg.weight_format)
    if not include_task:
        return result

    backend = cfg.backend.lower()
    if "embedding" in backend:
        task = "feature-extraction"
    elif "vlm" in backend or "vision" in backend:
        task = "image-text-to-text"
    else:
        task = None
    return (*result, task, cfg.trust_remote_code)


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Export a model to OpenVINO IR.")
    parser.add_argument("--id", help="Model id from models.json (resolves source/output/weights)")
    parser.add_argument("--model", help="Hugging Face source model id")
    parser.add_argument("--output", help="Output directory for the OpenVINO IR model")
    parser.add_argument(
        "--operation-id",
        default=None,
        help="Optional producer operation id for the JSON Lines progress stream.",
    )
    parser.add_argument(
        "--weight-format",
        choices=("int4", "int8", "fp16"),
        default=None,
        help="Override output weights. With --id, defaults to the catalog value; otherwise int4.",
    )
    parser.add_argument("--task", default=None, help="Optional optimum task override")
    parser.add_argument(
        "--trust-remote-code",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Allow Hugging Face repository code to execute during export. Disabled by "
            "default; with --id, the catalog setting is used when this flag is omitted."
        ),
    )
    parser.add_argument(
        "--group-size", type=int, default=None, help="Quantization group size for INT4"
    )
    parser.add_argument(
        "--ratio", type=float, default=None, help="Quantization ratio for INT4 (0.0 to 1.0)"
    )
    parser.add_argument("--sym", action="store_true", help="Enable symmetric quantization for INT4")
    args = parser.parse_args(argv)

    if args.ratio is not None and not 0.0 <= args.ratio <= 1.0:
        parser.error("--ratio must be between 0.0 and 1.0")
    if args.group_size is not None and args.group_size != -1 and args.group_size <= 0:
        parser.error("--group-size must be -1 or a positive integer")

    task = args.task
    catalog_trust_remote_code = False
    resolved_model_id = args.id
    if args.id:
        source_model, output_dir, weight_format, catalog_task, catalog_trust_remote_code = (
            _resolve_from_catalog(args.id, include_task=True)
        )
        weight_format = args.weight_format or weight_format
        task = task or catalog_task
    else:
        if not args.model or not args.output:
            parser.error("Provide either --id, or both --model and --output")
        source_model = args.model
        output_dir = Path(args.output)
        weight_format = args.weight_format or "int4"
        resolved_model_id = args.model

    try:
        export_model(
            source_model,
            output_dir,
            weight_format,
            trust_remote_code=(
                args.trust_remote_code
                if args.trust_remote_code is not None
                else catalog_trust_remote_code
            ),
            task=task,
            group_size=args.group_size,
            ratio=args.ratio,
            sym=args.sym,
            operation_id=args.operation_id,
            model_id=resolved_model_id,
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
