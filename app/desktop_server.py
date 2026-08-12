"""Packaged desktop server wrapper with onboarding, operations, and release APIs."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def owner_process_matches(
    owner_pid: int,
    owner_created_at: float,
    *,
    process_factory=None,
) -> bool:
    if owner_pid <= 0:
        return True
    try:
        if process_factory is None:
            import psutil

            process_factory = psutil.Process
        process = process_factory(owner_pid)
        if not process.is_running():
            return False
        if owner_created_at > 0:
            return abs(float(process.create_time()) - float(owner_created_at)) < 1.0
        return True
    except Exception:
        return False


def prepare_desktop_environment(
    *,
    portable: bool = False,
    data_dir: str | None = None,
    mock: bool = False,
) -> None:
    """Apply one explicit desktop launch mode without leaking stale process state."""

    os.environ["OV_LLM_DESKTOP"] = "1"
    if portable:
        os.environ["OV_LLM_PORTABLE"] = "1"
    else:
        os.environ.pop("OV_LLM_PORTABLE", None)
    if data_dir:
        os.environ["OV_LLM_DATA_DIR"] = str(Path(data_dir).expanduser())
    else:
        os.environ.pop("OV_LLM_DATA_DIR", None)
    if mock:
        os.environ["OV_LLM_MOCK"] = "1"
    else:
        os.environ.pop("OV_LLM_MOCK", None)


def _configure_file_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    log_path = logs_dir / "desktop.log"
    if any(getattr(handler, "baseFilename", None) == str(log_path) for handler in root.handlers):
        return
    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
    root.addHandler(handler)


def create_desktop_app(
    *,
    port: int,
    instance_nonce: str,
    control_token: str,
    owner_pid: int = 0,
    owner_created_at: float = 0.0,
    portable: bool = False,
    data_dir: str | None = None,
    mock: bool = False,
) -> Any:
    prepare_desktop_environment(portable=portable, data_dir=data_dir, mock=mock)

    from app.config import Settings
    from app.data_migrations import ensure_data_schema
    from app.desktop_onboarding import DesktopOnboardingService
    from app.desktop_operations import DesktopOperationsService
    from app.desktop_operations_routes import register_desktop_operations_routes
    from app.onboarding_routes import register_onboarding_routes
    from app.onboarding_state import OnboardingStateStore
    from app.paths import (
        ensure_data_root_writable,
        materialize_user_catalog,
        resolve_runtime_paths,
    )
    from app.release_routes import register_release_routes
    from app.runtime_health import register_runtime_health_routes
    from app.runtime_health_safety import HardenedRuntimeHealthService
    from app.storage_manager import StorageManagerService, register_storage_manager_routes
    from app.storage_root_safety import install_storage_root_safety
    from app.ui_composition import DESKTOP_CAPABILITY
    from app.ui_registry import activate

    # Filesystem safety is a backend contract, not a side effect of composing the UI.
    install_storage_root_safety()
    # The storage manager and runtime health center are always registered by
    # app.ui_composition. Activating the capability is what makes them render, and this is
    # the process that provides the routes they call. Import order no longer matters:
    # app.server resolves the composition per request rather than binding it at import.
    activate(DESKTOP_CAPABILITY)

    from app.server import create_app

    paths = resolve_runtime_paths(portable=portable, desktop=True)
    ensure_data_root_writable(paths)
    ensure_data_schema(paths)
    materialize_user_catalog(paths)
    _configure_file_logging(paths.logs_dir)
    os.environ.setdefault("HF_HOME", str(paths.huggingface_cache_dir))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(paths.huggingface_cache_dir / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(paths.huggingface_cache_dir / "transformers"))
    os.environ.setdefault("OV_CACHE_DIR", str(paths.compiled_cache_dir))

    settings = Settings.from_env().replace(host="127.0.0.1", port=port)
    state_store = OnboardingStateStore(paths.onboarding_file)
    state = state_store.load().state
    if state.get("completed") and not state.get("restart_requested"):
        selected_model = state.get("selected_model")
        selected_device = state.get("selected_device")
        settings = settings.replace(
            default_model=selected_model if selected_model else None,
            device=selected_device if selected_device else None,
        )

    app = create_app(settings)
    if app.state.manager.force_mock and not mock:
        raise RuntimeError(
            "OpenVINO GenAI could not be loaded by the packaged application. Run the desktop "
            "diagnostic command and reinstall a complete build. Mock mode is never enabled "
            "silently for a normal desktop launch."
        )
    onboarding = DesktopOnboardingService(
        settings=settings,
        manager=app.state.manager,
        paths=paths,
        state_store=state_store,
        endpoint_port=port,
    )
    operations = DesktopOperationsService(
        settings=settings,
        manager=app.state.manager,
        onboarding=onboarding,
        paths=paths,
        endpoint_port=port,
    )
    storage = StorageManagerService(
        settings=settings,
        manager=app.state.manager,
        paths=paths,
    )
    runtime_health = HardenedRuntimeHealthService(
        settings=settings,
        manager=app.state.manager,
        paths=paths,
        storage=storage,
    )
    app.state.desktop_paths = paths
    app.state.onboarding_service = onboarding
    app.state.desktop_operations_service = operations
    app.state.storage_manager_service = storage
    app.state.runtime_health_service = runtime_health
    app.state.shutting_down = False

    @app.middleware("http")
    async def desktop_shutdown_guard(request, call_next):
        if app.state.shutting_down and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"detail": "The desktop server is shutting down."},
                headers={"Retry-After": "5"},
            )
        return await call_next(request)

    register_onboarding_routes(app, service=onboarding, settings=settings)
    register_desktop_operations_routes(
        app,
        service=operations,
        settings=settings,
        instance_nonce=instance_nonce,
        control_token=control_token,
    )
    register_release_routes(app, paths=paths)
    register_storage_manager_routes(app, service=storage)
    register_runtime_health_routes(app, service=runtime_health)
    return app


def run_server(
    *,
    port: int,
    instance_nonce: str,
    control_token: str,
    owner_pid: int = 0,
    owner_created_at: float = 0.0,
    portable: bool = False,
    data_dir: str | None = None,
    mock: bool = False,
) -> int:
    import uvicorn

    app = create_desktop_app(
        port=port,
        instance_nonce=instance_nonce,
        control_token=control_token,
        portable=portable,
        data_dir=data_dir,
        mock=mock,
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=20,
    )
    server = uvicorn.Server(config)
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    monitor_stop = threading.Event()

    def monitor_owner() -> None:
        if owner_pid <= 0:
            return
        while not monitor_stop.wait(2.0):
            if owner_process_matches(owner_pid, owner_created_at):
                continue
            logging.getLogger("ov-llm.desktop").warning(
                "The tray owner process is no longer active; stopping the local server."
            )
            server.should_exit = True
            return

    monitor = threading.Thread(
        target=monitor_owner,
        name="ovllm-owner-monitor",
        daemon=True,
    )
    monitor.start()
    try:
        server.run()
    finally:
        monitor_stop.set()
        monitor.join(timeout=3)
    return 0 if server.started else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenVINO Windows LLM desktop server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--instance-nonce", default="")
    parser.add_argument("--control-token", default="")
    parser.add_argument("--owner-pid", type=int, default=0)
    parser.add_argument("--owner-created-at", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.port < 1 or args.port > 65535:
        parser.error("--port must be between 1 and 65535")
    nonce = args.instance_nonce or secrets.token_urlsafe(24)
    control = args.control_token or secrets.token_urlsafe(32)
    try:
        return run_server(
            port=args.port,
            instance_nonce=nonce,
            control_token=control,
            owner_pid=args.owner_pid,
            owner_created_at=args.owner_created_at,
            portable=args.portable,
            data_dir=args.data_dir,
            mock=args.mock,
        )
    except Exception:
        logging.getLogger("ov-llm.desktop").exception("Desktop server startup failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
