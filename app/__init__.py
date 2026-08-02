"""InferBridge application package.

A Windows-first, OpenAI-compatible local LLM and VLM server powered by OpenVINO
GenAI. Pure request/response logic remains importable in mock mode without an
OpenVINO runtime.
"""

from app.request_logging import install_request_log_filter
from app.version import __version__

install_request_log_filter()

__all__ = [
    "__version__",
    "brand",
    "body_limit",
    "build_info",
    "chat_format",
    "config",
    "context_budget",
    "context_budget_ui",
    "data_migrations",
    "desktop_controller",
    "desktop_launcher",
    "desktop_operations",
    "desktop_server",
    "diagnostics",
    "errors",
    "model_library",
    "model_library_routes",
    "model_library_ui",
    "model_manager",
    "model_recovery",
    "model_recovery_status",
    "model_recovery_ui",
    "model_registry",
    "multimodal",
    "onboarding_models",
    "onboarding_service",
    "onboarding_state",
    "openai_api",
    "paths",
    "rate_limit",
    "release_models",
    "release_routes",
    "request_logging",
    "server",
    "startup_registration",
    "telemetry",
    "tools",
    "tray_app",
    "tray_state",
    "ui_extension",
    "update_checker",
    "version",
]
