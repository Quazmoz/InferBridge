import asyncio
from types import SimpleNamespace

from app.model_preparation_timeouts import (
    PreparationTimeoutRecord,
    PreparationTimeouts,
    record_preparation_heartbeat,
    run_with_preparation_watchdog,
    timeout_message,
)


class FakeManager:
    def __init__(self, timeouts: PreparationTimeouts) -> None:
        self._preparation_timeouts = timeouts
        self._preparation_watchdog_states = {}
        self._preparation_timeout_records = {}
        self.progress = {"demo": {"operation_id": "convert-demo"}}
        self.catalog = {"demo": SimpleNamespace(name="Demo Model")}
        self.published: list[PreparationTimeoutRecord] = []


def _heartbeat(manager: FakeManager, phase: str, message: str) -> None:
    record_preparation_heartbeat(
        manager,
        "demo",
        phase,
        message,
        operation_id="convert-demo",
    )


def test_slow_active_conversion_is_not_timed_out() -> None:
    async def scenario() -> None:
        manager = FakeManager(
            PreparationTimeouts(
                download_stall_seconds=0.12,
                conversion_stall_seconds=0.12,
                finalization_stall_seconds=0.12,
                loading_seconds=0.12,
                compilation_seconds=0.12,
                poll_seconds=0.005,
            )
        )

        async def operation() -> str:
            for _ in range(8):
                _heartbeat(manager, "downloading", "Downloading model files…")
                await asyncio.sleep(0.02)
            return "complete"

        result = await run_with_preparation_watchdog(
            manager,
            "demo",
            "convert",
            operation,
            on_timeout=manager.published.append,
        )

        assert result == "complete"
        assert manager.published == []
        assert manager._preparation_timeout_records == {}

    asyncio.run(scenario())


def test_genuinely_stalled_conversion_is_cancelled_and_classified() -> None:
    async def scenario() -> None:
        manager = FakeManager(
            PreparationTimeouts(
                download_stall_seconds=0.05,
                conversion_stall_seconds=0.05,
                finalization_stall_seconds=0.05,
                loading_seconds=1.0,
                compilation_seconds=1.0,
                poll_seconds=0.005,
            )
        )

        async def operation() -> None:
            _heartbeat(manager, "converting", "Converting model to OpenVINO IR…")
            await asyncio.sleep(1.0)

        result = await run_with_preparation_watchdog(
            manager,
            "demo",
            "convert",
            operation,
            on_timeout=manager.published.append,
        )

        assert result is None
        assert len(manager.published) == 1
        record = manager.published[0]
        assert record.stage == "conversion"
        assert record.inactivity_timeout is True
        assert record.cleanup_pending is False
        assert record.last_progress_at > 0

    asyncio.run(scenario())


def test_compilation_uses_total_stage_deadline_even_with_parent_heartbeats() -> None:
    async def scenario() -> None:
        manager = FakeManager(
            PreparationTimeouts(
                download_stall_seconds=1.0,
                conversion_stall_seconds=1.0,
                finalization_stall_seconds=1.0,
                loading_seconds=1.0,
                compilation_seconds=0.08,
                poll_seconds=0.005,
            )
        )

        async def operation() -> None:
            for _ in range(20):
                _heartbeat(manager, "loading", "Still compiling Demo Model for CPU…")
                await asyncio.sleep(0.015)

        result = await run_with_preparation_watchdog(
            manager,
            "demo",
            "load",
            operation,
            on_timeout=manager.published.append,
        )

        assert result is None
        assert len(manager.published) == 1
        record = manager.published[0]
        assert record.stage == "compilation"
        assert record.inactivity_timeout is False
        assert record.elapsed_seconds >= record.timeout_seconds

    asyncio.run(scenario())


def test_loading_wait_has_a_separate_total_deadline() -> None:
    async def scenario() -> None:
        manager = FakeManager(
            PreparationTimeouts(
                download_stall_seconds=1.0,
                conversion_stall_seconds=1.0,
                finalization_stall_seconds=1.0,
                loading_seconds=0.08,
                compilation_seconds=1.0,
                poll_seconds=0.005,
            )
        )

        async def operation() -> None:
            for _ in range(20):
                _heartbeat(manager, "queued", "Waiting for another model preparation…")
                await asyncio.sleep(0.015)

        result = await run_with_preparation_watchdog(
            manager,
            "demo",
            "load",
            operation,
            on_timeout=manager.published.append,
        )

        assert result is None
        assert len(manager.published) == 1
        record = manager.published[0]
        assert record.stage == "loading"
        assert record.inactivity_timeout is False
        assert record.elapsed_seconds >= record.timeout_seconds

    asyncio.run(scenario())


def test_timeout_message_reports_stage_timestamp_and_preservation() -> None:
    record = PreparationTimeoutRecord(
        operation_kind="convert",
        operation_id="convert-demo",
        stage="download",
        timeout_seconds=600,
        elapsed_seconds=601,
        last_progress_at=1_787_000_000,
        timed_out_at=1_787_000_601,
        inactivity_timeout=True,
        task_identity=1,
    )

    message = timeout_message(record, "Demo Model")

    assert "during download" in message
    assert "Last successful progress:" in message
    assert "Downloaded cache entries" in message
    assert "Resume preparation" in message
