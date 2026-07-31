"""User-facing error formatting.

The legacy IPEX project surfaced cryptic stack traces for common, recoverable
problems (enterprise TLS, missing runtime, bad device). These helpers turn those
into short, actionable messages for the UI and logs.
"""

from __future__ import annotations

import os
import re
import ssl

from runtime import device_check

_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)


def _redact_secrets(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", str(text or ""))


def is_tls_certificate_error(exc: BaseException) -> bool:
    """True if anywhere in the exception chain is a TLS cert-verification failure."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__}: {current}"
        if (
            isinstance(current, ssl.SSLCertVerificationError)
            or "SSLCertVerificationError" in text
            or "CERTIFICATE_VERIFY_FAILED" in text
            or "unable to get local issuer certificate" in text
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def format_model_load_error(exc: BaseException) -> str:
    """Return a concise, actionable message for a failed model load."""
    if is_tls_certificate_error(exc):
        bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        hint = f" Active CA bundle: {bundle}." if bundle else ""
        return (
            "HTTPS download failed while contacting huggingface.co — Python could not verify the "
            "TLS certificate. On Windows, install python-certifi-win32, or set REQUESTS_CA_BUNDLE / "
            "SSL_CERT_FILE to your organization's CA bundle, then retry." + hint
        )
    return _redact_secrets(str(exc))


def format_model_convert_error(exc: BaseException) -> str:
    """Return a concise, actionable message for a failed model conversion."""
    text = _redact_secrets(str(exc))
    lowered = text.lower()

    if "not in the authorized list" in lowered or "403 client error" in lowered:
        model_url = None
        for word in text.split():
            clean_word = word.strip("()[].,'\"")
            if "huggingface.co/" in clean_word:
                model_url = clean_word
                break
        visit_hint = (
            f" Open {model_url} and complete the publisher approval step."
            if model_url
            else " Open the model page on huggingface.co and complete the publisher approval step."
        )
        return (
            "Your Hugging Face token is valid, but this account is not approved for the model."
            f"{visit_hint} Then use Settings > Hugging Face access to check access again."
        )

    if any(
        keyword in lowered
        for keyword in (
            "gatedrepoerror",
            "gated",
            "restricted",
            "unauthorized",
            "401 client error",
            "repositorynotfounderror",
        )
    ):
        return (
            "Hugging Face access is required for this model. Open Settings > Hugging Face access "
            "to add or replace a token, complete any publisher approval on the model page, and "
            "check access again. HF_TOKEN remains available only as an advanced environment fallback."
        )

    if is_tls_certificate_error(exc):
        bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        hint = f" Active CA bundle: {bundle}." if bundle else ""
        return (
            "HTTPS download failed while contacting huggingface.co — Python could not verify the "
            "TLS certificate. On Windows, install python-certifi-win32, or set REQUESTS_CA_BUNDLE / "
            "SSL_CERT_FILE to your organization's CA bundle, then retry." + hint
        )

    # Clean up long Optimum/subprocess tracebacks to focus on the root error.
    if "Traceback (most recent call last):" in text or "optimum-cli" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            for line in reversed(lines):
                if (
                    any(err in line.lower() for err in ("error", "exception", "failed", "oserror"))
                    and ":" in line
                ):
                    return f"Conversion failed: {line}"
            return f"Conversion failed: {lines[-1]}"

    return text


def format_missing_openvino() -> str:
    """Message shown when an OpenVINO-backed action is attempted without OpenVINO."""
    return (
        "OpenVINO GenAI is not installed in this environment, so models cannot be loaded for real "
        "inference. Install it with `pip install -r requirements.txt` on Windows, or set OV_LLM_MOCK=1 "
        "to run the built-in mock engine for UI/API testing."
    )


def format_model_not_converted(
    model_name: str,
    model_dir: str,
    source_model: str,
    weight_format: str = "fp16",
) -> str:
    """Message shown when a model's OpenVINO IR directory is missing."""
    resolved_weight_format = (weight_format or "fp16").strip() or "fp16"
    convert_hint = (
        f"optimum-cli export openvino --model {source_model} --weight-format {resolved_weight_format} "
        f'"{model_dir}"'
        if source_model
        else f'place a converted OpenVINO IR model in "{model_dir}"'
    )
    return (
        f"No converted OpenVINO model found for '{model_name}' at {model_dir}. "
        f"Convert it first:\n  {convert_hint}"
    )


def format_device_error(device: str, available: list[str]) -> str:
    """Message shown when the requested device is unavailable to OpenVINO."""
    avail = ", ".join(available) if available else "none detected"
    examples = ", ".join(device_check.supported_device_examples())
    extra = ""
    if device == "NPU":
        extra = (
            " Confirm the Intel NPU driver is installed and current, then retry. "
            "If the NPU still fails, fall back to --device CPU."
        )
    elif device == "GPU":
        extra = (
            " Confirm the Intel GPU driver is installed, then retry, or fall back to --device CPU."
        )
    return (
        f"OpenVINO device '{device}' is not available. Detected devices: {avail}. "
        f"Supported examples: {examples}.{extra}"
    )
