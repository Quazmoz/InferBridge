"""Canonical public support destinations for InferBridge."""

from __future__ import annotations

from urllib.parse import urlparse

SUPPORT_URL = "https://consultant.quinnfavo.com/apps/inferbridge#feedback"
GITHUB_ISSUES_URL = "https://github.com/Quazmoz/InferBridge/issues"


def validate_support_url(url: str) -> str:
    """Return a known-safe HTTPS support URL or raise for an invalid destination."""

    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("InferBridge support links must use HTTPS.")
    return value


__all__ = ["GITHUB_ISSUES_URL", "SUPPORT_URL", "validate_support_url"]
