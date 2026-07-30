"""FastAPI entry point: OpenAI-compatible routes, lifecycle, and the CLI.

Run via the CLI:
    python -m app.server --model tinyllama-1.1b-chat --device CPU
Or via uvicorn directly (settings come from environment variables):
    uvicorn app.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import struct
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app import __version__, chat_format, model_manager, multimodal, tools
from app.body_limit import RequestBodyLimitMiddleware
from app.brand import DISPLAY_NAME
from app.config import BASE_DIR, Settings
from app.openai_api import (
    BenchmarkRunRequest,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionResponseChoice,
    ChatExportRequest,
    DownloadCustomRequest,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingResponseData,
    EmbeddingResponseUsage,
    ModelConvertRequest,
    ModelDeleteRequest,
    ModelLoadRequest,
    ModelRegisterRequest,
    ModelUnloadRequest,
    ResponseObject,
    ResponseOutputMessage,
    ResponseRequest,
    UsageInfo,
)
from app.rate_limit import RateLimitMiddleware
from app.telemetry import cpu_stats, disk_stats, gpu_stats, memory_stats
from app.ui_extension import inject_multimodal_ui
from runtime import device_check
from runtime.benchmark_runner import (
    DEFAULT_BENCHMARK_PROMPT,
    BenchmarkStore,
    run_benchmark_suite,
)
from runtime.openvino_engine import BaseEngine, GenParams

request_id_var = contextvars.ContextVar("request_id", default="")
active_key_var = contextvars.ContextVar("active_key", default="default")

# Client-supplied X-Request-ID values must be short and log-safe (no newlines /
# control characters that could forge log lines); anything else is replaced.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get() or "-"
        return True


# Configure logging to console with Request ID support
root_logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(request_id)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)
handler.addFilter(RequestIDFilter())
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

logger = logging.getLogger("ov-llm.server")
START_TIME = time.time()


WEB_DIR = BASE_DIR / "web"


@lru_cache(maxsize=1)
def _index_html() -> str:
    """Load and extend the bundled UI once per server process."""

    return inject_multimodal_ui((WEB_DIR / "index.html").read_text(encoding="utf-8"))


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded environment from %s", env_path)


def _normalize_device_or_400(device: str | None) -> str | None:
    if device is None:
        return None
    try:
        return device_check.validate_device_expression(device)
    except device_check.DeviceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Generation helpers ----------------------------------------------------


def _params_for(
    request_max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    prompt_tokens: int,
    max_context_len: int,
    *,
    stop: list[str] | None = None,
    seed: int | None = None,
    response_format: dict | None = None,
    lora_path: str | None = None,
    lora_alpha: float | None = 1.0,
) -> GenParams:
    available = max_context_len - prompt_tokens - 8
    if available < 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Prompt requires {prompt_tokens} tokens and leaves no room in the "
                f"{max_context_len}-token context window. Reduce the prompt or image count."
            ),
        )
    requested = request_max_tokens or 512
    max_new = min(requested, available)
    temp = 0.7 if temperature is None else float(temperature)
    return GenParams(
        max_new_tokens=max_new,
        temperature=temp,
        top_p=1.0 if top_p is None else float(top_p),
        do_sample=temp > 0,
        stop=stop or None,
        seed=seed,
        response_format=response_format,
        lora_path=lora_path,
        lora_alpha=lora_alpha,
    )


def _normalize_and_build_chat_prompt(
    engine: BaseEngine,
    messages,
    max_prompt_len: int,
    request_tools,
    tool_choice,
    use_tools: bool,
):
    system_override = tools.format_tools_for_prompt(request_tools, tool_choice) if use_tools else ""
    if system_override:
        multimodal.preflight_request_contents([system_override])
    dict_messages = chat_format.normalize_messages(messages, system_override)
    prompt, prompt_tokens = chat_format.build_prompt_within_budget(
        dict_messages, engine.apply_chat_template, engine.count_tokens, max_prompt_len
    )
    return dict_messages, prompt, prompt_tokens


def _build_normalized_chat_prompt(engine: BaseEngine, dict_messages, max_prompt_len: int):
    return chat_format.build_prompt_within_budget(
        dict_messages, engine.apply_chat_template, engine.count_tokens, max_prompt_len
    )


def _normalize_and_build_response_prompt(
    engine: BaseEngine, request_input, instructions: str | None, max_prompt_len: int
):
    dict_messages = chat_format.responses_input_to_messages(request_input, instructions)
    prompt, prompt_tokens = chat_format.build_prompt_within_budget(
        dict_messages, engine.apply_chat_template, engine.count_tokens, max_prompt_len
    )
    return prompt, prompt_tokens


async def _build_prompt_off_thread(builder, *args):
    """Run tokenizer/image prompt work without blocking the event loop."""

    try:
        return await asyncio.to_thread(builder, *args)
    except multimodal.VisionCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_generation_request(
    engine: BaseEngine, model_id: str, contents, lora_path: str | None
) -> None:
    if multimodal.contents_have_images(contents) and not getattr(engine, "supports_vision", False):
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not vision-capable and cannot accept image input.",
        )
    if lora_path and getattr(engine, "supports_vision", False):
        raise HTTPException(
            status_code=400,
            detail="Dynamic LoRA adapters are not supported by the vision backend.",
        )


def _resolve_or_400(manager: model_manager.ModelManager, model_id: str) -> BaseEngine:
    try:
        return manager.resolve_engine(model_id)
    except model_manager.UnknownModel as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except model_manager.ModelLoading as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_manager.ModelNotLoaded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_manager.NoModelsLoaded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def encode_embedding(embedding: list[float], format_type: str | None) -> list[float] | str:
    if format_type == "base64":
        packed = struct.pack(f"{len(embedding)}f", *embedding)
        return base64.b64encode(packed).decode("utf-8")
    return embedding


def create_app(settings: Settings) -> FastAPI:
    manager = model_manager.ModelManager(settings)
    benchmark_store = BenchmarkStore(settings.benchmark_results_file)
    settings.validate(manager.catalog)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mode = "MOCK (no OpenVINO)" if manager.force_mock else f"device={settings.device}"
        logger.info("Starting %s server — %s", DISPLAY_NAME, mode)
        await manager.startup()
        try:
            yield
        finally:
            await manager.shutdown()
            logger.info("Server stopped; models unloaded.")

    app = FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.manager = manager
    app.state.benchmark_store = benchmark_store

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(_request: Request, exc: RequestValidationError):
        # FastAPI's default handler includes the rejected input value. For multimodal
        # requests that can echo large base64 payloads into error responses and logs.
        errors = [
            {
                "type": error.get("type", "value_error"),
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid request."),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    # Bound request bodies before JSON parsing/base64 decoding can amplify memory use.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=max(settings.max_request_body_mb, 1) * 1024 * 1024,
    )

    # Rate limiting middleware
    if settings.rate_limit > 0:
        app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit)

    # Request ID and structured request/response logging middleware
    @app.middleware("http")
    async def request_id_and_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "")
        if not _REQUEST_ID_RE.fullmatch(request_id):
            request_id = f"req-{uuid.uuid4().hex[:12]}"

        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "HTTP %s %s failed - Latency: %.2fms",
                request.method,
                request.url.path,
                duration,
            )
            raise
        else:
            duration = (time.perf_counter() - start_time) * 1000
            logger.info(
                "HTTP %s %s - Status: %d - Latency: %.2fms",
                request.method,
                request.url.path,
                response.status_code,
                duration,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)

    # Configure CORS middleware. Credentialed CORS is invalid with a wildcard
    # origin (browsers reject the combination), and this API authenticates via
    # the Authorization header rather than cookies, so credentials are only
    # enabled when explicit origins are configured.
    origins = [orig.strip() for orig in settings.cors_origins.split(",") if orig.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # --- auth & enterprise keys tracking -----------------------------------

    _auth_failures: dict[str, list[float]] = {}
    _AUTH_FAILURE_WINDOW = 300.0  # seconds
    _AUTH_FAILURE_MAX = 10  # failures in window before adding delay

    valid_keys = [k.strip() for k in (settings.api_key or "").split(",") if k.strip()]
    key_stats: dict[str, dict] = {}
    for k in valid_keys:
        prefix = k[:5] if len(k) > 7 else k[:2]
        fingerprint = hashlib.sha256(k.encode("utf-8")).hexdigest()[:8]
        key_stats[k] = {
            "key_name": f"{prefix}...{fingerprint}",
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_latency": 0.0,
        }
    if not key_stats:
        key_stats["default"] = {
            "key_name": "default (no auth)",
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_latency": 0.0,
        }

    def record_key_metrics(prompt_tokens: int, completion_tokens: int, latency: float):
        k = active_key_var.get()
        if k not in key_stats:
            k = (
                "default"
                if "default" in key_stats
                else list(key_stats.keys())[0]
                if key_stats
                else None
            )
        if k and k in key_stats:
            stats = key_stats[k]
            stats["requests"] += 1
            stats["prompt_tokens"] += prompt_tokens
            stats["completion_tokens"] += completion_tokens
            stats["total_latency"] += latency

    def auth_failure(source: str) -> HTTPException:
        now = time.monotonic()
        cutoff = now - _AUTH_FAILURE_WINDOW
        failures = [t for t in _auth_failures.get(source, []) if t > cutoff]
        failures.append(now)
        _auth_failures[source] = failures
        if len(failures) > _AUTH_FAILURE_MAX:
            retry_after = max(1, int(failures[0] + _AUTH_FAILURE_WINDOW - now) + 1)
            logger.warning(
                "Repeated auth failures from source '%s' (%d in window)", source, len(failures)
            )
            return HTTPException(
                status_code=429,
                detail="Too many invalid API-key attempts",
                headers={"Retry-After": str(retry_after)},
            )
        return HTTPException(status_code=401, detail="Invalid or missing API key")

    async def require_api_key(
        request: Request, authorization: str | None = Header(default=None)
    ) -> None:
        if not settings.api_key:
            active_key_var.set("default")
            return

        valid_keys_list = [k.strip() for k in settings.api_key.split(",") if k.strip()]
        if not valid_keys_list:
            active_key_var.set("default")
            return

        source = request.client.host if request.client else "unknown"
        if authorization is None or not authorization.startswith("Bearer "):
            raise auth_failure(source)

        token = authorization[len("Bearer ") :]
        matched_key = None
        for k in valid_keys_list:
            if secrets.compare_digest(token.encode("utf-8"), k.encode("utf-8")):
                matched_key = k
                break

        if matched_key is None:
            raise auth_failure(source)

        _auth_failures.pop(source, None)
        active_key_var.set(matched_key)

    auth = [Depends(require_api_key)]

    @app.get("/v1/keys/stats", dependencies=auth)
    async def get_keys_stats():
        return list(key_stats.values())

    # --- UI + health -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(
            _index_html(),
            headers={
                "Cache-Control": "no-store, must-revalidate",
                "Pragma": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; "
                    "base-uri 'none'; "
                    "connect-src 'self'; "
                    "font-src 'self'; "
                    "form-action 'self'; "
                    "frame-ancestors 'none'; "
                    "img-src 'self' data:; "
                    "object-src 'none'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/health")
    async def health():
        loading_count = manager.loading_count()
        return {
            "status": "ok" if loading_count == 0 else "busy",
            "version": app.version,
            "uptime_seconds": int(time.time() - START_TIME),
            "mock": manager.force_mock,
            "device": settings.device,
            "openvino": device_check.is_openvino_available(),
            "models_loaded": len(manager.engines),
            "loading_count": loading_count,
            "any_busy": manager.any_busy(),
        }

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(response: Response):
        loading_count = manager.loading_count()
        if loading_count > 0:
            response.status_code = 503
            return {"status": "busy", "message": "Models are loading"}
        return {"status": "ready"}

    # --- models ------------------------------------------------------------

    @app.get("/v1/models", dependencies=auth)
    async def list_models():
        data = []
        for model_id in manager.catalog:
            entry = manager.catalog_entry(model_id)
            data.append(
                {
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "openvino",
                    "name": entry["name"],
                    "description": entry["description"],
                    "status": entry["status"],
                }
            )
        return {"object": "list", "data": data}

    @app.post("/v1/models/register", dependencies=auth)
    async def register_model(req: ModelRegisterRequest):
        try:
            cfg = manager.register_model(req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "registered",
            "message": f"Successfully registered {cfg.name}.",
            "model": manager.catalog_entry(cfg.id),
        }

    @app.post("/v1/models/load", dependencies=auth)
    async def load_model(req: ModelLoadRequest):
        if req.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{req.model}'")
        device = _normalize_device_or_400(req.device)

        try:
            task = manager.schedule_load(req.model, device, draft_model=req.draft_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        entry = manager.catalog_entry(req.model)
        if task is None and entry["is_loaded"]:
            return {
                "status": "loaded",
                "message": f"{entry['name']} is already loaded.",
                "model": entry,
            }
        return {
            "status": entry["status"],
            "message": f"Loading {entry['name']}. First load can take a while.",
            "model": entry,
        }

    @app.post("/v1/models/convert", dependencies=auth)
    async def convert_model(req: ModelConvertRequest):
        if req.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{req.model}'")
        device = _normalize_device_or_400(req.device)
        cfg = manager.catalog[req.model]
        if not cfg.source_model:
            raise HTTPException(
                status_code=400, detail=f"Model '{req.model}' has no source model configured"
            )

        try:
            task = manager.schedule_convert(
                req.model,
                device,
                load_after=req.load_after,
                weight_format=req.weight_format,
                group_size=req.group_size,
                ratio=req.ratio,
                sym=req.sym,
                trust_remote_code=req.trust_remote_code,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        entry = manager.catalog_entry(req.model)
        if task is None and entry["is_downloaded"] and not req.weight_format:
            return {
                "status": entry["status"],
                "message": f"{entry['name']} is already converted.",
                "model": entry,
            }
        return {
            "status": entry["status"],
            "message": f"Converting {entry['name']}. This may take several minutes.",
            "model": entry,
        }

    @app.post("/v1/models/unload", dependencies=auth)
    async def unload_model(req: ModelUnloadRequest):
        if req.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{req.model}'")
        if req.model not in manager.engines:
            raise HTTPException(status_code=409, detail=f"Model '{req.model}' is not loaded")
        lock = manager.locks.get(req.model)
        if lock and lock.locked():
            raise HTTPException(status_code=409, detail=f"Model '{req.model}' is busy")

        manager.unload(req.model)
        entry = manager.catalog_entry(req.model)
        return {"status": "unloaded", "message": f"Unloaded {entry['name']}.", "model": entry}

    @app.post("/v1/models/delete", dependencies=auth)
    async def delete_model(req: ModelDeleteRequest):
        if req.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{req.model}'")
        if req.model in manager.engines:
            raise HTTPException(
                status_code=409,
                detail=f"Model '{req.model}' is loaded. Unload it before deleting.",
            )
        override = manager.status_overrides.get(req.model, {})
        if override.get("status") in {"queued", "loading"}:
            raise HTTPException(status_code=409, detail=f"Model '{req.model}' is still loading")

        try:
            # Deleting a multi-GB IR directory can take a while; keep it off the event loop.
            result = await asyncio.to_thread(manager.delete, req.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.exception("Model deletion failed for '%s': %s", req.model, exc)
            raise HTTPException(
                status_code=500,
                detail="Model deletion failed; see server logs for the request ID.",
            ) from exc

        entry = manager.catalog_entry(req.model)
        freed_gb = round(result["freed_bytes"] / (1024**3), 2)
        if not result["deleted"]:
            return {
                "status": "noop",
                "message": f"No local files for {entry['name']}.",
                "freed_gb": 0.0,
                "model": entry,
            }
        return {
            "status": "deleted",
            "message": f"Deleted {entry['name']} ({freed_gb} GB freed).",
            "freed_gb": freed_gb,
            "freed_bytes": result["freed_bytes"],
            "model": entry,
        }

    @app.get("/v1/models/search-hf", dependencies=auth)
    async def search_hf(
        query: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=10, ge=1, le=50),
        task: str | None = Query(default=None, max_length=80),
    ):
        import httpx

        query = query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="Search query cannot be blank.")
        filter_str = task or "text-generation"
        if task == "embeddings" or task == "embedding":
            filter_str = "sentence-similarity"

        url = "https://huggingface.co/api/models"
        params = {
            "search": query,
            "filter": filter_str,
            "sort": "downloads",
            "direction": -1,
            "limit": limit,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.exception("Failed to query Hugging Face API: %s", exc)
                raise HTTPException(
                    status_code=502, detail="Hugging Face API is unavailable."
                ) from exc

        if not isinstance(data, list):
            logger.error(
                "Hugging Face API returned an unexpected payload type: %s", type(data).__name__
            )
            raise HTTPException(
                status_code=502, detail="Hugging Face API returned an invalid response."
            )

        def nonnegative_int(value) -> int:
            if isinstance(value, bool):
                return int(value)
            try:
                return min(max(int(value), 0), 2**63 - 1)
            except (TypeError, ValueError, OverflowError):
                return 0

        results = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str):
                continue
            model_id = model_id.strip()
            if not model_id or len(model_id) > 240 or any(ord(char) < 32 for char in model_id):
                continue
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "-", model_id.lower()).strip("._-")
            safe_id = safe_id[:128].rstrip("._-")
            if not safe_id:
                continue

            raw_pipeline_tag = str(item.get("pipeline_tag") or "").lower()
            pipeline_tag = re.sub(r"[^a-z0-9_.-]", "-", raw_pipeline_tag)[:80].strip(".-")
            is_embedding = pipeline_tag in ("sentence-similarity", "feature-extraction")
            is_vision = task == "image-text-to-text" or pipeline_tag == "image-text-to-text"
            if is_embedding:
                backend = "openvino-embeddings"
            elif is_vision:
                backend = "openvino-vlm"
            else:
                backend = "openvino-genai"

            results.append(
                {
                    "id": model_id,
                    "suggested_local_id": safe_id,
                    "downloads": nonnegative_int(item.get("downloads")),
                    "likes": nonnegative_int(item.get("likes")),
                    "pipeline_tag": pipeline_tag,
                    "backend": backend,
                    "tags": [
                        str(tag)[:200]
                        for tag in item.get("tags", [])[:50]
                        if isinstance(tag, str | int | float)
                    ]
                    if isinstance(item.get("tags"), list)
                    else [],
                }
            )
        return results

    @app.post("/v1/models/download-custom", dependencies=auth)
    async def download_custom(req: DownloadCustomRequest):
        existing = manager.catalog.get(req.model_id)
        if existing is not None:
            requested = {
                "name": req.name,
                "source_model": req.source_model,
                "backend": req.backend,
                "weight_format": req.weight_format,
                "recommended_device": req.recommended_device,
                "max_context_len": req.max_context_len,
                "max_output_tokens": req.max_output_tokens,
                "trust_remote_code": req.trust_remote_code,
            }
            mismatches = [
                field for field, value in requested.items() if getattr(existing, field) != value
            ]
            if mismatches:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Model ID '{req.model_id}' already exists with different "
                        f"configuration fields: {', '.join(mismatches)}."
                    ),
                )
        else:
            try:
                manager.register_model(req)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        device = _normalize_device_or_400(req.recommended_device)
        try:
            manager.schedule_convert(
                req.model_id,
                device,
                load_after=req.load_after,
                weight_format=req.weight_format,
                group_size=req.group_size,
                ratio=req.ratio,
                sym=req.sym,
                trust_remote_code=req.trust_remote_code,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        entry = manager.catalog_entry(req.model_id)
        return {
            "status": entry["status"],
            "message": f"Successfully registered and queued {entry['name']} for download/conversion.",
            "model": entry,
        }

    # --- devices + system status ------------------------------------------

    @app.get("/v1/devices", dependencies=auth)
    async def devices():
        available = device_check.available_devices()
        return {
            "default_device": settings.device,
            "openvino_available": device_check.is_openvino_available(),
            "mock": manager.force_mock,
            "available": available,
            "devices": device_check.device_details(),
            "suggestions": device_check.suggested_device_targets(available),
            "supported_examples": device_check.supported_device_examples(),
        }

    @app.get("/v1/system/status", dependencies=auth)
    async def system_status():
        entries = manager.catalog_entries()
        available = device_check.available_devices()
        # GPU property queries and the models-dir size walk both touch
        # drivers/disk; run them off the event loop since the UI polls this.
        gpu, disk = await asyncio.gather(
            asyncio.to_thread(gpu_stats),
            asyncio.to_thread(disk_stats, settings.models_dir),
        )
        return {
            "memory": memory_stats(),
            "cpu": cpu_stats(),
            "gpu": gpu,
            "device": {
                "default": settings.device,
                "mock": manager.force_mock,
                "available": available,
                "suggestions": device_check.suggested_device_targets(available),
                "loaded": dict(manager.devices),
                "busy": manager.any_busy(),
            },
            "models": {
                "loaded": list(manager.engines.keys()),
                "count": len(manager.engines),
                "loading_count": manager.loading_count(),
                "available": entries,
            },
            "disk": {
                "models_dir": str(settings.models_dir.resolve()),
                **disk,
            },
            "metrics": manager.metrics_summary(),
            "events": manager.recent_events(),
        }

    # --- benchmarks --------------------------------------------------------

    def _benchmark_models_or_400(req: BenchmarkRunRequest) -> list[str]:
        requested = []
        if req.model:
            requested.append(req.model)
        if req.models:
            requested.extend(req.models)
        requested = [model_id for model_id in dict.fromkeys(requested) if model_id]
        if not requested:
            raise HTTPException(status_code=400, detail="Provide at least one benchmark model.")
        unknown = [model_id for model_id in requested if model_id not in manager.catalog]
        if unknown:
            raise HTTPException(status_code=404, detail=f"Unknown benchmark model '{unknown[0]}'")
        return requested

    def _benchmark_devices_or_400(req: BenchmarkRunRequest) -> list[str]:
        if not req.devices:
            raise HTTPException(status_code=400, detail="Provide at least one benchmark device.")
        devices = []
        for device in req.devices:
            devices.append(_normalize_device_or_400(device))
        return [device for device in dict.fromkeys(devices) if device]

    @app.post("/v1/benchmarks/run", dependencies=auth)
    async def run_benchmarks(req: BenchmarkRunRequest):
        model_ids = _benchmark_models_or_400(req)
        devices = _benchmark_devices_or_400(req)
        prompt = (req.prompt or DEFAULT_BENCHMARK_PROMPT).strip() or DEFAULT_BENCHMARK_PROMPT
        try:
            run = await run_benchmark_suite(
                manager,
                model_ids=model_ids,
                devices=devices,
                prompt=prompt,
                max_tokens=req.max_tokens,
                runs=req.runs,
            )
        except device_check.DeviceValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await asyncio.to_thread(benchmark_store.append, run)
        manager.emit_event(
            "info",
            f"Benchmarked {len(model_ids)} model(s) across {len(devices)} device target(s)",
        )
        return run

    @app.get("/v1/benchmarks", dependencies=auth)
    async def list_benchmarks():
        runs = await asyncio.to_thread(benchmark_store.list_runs)
        return {"object": "list", "data": list(reversed(runs))}

    @app.get("/v1/benchmarks/latest", dependencies=auth)
    async def latest_benchmark():
        run = await asyncio.to_thread(benchmark_store.latest)
        return {
            "run": run,
            "recommendation": run.get("recommendation") if run else None,
        }

    @app.delete("/v1/benchmarks", dependencies=auth)
    async def clear_benchmarks():
        deleted = await asyncio.to_thread(benchmark_store.clear)
        manager.emit_event("info", "Cleared benchmark results")
        return {"status": "cleared", "deleted_runs": deleted}

    # --- conversation export -----------------------------------------------

    @app.post("/v1/chat/export", dependencies=auth)
    async def export_chat(request: ChatExportRequest):
        if not request.messages:
            raise HTTPException(status_code=400, detail="No messages to export")

        now = datetime.now(UTC)
        ts_display = now.strftime("%Y-%m-%d %H:%M UTC")
        ts_file = now.strftime("%Y%m%d-%H%M%S")

        lines: list[str] = []
        lines.append(f"# Chat Export — {DISPLAY_NAME}")
        lines.append("")
        meta_parts = [f"**Exported:** {ts_display}"]
        if request.model:
            meta_parts.append(f"**Model:** {request.model}")
        if request.device:
            meta_parts.append(f"**Device:** {request.device}")
        lines.append(" · ".join(meta_parts))
        lines.append("")
        lines.append("---")
        lines.append("")

        try:
            exported_contents = await asyncio.to_thread(
                lambda: [multimodal.plain_text(msg.content).strip() for msg in request.messages]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        for msg, content in zip(request.messages, exported_contents, strict=True):
            role = (msg.role or "unknown").capitalize()
            if msg.role == "user":
                lines.append(f"### 🧑 {role}")
                lines.append("")
                for line in content.splitlines():
                    lines.append(f"> {line}")
            elif msg.role == "system":
                lines.append(f"### ⚙️ {role}")
                lines.append("")
                lines.append(f"_{content}_")
            else:
                lines.append(f"### ✨ {role}")
                lines.append("")
                lines.append(content)
            lines.append("")

        body = "\n".join(lines)
        filename = f"chat-export-{ts_file}.md"
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # --- chat completions --------------------------------------------------

    @app.post("/v1/chat/completions", dependencies=auth)
    async def chat_completions(request: ChatCompletionRequest):
        engine = _resolve_or_400(manager, request.model)
        if "embedding" in getattr(engine, "backend", "").lower():
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.model}' is an embedding model and cannot be used for text completions.",
            )
        _validate_generation_request(
            engine,
            request.model,
            [message.content for message in request.messages],
            request.lora_path,
        )
        cfg = manager.config_for(engine.model_id)
        max_context_len = cfg.max_context_len if cfg else 2048
        max_prompt_len = cfg.max_prompt_len if cfg else 1536

        use_tools = bool(request.tools) and request.tool_choice != "none"
        normalized_messages, prompt, prompt_tokens = await _build_prompt_off_thread(
            _normalize_and_build_chat_prompt,
            engine,
            request.messages,
            max_prompt_len,
            request.tools,
            request.tool_choice,
            use_tools,
        )
        try:
            params = _params_for(
                request.max_tokens,
                request.temperature,
                request.top_p,
                prompt_tokens,
                max_context_len,
                stop=chat_format.normalize_stop(request.stop),
                seed=request.seed,
                response_format=request.response_format,
                lora_path=request.lora_path,
                lora_alpha=request.lora_alpha,
            )
        except BaseException:
            multimodal.discard_prompt_context(prompt)
            raise

        if request.stream:
            req_id = request_id_var.get()
            return StreamingResponse(
                _stream_chat(engine, request, prompt, prompt_tokens, params, use_tools, req_id),
                media_type="text/event-stream",
                background=BackgroundTask(multimodal.discard_prompt_context, prompt),
            )
        return await _complete_chat(
            engine,
            request,
            prompt,
            prompt_tokens,
            params,
            use_tools,
            max_prompt_len,
            max_context_len,
            normalized_messages,
        )

    async def _complete_chat(
        engine,
        request,
        prompt,
        prompt_tokens,
        params,
        use_tools,
        max_prompt_len,
        max_context_len,
        normalized_messages,
    ):
        current_prompt = prompt
        try:
            MAX_RETRIES = 2
            start = time.perf_counter()
            text = ""
            completion_tokens = 0
            current_prompt_tokens = prompt_tokens

            for attempt in range(MAX_RETRIES + 1):
                result = await manager.generate(engine, current_prompt, params)
                text = result.text
                completion_tokens = result.completion_tokens
                if use_tools and tools.detect_incomplete_tool_call(text) and attempt < MAX_RETRIES:
                    logger.warning("Malformed tool call; retry %d/%d", attempt + 1, MAX_RETRIES)
                    retry_messages = list(normalized_messages) + [
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": tools.get_retry_prompt()},
                    ]
                    current_prompt, current_prompt_tokens = await _build_prompt_off_thread(
                        _build_normalized_chat_prompt,
                        engine,
                        retry_messages,
                        max_prompt_len,
                    )
                    normalized_messages = retry_messages
                    params = _params_for(
                        request.max_tokens,
                        request.temperature,
                        request.top_p,
                        current_prompt_tokens,
                        max_context_len,
                        stop=chat_format.normalize_stop(request.stop),
                        seed=request.seed,
                        response_format=request.response_format,
                        lora_path=request.lora_path,
                        lora_alpha=request.lora_alpha,
                    )
                    continue
                break

            tool_calls = None
            finish_reason = "stop"
            content: str | None = text
            if use_tools:
                remaining, parsed = tools.parse_tool_calls(text, request.tools)
                if parsed:
                    tool_calls = parsed
                    finish_reason = "tool_calls"
                    content = remaining or None
            elif params.stop:
                # Honor stop sequences (the runtime may not have applied them).
                truncated, hit = chat_format.truncate_at_stop(text, params.stop)
                if hit:
                    content = truncated
                    loop = asyncio.get_running_loop()
                    completion_tokens = await loop.run_in_executor(
                        None, engine.count_tokens, truncated
                    )

            latency = time.perf_counter() - start
            manager.record_request(
                engine.model_id, current_prompt_tokens, completion_tokens, latency
            )
            record_key_metrics(current_prompt_tokens, completion_tokens, latency)
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    ChatCompletionResponseChoice(
                        index=0,
                        message=ChatCompletionMessage(
                            role="assistant", content=content, tool_calls=tool_calls
                        ),
                        finish_reason=finish_reason,
                    )
                ],
                usage=UsageInfo(
                    prompt_tokens=current_prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=current_prompt_tokens + completion_tokens,
                ),
            )
        finally:
            multimodal.discard_prompt_context(current_prompt)

    async def _stream_chat(engine, request, prompt, prompt_tokens, params, use_tools, req_id):
        token = request_id_var.set(req_id)
        try:
            request_id = f"chatcmpl-{uuid.uuid4().hex}"
            start = time.perf_counter()

            def chunk(delta: dict, finish_reason=None) -> str:
                payload = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }
                return f"data: {json.dumps(payload)}\n\n"

            full_text = ""
            finish_reason = "stop"
            generation_failed = False
            stream_gen = manager.stream(engine, prompt, params)
            try:
                if use_tools:
                    # Tool detection needs the full output, so buffer then decide.
                    async for piece in stream_gen:
                        full_text += piece
                    remaining, parsed = tools.parse_tool_calls(full_text, request.tools)
                    if parsed:
                        finish_reason = "tool_calls"
                        yield chunk({"role": "assistant", "content": None})
                        for i, tc in enumerate(parsed):
                            yield chunk(
                                {
                                    "tool_calls": [
                                        {
                                            "index": i,
                                            "id": tc.id,
                                            "type": "function",
                                            "function": {
                                                "name": tc.function.name,
                                                "arguments": tc.function.arguments,
                                            },
                                        }
                                    ]
                                }
                            )
                    else:
                        yield chunk({"role": "assistant", "content": remaining or full_text})
                else:
                    # Real-time token streaming for normal chat, honoring stop sequences.
                    stopper = chat_format.StopStreamer(params.stop or [])
                    first = True
                    async for piece in stream_gen:
                        emit = stopper.feed(piece)
                        if emit:
                            full_text += emit
                            delta = (
                                {"role": "assistant", "content": emit}
                                if first
                                else {"content": emit}
                            )
                            first = False
                            yield chunk(delta)
                        if stopper.stopped:
                            finish_reason = "stop"
                            break
                    tail = stopper.flush()
                    if tail:
                        full_text += tail
                        yield chunk(
                            {"role": "assistant", "content": tail} if first else {"content": tail}
                        )
            except Exception as exc:  # noqa: BLE001 - report inline to the SSE client
                generation_failed = True
                logger.exception("Generation failed: %s", exc)
                yield chunk(
                    {
                        "content": (
                            "\n\n[error: generation failed; see server logs for the request ID]"
                        )
                    }
                )
            finally:
                # Promptly stop the worker + release the model lock if the client
                # disconnected, instead of waiting for the generator to be GC'd.
                await stream_gen.aclose()

            yield chunk({}, finish_reason=finish_reason)

            if not generation_failed:
                loop = asyncio.get_running_loop()
                completion_tokens = await loop.run_in_executor(None, engine.count_tokens, full_text)
                latency = time.perf_counter() - start
                manager.record_request(engine.model_id, prompt_tokens, completion_tokens, latency)
                record_key_metrics(prompt_tokens, completion_tokens, latency)

                if request.stream_options and request.stream_options.include_usage:
                    usage_payload = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": request.model,
                        "choices": [],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    }
                    yield f"data: {json.dumps(usage_payload)}\n\n"

            yield "data: [DONE]\n\n"
        finally:
            multimodal.discard_prompt_context(prompt)
            request_id_var.reset(token)

    # --- embeddings API ----------------------------------------------------

    @app.post("/v1/embeddings", dependencies=auth, response_model=EmbeddingResponse)
    async def create_embeddings(request: EmbeddingRequest):
        engine = _resolve_or_400(manager, request.model)
        if "embedding" not in getattr(engine, "backend", "").lower():
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.model}' is not an embedding model.",
            )

        inputs = [request.input] if isinstance(request.input, str) else request.input
        if not inputs:
            raise HTTPException(status_code=400, detail="Input cannot be empty.")

        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            embeddings_list = await loop.run_in_executor(None, engine.embed, inputs)
        except Exception as exc:
            logger.exception("Embedding generation failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="Embedding inference failed; see server logs for the request ID.",
            ) from exc

        prompt_tokens = await asyncio.to_thread(
            lambda: sum(engine.count_tokens(text) for text in inputs)
        )

        latency = time.perf_counter() - start
        manager.record_request(engine.model_id, prompt_tokens, 0, latency)
        record_key_metrics(prompt_tokens, 0, latency)

        data = []
        for idx, emb in enumerate(embeddings_list):
            encoded = encode_embedding(emb, request.encoding_format)
            data.append(
                EmbeddingResponseData(
                    object="embedding",
                    index=idx,
                    embedding=encoded,
                )
            )

        return EmbeddingResponse(
            object="list",
            data=data,
            model=request.model,
            usage=EmbeddingResponseUsage(
                prompt_tokens=prompt_tokens,
                total_tokens=prompt_tokens,
            ),
        )

    # --- responses API (n8n) ----------------------------------------------

    @app.post("/v1/responses", dependencies=auth)
    async def create_response(request: ResponseRequest):
        engine = _resolve_or_400(manager, request.model)
        if "embedding" in getattr(engine, "backend", "").lower():
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.model}' is an embedding model and cannot be used for response generation.",
            )
        response_contents = (
            [message.get("content") for message in request.input if isinstance(message, dict)]
            if isinstance(request.input, list)
            else []
        )
        _validate_generation_request(engine, request.model, response_contents, request.lora_path)
        cfg = manager.config_for(engine.model_id)
        max_context_len = cfg.max_context_len if cfg else 2048
        max_prompt_len = cfg.max_prompt_len if cfg else 1536

        prompt, prompt_tokens = await _build_prompt_off_thread(
            _normalize_and_build_response_prompt,
            engine,
            request.input,
            request.instructions,
            max_prompt_len,
        )
        try:
            params = _params_for(
                request.max_output_tokens,
                request.temperature,
                1.0,
                prompt_tokens,
                max_context_len,
                lora_path=request.lora_path,
                lora_alpha=request.lora_alpha,
            )
        except BaseException:
            multimodal.discard_prompt_context(prompt)
            raise

        response_id = f"resp-{uuid.uuid4().hex}"
        msg_id = f"msg-{uuid.uuid4().hex}"

        if request.stream:
            return StreamingResponse(
                _stream_response(
                    engine, request, prompt, prompt_tokens, params, response_id, msg_id
                ),
                media_type="text/event-stream",
                background=BackgroundTask(multimodal.discard_prompt_context, prompt),
            )

        start = time.perf_counter()
        try:
            result = await manager.generate(engine, prompt, params)
        finally:
            multimodal.discard_prompt_context(prompt)
        latency = time.perf_counter() - start
        manager.record_request(engine.model_id, prompt_tokens, result.completion_tokens, latency)
        record_key_metrics(prompt_tokens, result.completion_tokens, latency)
        return ResponseObject(
            id=response_id,
            created_at=int(time.time()),
            model=request.model,
            output=[
                ResponseOutputMessage(
                    id=msg_id,
                    content=[{"type": "output_text", "text": result.text}],
                )
            ],
        )

    async def _stream_response(engine, request, prompt, prompt_tokens, params, response_id, msg_id):
        """SSE stream for the Responses API, using OpenAI Responses event names."""
        created = int(time.time())
        start = time.perf_counter()

        def event(type_name: str, payload: dict) -> str:
            return f"event: {type_name}\ndata: {json.dumps(payload)}\n\n"

        def response_obj(text: str, status: str) -> dict:
            return {
                "id": response_id,
                "object": "response",
                "created_at": created,
                "model": request.model,
                "status": status,
                "output": [
                    {
                        "type": "message",
                        "id": msg_id,
                        "status": status,
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            }

        yield event(
            "response.created",
            {"type": "response.created", "response": response_obj("", "in_progress")},
        )

        full_text = ""
        generation_failed = False
        stream_gen = manager.stream(engine, prompt, params)
        try:
            async for piece in stream_gen:
                full_text += piece
                yield event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "item_id": msg_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": piece,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - report inline to the SSE client
            generation_failed = True
            logger.exception("Responses generation failed: %s", exc)
            yield event(
                "response.error",
                {
                    "type": "response.error",
                    "message": "Generation failed; see server logs for the request ID.",
                },
            )
        finally:
            await stream_gen.aclose()
            multimodal.discard_prompt_context(prompt)

        if generation_failed:
            yield "data: [DONE]\n\n"
            return

        loop = asyncio.get_running_loop()
        completion_tokens = await loop.run_in_executor(None, engine.count_tokens, full_text)
        latency = time.perf_counter() - start
        manager.record_request(engine.model_id, prompt_tokens, completion_tokens, latency)
        record_key_metrics(prompt_tokens, completion_tokens, latency)

        yield event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "item_id": msg_id,
                "output_index": 0,
                "content_index": 0,
                "text": full_text,
            },
        )
        yield event(
            "response.completed",
            {"type": "response.completed", "response": response_obj(full_text, "completed")},
        )
        yield "data: [DONE]\n\n"

    return app


# Module-level app for `uvicorn app.server:app` (settings from environment).
# Guarded to avoid side effects on import (tests, CLI, etc.).
def _create_default_app() -> FastAPI:
    _load_dotenv()
    return create_app(Settings.from_env())


class _LazyApp:
    """Delay app creation until uvicorn actually accesses the module attribute."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def _get(self) -> FastAPI:
        if self._app is None:
            self._app = _create_default_app()
        return self._app

    def __getattr__(self, name: str):
        return getattr(self._get(), name)

    def __call__(self, *args, **kwargs):
        return self._get()(*args, **kwargs)


