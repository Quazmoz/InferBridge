from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_setup_never_writes_hugging_face_tokens_to_env() -> None:
    script = (ROOT / "setup" / "windows" / "setup_all.ps1").read_text(
        encoding="utf-8"
    )

    assert "cachedTokenFile" not in script
    assert "Paste your Hugging Face token" not in script
    assert "HF_TOKEN=$tokenToSet" not in script
    assert "setup never copies tokens into .env" in script
    assert "Windows stores the token with DPAPI" in script
    assert "advanced environment fallback" in script
