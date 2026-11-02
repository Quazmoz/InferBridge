"""Validated, request-local image transport for vision-language models.

OpenAI-compatible APIs represent image input as content-part dictionaries.  The
server validates and decodes each image exactly once, then carries immutable
:class:`ImagePayload` objects through prompt construction.  Encoded image bytes
are never embedded in tokenizer text, logs, or browser conversation history.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import threading
import time
import uuid
import warnings
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
SUPPORTED_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
SUPPORTED_IMAGE_PART_TYPES = frozenset({"image_url", "input_image"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGES_PER_REQUEST = 4
MAX_REQUEST_IMAGE_BYTES = 24 * 1024 * 1024
MAX_REQUEST_IMAGE_PIXELS = 40_000_000
MAX_REQUEST_TEXT_CHARS = 2_000_000
MAX_CONTENT_PARTS_PER_REQUEST = 1_024

# Contexts bridge the existing prompt-string engine interface to VLMPipeline's
# separate ``images`` argument.  They are bounded rather than evicting active
# requests, because evicting a queued generation would make it fail nondeterministically.
_CONTEXT_NAMESPACE = uuid.uuid4().hex
_CONTEXT_MARKER_RE = re.compile(
    rf"\s*<ovllm-image-context:{_CONTEXT_NAMESPACE}:([A-Fa-f0-9]{{32}})>\s*$"
)
_CONTEXT_TTL_SECONDS = 30 * 60
_CONTEXT_LIMIT = 64
_CONTEXT_BYTES_LIMIT = 512 * 1024 * 1024
_IMAGE_TOKEN_RESERVE = 512


class VisionCapacityError(RuntimeError):
    """Raised when bounded request-local vision storage is temporarily exhausted."""


@dataclass(frozen=True, slots=True)
class ImagePayload:
    """One fully validated encoded image."""

    mime_type: str
    data: bytes = field(repr=False)
    width: int
    height: int

    @property
    def byte_size(self) -> int:
        return len(self.data)

    @property
    def pixel_count(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class MultimodalContent:
    """Normalized message content containing text fragments and validated images."""

    parts: tuple[str | ImagePayload, ...]


_context_lock = threading.Lock()
_contexts: OrderedDict[str, tuple[float, tuple[ImagePayload, ...]]] = OrderedDict()
_context_bytes = 0


def backend_supports_vision(backend: str | None) -> bool:
    value = str(backend or "").lower()
    return "vlm" in value or "vision" in value


def capabilities_for_backend(backend: str | None) -> tuple[str, ...]:
    value = str(backend or "").lower()
    if "rerank" in value:
        return ("rerank",)
    if "embedding" in value:
        return ("embeddings",)
    if backend_supports_vision(value):
        return ("chat", "vision")
    return ("chat",)


def _part_type(part: dict[str, Any]) -> str:
    return str(part.get("type") or "").strip().lower()


def _image_url_from_part(part: dict[str, Any]) -> str:
    part_type = _part_type(part)
    candidate: Any = part.get("image_url")
    if isinstance(candidate, dict):
        candidate = candidate.get("url")
    if not candidate and part_type == "input_image":
        candidate = part.get("file_data") or part.get("data")
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"Content part '{part_type}' must include an image data URL.")
    return candidate


def _text_from_part(part: dict[str, Any]) -> str:
    for key in ("text", "content", "value"):
        value = part.get(key)
        if isinstance(value, str):
            return value
    raise ValueError(f"Content part '{_part_type(part) or 'text'}' must include text.")


def _preflight_data_url(url: str) -> tuple[int, str, int]:
    """Validate data-URL metadata without copying the base64 payload."""

    if not isinstance(url, str) or url[:5].lower() != "data:":
        raise ValueError(
            "Image inputs must use a base64 data URL; remote image URLs are not fetched."
        )
    comma_index = url.find(",", 5)
    if comma_index < 0:
        raise ValueError("Image data URL is malformed.")
    if comma_index > 256:
        raise ValueError("Image data URL metadata is too long.")
    header = url[5:comma_index]
    pieces = [item.strip() for item in header.lower().split(";") if item.strip()]
    mime_type = pieces[0] if pieces else ""
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_MIME_TYPES))
        raise ValueError(f"Unsupported image type '{mime_type or 'unknown'}'. Use {supported}.")
    if "base64" not in pieces[1:]:
        raise ValueError("Image data URLs must be base64 encoded.")
    encoded_length = len(url) - comma_index - 1
    estimated_size = (encoded_length * 3) // 4
    if estimated_size > MAX_IMAGE_BYTES + 3:
        raise ValueError(f"Each image must be {MAX_IMAGE_BYTES // (1024 * 1024)} MiB or smaller.")
    return estimated_size, mime_type, comma_index


def preflight_request_contents(
    contents: Iterable[Any],
    *,
    roles: Iterable[str] | None = None,
) -> None:
    """Reject obviously invalid or oversized requests before image decoding."""

    content_list = list(contents)
    role_list = list(roles) if roles is not None else ["user"] * len(content_list)
    if len(role_list) != len(content_list):
        raise ValueError("Internal role/content validation mismatch.")

    image_count = 0
    estimated_bytes = 0
    decoded_pixels = 0
    text_chars = 0
    content_parts = 0

    def account_text(text: str) -> None:
        nonlocal text_chars
        text_chars += len(text)
        if text_chars > MAX_REQUEST_TEXT_CHARS:
            raise ValueError(
                f"Request text may contain at most {MAX_REQUEST_TEXT_CHARS:,} characters."
            )

    def account_part() -> None:
        nonlocal content_parts
        content_parts += 1
        if content_parts > MAX_CONTENT_PARTS_PER_REQUEST:
            raise ValueError(
                f"A request may contain at most {MAX_CONTENT_PARTS_PER_REQUEST:,} content parts."
            )

    for role, content in zip(role_list, content_list, strict=True):
        if content is None:
            continue
        if isinstance(content, str):
            account_text(content)
            continue
        if isinstance(content, MultimodalContent):
            for part in content.parts:
                account_part()
                if isinstance(part, str):
                    account_text(part)
            images = list(iter_image_payloads(content))
            if images and str(role).lower() != "user":
                raise ValueError("Image content is only supported in user messages.")
            image_count += len(images)
            estimated_bytes += sum(image.byte_size for image in images)
            decoded_pixels += sum(image.pixel_count for image in images)
            continue
        if not isinstance(content, list):
            raise ValueError("Message content must be a string, a list of content parts, or null.")

        for part in content:
            account_part()
            if isinstance(part, str):
                account_text(part)
                continue
            if not isinstance(part, dict):
                raise ValueError("Each message content part must be a string or object.")
            part_type = _part_type(part)
            if part_type in SUPPORTED_IMAGE_PART_TYPES:
                if str(role).lower() != "user":
                    raise ValueError("Image content is only supported in user messages.")
                size, _, _ = _preflight_data_url(_image_url_from_part(part))
                image_count += 1
                estimated_bytes += size
                if image_count > MAX_IMAGES_PER_REQUEST:
                    raise ValueError(
                        f"A request may contain at most {MAX_IMAGES_PER_REQUEST} images."
                    )
                if estimated_bytes > MAX_REQUEST_IMAGE_BYTES + 3 * image_count:
                    raise ValueError(
                        f"Combined image data must be {MAX_REQUEST_IMAGE_BYTES // (1024 * 1024)} MiB or smaller."
                    )
            elif part_type in SUPPORTED_TEXT_PART_TYPES or not part_type:
                account_text(_text_from_part(part))
            else:
                raise ValueError(f"Unsupported message content part type '{part_type}'.")

    if image_count > MAX_IMAGES_PER_REQUEST:
        raise ValueError(f"A request may contain at most {MAX_IMAGES_PER_REQUEST} images.")
    if estimated_bytes > MAX_REQUEST_IMAGE_BYTES:
        raise ValueError(
            f"Combined image data must be {MAX_REQUEST_IMAGE_BYTES // (1024 * 1024)} MiB or smaller."
        )
    if decoded_pixels > MAX_REQUEST_IMAGE_PIXELS:
        raise ValueError(
            f"Combined image dimensions exceed the {MAX_REQUEST_IMAGE_PIXELS:,}-pixel request limit."
        )


def decode_data_url(url: str) -> ImagePayload:
    """Decode and verify one local JPEG, PNG, or WebP data URL.

    Remote URLs are deliberately rejected so the local API cannot become an
    SSRF-capable fetch proxy.  Animated images are rejected because VLM pipelines
    accept a single image tensor and silently choosing a frame is ambiguous.
    """

    _, mime_type, comma_index = _preflight_data_url(url)
    encoded = url[comma_index + 1 :]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Image data URL contains invalid base64 data.") from exc
    if not data:
        raise ValueError("Image input is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Each image must be {MAX_IMAGE_BYTES // (1024 * 1024)} MiB or smaller.")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise RuntimeError("Pillow is required for image validation.") from exc

    format_to_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                actual_mime = format_to_mime.get(str(image.format or "").upper())
                if getattr(image, "is_animated", False) or int(getattr(image, "n_frames", 1)) > 1:
                    raise ValueError("Animated images are not supported; provide a single frame.")
                if width <= 0 or height <= 0:
                    raise ValueError("Image dimensions must be positive.")
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    raise ValueError(
                        f"Image dimensions may not exceed {MAX_IMAGE_DIMENSION} pixels per side."
                    )
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError(f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel safety limit.")
                image.load()
    except ValueError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Image dimensions exceed the decoder safety limit.") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Image data could not be decoded as JPEG, PNG, or WebP.") from exc

    if actual_mime != mime_type:
        raise ValueError(
            f"Image content is {actual_mime or 'an unknown format'}, not the declared {mime_type}."
        )
    return ImagePayload(mime_type=mime_type, data=data, width=width, height=height)


def validate_content(content: Any) -> Any:
    """Normalize one OpenAI-style content value and decode each image once."""

    if content is None or isinstance(content, str | MultimodalContent):
        return content
    if not isinstance(content, list):
        raise ValueError("Message content must be a string, a list of content parts, or null.")

    preflight_request_contents([content])
    normalized: list[str | ImagePayload] = []
    contains_image = False
    image_count = 0
    image_bytes = 0
    image_pixels = 0
    for part in content:
        if isinstance(part, str):
            normalized.append(part)
            continue
        if not isinstance(part, dict):
            raise ValueError("Each message content part must be a string or object.")

        part_type = _part_type(part)
        if part_type in SUPPORTED_IMAGE_PART_TYPES:
            payload = decode_data_url(_image_url_from_part(part))
            image_count += 1
            image_bytes += payload.byte_size
            image_pixels += payload.pixel_count
            if image_count > MAX_IMAGES_PER_REQUEST:
                raise ValueError(f"A request may contain at most {MAX_IMAGES_PER_REQUEST} images.")
            if image_bytes > MAX_REQUEST_IMAGE_BYTES:
                raise ValueError(
                    f"Combined image data must be {MAX_REQUEST_IMAGE_BYTES // (1024 * 1024)} MiB or smaller."
                )
            if image_pixels > MAX_REQUEST_IMAGE_PIXELS:
                raise ValueError(
                    f"Combined image dimensions exceed the {MAX_REQUEST_IMAGE_PIXELS:,}-pixel request limit."
                )
            normalized.append(payload)
            contains_image = True
        elif part_type in SUPPORTED_TEXT_PART_TYPES or not part_type:
            normalized.append(_text_from_part(part))
        else:
            raise ValueError(f"Unsupported message content part type '{part_type}'.")

    if contains_image:
        result = MultimodalContent(tuple(normalized))
        validate_request_contents([result])
        return result
    return "\n".join(part for part in normalized if isinstance(part, str))


def iter_image_payloads(content: Any) -> Iterable[ImagePayload]:
    if isinstance(content, MultimodalContent):
        yield from (part for part in content.parts if isinstance(part, ImagePayload))


def content_has_images(content: Any) -> bool:
    if isinstance(content, MultimodalContent):
        return any(isinstance(part, ImagePayload) for part in content.parts)
    if isinstance(content, list):
        return any(
            isinstance(part, dict) and _part_type(part) in SUPPORTED_IMAGE_PART_TYPES
            for part in content
        )
    return False


def contents_have_images(contents: Iterable[Any]) -> bool:
    return any(content_has_images(content) for content in contents)


def validate_request_contents(
    contents: Iterable[Any],
    *,
    roles: Iterable[str] | None = None,
) -> None:
    """Enforce image limits across an entire request, not per message."""

    content_list = list(contents)
    role_list = list(roles) if roles is not None else ["user"] * len(content_list)
    if len(role_list) != len(content_list):
        raise ValueError("Internal role/content validation mismatch.")

    payloads: list[ImagePayload] = []
    for role, content in zip(role_list, content_list, strict=True):
        images = list(iter_image_payloads(content))
        if images and str(role).lower() != "user":
            raise ValueError("Image content is only supported in user messages.")
        payloads.extend(images)

    if len(payloads) > MAX_IMAGES_PER_REQUEST:
        raise ValueError(f"A request may contain at most {MAX_IMAGES_PER_REQUEST} images.")
    total_bytes = sum(payload.byte_size for payload in payloads)
    if total_bytes > MAX_REQUEST_IMAGE_BYTES:
        raise ValueError(
            f"Combined image data must be {MAX_REQUEST_IMAGE_BYTES // (1024 * 1024)} MiB or smaller."
        )
    total_pixels = sum(payload.pixel_count for payload in payloads)
    if total_pixels > MAX_REQUEST_IMAGE_PIXELS:
        raise ValueError(
            f"Combined image dimensions exceed the {MAX_REQUEST_IMAGE_PIXELS:,}-pixel request limit."
        )


def content_to_transport_text(content: Any) -> str | MultimodalContent:
    """Return normalized prompt content without serializing image bytes into text."""

    normalized = validate_content(content)
    if normalized is None:
        return ""
    if isinstance(normalized, str | MultimodalContent):
        return normalized
    raise TypeError(f"Unexpected normalized content type: {type(normalized).__name__}")


def plain_text(content: Any, *, image_placeholder: str = "[Image]") -> str:
    """Render content safely for a text-only template or export."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        content = validate_content(content)
    if isinstance(content, MultimodalContent):
        fragments = [part if isinstance(part, str) else image_placeholder for part in content.parts]
        return "\n".join(fragment for fragment in fragments if fragment)
    return str(content)


