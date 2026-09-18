"""Inference engine wrappers for text, embeddings, and vision-language models.

``OpenVINOEngine`` wraps ``openvino_genai.LLMPipeline``.
``OpenVINOVisionEngine`` wraps ``openvino_genai.VLMPipeline`` and consumes the
validated image contexts produced by :mod:`app.multimodal`.
Mock engines preserve the same contracts so API and browser work can be tested on
machines without OpenVINO hardware.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import chat_format, multimodal
from runtime.device_check import is_openvino_available, normalize_device

logger = logging.getLogger("ov-llm.engine")

_SENTINEL = object()


def _streamer_status(ov_module: Any, *, stop: bool):
    """Return the current OpenVINO streaming status with legacy-bool fallback."""

    status_type = getattr(ov_module, "StreamingStatus", None)
    if status_type is None:
        return stop
    return status_type.STOP if stop else status_type.RUNNING


@dataclass
class GenParams:
    max_new_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 1.0
    do_sample: bool = True
    seed: int | None = None
    stop: list[str] | None = None
    response_format: dict | None = None
    lora_path: str | None = None
    lora_alpha: float | None = 1.0


@dataclass
class GenResult:
    text: str
    completion_tokens: int


class StreamHandle:
    """Thread-safe bridge from a synchronous generation worker to an async caller."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=1024)
        self.error: BaseException | None = None
        self.text = ""
        self._stop = threading.Event()
        self._done = threading.Event()

    def push(self, piece: str) -> bool:
        """Queue a generated chunk unless cancellation was requested."""

        while not self.should_stop():
            try:
                self._q.put(piece, timeout=0.1)
            except queue.Full:
                continue
            self.text += piece
            return True
        return False

    def finish(self, error: BaseException | None = None) -> None:
        self.error = error
        try:
            while True:
                try:
                    self._q.put(_SENTINEL, timeout=0.1)
                    break
                except queue.Full:
                    if not self.should_stop():
                        continue
                    with contextlib.suppress(queue.Empty):
                        self._q.get_nowait()
        finally:
            self._done.set()

    def next_chunk(self) -> str | None:
        item = self._q.get()
        return None if item is _SENTINEL else item

    def request_stop(self) -> None:
        self._stop.set()

    def should_stop(self) -> bool:
        return self._stop.is_set()

    def wait_closed(self, timeout: float | None = 30.0) -> bool:
        return self._done.wait(timeout)


class BaseEngine:
    backend = "base"
    supports_vision = False
    model_id: str = ""
    model_path: str = ""
    device: str = "CPU"

    def apply_chat_template(self, messages: list[dict], add_generation_prompt: bool = True) -> str:
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        raise NotImplementedError

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        raise NotImplementedError

    def stream(self, prompt: str, params: GenParams) -> StreamHandle:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def close(self) -> None:  # noqa: B027 - intentional no-op default
        pass

    def _build_adapters_config(self, params: GenParams) -> Any | None:
        return None


# --- Mock engines ----------------------------------------------------------


