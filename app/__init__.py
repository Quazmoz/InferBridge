"""InferBridge application package.

A Windows-first, OpenAI-compatible local LLM and VLM server powered by OpenVINO
GenAI. Pure request/response logic remains importable in mock mode without an
OpenVINO runtime.
"""

from app.version import __version__

__all__ = [
    "__version__",
    "brand",
    "body_limit",
    "build_info",
    "chat_format",
    "config",
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
