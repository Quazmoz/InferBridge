"""One-time InferBridge embedded UI and current-document finalizer."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def main() -> None:
    text = read("app/release_ui.py")
    if "from app.brand import DISPLAY_NAME" not in text:
        text = text.replace(
            '"""Dependency-free About and Updates UI extension."""\n',
            '"""Dependency-free About and Updates UI extension."""\n\nfrom app.brand import DISPLAY_NAME\n',
            1,
        )
    text = text.replace("OpenVINO Windows LLM", "__INFERBRIDGE_DISPLAY_NAME__")
    if "RELEASE_EXTENSION_JS = RELEASE_EXTENSION_JS.replace" not in text:
        text += (
            '\nRELEASE_EXTENSION_JS = RELEASE_EXTENSION_JS.replace('
            '"__INFERBRIDGE_DISPLAY_NAME__", DISPLAY_NAME)\n'
        )
    write("app/release_ui.py", text)

    text = read("app/onboarding_ui.py")
    if "from app.brand import DISPLAY_NAME" not in text:
        marker = "from app import ui_extension\n"
        if marker not in text:
            raise RuntimeError("Onboarding import marker not found")
        text = text.replace(
            marker,
            marker + "from app.brand import DISPLAY_NAME\nfrom app.version import __version__\n",
            1,
        )
    text = text.replace("OpenVINO Windows LLM", "__INFERBRIDGE_DISPLAY_NAME__")
    text = text.replace("Version 0.3.0", "Version __INFERBRIDGE_VERSION__")
    if "ONBOARDING_UI = ONBOARDING_UI.replace" not in text:
        text += (
            '\nONBOARDING_UI = ONBOARDING_UI.replace('
            '"__INFERBRIDGE_DISPLAY_NAME__", DISPLAY_NAME).replace('
            '"__INFERBRIDGE_VERSION__", __version__)\n'
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