def prepare_text_messages(messages: list[dict]) -> list[dict]:
    """Ensure a text-only tokenizer never receives encoded image bytes."""

    prepared: list[dict] = []
    for message in messages:
        item = dict(message)
        item["content"] = plain_text(
            item.get("content"),
            image_placeholder="[Image omitted: selected model is not vision-capable.]",
        )
        prepared.append(item)
    return prepared


def _purge_contexts_locked(now: float) -> None:
    global _context_bytes
    expired = [
        key for key, (created, _) in _contexts.items() if now - created > _CONTEXT_TTL_SECONDS
    ]
    for key in expired:
        _, payloads = _contexts.pop(key)
        _context_bytes -= sum(payload.byte_size for payload in payloads)


def _store_context(payloads: list[ImagePayload]) -> str:
    global _context_bytes
    immutable_payloads = tuple(payloads)
    payload_bytes = sum(payload.byte_size for payload in immutable_payloads)
    key = uuid.uuid4().hex
    now = time.monotonic()
    with _context_lock:
        _purge_contexts_locked(now)
        if len(_contexts) >= _CONTEXT_LIMIT:
            raise VisionCapacityError(
                "Too many active vision requests. Retry after current requests finish."
            )
        if _context_bytes + payload_bytes > _CONTEXT_BYTES_LIMIT:
            raise VisionCapacityError(
                "Vision request memory limit reached. Retry after current requests finish."
            )
        _contexts[key] = (now, immutable_payloads)
        _context_bytes += payload_bytes
    return key


