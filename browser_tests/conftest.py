from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright

from app.config import BASE_DIR, Settings
from app.server import create_app


@pytest.fixture(scope="session")
def inferbridge_url(tmp_path_factory) -> str:
    runtime_dir = tmp_path_factory.mktemp("browser-runtime")
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=runtime_dir / "models",
        cache_dir=runtime_dir / "cache",
        benchmark_results_file=runtime_dir / "benchmarks.json",
        force_mock=True,
        default_model=None,
    )
    app = create_app(settings)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="inferbridge-browser-server", daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("InferBridge browser test server did not start.")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=15)
    if thread.is_alive():
        raise RuntimeError("InferBridge browser test server did not stop cleanly.")


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture()
def page(browser: Browser) -> Page:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    yield page
    context.close()
