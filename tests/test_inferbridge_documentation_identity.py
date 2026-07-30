from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent


def test_readme_uses_canonical_inferbridge_identity_and_valid_local_links():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.startswith("# InferBridge\n")
    assert "Local AI for Intel hardware. Powered by OpenVINO GenAI." in readme
    assert "https://github.com/Quazmoz/InferBridge.git" in readme
    assert "docs/INFERBRIDGE_MIGRATION.md" in readme
    assert "docs/INFERBRIDGE_IDENTITY_INVENTORY.md" in readme
    assert "This branch rebrands the product as InferBridge" not in readme

    # Historical 0.6.1 links and filenames remain accurate by design.
    assert "Quazmoz/openvino-windows-llm/releases/download/v0.6.1" in readme
    assert "OpenVINO-Windows-LLM-0.6.1-windows-x64-installer.exe" in readme

    missing: list[str] = []
    for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", readme):
        value = target.strip().split("#", 1)[0].split("?", 1)[0]
        if not value or value.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        resolved = (ROOT / unquote(value)).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            missing.append(f"unsafe path: {target}")
            continue
        if not resolved.exists():
            missing.append(target)
    assert not missing, f"README contains missing local targets: {missing}"


def test_current_docs_do_not_present_legacy_identity_as_canonical():
    current_docs = [
        "README.md",
        "QUICKSTART.md",
        "CONTRIBUTING.md",
        "OPENWEBUI.md",
        "docs/API_CONTRACT.md",
        "docs/DIAGNOSTICS.md",
        "docs/FEDORA.md",
        "docs/MODEL_LIBRARY.md",
        "docs/PACKAGING_RELEASE.md",
        "docs/RELEASE_PROCESS.md",
        "docs/UBUNTU.md",
        ".claude/skills/use_memoryops.md",
        ".gemini/skills/use_memoryops.md",
    ]
    forbidden = [
        "git clone https://github.com/Quazmoz/openvino-windows-llm.git",
        "cd openvino-windows-llm",
        "openvino-windows-llm-diagnostics-",
        (
            "https://github.com/Quazmoz/openvino-windows-llm/releases/latest/download/"
            "model-library-manifest.json"
        ),
        "OpenVINO Windows LLM implements",
        "This branch rebrands the product as InferBridge",
    ]

    failures: list[str] = []
    for relative in current_docs:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for value in forbidden:
            if value in text:
                failures.append(f"{relative}: {value}")
    assert not failures, "Legacy identity remains canonical in current docs:\n" + "\n".join(
        failures
    )


def test_package_metadata_points_to_canonical_repository():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    # The distribution name remains stable for pip upgrade compatibility.
    assert project["name"] == "openvino-windows-llm"
    assert project["description"].startswith("InferBridge is a Windows-first local AI server")
    assert project["urls"] == {
        "Homepage": "https://github.com/Quazmoz/InferBridge",
        "Repository": "https://github.com/Quazmoz/InferBridge",
        "Documentation": "https://github.com/Quazmoz/InferBridge#documentation",
        "Issues": "https://github.com/Quazmoz/InferBridge/issues",
        "Releases": "https://github.com/Quazmoz/InferBridge/releases",
    }
    assert project["scripts"]["inferbridge"] == "app.server:main"
    assert project["scripts"]["inferbridge-desktop"] == "app.desktop_launcher:main"


def test_current_generated_names_match_inferbridge():
    diagnostics = (ROOT / "docs/DIAGNOSTICS.md").read_text(encoding="utf-8")
    model_library = (ROOT / "docs/MODEL_LIBRARY.md").read_text(encoding="utf-8")
    release_process = (ROOT / "docs/RELEASE_PROCESS.md").read_text(encoding="utf-8")

    assert "inferbridge-diagnostics-YYYYMMDD-HHMMSS.zip" in diagnostics
    assert (
        "https://github.com/Quazmoz/InferBridge/releases/latest/download/"
        "model-library-manifest.json"
    ) in model_library
    assert "InferBridge-<version>-windows-x64-installer.exe" in release_process
    assert "InferBridge-<version>-windows-x64-portable.zip" in release_process
