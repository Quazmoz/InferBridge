from __future__ import annotations

import shutil
import subprocess

from app.huggingface_access_ui import HUGGINGFACE_ACCESS_JS


def test_huggingface_ui_never_persists_hf_credentials_in_browser_storage():
    assert "localStorage.setItem" not in HUGGINGFACE_ACCESS_JS
    assert "localStorage.setItem('hf" not in HUGGINGFACE_ACCESS_JS
    assert "/v1/huggingface/token" in HUGGINGFACE_ACCESS_JS
    assert 'type="password"' in HUGGINGFACE_ACCESS_JS


def test_huggingface_ui_has_required_recovery_actions():
    for text in (
        "Hugging Face access required",
        "Configure token",
        "Open model agreement",
        "Check access again",
    ):
        assert text in HUGGINGFACE_ACCESS_JS


def test_huggingface_ui_rewrites_structured_preflight_errors_for_existing_handlers():
    assert "hf_access: detail" in HUGGINGFACE_ACCESS_JS
    assert "detail: detail.message" in HUGGINGFACE_ACCESS_JS
    assert "load-model-btn" in HUGGINGFACE_ACCESS_JS


def test_huggingface_ui_javascript_parses(tmp_path):
    node = shutil.which("node")
    if node is None:
        return
    script = tmp_path / "huggingface-access.js"
    script.write_text(HUGGINGFACE_ACCESS_JS, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
