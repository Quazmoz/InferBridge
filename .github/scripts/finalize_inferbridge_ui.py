"""One-time InferBridge embedded UI and current-document finalizer."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"Expected text not found in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def main() -> None:
    replace(
        "app/release_ui.py",
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nfrom app.brand import DISPLAY_NAME\n",
    )
    text = read("app/release_ui.py")
    if "RELEASE_EXTENSION_JS = RELEASE_EXTENSION_JS.replace" not in text:
        text += '\nRELEASE_EXTENSION_JS = RELEASE_EXTENSION_JS.replace("OpenVINO Windows LLM", DISPLAY_NAME)\n'
    write("app/release_ui.py", text)

    replace(
        "app/onboarding_ui.py",
        "from app import ui_extension\n",
        "from app import ui_extension\nfrom app.brand import DISPLAY_NAME\nfrom app.version import __version__\n",
    )
    text = read("app/onboarding_ui.py")
    if "ONBOARDING_UI = ONBOARDING_UI.replace" not in text:
        text += (
            '\nONBOARDING_UI = ONBOARDING_UI.replace("OpenVINO Windows LLM", DISPLAY_NAME).replace('
            '"Version 0.3.0", f"Version {__version__}")\n'
        )
    write("app/onboarding_ui.py", text)

    for path in ("app/ui_quality.py", "app/ui_polish.py", "app/doctor_ui.py"):
        text = read(path)
        text = text.replace("OpenVINO Windows LLM", "InferBridge")
        text = text.replace("OpenVINO LLM process", "InferBridge process")
        write(path, text)

    text = read("web/index.html")
    text = text.replace("<title>OpenVINO LLM</title>", "<title>InferBridge</title>")
    text = text.replace(">OpenVINO LLM<", ">InferBridge<")
    text = text.replace(">OpenVINO GenAI<", ">Local AI for Intel hardware<")
    write("web/index.html", text)

    current_files = (
        "setup.bat",
        "setup.sh",
        "start_server.bat",
        "setup/linux/install_deps.sh",
        "setup/windows/setup_all.ps1",
        "QUICKSTART.md",
        "CONTRIBUTING.md",
        "OPENWEBUI.md",
        "docs/TRAY.md",
        "docs/PORTABLE.md",
        "docs/INSTALLER.md",
        "docs/DATA_PATHS.md",
        "docs/UPGRADE_ROLLBACK.md",
        "docs/DESKTOP_ARCHITECTURE.md",
        "docs/TROUBLESHOOTING_DESKTOP.md",
        "docs/UPDATE_POLICY.md",
        "docs/RELEASE_PROCESS.md",
        "docs/DIAGNOSTICS.md",
        "docs/MODEL_LIBRARY.md",
        "docs/VISION.md",
    )
    for path in current_files:
        target = ROOT / path
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8")
        text = text.replace("OpenVINO Windows LLM", "InferBridge")
        text = text.replace("OpenVINO LLM server", "InferBridge server")
        target.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
