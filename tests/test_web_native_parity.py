"""Browser-side additions that match native WinUI capabilities."""

from __future__ import annotations

from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_web_exposes_top_p_like_native_settings():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="settings-top-p"' in html
    assert 'min="0" max="1" step="0.05"' in html
    assert "top_p: Number.isFinite(topP) ? topP : 1.0" in html
    assert "localStorage.setItem('ovllm.top_p.v1'" in html


def test_web_and_native_render_the_same_heading_levels():
    html = INDEX.read_text(encoding="utf-8")
    native = (
        INDEX.parents[1] / "windows-native" / "InferBridge.Native" / "ChatMarkdown.cs"
    ).read_text(encoding="utf-8")
    assert "line.match(/^(#{1,6})\\s+(.+)$/)" in html
    assert '@"^(#{1,6})\\s+(.+)$"' in native