app = _LazyApp()


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    base = Settings.from_env()

    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} server")
    parser.add_argument("--model", help="Model id to auto-load on startup (from models.json)")
    parser.add_argument(
        "--device",
        help="Inference device expression, e.g. CPU, GPU, NPU, AUTO, AUTO:NPU,GPU,CPU",
    )
    parser.add_argument("--host", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Bind port (default 8000)")
    parser.add_argument(
        "--max-request-body-mb",
        type=int,
        help="Maximum HTTP request body in MiB (default 40)",
    )
    parser.add_argument("--mock", action="store_true", help="Force the mock engine (no OpenVINO)")
    parser.add_argument("--list", action="store_true", help="List catalog models and exit")
    parser.add_argument(
        "--check-devices", action="store_true", help="Show OpenVINO devices and exit"
    )
    parser.add_argument(
        "--auto-convert",
        action="store_true",
        help="Auto-convert/download models on startup or load",
    )
    parser.add_argument(
        "--benchmark", action="store_true", help="Run a hardware benchmark and exit"
    )
    parser.add_argument("--benchmark-model", help="Catalog model id to benchmark")
    parser.add_argument(
        "--benchmark-devices",
        default="CPU,GPU,NPU,AUTO",
        help="Benchmark devices, e.g. CPU,GPU,NPU,AUTO or CPU;AUTO:NPU,GPU,CPU",
    )
    parser.add_argument(
        "--benchmark-runs", type=int, default=1, help="Generation runs per model/device"
    )
    parser.add_argument(
        "--benchmark-max-tokens", type=int, default=64, help="Generated token limit per run"
    )
    args = parser.parse_args(argv)

    if args.list:
        catalog = model_manager.registry.load_catalog(base.models_file)
        if not catalog:
            print("No models found. Check models.json.")
            return 0
        print("\nAvailable models:")
        print("-" * 70)
        for model_id, cfg in catalog.items():
            print(f"  {model_id:28} {cfg.name}")
            print(f"  {'':28} {cfg.description}")
            print(
                f"  {'':28} source: {cfg.source_model or '(none)'}  device: {cfg.recommended_device}"
            )
            print()
        return 0

    if args.check_devices:
        available = device_check.available_devices()
        print(f"OpenVINO available: {device_check.is_openvino_available()}")
        print(f"Devices: {', '.join(available) if available else '(none detected)'}")
        for d in device_check.device_details():
            print(f"  {d['device']}: {d['full_name']}")
        suggestions = device_check.suggested_device_targets(available)
        if suggestions:
            print("Suggested advanced targets:")
            for item in suggestions:
                suffix = " (experimental)" if item["experimental"] else ""
                print(f"  {item['device']}{suffix}: {item['note']}")
        return 0

    if args.mock:
        os.environ["OV_LLM_MOCK"] = "1"

    if args.benchmark:
        if not args.benchmark_model:
            parser.error("--benchmark requires --benchmark-model")
        from runtime import benchmark_runner

        bench_args = [
            "--benchmark-model",
            args.benchmark_model,
            "--benchmark-devices",
            args.benchmark_devices,
            "--runs",
            str(args.benchmark_runs),
            "--max-tokens",
            str(args.benchmark_max_tokens),
        ]
        if args.mock:
            bench_args.append("--mock")
        return benchmark_runner.main(bench_args)

    if args.device is not None:
        try:
            device = device_check.validate_device_expression(args.device)
        except device_check.DeviceValidationError as exc:
            parser.error(str(exc))
    else:
        device = None

    overrides = {
        "device": device,
        "host": args.host,
        "port": args.port,
        "max_request_body_mb": args.max_request_body_mb,
        "default_model": args.model,
        "force_mock": True if args.mock else None,
        "auto_convert": True if args.auto_convert else None,
    }
    resolved = base.replace(**overrides)
    if resolved.max_request_body_mb < 1:
        parser.error("--max-request-body-mb must be at least 1")

    import uvicorn

    logger.info("Visit http://localhost:%d", resolved.port)
    if resolved.host == "0.0.0.0":
        logger.warning(
            "Binding to 0.0.0.0 exposes the server on your LAN. Use a trusted network only."
        )

    uvicorn.run(create_app(resolved), host=resolved.host, port=resolved.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