class MockEngine(BaseEngine):
    """Dependency-free canned text engine for API and UI testing."""

    backend = "mock"

    def __init__(self, model_id: str, model_path: str = "", device: str = "MOCK") -> None:
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = device

    def apply_chat_template(self, messages: list[dict], add_generation_prompt: bool = True) -> str:
        messages = multimodal.prepare_text_messages(messages)
        return chat_format.render_chatml(messages, add_generation_prompt)

    def count_tokens(self, text: str) -> int:
        return max(1, len(multimodal.strip_prompt_context(text)) // 4)

    def _reply(self, prompt: str) -> str:
        matches = re.findall(r"<\|im_start\|>user\n(.*?)<\|im_end\|>", prompt, re.DOTALL)
        last_user = matches[-1].strip() if matches else "(no user message found)"
        return (
            "**Mock engine** — OpenVINO is not active, so this is a canned response.\n\n"
            f"You said: _{last_user[:400]}_\n\n"
            "This lets you exercise the full chat UI, streaming, tokens, and the OpenAI API on any "
            "machine. On Windows with OpenVINO installed and a converted model, you'll get real "
            f"output instead.\n\n```python\nprint('hello from {self.model_id}')\n```"
        )

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        text = self._reply(prompt)
        return GenResult(text=text, completion_tokens=self.count_tokens(text))

    def stream(self, prompt: str, params: GenParams) -> StreamHandle:
        handle = StreamHandle()
        text = self._reply(prompt)

        def worker() -> None:
            for token in re.findall(r"\S+\s*", text):
                if handle.should_stop():
                    break
                time.sleep(0.015)
                if not handle.push(token):
                    break
            handle.finish()

        threading.Thread(target=worker, daemon=True).start()
        return handle


class MockVisionEngine(MockEngine):
    """Mock VLM that verifies the complete image transport without inference."""

    backend = "mock-vlm"
    supports_vision = True

    def apply_chat_template(self, messages: list[dict], add_generation_prompt: bool = True) -> str:
        prepared, context_key = multimodal.prepare_vision_messages(messages)
        prompt = chat_format.render_chatml(prepared, add_generation_prompt)
        return multimodal.append_prompt_context(prompt, context_key)

    def count_tokens(self, text: str) -> int:
        return super().count_tokens(text) + multimodal.prompt_image_token_reserve(text)

    def _reply(self, prompt: str) -> str:
        clean_prompt, images = multimodal.consume_prompt_context(prompt)
        matches = re.findall(r"<\|im_start\|>user\n(.*?)<\|im_end\|>", clean_prompt, re.DOTALL)
        last_user = matches[-1].strip() if matches else "(no user message found)"
        last_user = re.sub(r"<ov_genai_image_\d+>", "[image]", last_user)
        dimensions = ", ".join(f"{item.width}×{item.height}" for item in images) or "none"
        return (
            "**Mock vision engine** — OpenVINO VLM inference is not active.\n\n"
            f"Received **{len(images)} image(s)** ({dimensions}).\n\n"
            f"Prompt: _{last_user[:400]}_\n\n"
            "The image validation, OpenAI content-part parsing, model routing, and streaming "
            "path completed successfully. Load a converted `openvino-vlm` model for real analysis."
        )


class MockEmbeddingEngine(BaseEngine):
    """Mock embedding engine returning deterministic 384-dimensional vectors."""

    backend = "mock-embeddings"

    def __init__(self, model_id: str, model_path: str = "", device: str = "MOCK") -> None:
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = device
        self._closed = False

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._closed:
            raise RuntimeError(f"Mock embedding engine for '{self.model_id}' is closed")
        import random

        results = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
            rng = random.Random(seed)
            results.append([rng.uniform(-1.0, 1.0) for _ in range(384)])
        return results

    def close(self) -> None:
        self._closed = True


# --- OpenVINO text and vision engines -------------------------------------


class OpenVINOEngine(BaseEngine):
    """Wrap ``openvino_genai.LLMPipeline``."""

    backend = "openvino-genai"

    def __init__(
        self,
        model_id: str,
        model_path: str,
        device: str,
        plugin_config: dict | None = None,
        draft_model_path: str | None = None,
    ) -> None:
        import openvino_genai as ov_genai

        self._ov = ov_genai
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = normalize_device(device)
        self._closed = False

        config = dict(plugin_config or {})
        draft_obj = None
        if draft_model_path:
            logger.info("Initializing speculative decoding with draft model: %s", draft_model_path)
            draft_model_fn = getattr(ov_genai, "draft_model", None)
            if draft_model_fn is None:
                raise RuntimeError(
                    "This OpenVINO GenAI version does not support speculative decoding."
                )
            try:
                draft_obj = draft_model_fn(str(draft_model_path), self.device)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load draft model '{draft_model_path}': {exc}"
                ) from exc

        logger.info("Loading '%s' on %s from %s", model_id, self.device, self.model_path)
        if draft_obj is not None:
            self._pipe = ov_genai.LLMPipeline(
                self.model_path, self.device, draft_model=draft_obj, **config
            )
        elif config:
            self._pipe = ov_genai.LLMPipeline(self.model_path, self.device, **config)
        else:
            self._pipe = ov_genai.LLMPipeline(self.model_path, self.device)
        self._tokenizer = self._pipe.get_tokenizer()
        logger.info("Model '%s' ready on %s", model_id, self.device)

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError(f"Engine for '{self.model_id}' is closed")

    def _render_messages(self, messages: list[dict], add_generation_prompt: bool) -> str:
        try:
            try:
                return self._tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=add_generation_prompt,
                )
            except TypeError:
                return self._tokenizer.apply_chat_template(messages, add_generation_prompt)
        except Exception as exc:
            logger.debug("apply_chat_template failed (%s); falling back to ChatML", exc)
            return chat_format.render_chatml(messages, add_generation_prompt)

    def apply_chat_template(self, messages: list[dict], add_generation_prompt: bool = True) -> str:
        self._check_closed()
        return self._render_messages(
            multimodal.prepare_text_messages(messages), add_generation_prompt
        )

    def count_tokens(self, text: str) -> int:
        self._check_closed()
        text = multimodal.strip_prompt_context(text)
        try:
            ids = self._tokenizer.encode(text).input_ids
            try:
                return int(ids.get_shape()[-1])
            except Exception:
                return int(ids.shape[-1])
        except Exception:
            return max(1, len(text) // 4)

    def _build_config(self, params: GenParams):
        cfg = self._ov.GenerationConfig()
        # Prompts already contain the model's chat template before token budgeting.
        cfg.apply_chat_template = False
        cfg.max_new_tokens = int(params.max_new_tokens)
        if params.do_sample and params.temperature and params.temperature > 0:
            cfg.do_sample = True
            cfg.temperature = float(params.temperature)
            cfg.top_p = float(params.top_p)
            if params.seed is not None:
                with contextlib.suppress(Exception):
                    cfg.rng_seed = int(params.seed)
        else:
            cfg.do_sample = False
        if params.stop:
            with contextlib.suppress(Exception):
                cfg.stop_strings = set(params.stop)
                cfg.include_stop_str_in_output = False
        if params.response_format:
            structured_output = getattr(self._ov, "StructuredOutputConfig", None)
            if structured_output is not None:
                import json

                format_type = params.response_format.get("type")
                if format_type == "json_schema":
                    schema_data = params.response_format.get("json_schema", {}).get("schema")
                    if schema_data:
                        output_config = structured_output(json_schema=json.dumps(schema_data))
                        with contextlib.suppress(Exception):
                            output_config.backend = "xgrammar"
                        cfg.structured_output_config = output_config
                elif format_type == "json_object":
                    output_config = structured_output(json_schema=json.dumps({"type": "object"}))
                    with contextlib.suppress(Exception):
                        output_config.backend = "xgrammar"
                    cfg.structured_output_config = output_config
        return cfg

    def _build_adapters_config(self, params: GenParams):
        if not params.lora_path:
            return None
        adapter = getattr(self._ov, "Adapter", None)
        adapter_config_type = getattr(self._ov, "AdapterConfig", None)
        if adapter is None or adapter_config_type is None:
            raise RuntimeError(
                "This OpenVINO GenAI version does not support dynamic LoRA adapters."
            )
        try:
            adapter_config = adapter_config_type()
            adapter_config.add(
                adapter(str(params.lora_path)),
                alpha=float(params.lora_alpha or 1.0),
            )
            return adapter_config
        except Exception as exc:
            raise RuntimeError(
                f"Failed to construct LoRA adapter config for '{params.lora_path}': {exc}"
            ) from exc

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        self._check_closed()
        kwargs = {}
        adapters_config = self._build_adapters_config(params)
        if adapters_config is not None:
            kwargs["adapters"] = adapters_config
        result = self._pipe.generate(prompt, self._build_config(params), **kwargs)
        text = _result_text(result)
        return GenResult(text=text, completion_tokens=self.count_tokens(text))

    def stream(self, prompt: str, params: GenParams) -> StreamHandle:
        self._check_closed()
        handle = StreamHandle()
        config = self._build_config(params)
        pipe = self._pipe

        def streamer(subword: str):
            return _streamer_status(self._ov, stop=not handle.push(subword))

        def worker() -> None:
            try:
                kwargs = {}
                adapters_config = self._build_adapters_config(params)
                if adapters_config is not None:
                    kwargs["adapters"] = adapters_config
                pipe.generate(prompt, config, streamer, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - surface to the consumer
                handle.finish(error=exc)
                return
            handle.finish()

        threading.Thread(target=worker, daemon=True).start()
        return handle

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("Closing engine for '%s'", self.model_id)
        self._pipe = None
        self._tokenizer = None


class OpenVINOVisionEngine(OpenVINOEngine):
    """Wrap ``openvino_genai.VLMPipeline`` for OpenAI-compatible image inputs."""

    backend = "openvino-vlm"
    supports_vision = True

    def __init__(
        self,
        model_id: str,
        model_path: str,
        device: str,
        plugin_config: dict | None = None,
        draft_model_path: str | None = None,
    ) -> None:
        if draft_model_path:
            raise RuntimeError("Speculative draft models are not supported by the VLM backend.")

        import openvino_genai as ov_genai

        if not hasattr(ov_genai, "VLMPipeline"):
            raise RuntimeError(
                "This OpenVINO GenAI version does not provide VLMPipeline. Upgrade openvino-genai."
            )

        self._ov = ov_genai
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = normalize_device(device)
        self._closed = False
        config = dict(plugin_config or {})

        logger.info(
            "Loading vision model '%s' on %s from %s", model_id, self.device, self.model_path
        )
        if self.device == "NPU" and config:
            # VLM NPU plugin options are nested under DEVICE_PROPERTIES.
            self._pipe = ov_genai.VLMPipeline(
                self.model_path,
                self.device,
                config={"DEVICE_PROPERTIES": {"NPU": config}},
            )
        elif config:
            self._pipe = ov_genai.VLMPipeline(self.model_path, self.device, **config)
        else:
            self._pipe = ov_genai.VLMPipeline(self.model_path, self.device)
        self._tokenizer = self._pipe.get_tokenizer()
        logger.info("Vision model '%s' ready on %s", model_id, self.device)

    def apply_chat_template(self, messages: list[dict], add_generation_prompt: bool = True) -> str:
        self._check_closed()
        prepared, context_key = multimodal.prepare_vision_messages(messages)
        prompt = self._render_messages(prepared, add_generation_prompt)
        return multimodal.append_prompt_context(prompt, context_key)

    def count_tokens(self, text: str) -> int:
        # The text tokenizer cannot account for visual embeddings. Reserve a
        # conservative per-image allowance so output-token capping never assumes
        # the full text-only context budget remains available.
        return super().count_tokens(text) + multimodal.prompt_image_token_reserve(text)

    def _generation_inputs(self, prompt: str) -> tuple[str, list[Any]]:
        clean_prompt, payloads = multimodal.consume_prompt_context(prompt)
        return clean_prompt, multimodal.to_openvino_tensors(payloads)

    def _build_adapters_config(self, params: GenParams):
        if params.lora_path:
            raise RuntimeError("Dynamic LoRA adapters are not supported by the VLM backend.")
        return None

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        self._check_closed()
        self._build_adapters_config(params)
        clean_prompt, images = self._generation_inputs(prompt)
        kwargs: dict[str, Any] = {"generation_config": self._build_config(params)}
        if images:
            kwargs["images"] = images
        result = self._pipe.generate(clean_prompt, **kwargs)
        text = _result_text(result)
        return GenResult(text=text, completion_tokens=self.count_tokens(text))

    def stream(self, prompt: str, params: GenParams) -> StreamHandle:
        self._check_closed()
        self._build_adapters_config(params)
        clean_prompt, images = self._generation_inputs(prompt)
        handle = StreamHandle()
        config = self._build_config(params)
        pipe = self._pipe

        def streamer(subword: str):
            return _streamer_status(self._ov, stop=not handle.push(subword))

        def worker() -> None:
            try:
                kwargs: dict[str, Any] = {
                    "generation_config": config,
                    "streamer": streamer,
                }
                if images:
                    kwargs["images"] = images
                pipe.generate(clean_prompt, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - surface to the consumer
                handle.finish(error=exc)
                return
            handle.finish()

        threading.Thread(target=worker, daemon=True).start()
        return handle


# --- Embedding engine ------------------------------------------------------


def _result_text(result) -> str:
    """Extract generated text from an OpenVINO decoded result defensively."""

    try:
        if hasattr(result, "texts") and result.texts:
            return str(result.texts[0])
    except Exception:
        pass
    return str(result)


class OpenVINOEmbeddingEngine(BaseEngine):
    """Wrap ``openvino_genai.TextEmbeddingPipeline``."""

    backend = "openvino-embeddings"

    def __init__(
        self,
        model_id: str,
        model_path: str,
        device: str,
        plugin_config: dict | None = None,
    ) -> None:
        import openvino_genai as ov_genai

        self._ov = ov_genai
        self.model_id = model_id
        self.model_path = str(model_path)
        self.device = normalize_device(device)
        self._closed = False

        config = dict(plugin_config or {})
        logger.info(
            "Loading embedding model '%s' on %s from %s", model_id, self.device, self.model_path
        )
        if config:
            self._pipe = ov_genai.TextEmbeddingPipeline(self.model_path, self.device, **config)
        else:
            self._pipe = ov_genai.TextEmbeddingPipeline(self.model_path, self.device)
        logger.info("Embedding model '%s' ready on %s", model_id, self.device)

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError(f"Embedding engine for '{self.model_id}' is closed")

    def count_tokens(self, text: str) -> int:
        self._check_closed()
        return max(1, len(text) // 4)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._check_closed()
        if hasattr(self._pipe, "embed_documents"):
            result = self._pipe.embed_documents(texts)
        elif hasattr(self._pipe, "embed"):
            result = self._pipe.embed(texts)
        else:
            raise AttributeError(
                "OpenVINO TextEmbeddingPipeline does not have 'embed_documents' or 'embed' methods."
            )

        if hasattr(result, "tolist"):
            return result.tolist()
        if hasattr(result, "embeddings"):
            embeddings = result.embeddings
            if hasattr(embeddings, "tolist"):
                return embeddings.tolist()
        try:
            import numpy as np

            return np.array(result).tolist()
        except Exception:
            pass

        output = []
        for row in result:
            if hasattr(row, "tolist"):
                output.append(row.tolist())
            else:
                try:
                    output.append([float(value) for value in row])
                except Exception:
                    output.append(float(row))
        return output

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("Closing embedding engine for '%s'", self.model_id)
        self._pipe = None


# --- Factory ---------------------------------------------------------------


def build_plugin_config(
    device: str,
    max_prompt_len: int | None,
    cache_dir: str | Path | None = None,
) -> dict:
    """Build device-specific OpenVINO plugin configuration."""

    device = normalize_device(device)
    config: dict = {}
    if device == "NPU" and max_prompt_len:
        config["MAX_PROMPT_LEN"] = int(max_prompt_len)

    if cache_dir:
        try:
            cache_path = Path(cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
            config["CACHE_DIR"] = str(cache_path)
        except Exception as exc:
            logger.warning("Failed to create cache directory '%s': %s", cache_dir, exc)

    return config


def create_engine(
    *,
    model_id: str,
    model_path: str,
    device: str,
    max_prompt_len: int | None = None,
    force_mock: bool = False,
    cache_dir: str | Path | None = None,
    backend: str = "openvino-genai",
    draft_model_path: str | None = None,
) -> BaseEngine:
    """Create the configured text, embedding, or vision engine."""

    device = normalize_device(device)
    backend_lower = backend.lower()
    is_embedding = "embedding" in backend_lower
    is_vision = multimodal.backend_supports_vision(backend_lower)

    if force_mock or not is_openvino_available():
        reason = "forced" if force_mock else "OpenVINO not installed"
        if is_embedding:
            logger.info("Using MOCK embedding engine for '%s' (%s)", model_id, reason)
            return MockEmbeddingEngine(model_id, model_path, device if force_mock else "MOCK")
        if is_vision:
            logger.info("Using MOCK vision engine for '%s' (%s)", model_id, reason)
            return MockVisionEngine(model_id, model_path, device if force_mock else "MOCK")
        logger.info("Using MOCK engine for '%s' (%s)", model_id, reason)
        return MockEngine(model_id, model_path, device if force_mock else "MOCK")

    plugin_config = build_plugin_config(device, max_prompt_len, cache_dir)
    if is_embedding:
        return OpenVINOEmbeddingEngine(model_id, model_path, device, plugin_config)
    if is_vision:
        return OpenVINOVisionEngine(
            model_id,
            model_path,
            device,
            plugin_config,
            draft_model_path=draft_model_path,
        )
    return OpenVINOEngine(
        model_id,
        model_path,
        device,
        plugin_config,
        draft_model_path=draft_model_path,
    )
