"""Server configuration resolved from environment variables and desktop paths."""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.branding_ui import install_branding_extension
from app.chat_context_ui import install_chat_context_extension
from app.chat_guard_ui import install_chat_guard_extension
from app.chat_queue_ui import install_chat_queue_extension
from app.desktop_operations_ui import install_desktop_operations_ui_extension
from app.doctor_ui import install_system_doctor_extension
from app.engine_handoff_routes import install_engine_handoff_routes_extension
from app.gui_stability import install_gui_stability_extension
from app.header_overflow_ui import install_header_overflow_extension
from app.model_library_routes import install_model_library_routes_extension
from app.model_library_ui import install_model_library_ui_extension
from app.onboarding_ui import install_onboarding_ui_extension
from app.paths import resolve_runtime_paths
from app.progress_reliability import install_progress_ui_extension
from app.ui_polish import install_ui_polish_extension
from app.ui_quality import install_ui_quality_extension
from runtime.device_check import normalize_device
from runtime.npu_compat import install_openvino_genai_compat

# Install compatibility and UI composition before app.model_manager/app.server bind
# their imported engine and browser-injection functions. Desktop-only surfaces probe
# for their APIs and remain dormant in ordinary development-server mode.
install_openvino_genai_compat()
install_model_library_routes_extension()
install_engine_handoff_routes_extension()
install_branding_extension()
install_chat_context_extension()
install_chat_queue_extension()
install_chat_guard_extension()
install_ui_polish_extension()
install_ui_quality_extension()
install_system_doctor_extension()
install_header_overflow_extension()
install_progress_ui_extension()
install_onboarding_ui_extension()
install_model_library_ui_extension()
install_desktop_operations_ui_extension()
install_gui_stability_extension()

logger = logging.getLogger("ov-llm.config")
_RUNTIME_PATHS = resolve_runtime_paths()
BASE_DIR = _RUNTIME_PATHS.resource_root

VALID_DEVICES = ("CPU", "GPU", "NPU", "AUTO")
_TRUTHY = {"1", "true", "yes", "on"}


def _resolve(path_str: str) -> Path:
    """Resolve explicit relative paths against packaged resources or the repo root."""

    path = Path(path_str).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path)


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read a bounded integer environment value without making startup brittle."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Config: %s=%r is not an integer; using %d", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Config: %s=%r is below %d; using %d", name, raw, minimum, default)
        return default
    if maximum is not None and value > maximum:
        logger.warning("Config: %s=%r exceeds %d; using %d", name, raw, maximum, default)
        return default
    return value


