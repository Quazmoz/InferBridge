"""Validate converted OpenVINO model directories before lifecycle use.

A partially written export can contain an IR XML file before its weights or model
configuration are durable. Treating that directory as downloaded causes retries to
skip conversion and sends an incomplete artifact into the native OpenVINO loader.
This module provides one dependency-light readiness check shared by catalog, recovery,
and load paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_IR_MARKERS = ("openvino_model.xml", "openvino_language_model.xml")
_CONFIG_FILENAME = "config.json"
_XML_PROBE_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ModelArtifactValidation:
    """Result of inspecting one converted model directory."""

    ready: bool
    reason: str
    ir_xml: Path | None = None
    ir_bin: Path | None = None


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _valid_ir_xml(path: Path) -> bool:
    """Check the bounded beginning and end of an OpenVINO IR XML document."""

    try:
        size = path.stat().st_size
        if not path.is_file() or size <= 0:
            return False
        with path.open("rb") as stream:
            head = stream.read(_XML_PROBE_BYTES)
            stream.seek(max(0, size - _XML_PROBE_BYTES))
            tail = stream.read(_XML_PROBE_BYTES)
    except OSError:
        return False

    # OpenVINO IR files use a ``net`` root. Checking both boundaries catches the
    # common interrupted-write case without parsing a potentially very large graph
    # on every model-status request.
    return b"<net" in head and b"</net>" in tail


def _valid_config(path: Path) -> bool:
    if not _nonempty_file(path):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


def validate_openvino_model_dir(model_dir: str | Path) -> ModelArtifactValidation:
    """Return whether *model_dir* is complete enough for OpenVINO GenAI to load.

    A ready directory must contain a structurally complete primary IR XML file, its
    non-empty sibling BIN weights file, and a valid JSON object in ``config.json``.
    These are stable requirements across the text, embedding, and vision model layouts
    used by Optimum Intel, while avoiding assumptions about optional tokenizer files.
    """

    directory = Path(model_dir)
    if not directory.is_dir():
        return ModelArtifactValidation(False, "model directory does not exist")

    failures: list[str] = []
    found_marker = False
    for marker in _IR_MARKERS:
        ir_xml = directory / marker
        if not ir_xml.exists():
            continue
        found_marker = True
        ir_bin = ir_xml.with_suffix(".bin")

        if not _valid_ir_xml(ir_xml):
            failures.append(f"{marker} is empty, unreadable, or truncated")
            continue
        if not _nonempty_file(ir_bin):
            failures.append(f"{ir_bin.name} is missing or empty")
            continue

        config = directory / _CONFIG_FILENAME
        if not _valid_config(config):
            failures.append(f"{_CONFIG_FILENAME} is missing, empty, or invalid")
            continue

        return ModelArtifactValidation(
            True,
            "OpenVINO IR, weights, and configuration are present",
            ir_xml=ir_xml,
            ir_bin=ir_bin,
        )

    if not found_marker:
        return ModelArtifactValidation(False, "primary OpenVINO IR XML file is missing")
    return ModelArtifactValidation(False, "; ".join(failures) or "model output is incomplete")


def is_openvino_model_dir(model_dir: str | Path) -> bool:
    """Return whether *model_dir* contains a loadable converted model artifact."""

    return validate_openvino_model_dir(model_dir).ready


__all__ = [
    "ModelArtifactValidation",
    "is_openvino_model_dir",
    "validate_openvino_model_dir",
]
