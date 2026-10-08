"""Stage the Microsoft Store MSIX layout for the PyInstaller one-directory build.

Writes AppxManifest.xml, the tile assets, and a MakeAppx mapping file that points at the
built files in place (no multi-gigabyte staging copy). scripts/build_msix.ps1 packs it.
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
# Tile sizes the manifest references; MakeAppx rejects a package missing any of them.
ASSETS = {
    "StoreLogo.png": (50, 50),
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "Wide310x150Logo.png": (310, 150),
}


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
    *, identity_name: str, publisher: str, publisher_display_name: str, version: str
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
    written = []
    for name, (width, height) in ASSETS.items():
        side = min(width, height)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        mark = render_brand_icon(max(side, 16)).resize((side, side), Image.Resampling.LANCZOS)
        canvas.paste(mark, ((width - side) // 2, (height - side) // 2), mark)
        path = directory / name
        canvas.save(path, format="PNG", optimize=True)
        written.append(path)
    return written


def stage(
    *,
    distribution: Path,
    output_dir: Path,
    identity_name: str,
    publisher: str,
    publisher_display_name: str,
    version: str,
) -> Path:
    distribution = distribution.resolve()
    if not (distribution / "InferBridge.exe").is_file():
        raise FileNotFoundError(f"InferBridge.exe not found in {distribution}")
    if (distribution / "portable.flag").exists():
        raise RuntimeError("The Store package must be built from the installed-mode layout.")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "AppxManifest.xml"
    manifest.write_text(
        render_manifest(
            identity_name=identity_name,
            publisher=publisher,
            publisher_display_name=publisher_display_name,
            version=version,
        ),
        encoding="utf-8",
    )
    entries = [(manifest, "AppxManifest.xml")]
    entries += [(path, f"Assets\\{path.name}") for path in write_assets(output_dir / "Assets")]
    for path in sorted(distribution.rglob("*")):
        if path.is_file():
            relative = path.relative_to(distribution)
            if relative.parts[0].casefold() in {"appxmanifest.xml", "assets"}:
                raise RuntimeError(f"Distribution file collides with package metadata: {relative}")
            entries.append((path, str(relative).replace("/", "\\")))
    mapping = output_dir / "mapping.txt"
    lines = ["[Files]"] + [f'"{source}" "{target}"' for source, target in entries]
    mapping.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True, type=Path)
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
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
