"""Contain malformed non-secret Hugging Face access metadata."""

from __future__ import annotations

import functools
from typing import Any

_INSTALL_FLAG = "_INFERBRIDGE_HF_METADATA_SAFETY_INSTALLED"
_MAX_TIMESTAMP = 2**63 - 1


def _clean_text(value: Any, *, limit: int, fallback: str | None = None) -> str | None:
    if not isinstance(value, str):
        return fallback
    text = value.replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)[:limit]
    return text or fallback


def _normalize_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    checked = payload.get("last_checked")
    if (
        isinstance(checked, bool)
        or not isinstance(checked, int)
        or checked <= 0
        or checked > _MAX_TIMESTAMP
    ):
        checked = None
    return {
        "state": _clean_text(payload.get("state"), limit=40, fallback="unverified"),
        "username": _clean_text(payload.get("username"), limit=200),
        "last_checked": checked,
    }


def install_huggingface_metadata_safety() -> None:
    from app.huggingface_access import HuggingFaceCredentialStore

    cls = HuggingFaceCredentialStore
    if getattr(cls, _INSTALL_FLAG, False):
        return
    original_read = cls.read_metadata
    original_write = cls.write_metadata

    @functools.wraps(original_read)
    def read_metadata_safely(self) -> dict[str, Any]:
        return _normalize_metadata(original_read(self))

    @functools.wraps(original_write)
    def write_metadata_safely(self, payload: dict[str, Any]) -> None:
        original_write(self, _normalize_metadata(payload))

    cls.read_metadata = read_metadata_safely
    cls.write_metadata = write_metadata_safely
    setattr(cls, _INSTALL_FLAG, True)


__all__ = ["install_huggingface_metadata_safety"]
