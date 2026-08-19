from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.model_library_routes import register_model_library_routes


class _RefreshService:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def refresh_official(self):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            # Give a concurrently scheduled request time to enter this method. Without
            # the route transaction lock both calls overlap and max_active becomes 2.
            await asyncio.sleep(0.05)
            return {"source": f"refresh-{self.calls}"}
        finally:
            self.active -= 1

    def snapshot(self):
        return {"schema_version": 1, "items": [], "count": 0}


def test_official_refresh_requests_are_serialized_on_one_app_instance():
    async def scenario() -> None:
        app = FastAPI()
        service = _RefreshService()
        app.state.settings = SimpleNamespace(api_key=None)
        app.state.model_library_service = service
        register_model_library_routes(app)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first, second = await asyncio.gather(
                client.post("/v1/model-library/refresh"),
                client.post("/v1/model-library/refresh"),
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert service.calls == 2
        assert service.max_active == 1

    asyncio.run(scenario())
