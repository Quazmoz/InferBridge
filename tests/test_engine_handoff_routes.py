from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.engine_handoff_routes import (
    install_engine_handoff_routes_extension,
    register_engine_handoff_handlers,
)
from app.engine_handoff_safety import ModelBusyError


def test_busy_model_error_is_returned_as_http_conflict() -> None:
    app = FastAPI()
    register_engine_handoff_handlers(app)

    @app.get("/busy")
    async def busy():
        raise ModelBusyError("Model is serving a request.")

    with TestClient(app) as client:
        response = client.get("/busy")

    assert response.status_code == 409
    assert response.json() == {"detail": "Model is serving a request."}


def test_openvino_app_registration_is_automatic_and_idempotent() -> None:
    install_engine_handoff_routes_extension()
    install_engine_handoff_routes_extension()

    app = FastAPI(title="OpenVINO Windows LLM")

    assert app.exception_handlers[ModelBusyError]
    assert app.state.engine_handoff_handler_registered is True
