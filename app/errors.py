"""User-facing error formatting.

The legacy IPEX project surfaced cryptic stack traces for common, recoverable
problems (enterprise TLS, missing runtime, bad device). These helpers turn those
into short, actionable messages for the UI and logs.
"""

from __future__ import annotations

import errno
import os
import re
import ssl
import sys

from runtime import device_check

_SECRET_RE = re.compile(
    r"(hf_[A-Za-z0-9_=-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]+|token\s*[:=]\s*[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_CONVERSION_FAILURE_WRAPPER_RE = re.compile(
    r"^(?:(?:RuntimeError|Exception):\s*)?(?:Conversion failed:\s*)+",
    re.IGNORECASE,
)


def _redact_secrets(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", str(text or ""))


def _format_conversion_failure_detail(detail: str) -> str:
    """Return one conversion prefix around the underlying actionable detail."""

    clean = str(detail or "").strip()
    while clean:
        unwrapped = _CONVERSION_FAILURE_WRAPPER_RE.sub("", clean, count=1).strip()
        if unwrapped == clean:
            break
        clean = unwrapped
    return f"Conversion failed: {clean}" if clean else "Conversion failed."


def _exception_chain_text(exc: BaseException) -> str:
    """Return bounded text from an exception and its cause/context chain."""

    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return "\n".join(parts)[:4000]


def _exception_chain_has_errno(exc: BaseException, values: set[int]) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "errno", None) in values:
            return True
        current = current.__cause__ or current.__context__
    return False


def is_openvino_tokenizer_runtime_error(exc: BaseException) -> bool:
    """Detect package-level OpenVINO tokenizer native-library failures.

    These failures are independent of CPU/GPU/NPU selection. Retrying on another
    device cannot repair a missing, incompatible, or unresolved tokenizer DLL.
    """

    text = _exception_chain_text(exc).lower()
    tokenizer_marker = (
        "openvino_tokenizers" in text
        or "openvino tokenizer runtime" in text
        or "openvino tokenizer extension" in text
    )
    if not tokenizer_marker:
        return False
    return any(
        marker in text
        for marker in (
            "cannot load library",
            "cannot add extension",
            "entry point to the extension library",
            "winerror 126",
            "error 126",
            ": 126",
            "native runtime",
            "package-level error",
        )
    )


def format_openvino_tokenizer_runtime_error() -> str:
    """Return an actionable message for a broken tokenizer native runtime."""

    if getattr(sys, "frozen", False):
        return (
            "InferBridge could not load the bundled OpenVINO tokenizer runtime. Reinstall the "
            "latest InferBridge build over the current installation; downloaded models, settings, "
            "and logs are preserved. Changing devices or falling back to CPU will not fix this "
            "package-level error."
        )
    return (
        "OpenVINO could not load the tokenizer native runtime. Reinstall matching versions of "
        "openvino, openvino-genai, and openvino-tokenizers, then retry. Changing devices will not "
        "fix this dependency-level error."
    )


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
    if is_openvino_tokenizer_runtime_error(exc):
        return format_openvino_tokenizer_runtime_error()
    if is_tls_certificate_error(exc):
        bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        hint = f" Active CA bundle: {bundle}." if bundle else ""
        return (
            "HTTPS download failed while contacting huggingface.co. Python could not verify the "
            "TLS certificate. On Windows, install python-certifi-win32, or set REQUESTS_CA_BUNDLE / "
            "SSL_CERT_FILE to your organization's CA bundle, then retry." + hint
        )
    return _redact_secrets(str(exc))


def format_model_convert_error(exc: BaseException) -> str:
    """Return a concise, actionable message for a failed model conversion."""
    if is_openvino_tokenizer_runtime_error(exc):
        return format_openvino_tokenizer_runtime_error()

    text = _redact_secrets(str(exc))
    chain_text = _redact_secrets(_exception_chain_text(exc))
    lowered = text.lower()
    lowered_chain = chain_text.lower()

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
            "HTTPS download failed while contacting huggingface.co. Python could not verify the "
            "TLS certificate. On Windows, install python-certifi-win32, or set REQUESTS_CA_BUNDLE / "
            "SSL_CERT_FILE to your organization's CA bundle, then retry." + hint
        )

    if _exception_chain_has_errno(exc, {errno.ENOSPC}) or any(
        marker in lowered_chain
        for marker in (
            "no space left on device",
            "disk full",
            "not enough space",
            "winerror 112",
        )
    ):
        return (
            "There is not enough free disk space to finish downloading and converting this model. "
            "Free space on the drive containing InferBridge model data, then retry. Cached download "
            "files and any previously working model are preserved when possible."
        )

    if "already preparing this model" in lowered_chain:
        return (
            "Another InferBridge process is already preparing this model. Wait for it to finish, "
            "or close the other InferBridge instance before retrying."
        )

    if (
        isinstance(exc, PermissionError)
        or _exception_chain_has_errno(
            exc,
            {errno.EACCES, errno.EBUSY, errno.EPERM},
        )
        or any(
            marker in lowered_chain
            for marker in (
                "access is denied",
                "permission denied",
                "sharing violation",
                "being used by another process",
                "winerror 5",
                "winerror 32",
                "winerror 33",
            )
        )
    ):
        return (
            "InferBridge could not update the model files because Windows is using or blocking them. "
            "Close other InferBridge instances and File Explorer windows on the model folder, allow "
            "any antivirus scan to finish, then retry. Any previously working model is preserved."
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
                    return _format_conversion_failure_detail(line)
            return _format_conversion_failure_detail(lines[-1])

    if _CONVERSION_FAILURE_WRAPPER_RE.match(text):
        return _format_conversion_failure_detail(text)
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
