from __future__ import annotations

import argparse
import re
import textwrap
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HISTORICAL_PREFIXES = (
    "docs/releases/",
    "docs/certification/",
)
GENERATED_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "artifacts",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
}
CUSTOM_PATHS = {
    ".github/scripts/apply_inferbridge_migration.py",
    ".github/workflows/inferbridge-migration.yml",
    "app/__init__.py",
    "app/brand.py",
    "app/desktop_launcher.py",
    "app/distribution.py",
    "app/paths.py",
    "app/release_models.py",
    "app/startup_registration.py",
    "app/update_checker.py",
    "docs/INFERBRIDGE_IDENTITY_INVENTORY.md",
    "docs/INFERBRIDGE_MIGRATION.md",
    "packaging/installer.iss",
    "packaging/openvino_windows_llm.spec",
    "pyproject.toml",
}
TEXT_SUFFIXES = {
    "",
    ".bat",
    ".css",
    ".html",
    ".ini",
    ".iss",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IDENTITY_TOKENS = (
    "OpenVINO Windows LLM",
    "OpenVINO LLM",
    "OpenVINOWindowsLLM",
    "OpenVINO-Windows-LLM",
    "openvino-windows-llm",
    "ovllm",
    "OV_LLM",
    "https://github.com/Quazmoz/openvino-windows-llm",
    "{F94A3938-C943-4E6D-B482-852D4AAE06F8}",
)


def _path(relative: str) -> Path:
    return ROOT / relative


def _read(relative: str) -> str:
    return _path(relative).read_text(encoding="utf-8")


def _write(relative: str, content: str) -> None:
    target = _path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = textwrap.dedent(content).lstrip("\n").rstrip() + "\n"
    target.write_text(normalized, encoding="utf-8")


def _replace_required(text: str, old: str, new: str, *, path: str) -> str:
    if old not in text:
        raise RuntimeError(f"Expected text was not found in {path}: {old[:120]!r}")
    return text.replace(old, new)


def _replace_between(text: str, start: str, end: str, replacement: str, *, path: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"Start marker was not found in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"End marker was not found in {path}: {end!r}")
    return text[:start_index] + replacement.rstrip() + "\n\n" + text[end_index:]


def _iter_text_files():
    for candidate in ROOT.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(ROOT)
        if any(part in GENERATED_PARTS for part in relative.parts):
            continue
        if candidate.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield relative.as_posix(), candidate, text


def _classification(relative: str, token: str) -> str:
    if relative.startswith(HISTORICAL_PREFIXES):
        return "historical documentation"
    if relative in CUSTOM_PATHS or token in {"OV_LLM", "ovllm"}:
        return "compatibility-sensitive legacy identity"
    if token == "OpenVINO" or "OpenVINO GenAI" in token:
        return "technical OpenVINO terminology"
    return "public display branding"


def _write_inventory() -> None:
    matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    for relative, _candidate, text in _iter_text_files():
        for token in IDENTITY_TOKENS:
            if token in text:
                matches[(token, _classification(relative, token))].add(relative)

    lines = [
        "# InferBridge identity inventory",
        "",
        "This inventory was generated from the source tree before the InferBridge edits were applied.",
        "It classifies product branding separately from OpenVINO runtime terminology and compatibility contracts.",
        "",
        "## Classification rules",
        "",
        "1. Public display branding is migrated to InferBridge.",
        "2. OpenVINO and OpenVINO GenAI runtime terminology remains unchanged.",
        "3. Compatibility-sensitive identifiers remain accepted where required for upgrades, data, startup, CLI, environment, and updates.",
        "4. Historical release and certification documents remain historically accurate.",
        "5. Generated and transient output directories are not edited.",
        "",
        "## Matches",
        "",
        "| Identifier | Classification | Matching paths |",
        "| --- | --- | --- |",
    ]
    for (token, classification), paths in sorted(matches.items()):
        escaped = token.replace("|", "\\|")
        rendered_paths = "<br>".join(f"`{path}`" for path in sorted(paths))
        lines.append(f"| `{escaped}` | {classification} | {rendered_paths} |")
    lines.extend(
        [
            "",
            "## Deliberately retained interfaces",
            "",
            "- All `OV_LLM_*` environment variables.",
            "- The `ov-llm` and `ov-llm-desktop` console commands.",
            "- Internal `ovllm` browser element IDs and state keys where renaming would break persisted or tested behavior.",
            "- The legacy executable, data-directory, startup-value, repository, release URL, manifest, and artifact identities as accepted aliases.",
            "- The Inno Setup `AppId` `{F94A3938-C943-4E6D-B482-852D4AAE06F8}`.",
            "- OpenVINO package names, cache terminology, device names, and OpenVINO GenAI attribution.",
        ]
    )
    _write("docs/INFERBRIDGE_IDENTITY_INVENTORY.md", "\n".join(lines))


def _apply_public_replacements() -> None:
    replacements = (
        ("https://github.com/Quazmoz/openvino-windows-llm", "https://github.com/Quazmoz/InferBridge"),
        ("OpenVINO-Windows-LLM", "InferBridge"),
        ("OpenVINOWindowsLLM.exe", "InferBridge.exe"),
        ("OpenVINOWindowsLLM", "InferBridge"),
        ("OpenVINO Windows LLM", "InferBridge"),
        ("OpenVINO LLM", "InferBridge"),
    )
    for relative, candidate, text in _iter_text_files():
        if relative in CUSTOM_PATHS or relative.startswith(HISTORICAL_PREFIXES):
            continue
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if relative in {"README.md", "QUICKSTART.md", "CONTRIBUTING.md"}:
            updated = updated.replace("cd openvino-windows-llm", "cd InferBridge")
        if updated != text:
            candidate.write_text(updated, encoding="utf-8")


def stage_brand() -> None:
    _write_inventory()
    _apply_public_replacements()

    _write(
        "app/brand.py",
        '''
        """Canonical public branding and legacy compatibility identifiers."""

        from __future__ import annotations

        DISPLAY_NAME = "InferBridge"
        LEGACY_DISPLAY_NAME = "OpenVINO Windows LLM"

        EXECUTABLE_BASENAME = "InferBridge"
        LEGACY_EXECUTABLE_BASENAME = "OpenVINOWindowsLLM"
        DATA_DIR_NAME = EXECUTABLE_BASENAME
        LEGACY_DATA_DIR_NAME = LEGACY_EXECUTABLE_BASENAME

        REPOSITORY_OWNER = "Quazmoz"
        REPOSITORY_NAME = "InferBridge"
        LEGACY_REPOSITORY_NAME = "openvino-windows-llm"
        REPOSITORY_URL = f"https://github.com/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
        LEGACY_REPOSITORY_URL = (
            f"https://github.com/{REPOSITORY_OWNER}/{LEGACY_REPOSITORY_NAME}"
        )
        REPOSITORY_URLS = (REPOSITORY_URL, LEGACY_REPOSITORY_URL)
        RELEASE_URL_PREFIXES = tuple(f"{url}/releases/download/" for url in REPOSITORY_URLS)

        ARTIFACT_PREFIX = "InferBridge"
        LEGACY_ARTIFACT_PREFIXES = ("OpenVINO-Windows-LLM", "OpenVINOWindowsLLM")
        USER_AGENT_PRODUCT = "InferBridge"

        APPLICATION_DESCRIPTION = (
            "Windows-first local AI server with an OpenAI-compatible API, powered by "
            "OpenVINO GenAI for Intel CPU, GPU, NPU, and AUTO targets."
        )
        APPLICATION_TAGLINE = "Local AI for Intel hardware"
        POWERED_BY = "Powered by OpenVINO GenAI"

        __all__ = [
            "APPLICATION_DESCRIPTION",
            "APPLICATION_TAGLINE",
            "ARTIFACT_PREFIX",
            "DATA_DIR_NAME",
            "DISPLAY_NAME",
            "EXECUTABLE_BASENAME",
            "LEGACY_ARTIFACT_PREFIXES",
            "LEGACY_DATA_DIR_NAME",
            "LEGACY_DISPLAY_NAME",
            "LEGACY_EXECUTABLE_BASENAME",
            "LEGACY_REPOSITORY_NAME",
            "LEGACY_REPOSITORY_URL",
            "POWERED_BY",
            "RELEASE_URL_PREFIXES",
            "REPOSITORY_NAME",
            "REPOSITORY_OWNER",
            "REPOSITORY_URL",
            "REPOSITORY_URLS",
            "USER_AGENT_PRODUCT",
        ]
        ''',
    )

    path = "app/__init__.py"
    text = _read(path)
    text = text.replace('"""OpenVINO Windows LLM application package.', '"""InferBridge application package.')
    text = text.replace('    "body_limit",', '    "brand",\n    "body_limit",')
    _write(path, text)

    path = "app/desktop_launcher.py"
    text = _read(path)
    text = _replace_required(
        text,
        "from typing import BinaryIO\n",
        "from typing import BinaryIO\n\nfrom app.brand import APPLICATION_DESCRIPTION, DISPLAY_NAME\n",
        path=path,
    )
    text = _replace_required(text, '_APP_TITLE = "OpenVINO Windows LLM"', "_APP_TITLE = DISPLAY_NAME", path=path)
    text = _replace_required(
        text,
        'parser = argparse.ArgumentParser(description="OpenVINO Windows LLM desktop tray launcher")',
        "parser = argparse.ArgumentParser(description=APPLICATION_DESCRIPTION)",
        path=path,
    )
    _write(path, text)

    path = "pyproject.toml"
    text = _read(path)
    text = _replace_required(
        text,
        'description = "Windows-first local LLM and VLM server built on OpenVINO GenAI for Intel CPU/GPU/NPU."',
        'description = "InferBridge is a Windows-first local AI server with an OpenAI-compatible API, powered by OpenVINO GenAI for Intel CPU/GPU/NPU."',
        path=path,
    )
    text = _replace_required(
        text,
        'ov-llm-desktop = "app.desktop_launcher:main"',
        'ov-llm-desktop = "app.desktop_launcher:main"\ninferbridge = "app.server:main"\ninferbridge-desktop = "app.desktop_launcher:main"',
        path=path,
    )
    _write(path, text)


def stage_compatibility() -> None:
    path = "app/paths.py"
    text = _read(path)
    text = _replace_required(text, "import sys\n", "import sys\nimport warnings\n", path=path)
    text = _replace_required(
        text,
        "from pathlib import Path\n\n_APP_DIR_NAME = \"OpenVINOWindowsLLM\"",
        "from pathlib import Path\n\nfrom app.brand import DATA_DIR_NAME, DISPLAY_NAME, LEGACY_DATA_DIR_NAME\n\n_APP_DIR_NAME = DATA_DIR_NAME",
        path=path,
    )
    marker = '''def _default_local_app_data(env: Mapping[str, str]) -> Path:
    configured = str(env.get("LOCALAPPDATA") or "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path.home() / "AppData" / "Local"
    return Path.home() / ".local" / "share"
'''
    replacement = marker + '''


def _contains_data(path: Path) -> bool:
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _desktop_data_root(env: Mapping[str, str]) -> Path:
    local_app_data = _default_local_app_data(env)
    current = local_app_data / DATA_DIR_NAME
    legacy = local_app_data / LEGACY_DATA_DIR_NAME
    if current.exists():
        if _contains_data(current) and _contains_data(legacy):
            warnings.warn(
                "Both InferBridge and legacy application data directories contain data; "
                "InferBridge was selected without moving or merging files.",
                RuntimeWarning,
                stacklevel=2,
            )
        return current
    if legacy.exists():
        return legacy
    return current
'''
    text = _replace_required(text, marker, replacement, path=path)
    text = _replace_required(
        text,
        "data_root = _default_local_app_data(values) / _APP_DIR_NAME",
        "data_root = _desktop_data_root(values)",
        path=path,
    )
    text = _replace_required(
        text,
        '"OpenVINO Windows LLM cannot write to its application data directory. "',
        'f"{DISPLAY_NAME} cannot write to its application data directory. "',
        path=path,
    )
    _write(path, text)

    _write(
        "app/startup_registration.py",
        '''
        """Per-user Windows startup registration with InferBridge legacy migration."""

        from __future__ import annotations

        import os
        import re
        import subprocess
        import sys
        from dataclasses import dataclass
        from pathlib import Path
        from typing import Protocol

        from app.brand import EXECUTABLE_BASENAME, LEGACY_EXECUTABLE_BASENAME

        RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
        CURRENT_VALUE_NAME = "InferBridge"
        LEGACY_VALUE_NAME = "OpenVINOWindowsLLM"
        _RUN_KEY = RUN_KEY
        _VALUE_NAME = CURRENT_VALUE_NAME


        class RegistryBackend(Protocol):
            def read(self, key: str, name: str) -> str | None: ...

            def write(self, key: str, name: str, value: str) -> None: ...

            def delete(self, key: str, name: str) -> None: ...


        class WinRegBackend:
            def read(self, key: str, name: str) -> str | None:
                if os.name != "nt":
                    return None
                import winreg

                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
                        value, _kind = winreg.QueryValueEx(handle, name)
                        return str(value)
                except FileNotFoundError:
                    return None

            def write(self, key: str, name: str, value: str) -> None:
                if os.name != "nt":
                    raise RuntimeError("Start with Windows is only available on Windows.")
                import winreg

                with winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER,
                    key,
                    0,
                    winreg.KEY_SET_VALUE,
                ) as handle:
                    winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

            def delete(self, key: str, name: str) -> None:
                if os.name != "nt":
                    return
                import winreg

                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        key,
                        0,
                        winreg.KEY_SET_VALUE,
                    ) as handle:
                        winreg.DeleteValue(handle, name)
                except FileNotFoundError:
                    return


        @dataclass(frozen=True)
        class StartupRegistrationState:
            enabled: bool
            command: str | None
            location: str = f"HKCU\\{RUN_KEY}\\{CURRENT_VALUE_NAME}"


        def quote_windows_argument(value: str) -> str:
            text = str(value)
            if not text:
                return '""'
            if not any(char.isspace() or char in '"' for char in text):
                return text
            return subprocess_list2cmdline([text])


        def subprocess_list2cmdline(arguments: list[str]) -> str:
            return subprocess.list2cmdline(arguments)


        def startup_command(executable: Path, *, portable: bool, open_browser: bool = False) -> str:
            executable = Path(executable).expanduser().resolve()
            command = [str(executable), "--startup"]
            if portable:
                command.append("--portable")
            if not open_browser:
                command.append("--no-browser")
            return subprocess_list2cmdline(command)


        def _command_executable_name(command: str | None) -> str | None:
            value = str(command or "").strip()
            if not value:
                return None
            match = re.match(r'^\s*(?:"([^"]+)"|([^\s]+))', value)
            if not match:
                return None
            return Path(match.group(1) or match.group(2)).name.casefold()


        def _recognized_legacy_command(command: str | None) -> bool:
            executable_name = _command_executable_name(command)
            return executable_name == f"{LEGACY_EXECUTABLE_BASENAME}.exe".casefold()


        class StartupRegistration:
            def __init__(
                self,
                *,
                executable: Path | None = None,
                portable: bool = False,
                backend: RegistryBackend | None = None,
            ) -> None:
                self.executable = Path(executable or sys.executable).expanduser().resolve()
                self.portable = bool(portable)
                self.backend = backend or WinRegBackend()

            @property
            def expected_command(self) -> str:
                return startup_command(self.executable, portable=self.portable, open_browser=False)

            def _migrate_legacy_if_enabled(self) -> None:
                current = self.backend.read(RUN_KEY, CURRENT_VALUE_NAME)
                legacy = self.backend.read(RUN_KEY, LEGACY_VALUE_NAME)
                if current is None and _recognized_legacy_command(legacy):
                    self.backend.write(RUN_KEY, CURRENT_VALUE_NAME, self.expected_command)
                    self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
                elif current == self.expected_command and _recognized_legacy_command(legacy):
                    self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)

            def state(self) -> StartupRegistrationState:
                self._migrate_legacy_if_enabled()
                current = self.backend.read(RUN_KEY, CURRENT_VALUE_NAME)
                return StartupRegistrationState(
                    enabled=current == self.expected_command,
                    command=current,
                )

            def set_enabled(self, enabled: bool) -> StartupRegistrationState:
                if enabled and self.portable:
                    raise RuntimeError(
                        "Start with Windows is disabled in portable mode. Install the application "
                        "per-user before enabling automatic startup."
                    )
                if enabled:
                    self.backend.write(RUN_KEY, CURRENT_VALUE_NAME, self.expected_command)
                    legacy = self.backend.read(RUN_KEY, LEGACY_VALUE_NAME)
                    if _recognized_legacy_command(legacy):
                        self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
                else:
                    self.backend.delete(RUN_KEY, CURRENT_VALUE_NAME)
                    self.backend.delete(RUN_KEY, LEGACY_VALUE_NAME)
                return self.state()


        class MemoryRegistryBackend:
            """Small deterministic registry substitute for unit tests."""

            def __init__(self) -> None:
                self.values: dict[tuple[str, str], str] = {}

            def read(self, key: str, name: str) -> str | None:
                return self.values.get((key, name))

            def write(self, key: str, name: str, value: str) -> None:
                self.values[(key, name)] = value

            def delete(self, key: str, name: str) -> None:
                self.values.pop((key, name), None)
        ''',
    )

    path = "app/release_models.py"
    text = _read(path)
    text = _replace_required(
        text,
        "from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator\n",
        "from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator\n\n"
        "from app.brand import (\n"
        "    ARTIFACT_PREFIX,\n"
        "    LEGACY_ARTIFACT_PREFIXES,\n"
        "    LEGACY_REPOSITORY_NAME,\n"
        "    REPOSITORY_NAME,\n"
        "    REPOSITORY_OWNER,\n"
        ")\n",
        path=path,
    )
    text = _replace_required(
        text,
        '_ALLOWED_RELEASE_HOST = "github.com"\n_ALLOWED_RELEASE_PREFIX = "/Quazmoz/openvino-windows-llm/releases/download/"',
        '_ALLOWED_RELEASE_HOST = "github.com"\n_ALLOWED_REPOSITORY_NAMES = (REPOSITORY_NAME, LEGACY_REPOSITORY_NAME)\n_ALLOWED_RELEASE_PREFIXES = tuple(\n    f"/{REPOSITORY_OWNER}/{repository}/releases/download/"\n    for repository in _ALLOWED_REPOSITORY_NAMES\n)',
        path=path,
    )
    text = _replace_required(
        text,
        '    prefix = f"OpenVINO-Windows-LLM-{version}"',
        '    prefix = f"{ARTIFACT_PREFIX}-{version}"',
        path=path,
    )
    artifact_function_end = '''    }
    return prefix + suffixes[artifact_type]
'''
    artifact_helpers = artifact_function_end + '''


def artifact_filenames(version: str, artifact_type: ArtifactType) -> tuple[str, ...]:
    canonical = artifact_filename(version, artifact_type)
    suffix = canonical.removeprefix(f"{ARTIFACT_PREFIX}-{version}")
    aliases = tuple(
        f"{prefix}-{version}{suffix}" for prefix in LEGACY_ARTIFACT_PREFIXES
    )
    return (canonical, *aliases)
'''
    text = _replace_required(text, artifact_function_end, artifact_helpers, path=path)
    old_url_function = '''def is_official_release_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _ALLOWED_RELEASE_HOST
        and parsed.path.startswith(_ALLOWED_RELEASE_PREFIX)
        and not parsed.username
        and not parsed.password
        and parsed.port is None
    )
'''
    new_url_function = '''def _safe_github_url(value: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    valid = (
        parsed.scheme == "https"
        and parsed.hostname == _ALLOWED_RELEASE_HOST
        and not parsed.username
        and not parsed.password
        and parsed.port is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    return valid, parsed.path


def is_official_release_url(value: str) -> bool:
    valid, path = _safe_github_url(value)
    return valid and any(path.startswith(prefix) for prefix in _ALLOWED_RELEASE_PREFIXES)


def is_official_repository_url(value: str) -> bool:
    valid, path = _safe_github_url(value)
    if not valid:
        return False
    return any(
        path == f"/{REPOSITORY_OWNER}/{repository}"
        or path.startswith(f"/{REPOSITORY_OWNER}/{repository}/")
        for repository in _ALLOWED_REPOSITORY_NAMES
    )
'''
    text = _replace_required(text, old_url_function, new_url_function, path=path)
    text = _replace_required(
        text,
        "            if artifact.filename != artifact_filename(self.version, artifact.type):",
        "            if artifact.filename not in artifact_filenames(self.version, artifact.type):",
        path=path,
    )
    old_docs_validation = '''            parsed_url = urlparse(str(value))
            if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
                raise ValueError("release documentation URLs must use the official GitHub host")
'''
    new_docs_validation = '''            if not is_official_repository_url(str(value)):
                raise ValueError(
                    "release documentation URLs must use an approved InferBridge repository"
                )
'''
    text = _replace_required(text, old_docs_validation, new_docs_validation, path=path)
    _write(path, text)

    path = "app/update_checker.py"
    text = _read(path)
    text = _replace_required(
        text,
        "from pydantic import BaseModel, ConfigDict, Field\n",
        "from pydantic import BaseModel, ConfigDict, Field\n\n"
        "from app.brand import (\n"
        "    ARTIFACT_PREFIX,\n"
        "    LEGACY_ARTIFACT_PREFIXES,\n"
        "    LEGACY_REPOSITORY_NAME,\n"
        "    REPOSITORY_NAME,\n"
        "    REPOSITORY_OWNER,\n"
        "    USER_AGENT_PRODUCT,\n"
        ")\n",
        path=path,
    )
    text = _replace_required(
        text,
        '_RELEASES_API = "https://api.github.com/repos/Quazmoz/openvino-windows-llm/releases?per_page=20"',
        '_RELEASES_APIS = tuple(\n    f"https://api.github.com/repos/{REPOSITORY_OWNER}/{repository}/releases?per_page=20"\n    for repository in (LEGACY_REPOSITORY_NAME, REPOSITORY_NAME)\n)\n_RELEASES_API = _RELEASES_APIS[0]\n_MANIFEST_PREFIXES = (ARTIFACT_PREFIX, *LEGACY_ARTIFACT_PREFIXES)',
        path=path,
    )
    candidate_start = "def _candidate_manifest_url(releases: object, channel: ReleaseChannel) -> tuple[str, str] | None:\n"
    class_marker = "class UpdateChecker:\n"
    candidate_replacement = '''def _candidate_manifest_url(releases: object, channel: ReleaseChannel) -> tuple[str, str] | None:
    if not isinstance(releases, list):
        raise ValueError("GitHub releases response is not a list.")
    candidates: list[tuple[SemanticVersion, int, str, str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if channel == "stable" and release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "")
        version = tag[1:] if tag.startswith("v") else tag
        try:
            parsed = SemanticVersion.parse(version)
        except ValueError:
            continue
        if not channel_accepts(channel, version):
            continue
        expected = {
            f"{prefix}-{version}-release-manifest.json": priority
            for priority, prefix in enumerate(reversed(_MANIFEST_PREFIXES), start=1)
        }
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            priority = expected.get(str(asset.get("name") or ""))
            if priority is None:
                continue
            url = str(asset.get("browser_download_url") or "")
            if is_official_release_url(url):
                candidates.append((parsed, priority, version, url))
    if not candidates:
        return None
    _parsed, _priority, version, url = max(candidates, key=lambda item: (item[0], item[1]))
    return version, url


def _fetch_release_index(*, opener: Callable, timeout_seconds: float, etag: str | None):
    last_error: BaseException | None = None
    for index, releases_api in enumerate(_RELEASES_APIS):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USER_AGENT_PRODUCT}/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if index == 0 and etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(releases_api, headers=headers)
        try:
            with opener(request, timeout=timeout_seconds) as response:
                return _read_json(response), response.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                raise
            if exc.code in {301, 302, 307, 308, 404}:
                last_error = exc
                continue
            raise
        except (TimeoutError, OSError) as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise OSError("No approved GitHub release endpoint was available.")
'''
    text = _replace_between(text, candidate_start, class_marker, candidate_replacement, path=path)
    old_fetch_block = '''        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"OpenVINO-Windows-LLM/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if cache.releases_etag:
            headers["If-None-Match"] = cache.releases_etag
        request = urllib.request.Request(_RELEASES_API, headers=headers)
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                releases = _read_json(response)
                etag = response.headers.get("ETag")
            candidate = _candidate_manifest_url(releases, preferences.channel)
'''
    new_fetch_block = '''        try:
            releases, etag = _fetch_release_index(
                opener=self.opener,
                timeout_seconds=self.timeout_seconds,
                etag=cache.releases_etag,
            )
            candidate = _candidate_manifest_url(releases, preferences.channel)
'''
    text = _replace_required(text, old_fetch_block, new_fetch_block, path=path)
    text = text.replace(
        '"User-Agent": f"OpenVINO-Windows-LLM/{__version__}"',
        '"User-Agent": f"{USER_AGENT_PRODUCT}/{__version__}"',
    )
    _write(path, text)

    path = "app/distribution.py"
    text = _read(path)
    text = _replace_required(
        text,
        "from dataclasses import dataclass\n",
        "from dataclasses import dataclass\n\nfrom app.brand import ARTIFACT_PREFIX\n",
        path=path,
    )
    text = _replace_required(
        text,
        '    stem = f"OpenVINOWindowsLLM-{version}-windows-x64"',
        '    stem = f"{ARTIFACT_PREFIX}-{version}-windows-x64"',
        path=path,
    )
    text = _replace_required(
        text,
        '        "checksums": f"OpenVINOWindowsLLM-{version}-SHA256SUMS.txt",',
        '        "checksums": f"{ARTIFACT_PREFIX}-{version}-SHA256SUMS.txt",',
        path=path,
    )
    _write(path, text)


def stage_packaging() -> None:
    path = "packaging/openvino_windows_llm.spec"
    text = _read(path)
    text = _replace_required(text, '    name="OpenVINOWindowsLLM",', '    name="InferBridge",', path=path)
    _write(path, text)

    path = "packaging/installer.iss"
    text = _read(path)
    text = _replace_required(
        text,
        '#define MyAppName "OpenVINO Windows LLM"\n#define MyAppPublisher "Quazmoz"\n#define MyAppExeName "OpenVINOWindowsLLM.exe"',
        '#define MyAppName "InferBridge"\n#define MyLegacyAppName "OpenVINO Windows LLM"\n#define MyAppPublisher "Quazmoz"\n#define MyAppExeName "InferBridge.exe"\n#define MyLegacyAppExeName "OpenVINOWindowsLLM.exe"',
        path=path,
    )
    text = text.replace("https://github.com/Quazmoz/openvino-windows-llm", "https://github.com/Quazmoz/InferBridge")
    text = _replace_required(
        text,
        "DefaultDirName={localappdata}\\Programs\\OpenVINOWindowsLLM",
        "DefaultDirName={localappdata}\\Programs\\InferBridge",
        path=path,
    )
    text = _replace_required(
        text,
        "OutputBaseFilename=OpenVINO-Windows-LLM-{#MyAppVersion}-windows-x64-installer",
        "OutputBaseFilename=InferBridge-{#MyAppVersion}-windows-x64-installer",
        path=path,
    )
    text = _replace_required(
        text,
        "CloseApplicationsFilter={#MyAppExeName},*.dll,*.pyd",
        "CloseApplicationsFilter={#MyAppExeName},{#MyLegacyAppExeName},*.dll,*.pyd",
        path=path,
    )
    text = _replace_required(text, "UsePreviousGroup=yes", "UsePreviousGroup=no", path=path)
    text = _replace_required(
        text,
        "; Mutable data is stored under %LOCALAPPDATA%\\OpenVINOWindowsLLM, outside {app}.",
        "; Mutable data remains outside {app}; the application resolves InferBridge and legacy data roots safely.",
        path=path,
    )
    install_delete_marker = 'Type: files; Name: "{app}\\{#MyAppExeName}"\n'
    install_delete_replacement = install_delete_marker + '''Type: files; Name: "{app}\\{#MyLegacyAppExeName}"
Type: filesandordirs; Name: "{userprograms}\\{#MyLegacyAppName}"
Type: files; Name: "{userdesktop}\\{#MyLegacyAppName}.lnk"
Type: files; Name: "{commondesktop}\\{#MyLegacyAppName}.lnk"
'''
    text = _replace_required(text, install_delete_marker, install_delete_replacement, path=path)
    text = _replace_required(
        text,
        "      DelTree(ExpandConstant('{localappdata}\\OpenVINOWindowsLLM'), True, True, True);",
        "    begin\n"
        "      DelTree(ExpandConstant('{localappdata}\\InferBridge'), True, True, True);\n"
        "      DelTree(ExpandConstant('{localappdata}\\OpenVINOWindowsLLM'), True, True, True);\n"
        "    end;",
        path=path,
    )
    _write(path, text)


def stage_docs_and_tests() -> None:
    path = "app/version.py"
    text = _read(path)
    text = _replace_required(text, '__version__ = "0.6.3-beta.1"', '__version__ = "0.7.0"', path=path)
    _write(path, text)

    path = "README.md"
    text = _read(path)
    text = re.sub(
        r"Current development version: `[^`]+`\.[^\n]*",
        "Current development version: `0.7.0`. This feature release rebrands the product as InferBridge while preserving OpenVINO GenAI, existing models and settings, the installer upgrade identity, `OV_LLM_*` variables, legacy console commands, and approved legacy update sources.",
        text,
        count=1,
    )
    _write(path, text)

    path = ".github/workflows/ci.yml"
    text = _read(path)
    text = _replace_required(
        text,
        'branches: ["main", "development", "beta"]',
        'branches: ["main", "development", "beta", "rename/inferbridge"]',
        path=path,
    )
    targeted_marker = "          tests/test_update_checker_resilience.py\n"
    targeted_addition = targeted_marker + '''          tests/test_inferbridge_brand.py
          tests/test_inferbridge_paths.py
          tests/test_inferbridge_startup.py
          tests/test_inferbridge_update_compatibility.py
          tests/test_inferbridge_packaging_contract.py
'''
    text = _replace_required(text, targeted_marker, targeted_addition, path=path)
    _write(path, text)

    _write(
        "docs/releases/0.7.0.md",
        '''
        # InferBridge 0.7.0

        OpenVINO Windows LLM is now **InferBridge**.

        InferBridge remains a Windows-first local AI server with an OpenAI-compatible API, powered by OpenVINO GenAI. This release does not replace, fork, or claim ownership of OpenVINO.

        ## Migration guarantees

        - Existing models, converted OpenVINO models, compiled caches, settings, benchmarks, logs, and onboarding state are preserved.
        - The installer keeps the existing Inno Setup `AppId`, so supported installations upgrade in place.
        - Existing `%LOCALAPPDATA%\\OpenVINOWindowsLLM` data remains usable without moving or reconverting large models.
        - New installations use `%LOCALAPPDATA%\\InferBridge` unless a legacy data directory must be honored.
        - All existing `OV_LLM_*` environment variables remain supported.
        - `ov-llm` and `ov-llm-desktop` remain supported; `inferbridge` and `inferbridge-desktop` are aliases to the same implementations.
        - The updater accepts approved release metadata and assets from both `Quazmoz/openvino-windows-llm` and `Quazmoz/InferBridge` during the transition.

        ## Packaging changes

        - New launcher: `InferBridge.exe`
        - New portable root: `InferBridge-<version>`
        - New release asset prefix: `InferBridge-<version>`
        - Add or Remove Programs, Start Menu, desktop shortcuts, browser metadata, tray presentation, and diagnostics use InferBridge.
        - The installer removes recognized legacy executable and shortcut remnants during a successful upgrade.

        ## Compatibility retained

        - OpenAI-compatible chat completions, Responses API, embeddings, streaming, tool calls, stop sequences, seed handling, API-key behavior, model IDs, and stored schemas are unchanged.
        - Intel CPU, GPU, NPU, and AUTO remain OpenVINO runtime targets.
        - No new hardware certification claims are made by the rename.

        ## Repository transition

        Ship this compatibility-aware release from the existing repository slug before renaming the repository when feasible. After validation, rename the repository manually through:

        `Repository Settings > General > Repository name > InferBridge > Rename`

        Do not create a replacement repository at the old path because that would consume GitHub's redirect.

        ## Validation limits

        Mock and static validation do not prove Authenticode signing, installer upgrade behavior, or real Intel CPU/GPU/NPU execution. Those checks require the documented Windows release and hardware certification procedures.
        ''',
    )

    _write(
        "docs/INFERBRIDGE_MIGRATION.md",
        '''
        # InferBridge migration and repository-rename plan

        ## Product positioning

        **InferBridge** is local AI for Intel hardware: a Windows-first local AI server with an OpenAI-compatible API, powered by OpenVINO GenAI.

        OpenVINO remains the inference runtime and dependency. InferBridge does not replace or own OpenVINO and does not claim affiliation with Intel.

        ## Compatibility strategy

        The safest repository transition is to publish a compatibility-aware InferBridge release from `Quazmoz/openvino-windows-llm` before changing the GitHub slug. That client accepts both old and new repository release locations, manifest names, and artifact aliases. Older installations that predate this compatibility support may require one manual installer upgrade if GitHub's release API or embedded asset URLs do not redirect reliably after the rename.

        The repository has not been renamed by this code migration. The final manual action is:

        `Repository Settings > General > Repository name > InferBridge > Rename`

        Never create a replacement repository at `Quazmoz/openvino-windows-llm`; GitHub needs the old path to remain available as a redirect.

        ## Installer behavior

        - Preserve `AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}` exactly.
        - New installs default to `%LOCALAPPDATA%\\Programs\\InferBridge`.
        - Existing installs may retain their previous install directory through the preserved `AppId` and `UsePreviousAppDir=yes`.
        - Upgrade cleanup recognizes `InferBridge.exe` and `OpenVINOWindowsLLM.exe` and removes legacy executable and shortcut remnants.
        - Uninstall preserves mutable data by default. Explicit removal deletes only the exact InferBridge and legacy application-data directories.

        ## Data-directory selection

        1. `OV_LLM_DATA_DIR` remains the highest-priority override.
        2. Portable mode continues using `<portable root>\\data`.
        3. Installed mode uses `%LOCALAPPDATA%\\InferBridge` when that directory already exists.
        4. If the new directory does not exist and `%LOCALAPPDATA%\\OpenVINOWindowsLLM` exists, the legacy directory remains active.
        5. A clean install uses `%LOCALAPPDATA%\\InferBridge`.
        6. No automatic multi-gigabyte move or merge occurs.
        7. If both directories contain data, InferBridge is selected and a privacy-safe warning is emitted.

        ## Start with Windows

        InferBridge uses the registry value name `InferBridge`. A recognized enabled `OpenVINOWindowsLLM` value that points to the previous executable is migrated to the new value and command. Disabled users remain disabled, portable mode remains ineligible, and unrelated registry values are untouched.

        ## Post-rename verification

        After the manual rename, verify clone, fetch, push, web redirects, Git redirects, issues, stars, releases, tags, branches, history, release API responses, manifest downloads, checksums, and release asset redirects. Update the local clone origin to `https://github.com/Quazmoz/InferBridge.git` only after the rename succeeds.
        ''',
    )

    _write(
        "tests/test_inferbridge_brand.py",
        '''
        from pathlib import Path

        from app.brand import (
            APPLICATION_DESCRIPTION,
            ARTIFACT_PREFIX,
            DISPLAY_NAME,
            EXECUTABLE_BASENAME,
            LEGACY_DISPLAY_NAME,
            LEGACY_EXECUTABLE_BASENAME,
            LEGACY_REPOSITORY_NAME,
            REPOSITORY_NAME,
            REPOSITORY_OWNER,
        )


        def test_brand_constants_are_consistent():
            assert DISPLAY_NAME == "InferBridge"
            assert LEGACY_DISPLAY_NAME == "OpenVINO Windows LLM"
            assert EXECUTABLE_BASENAME == "InferBridge"
            assert LEGACY_EXECUTABLE_BASENAME == "OpenVINOWindowsLLM"
            assert REPOSITORY_OWNER == "Quazmoz"
            assert REPOSITORY_NAME == "InferBridge"
            assert LEGACY_REPOSITORY_NAME == "openvino-windows-llm"
            assert ARTIFACT_PREFIX == "InferBridge"
            assert "OpenVINO GenAI" in APPLICATION_DESCRIPTION


        def test_python_distribution_and_legacy_commands_remain_compatible():
            pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
            assert 'name = "openvino-windows-llm"' in pyproject
            assert 'ov-llm = "app.server:main"' in pyproject
            assert 'ov-llm-desktop = "app.desktop_launcher:main"' in pyproject
            assert 'inferbridge = "app.server:main"' in pyproject
            assert 'inferbridge-desktop = "app.desktop_launcher:main"' in pyproject
        ''',
    )

    _write(
        "tests/test_inferbridge_paths.py",
        '''
        from pathlib import Path

        import pytest

        from app.paths import resolve_runtime_paths


        def _env(root: Path) -> dict[str, str]:
            return {"LOCALAPPDATA": str(root)}


        def test_clean_install_uses_inferbridge_data_root(tmp_path):
            paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
            assert paths.data_root == (tmp_path / "InferBridge").resolve()


        def test_legacy_upgrade_keeps_existing_data_root(tmp_path):
            legacy = tmp_path / "OpenVINOWindowsLLM"
            legacy.mkdir()
            (legacy / "models").mkdir()
            paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
            assert paths.data_root == legacy.resolve()


        def test_existing_new_root_is_preferred(tmp_path):
            current = tmp_path / "InferBridge"
            current.mkdir()
            legacy = tmp_path / "OpenVINOWindowsLLM"
            legacy.mkdir()
            paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
            assert paths.data_root == current.resolve()


        def test_two_populated_roots_choose_new_without_merging(tmp_path):
            current = tmp_path / "InferBridge"
            legacy = tmp_path / "OpenVINOWindowsLLM"
            current.mkdir()
            legacy.mkdir()
            (current / "current.txt").write_text("current", encoding="utf-8")
            (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")
            with pytest.warns(RuntimeWarning, match="without moving or merging"):
                paths = resolve_runtime_paths(desktop=True, portable=False, env=_env(tmp_path))
            assert paths.data_root == current.resolve()
            assert (legacy / "legacy.txt").is_file()


        def test_explicit_override_remains_highest_priority(tmp_path):
            override = tmp_path / "custom"
            paths = resolve_runtime_paths(
                desktop=True,
                portable=False,
                env={"LOCALAPPDATA": str(tmp_path), "OV_LLM_DATA_DIR": str(override)},
            )
            assert paths.data_root == override.resolve()


        def test_portable_mode_remains_sibling_data(monkeypatch, tmp_path):
            monkeypatch.setattr("app.paths.executable_dir", lambda: tmp_path)
            paths = resolve_runtime_paths(desktop=True, portable=True, env={})
            assert paths.data_root == (tmp_path / "data").resolve()
        ''',
    )

    _write(
        "tests/test_inferbridge_startup.py",
        '''
        from pathlib import Path

        import pytest

        from app.startup_registration import (
            CURRENT_VALUE_NAME,
            LEGACY_VALUE_NAME,
            RUN_KEY,
            MemoryRegistryBackend,
            StartupRegistration,
        )


        def test_enabled_legacy_value_migrates_to_inferbridge(tmp_path):
            backend = MemoryRegistryBackend()
            backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = (
                '"C:\\Program Files\\OpenVINO Windows LLM\\OpenVINOWindowsLLM.exe" '
                "--startup --no-browser"
            )
            executable = tmp_path / "InferBridge.exe"
            registration = StartupRegistration(executable=executable, backend=backend)
            state = registration.state()
            assert state.enabled
            assert backend.read(RUN_KEY, CURRENT_VALUE_NAME) == registration.expected_command
            assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is None


        def test_absent_legacy_value_does_not_enable_startup(tmp_path):
            registration = StartupRegistration(
                executable=tmp_path / "InferBridge.exe",
                backend=MemoryRegistryBackend(),
            )
            assert not registration.state().enabled


        def test_unrecognized_legacy_value_is_not_removed(tmp_path):
            backend = MemoryRegistryBackend()
            backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = '"C:\\Other\\unrelated.exe"'
            registration = StartupRegistration(executable=tmp_path / "InferBridge.exe", backend=backend)
            assert not registration.state().enabled
            assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is not None


        def test_disabling_removes_current_and_legacy_values_only(tmp_path):
            backend = MemoryRegistryBackend()
            backend.values[(RUN_KEY, CURRENT_VALUE_NAME)] = "current"
            backend.values[(RUN_KEY, LEGACY_VALUE_NAME)] = "legacy"
            backend.values[(RUN_KEY, "OtherApplication")] = "preserve"
            registration = StartupRegistration(executable=tmp_path / "InferBridge.exe", backend=backend)
            registration.set_enabled(False)
            assert backend.read(RUN_KEY, CURRENT_VALUE_NAME) is None
            assert backend.read(RUN_KEY, LEGACY_VALUE_NAME) is None
            assert backend.read(RUN_KEY, "OtherApplication") == "preserve"


        def test_portable_mode_cannot_enable_startup(tmp_path):
            registration = StartupRegistration(
                executable=tmp_path / "InferBridge.exe",
                portable=True,
                backend=MemoryRegistryBackend(),
            )
            with pytest.raises(RuntimeError, match="portable mode"):
                registration.set_enabled(True)
        ''',
    )

    _write(
        "tests/test_inferbridge_update_compatibility.py",
        '''
        from app.release_models import artifact_filenames, is_official_release_url
        from app.update_checker import _candidate_manifest_url


        def test_new_and_legacy_release_urls_are_approved():
            assert is_official_release_url(
                "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-windows-x64-portable.zip"
            )
            assert is_official_release_url(
                "https://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.6.3/OpenVINO-Windows-LLM-0.6.3-windows-x64-portable.zip"
            )


        def test_release_url_lookalikes_and_prefix_tricks_are_rejected():
            rejected = (
                "https://github.com/Quazmoz/InferBridge-malicious/releases/download/v0.7.0/file.zip",
                "https://github.com/Quazmoz/openvino-windows-llm.evil/releases/download/v0.7.0/file.zip",
                "https://github.com/Other/InferBridge/releases/download/v0.7.0/file.zip",
                "https://user:pass@github.com/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
                "https://github.com:444/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
                "http://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
            )
            assert not any(is_official_release_url(value) for value in rejected)


        def test_manifest_and_artifact_aliases_remain_accepted():
            names = artifact_filenames("0.7.0", "manifest")
            assert "InferBridge-0.7.0-release-manifest.json" in names
            assert "OpenVINO-Windows-LLM-0.7.0-release-manifest.json" in names
            assert "OpenVINOWindowsLLM-0.7.0-release-manifest.json" in names


        def test_candidate_selection_accepts_both_manifest_names_and_prefers_canonical():
            releases = [
                {
                    "draft": False,
                    "prerelease": False,
                    "tag_name": "v0.7.0",
                    "assets": [
                        {
                            "name": "OpenVINO-Windows-LLM-0.7.0-release-manifest.json",
                            "browser_download_url": "https://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.7.0/OpenVINO-Windows-LLM-0.7.0-release-manifest.json",
                        },
                        {
                            "name": "InferBridge-0.7.0-release-manifest.json",
                            "browser_download_url": "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-release-manifest.json",
                        },
                    ],
                }
            ]
            assert _candidate_manifest_url(releases, "stable") == (
                "0.7.0",
                "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-release-manifest.json",
            )
        ''',
    )

    _write(
        "tests/test_inferbridge_packaging_contract.py",
        '''
        from pathlib import Path


        APP_ID = "{F94A3938-C943-4E6D-B482-852D4AAE06F8}"


        def test_installer_preserves_upgrade_identity_and_migrates_branding():
            installer = Path("packaging/installer.iss").read_text(encoding="utf-8")
            assert f"AppId={{{APP_ID}" in installer
            assert '#define MyAppName "InferBridge"' in installer
            assert '#define MyAppExeName "InferBridge.exe"' in installer
            assert '#define MyLegacyAppExeName "OpenVINOWindowsLLM.exe"' in installer
            assert "CloseApplicationsFilter={#MyAppExeName},{#MyLegacyAppExeName}" in installer
            assert 'Name: "{app}\\{#MyLegacyAppExeName}"' in installer
            assert "{#MyLegacyAppName}" in installer
            assert "UsePreviousAppDir=yes" in installer
            assert "DefaultDirName={localappdata}\\Programs\\InferBridge" in installer
            assert "OutputBaseFilename=InferBridge-" in installer


        def test_uninstall_preserves_data_by_default_and_removes_only_named_roots_on_request():
            installer = Path("packaging/installer.iss").read_text(encoding="utf-8")
            assert "IDYES" in installer
            assert "{localappdata}\\InferBridge" in installer
            assert "{localappdata}\\OpenVINOWindowsLLM" in installer


        def test_pyinstaller_outputs_inferbridge_executable_and_directory():
            spec = Path("packaging/openvino_windows_llm.spec").read_text(encoding="utf-8")
            assert spec.count('name="InferBridge"') == 2
            assert 'name="OpenVINOWindowsLLM"' not in spec
        ''',
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=("brand", "compatibility", "packaging", "docs-tests"),
    )
    args = parser.parse_args()
    stages = {
        "brand": stage_brand,
        "compatibility": stage_compatibility,
        "packaging": stage_packaging,
        "docs-tests": stage_docs_and_tests,
    }
    stages[args.stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