def prepare_vision_messages(messages: list[dict]) -> tuple[list[dict], str | None]:
    """Replace typed image parts with OpenVINO tags and retain payloads request-locally."""

    payloads: list[ImagePayload] = []
    prepared: list[dict] = []
    for message in messages:
        item = dict(message)
        content = item.get("content")
        if isinstance(content, MultimodalContent):
            fragments: list[str] = []
            for part in content.parts:
                if isinstance(part, str):
                    if part:
                        fragments.append(part)
                else:
                    payloads.append(part)
                    fragments.append(f"<ov_genai_image_{len(payloads) - 1}>")
            item["content"] = "\n".join(fragments)
        else:
            item["content"] = plain_text(content)
        prepared.append(item)

    validate_request_contents(
        [message.get("content") for message in messages],
        roles=[str(message.get("role", "user")) for message in messages],
    )
    if not payloads:
        return prepared, None
    return prepared, _store_context(payloads)


def append_prompt_context(prompt: str, context_key: str | None) -> str:
    if not context_key:
        return prompt
    return f"{prompt}\n<ovllm-image-context:{_CONTEXT_NAMESPACE}:{context_key}>"


def strip_prompt_context(prompt: str) -> str:
    return _CONTEXT_MARKER_RE.sub("", prompt)


def prompt_image_count(prompt: str) -> int:
    """Return the number of images attached to a rendered prompt without consuming them."""

    match = _CONTEXT_MARKER_RE.search(prompt)
    if not match:
        return 0
    with _context_lock:
        item = _contexts.get(match.group(1))
        return len(item[1]) if item else 0


