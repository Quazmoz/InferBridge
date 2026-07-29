import asyncio
from types import SimpleNamespace

from app.conversion_stream_safety import read_conversion_stream_safely


class Stream:
    def __init__(self, *lines: bytes):
        self.lines = list(lines) + [b""]

    async def readline(self):
        return self.lines.pop(0)


def test_late_output_does_not_replace_cancelled_state():
    calls = []
    manager = SimpleNamespace(
        status_overrides={"model": {"status": "cancelled"}},
        progress={"model": {"phase": "cancelled"}},
        _sanitize_progress_line=lambda value: value.strip(),
        _progress_from_converter_line=lambda *_args: ("converting", "Converting", 50),
        _set_progress=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    lines = asyncio.run(
        read_conversion_stream_safely(
            manager,
            "model",
            SimpleNamespace(name="Model"),
            Stream(b"late output\n"),
        )
    )
    assert lines == ["late output"]
    assert calls == []


def test_active_conversion_still_reports_progress():
    calls = []
    manager = SimpleNamespace(
        status_overrides={"model": {"status": "converting"}},
        progress={"model": {"phase": "converting"}},
        _sanitize_progress_line=lambda value: value.strip(),
        _progress_from_converter_line=lambda *_args: ("converting", "Converting", 50),
        _set_progress=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    asyncio.run(
        read_conversion_stream_safely(
            manager,
            "model",
            SimpleNamespace(name="Model"),
            Stream(b"50% converting\n"),
        )
    )
    assert len(calls) == 1
    assert calls[0][1]["percent"] == 50
