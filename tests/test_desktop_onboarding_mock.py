import json
import time

from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.onboarding_routes import register_onboarding_routes
from app.onboarding_service import OnboardingService
from app.onboarding_state import OnboardingStateStore
from app.paths import RuntimePaths
from app.server import create_app


def test_fresh_mock_onboarding_reaches_chat_and_persists_completion(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    models_file = config_dir / "models.json"
    models_file.write_text((BASE_DIR / "models.json").read_text(encoding="utf-8"), encoding="utf-8")
    paths = RuntimePaths(
        resource_root=BASE_DIR,
        data_root=tmp_path,
        config_dir=config_dir,
        logs_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
        huggingface_cache_dir=tmp_path / "cache" / "huggingface",
        compiled_cache_dir=tmp_path / "cache" / "openvino",
        benchmarks_dir=tmp_path / "benchmarks",
        diagnostics_dir=tmp_path / "diagnostics",
        onboarding_dir=tmp_path / "onboarding",
        models_file=models_file,
        portable=True,
        packaged=False,
    )
    settings = Settings(
        models_file=models_file,
        models_dir=paths.models_dir,
        cache_dir=paths.compiled_cache_dir,
        benchmark_results_file=paths.benchmarks_dir / "benchmarks.json",
        force_mock=True,
        device="CPU",
    )
    app = create_app(settings)
    store = OnboardingStateStore(paths.onboarding_file)
    service = OnboardingService(
        settings=settings,
        manager=app.state.manager,
        paths=paths,
        state_store=store,
        endpoint_port=8765,
    )
    register_onboarding_routes(app, service=service, settings=settings)

    with TestClient(app) as client:
        documentation = client.get("/v1/onboarding/documentation", follow_redirects=False)
        assert documentation.status_code == 307
        assert documentation.headers["location"] == (
            "https://github.com/Quazmoz/InferBridge/blob/main/docs/FIRST_RUN.md"
        )

        status = client.get("/v1/onboarding/status").json()
        assert status["completed"] is False
        scan = client.get("/v1/onboarding/system-scan").json()
        assert scan["mock"] is True
        recommendation = client.get("/v1/onboarding/recommendation").json()

        started = client.post(
            "/v1/onboarding/prepare",
            json={
                "model_id": recommendation["model_id"],
                "device": recommendation["requested_device"],
                "confirm_license": True,
                "confirm_disk_requirement": True,
                "acknowledge_warnings": True,
                "trust_remote_code": False,
            },
        )
        assert started.status_code == 202, started.text
        job = started.json()
        deadline = time.time() + 10
        while job["status"] == "running" and time.time() < deadline:
            time.sleep(0.03)
            job = client.get(f"/v1/onboarding/preparation/{job['job_id']}").json()

        assert job["status"] == "ready", job
        assert job["benchmark"]["mock"] is True
        assert job["benchmark"]["success"] is True
        assert {row["stage"] for row in job["stages"]} >= {
            "downloading",
            "converting",
            "validating",
            "compiling",
            "loading",
            "benchmarking",
            "ready",
        }

        connection = client.post("/v1/onboarding/complete", json={"job_id": job["job_id"]}).json()
        assert connection["base_url"] == "http://127.0.0.1:8765/v1"
        assert connection["active_model_id"] == recommendation["model_id"]

        chat = client.post(
            "/v1/chat/completions",
            json={
                "model": recommendation["model_id"],
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 8,
            },
        )
        assert chat.status_code == 200

    persisted = json.loads(paths.onboarding_file.read_text(encoding="utf-8"))
    assert persisted["completed"] is True
    assert persisted["selected_model"] == recommendation["model_id"]
