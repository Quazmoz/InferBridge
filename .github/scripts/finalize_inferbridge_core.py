"""One-time InferBridge desktop, server, diagnostics, and runtime-hook finalizer."""

from __future__ import annotations

import re
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
        "app/desktop_launcher.py",
        'from typing import BinaryIO\n\n_APP_TITLE = "OpenVINO Windows LLM"',
        'from typing import BinaryIO\n\nfrom app.brand import DISPLAY_NAME\n\n_APP_TITLE = DISPLAY_NAME',
    )
    replace(
        "app/desktop_launcher.py",
        'argparse.ArgumentParser(description="OpenVINO Windows LLM desktop tray launcher")',
        'argparse.ArgumentParser(description=f"{DISPLAY_NAME} desktop tray launcher")',
    )
    replace(
        "app/tray_support.py",
        'from app.tray_state import TrayPhase\n\nAPP_TITLE = "OpenVINO Windows LLM"',
        'from app.brand import DISPLAY_NAME\nfrom app.tray_state import TrayPhase\n\nAPP_TITLE = DISPLAY_NAME',
    )
    replace(
        "app/tray_menu.py",
        'f"OpenVINO Windows LLM {__version__}\\n\\n"',
        'f"{APP_TITLE} {__version__}\\n\\n"',
    )

    replace(
        "app/server.py",
        "from app.body_limit import RequestBodyLimitMiddleware\n",
        "from app.body_limit import RequestBodyLimitMiddleware\nfrom app.brand import DISPLAY_NAME\n",
    )
    for old, new in {
        'logger.info("Starting OpenVINO Windows LLM server — %s", mode)': 'logger.info("Starting %s server — %s", DISPLAY_NAME, mode)',
        'FastAPI(title="OpenVINO Windows LLM", version=__version__, lifespan=lifespan)': 'FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=lifespan)',
        'lines.append("# Chat Export — OpenVINO LLM")': 'lines.append(f"# Chat Export — {DISPLAY_NAME}")',
        'argparse.ArgumentParser(description="OpenVINO Windows LLM server")': 'argparse.ArgumentParser(description=f"{DISPLAY_NAME} server")',
    }.items():
        replace("app/server.py", old, new)

    for path in ("app/engine_handoff_routes.py", "app/model_library_routes.py"):
        text = read(path)
        import_line = "from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME"
        if import_line not in text:
            text = text.replace(
                "from __future__ import annotations\n",
                f"from __future__ import annotations\n\n{import_line}\n",
                1,
            )
        old = 'getattr(self, "title", "") == "OpenVINO Windows LLM"'
        if old not in text:
            raise RuntimeError(f"Expected title guard not found in {path}")
        write(
            path,
            text.replace(
                old,
                'getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}',
            ),
        )

    replace(
        "app/diagnostics.py",
        "from app import __version__\n",
        "from app import __version__\nfrom app.brand import DISPLAY_NAME\n",
    )
    text = read("app/diagnostics.py").replace(
        "openvino-windows-llm-diagnostics-", "inferbridge-diagnostics-"
    )
    old = 'return {\n            "application_version": __version__,'
    if old not in text:
        raise RuntimeError("Diagnostics application payload marker not found")
    write(
        "app/diagnostics.py",
        text.replace(
            old,
            'return {\n            "application_name": DISPLAY_NAME,\n            "application_version": __version__,',
            1,
        ),
    )

    text = read("packaging/runtime_hook.py")
    text = text.replace('_APP_TITLE = "OpenVINO Windows LLM"', '_APP_TITLE = "InferBridge"')
    text = text.replace(
        "_RUNTIME_FAILURE_EXIT_CODE = 12",
        '_CURRENT_DATA_DIR_NAME = "InferBridge"\n_LEGACY_DATA_DIR_NAME = "OpenVINOWindowsLLM"\n_RUNTIME_FAILURE_EXIT_CODE = 12',
        1,
    )
    old_root = '        root = os.path.join(local_app_data, "OpenVINOWindowsLLM")'
    if old_root not in text:
        raise RuntimeError("Legacy runtime-hook data root marker not found")
    text = text.replace(
        old_root,
        "        current = os.path.join(local_app_data, _CURRENT_DATA_DIR_NAME)\n"
        "        legacy = os.path.join(local_app_data, _LEGACY_DATA_DIR_NAME)\n"
        "        root = current if os.path.isdir(current) or not os.path.isdir(legacy) else legacy",
        1,
    ).replace("Close OpenVINO Windows LLM", "Close InferBridge")
    write("packaging/runtime_hook.py", text)

    text = read("app/startup_registration.py")
    text, changes = re.subn(
        r'location: str = f"HKCU.*CURRENT_VALUE_NAME\}"',
        r'location: str = f"HKCU\\{RUN_KEY}\\{CURRENT_VALUE_NAME}"',
        text,
        count=1,
    )
    if changes != 1:
        raise RuntimeError("Startup registry location marker not found")
    write("app/startup_registration.py", text)


if __name__ == "__main__":
    main()