def prompt_image_token_reserve(prompt: str) -> int:
    return prompt_image_count(prompt) * _IMAGE_TOKEN_RESERVE


def discard_prompt_context(prompt: str) -> None:
    """Release a prompt's image context when it will not be generated."""

    global _context_bytes
    match = _CONTEXT_MARKER_RE.search(prompt)
    if not match:
        return
    with _context_lock:
        item = _contexts.pop(match.group(1), None)
        if item is not None:
            _context_bytes -= sum(payload.byte_size for payload in item[1])


def consume_prompt_context(prompt: str) -> tuple[str, list[ImagePayload]]:
    """Remove a prompt context marker and return its payloads exactly once."""

    global _context_bytes
    match = _CONTEXT_MARKER_RE.search(prompt)
    if not match:
        return prompt, []
    clean_prompt = prompt[: match.start()].rstrip()
    with _context_lock:
        item = _contexts.pop(match.group(1), None)
        if item is not None:
            _context_bytes -= sum(payload.byte_size for payload in item[1])
    if item is None:
        raise RuntimeError("Image context expired before generation started. Retry the request.")
    return clean_prompt, list(item[1])


def to_openvino_tensors(payloads: list[ImagePayload]) -> list[Any]:
    """Convert validated images into contiguous OpenVINO uint8 NHWC tensors."""

    if not payloads:
        return []
    try:
        import numpy as np
        from openvino import Tensor
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - real OpenVINO environments provide these
        raise RuntimeError(
            "OpenVINO, NumPy, and Pillow are required for vision inference."
        ) from exc

    tensors: list[Any] = []
    for payload in payloads:
        with Image.open(io.BytesIO(payload.data)) as image:
            oriented = ImageOps.exif_transpose(image)
            rgb = oriented.convert("RGB")
            array = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8))
        tensors.append(Tensor(array.reshape(1, array.shape[0], array.shape[1], 3)))
    return tensors