def _device_env(name: str = "OV_LLM_DEVICE", default: str = "NPU") -> str:
    """Read and normalize a device target, falling back after malformed overrides."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return normalize_device(default)
    try:
        return normalize_device(raw)
    except ValueError:
        logger.warning("Config: %s=%r is invalid; using %s", name, raw, default)
        return normalize_device(default)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings. Use :meth:`replace` to apply CLI overrides."""

    host: str = "127.0.0.1"
    port: int = 8000
    device: str = "NPU"
    models_file: Path = _RUNTIME_PATHS.models_file
    models_dir: Path = _RUNTIME_PATHS.models_dir
    cache_dir: Path = _RUNTIME_PATHS.compiled_cache_dir
    benchmark_results_file: Path = _RUNTIME_PATHS.benchmarks_dir / "benchmarks.json"
    default_model: str | None = None
    api_key: str | None = None
    force_mock: bool = False
    auto_convert: bool = False
    cors_origins: str = ""
    rate_limit: int = 0
    max_request_body_mb: int = 40

    def __post_init__(self) -> None:
        from app.conversion_stream_safety import install_conversion_stream_safety
        from app.desktop_model_paths import install_desktop_model_path_extension
        from app.desktop_shutdown_safety import install_desktop_shutdown_safety
        from app.engine_handoff_safety import install_engine_handoff_safety
        from app.lifecycle_safety import install_model_lifecycle_safety
        from app.model_load_target import install_model_load_target_routing
        from app.structured_progress import install_structured_progress_protocol

        install_desktop_model_path_extension()
        install_model_load_target_routing()
        install_engine_handoff_safety()
        install_conversion_stream_safety()
        # The structured reader intentionally installs after the stream-safety layer
        # and retains its terminal-state protection while adding schema validation.
        install_structured_progress_protocol()
        install_model_lifecycle_safety()
        install_desktop_shutdown_safety()

    @classmethod
    def from_env(cls) -> Settings:
        runtime_paths = resolve_runtime_paths()
        return cls(
            host=os.environ.get("OV_LLM_HOST", "127.0.0.1"),
            port=_int_env("OV_LLM_PORT", 8000, minimum=1, maximum=65535),
            device=_device_env(),
            models_file=_resolve(
                os.environ.get("OV_LLM_MODELS_FILE", str(runtime_paths.models_file))
            ),
            models_dir=_resolve(os.environ.get("OV_LLM_MODELS_DIR", str(runtime_paths.models_dir))),
            cache_dir=_resolve(
                os.environ.get("OV_LLM_CACHE_DIR", str(runtime_paths.compiled_cache_dir))
            ),
            benchmark_results_file=_resolve(
                os.environ.get(
                    "OV_LLM_BENCHMARK_RESULTS",
                    str(runtime_paths.benchmarks_dir / "benchmarks.json"),
                )
            ),
            default_model=(os.environ.get("OV_LLM_MODEL") or "").strip() or None,
            api_key=(os.environ.get("OV_LLM_API_KEY") or "").strip() or None,
            force_mock=_bool_env("OV_LLM_MOCK"),
            auto_convert=_bool_env("OV_LLM_AUTO_CONVERT"),
            cors_origins=os.environ.get("OV_LLM_CORS_ORIGINS", ""),
            rate_limit=_int_env("OV_LLM_RATE_LIMIT", 0, minimum=0),
            max_request_body_mb=_int_env("OV_LLM_MAX_REQUEST_BODY_MB", 40, minimum=1),
        )

    def replace(self, **changes) -> Settings:
        clean = {key: value for key, value in changes.items() if value is not None}
        return dataclasses.replace(self, **clean)

    def validate(self, catalog: dict | None = None) -> list[str]:
        warnings: list[str] = []

        if not self.models_file.exists():
            warnings.append(f"Models catalog not found at {self.models_file}")
        if self.port < 1 or self.port > 65535:
            warnings.append(f"Port {self.port} is out of the valid range (1-65535)")
        if self.default_model and catalog is not None and self.default_model not in catalog:
            warnings.append(
                f"Default model '{self.default_model}' is not in the catalog. "
                f"Available: {', '.join(catalog) or '(none)'}"
            )
        if self.rate_limit < 0:
            warnings.append(f"Rate limit {self.rate_limit} is negative; treating as disabled (0)")
        if self.max_request_body_mb < 1:
            warnings.append(
                f"Request body limit {self.max_request_body_mb} MiB is invalid; use at least 1 MiB"
            )

        origins = {origin.strip() for origin in self.cors_origins.split(",") if origin.strip()}
        if "*" in origins and not self.api_key:
            warnings.append(
                "Wildcard CORS allows arbitrary websites to call the local API. Set explicit "
                "OV_LLM_CORS_ORIGINS values or configure OV_LLM_API_KEY."
            )
        if self.host.strip() in {"0.0.0.0", "::"} and not self.api_key:
            warnings.append(
                f"OV_LLM_HOST is set to {self.host!r}, which can expose the server beyond "
                "localhost, but OV_LLM_API_KEY is not set. Set OV_LLM_API_KEY before "
                "exposing the server beyond a trusted local machine/network."
            )

        for warning in warnings:
            logger.warning("Config: %s", warning)
        return warnings
