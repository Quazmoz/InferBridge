"""Stage the Microsoft Store MSIX layout: the WinUI client plus the Python engine.

The package launches the native client from native/ and keeps the installed-mode
PyInstaller engine at the root. Writes AppxManifest.xml and the scaled logo
assets under layout/ (indexed into resources.pri by build_msix.ps1), and a
MakeAppx mapping file that points at the built files in place (no multi-gigabyte
staging copy). scripts/build_msix.ps1 packs it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "packaging" / "msix" / "AppxManifest.xml"
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{2,49}$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# Logos the manifest references, as (100% width, 100% height, mark size as a
# fraction of the shorter side). Each is written at every Store scale; the
# packaged resources.pri (scripts/build_msix.ps1) lets Windows pick the sharpest.
TILES = {
    "StoreLogo": (50, 50, 1.0),
    "Square44x44Logo": (44, 44, 1.0),
    "Square71x71Logo": (71, 71, 0.75),
    "Square150x150Logo": (150, 150, 0.5),
    "Wide310x150Logo": (310, 150, 0.5),
    "Square310x310Logo": (310, 310, 0.5),
    "SplashScreen": (620, 300, 0.5),
}
SCALES = (100, 125, 150, 200, 400)
# Taskbar, Start and Explorer icons; "altform-unplated" drops the accent plate.
APP_ICON_TARGET_SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256)


def _asset_sizes() -> dict[str, tuple[int, int, float]]:
    assets = {}
    for name, (width, height, mark) in TILES.items():
        for scale in SCALES:
            size = (width * scale + 50) // 100, (height * scale + 50) // 100
            assets[f"{name}.scale-{scale}.png"] = (*size, mark)
    for size in APP_ICON_TARGET_SIZES:
        assets[f"Square44x44Logo.targetsize-{size}.png"] = (size, size, 1.0)
        assets[f"Square44x44Logo.targetsize-{size}_altform-unplated.png"] = (size, size, 1.0)
    return assets


ASSETS = _asset_sizes()
LAYOUT = "layout"  # MakePri indexes only this folder: the manifest and Assets\.
RESOURCES_PRI = "resources.pri"


def msix_version(version: str) -> str:
    """Map semantic version to MSIX x.y.z.0 (the Store reserves the revision field)."""
    match = _VERSION_RE.fullmatch(version.strip())
    if not match:
        raise ValueError(f"Not a semantic version: {version!r}")
    parts = [int(part) for part in match.groups()]
    if any(part > 65535 for part in parts):
        raise ValueError(f"Version {version!r} cannot be expressed as an MSIX version.")
    return ".".join(str(part) for part in parts) + ".0"


def render_manifest(
    *,
    identity_name: str,
    publisher: str,
    publisher_display_name: str,
    version: str,
) -> str:
    if not _IDENTITY_RE.fullmatch(identity_name):
        raise ValueError("Identity name must be 3-50 letters, digits, periods, or hyphens.")
    if not publisher.startswith("CN="):
        raise ValueError("Publisher must be the Partner Center publisher ID, e.g. CN=XXXXXXXX-...")
    if not publisher_display_name.strip():
        raise ValueError("Publisher display name is required.")
    attribute = {'"': "&quot;"}
    values = {
        "{{IDENTITY_NAME}}": escape(identity_name, attribute),
        "{{PUBLISHER}}": escape(publisher, attribute),
        "{{PUBLISHER_DISPLAY_NAME}}": escape(publisher_display_name.strip()),
        "{{VERSION}}": msix_version(version),
    }
    manifest = TEMPLATE.read_text(encoding="utf-8")
    for token, value in values.items():
        manifest = manifest.replace(token, value)
    if "{{" in manifest:
        raise RuntimeError("AppxManifest.xml template has an unfilled token.")
    return manifest


def write_assets(directory: Path) -> list[Path]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_brand_assets import render_brand_icon
    from PIL import Image

    directory.mkdir(parents=True, exist_ok=True)
    master = render_brand_icon()
    written = []
    for name, (width, height, fraction) in ASSETS.items():
        side = max(1, round(min(width, height) * fraction))
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        mark = master.resize((side, side), Image.Resampling.LANCZOS)
        canvas.paste(mark, ((width - side) // 2, (height - side) // 2), mark)
        path = directory / name
        canvas.save(path, format="PNG", optimize=True)
        written.append(path)
    return written


def native_entries(native_publish: Path) -> list[tuple[Path, str]]:
    """Validate native publish output and map it under native/ in the MSIX."""
    if native_publish.is_symlink():
        raise RuntimeError("Native publish root must not be a symbolic link.")
    native_publish = native_publish.resolve()
    if not (native_publish / "InferBridge.Native.exe").is_file():
        raise FileNotFoundError("Native publish output is missing InferBridge.Native.exe.")
    # Reject a lone application host without its self-contained .NET runtime.
    for required in (
        "InferBridge.Native.deps.json",
        "InferBridge.Native.runtimeconfig.json",
        "coreclr.dll",
    ):
        if not (native_publish / required).is_file():
            raise FileNotFoundError(f"Native publish output is missing {required}.")
    entries: list[tuple[Path, str]] = []
    for path in sorted(native_publish.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Native publish output must not contain symbolic links.")
        if path.is_file():
            target = "native\\" + str(path.relative_to(native_publish)).replace("/", "\\")
            entries.append((path, target))
    return entries


def stage(
    *,
    distribution: Path,
    output_dir: Path,
    identity_name: str,
    publisher: str,
    publisher_display_name: str,
    version: str,
    native_publish: Path,
) -> Path:
    if distribution.is_symlink():
        raise RuntimeError("Python distribution root must not be a symbolic link.")
    distribution = distribution.resolve()
    output_dir = output_dir.resolve()
    if output_dir == distribution or output_dir.is_relative_to(distribution):
        raise ValueError("MSIX output must be outside the Python distribution.")
    if not (distribution / "InferBridge.exe").is_file():
        raise FileNotFoundError(f"InferBridge.exe not found in {distribution}")
    if (distribution / "portable.flag").exists():
        raise RuntimeError("The Store package must be built from the installed-mode layout.")
    if native_publish.is_symlink():
        raise RuntimeError("Native publish root must not be a symbolic link.")
    native_publish = native_publish.resolve()
    if output_dir == native_publish or output_dir.is_relative_to(native_publish):
        raise ValueError("MSIX output must be outside the native publish directory.")
    bundled_native = distribution / "native"
    bundled = native_publish == bundled_native
    if native_publish == distribution or (
        native_publish.is_relative_to(distribution) and not bundled
    ):
        raise ValueError(
            "Native publish output must be outside the Python distribution or at native/."
        )
    if bundled_native.exists() and not bundled:
        raise RuntimeError("The Python distribution already contains a different native/ payload.")
    native_payload = native_entries(native_publish)
    # The native client starts the bundled engine; a launcher without its
    # runtime would leave the window permanently waiting for an engine.
    internal = distribution / "_internal"
    if not internal.is_dir() or not any(internal.glob("python*.dll")):
        raise FileNotFoundError("The Store package requires the installed Python runtime DLL.")

    # Windows target paths are case-insensitive; reject collisions and symlinks
    # before emitting a partial staging layout.
    distribution_payload: list[tuple[Path, str]] = []
    for file in sorted(distribution.rglob("*")):
        if file.is_symlink():
            raise RuntimeError("Python distribution must not contain symbolic links.")
        if file.is_file():
            relative = file.relative_to(distribution)
            if bundled and relative.parts[0].casefold() == "native":
                continue
            if relative.parts[0].casefold() in {"appxmanifest.xml", "assets", RESOURCES_PRI}:
                raise RuntimeError(f"Distribution file collides with package metadata: {relative}")
            distribution_payload.append((file, str(relative).replace("/", "\\")))
    targets = {
        "appxmanifest.xml",
        RESOURCES_PRI,
        *(("assets\\" + name).casefold() for name in ASSETS),
    }
    for source, target in [*distribution_payload, *native_payload]:
        if '"' in str(source) or '"' in target:
            raise RuntimeError("MakeAppx mapping paths must not contain quotes.")
        folded = target.casefold()
        if folded in targets:
            raise RuntimeError(f"Duplicate MSIX package target: {target}")
        targets.add(folded)
    manifest_xml = render_manifest(
        identity_name=identity_name,
        publisher=publisher,
        publisher_display_name=publisher_display_name,
        version=version,
    )
    layout = output_dir / LAYOUT
    layout.mkdir(parents=True, exist_ok=True)
    manifest = layout / "AppxManifest.xml"
    manifest.write_text(manifest_xml, encoding="utf-8")
    entries = [(manifest, "AppxManifest.xml")]
    entries += [(path, f"Assets\\{path.name}") for path in write_assets(layout / "Assets")]
    # build_msix.ps1 runs MakePri over the layout before packing.
    entries.append((layout / RESOURCES_PRI, RESOURCES_PRI))
    entries.extend(distribution_payload)
    entries.extend(native_payload)
    mapping = output_dir / "mapping.txt"
    lines = ["[Files]"] + [f'"{source}" "{target}"' for source, target in entries]
    mapping.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True, type=Path)
    parser.add_argument("--native-publish-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--identity-name", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--publisher-display-name", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    print(
        stage(
            distribution=args.distribution,
            output_dir=args.output_dir,
            identity_name=args.identity_name,
            publisher=args.publisher,
            publisher_display_name=args.publisher_display_name,
            version=args.version,
            native_publish=args.native_publish_dir,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
