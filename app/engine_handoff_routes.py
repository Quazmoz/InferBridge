"""HTTP mapping for model lifecycle races detected below the route layer."""

from __future__ import annotations

import functools
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.engine_handoff_safety import ModelBusyError

_INSTALL_FLAG = "_ovllm_engine_handoff_routes_installed"


def register_engine_handoff_handlers(app: FastAPI) -> None:
    """Return a recoverable conflict when an unload loses a lock race."""

    if getattr(app.state, "engine_handoff_handler_registered", False):
        return

    @app.exception_handler(ModelBusyError)
    async def model_busy_error(_request: Request, exc: ModelBusyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)[:300]})

    app.state.engine_handoff_handler_registered = True


def install_engine_handoff_routes_extension() -> None:
    """Register the lifecycle handler on OpenVINO Windows LLM FastAPI apps."""

    if getattr(FastAPI, _INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_engine_handoff(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_engine_handoff_handlers(self)

    FastAPI.__init__ = init_with_engine_handoff  # type: ignore[method-assign]
    setattr(FastAPI, _INSTALL_FLAG, True)
