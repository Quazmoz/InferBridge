"""One-time InferBridge desktop, server, diagnostics, and runtime-hook finalizer."""

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
        if new in text:
            return
        raise RuntimeError(f"Expected text not found in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def ensure_single_import(path: str, anchor: str, import_line: str) -> None:
    text = read(path)
    lines = text.splitlines()
    filtered: list[str] = []
    seen = False
    for line in lines:
        if line == import_line:
            if seen:
                continue
            seen = True
        filtered.append(line)
    text = "\n".join(filtered) + "\n"
    if not seen:
        if anchor not in text:
            raise RuntimeError(f"Import anchor not found in {path}: {anchor!r}")
        text = text.replace(anchor, anchor + import_line + "\n", 1)
    write(path, text)


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

    ensure_single_import(
        "app/server.py",
        "from app.body_limit import RequestBodyLimitMiddleware\n",
        "from app.brand import DISPLAY_NAME",
    )
    for old, new in {
        'logger.info("Starting OpenVINO Windows LLM server — %s", mode)': 'logger.info("Starting %s server — %s", DISPLAY_NAME, mode)',
        'FastAPI(title="OpenVINO Windows LLM", version=__version__, lifespan=lifespan)': 'FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=lifespan)',
        'lines.append("# Chat Export — OpenVINO LLM")': 'lines.append(f"# Chat Export — {DISPLAY_NAME}")',
        'argparse.ArgumentParser(description="OpenVINO Windows LLM server")': 'argparse.ArgumentParser(description=f"{DISPLAY_NAME} server")',
    }.items():
        replace("app/server.py", old, new)

    current_guard = 'getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}'
    for path in ("app/engine_handoff_routes.py", "app/model_library_routes.py"):
        text = read(path)
        import_line = "from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME"
        lines = text.splitlines()
        deduplicated: list[str] = []
        seen_import = False
        for line in lines:
            if line == import_line:
                if seen_import:
                    continue
                seen_import = True
            deduplicated.append(line)
        text = "\n".join(deduplicated) + "\n"
        if not seen_import:
            text = text.replace(
                "from __future__ import annotations\n",
                f"from __future__ import annotations\n\n{import_line}\n",
                1,
            )
        legacy_guard = 'getattr(self, "title", "") == "OpenVINO Windows LLM"'
        if legacy_guard in text:
            text = text.replace(legacy_guard, current_guard)
        elif current_guard not in text:
            raise RuntimeError(f"Expected title guard not found in {path}")
        write(path, text)

    ensure_single_import(
        "app/diagnostics.py",
        "from app import __version__\n",
        "from app.brand import DISPLAY_NAME",
    )
    text = read("app/diagnostics.py").replace(
        "openvino-windows-llm-diagnostics-", "inferbridge-diagnostics-"
    )
    legacy_payload = 'return {\n            "application_version": __version__,'
    current_payload = 'return {\n            "application_name": DISPLAY_NAME,\n            "application_version": __version__,'
    if legacy_payload in text:
        text = text.replace(legacy_payload, current_payload, 1)
    elif current_payload not in text:
        raise RuntimeError("Diagnostics application payload marker not found")
    write("app/diagnostics.py", text)

    text = read("packaging/runtime_hook.py")
    text = text.replace('_APP_TITLE = "OpenVINO Windows LLM"', '_APP_TITLE = "InferBridge"')
    if "_CURRENT_DATA_DIR_NAME" not in text:
        text = text.replace(
            "_RUNTIME_FAILURE_EXIT_CODE = 12",
            '_CURRENT_DATA_DIR_NAME = "InferBridge"\n_LEGACY_DATA_DIR_NAME = "OpenVINOWindowsLLM"\n_RUNTIME_FAILURE_EXIT_CODE = 12',
            1,
        )
    legacy_root = '        root = os.path.join(local_app_data, "OpenVINOWindowsLLM")'
    current_root = "        current = os.path.join(local_app_data, _CURRENT_DATA_DIR_NAME)"
    if legacy_root in text:
        text = text.replace(
            legacy_root,
            current_root
            + "\n        legacy = os.path.join(local_app_data, _LEGACY_DATA_DIR_NAME)"
            + "\n        root = current if os.path.isdir(current) or not os.path.isdir(legacy) else legacy",
            1,
        )
    elif current_root not in text:
        raise RuntimeError("Runtime-hook data root marker not found")
    write(
        "packaging/runtime_hook.py",
        text.replace("Close OpenVINO Windows LLM", "Close InferBridge"),
    )

    text = read("app/startup_registration.py")
    old_location = 'location: str = f"HKCU\\{RUN_KEY}\\{CURRENT_VALUE_NAME}"'
    new_location = 'location: str = f"HKCU\\\\{RUN_KEY}\\\\{CURRENT_VALUE_NAME}"'
    if new_location not in text:
        if old_location not in text:
            raise RuntimeError("Startup registry location marker not found")
        text = text.replace(old_location, new_location, 1)
    write("app/startup_registration.py", text)


if __name__ == "__main__":
    main()
