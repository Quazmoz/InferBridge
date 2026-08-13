#!/usr/bin/env python3
"""Syntax-check every piece of JavaScript the browser client loads.

The UI is a static document plus CSS/JavaScript payloads that live in Python string
literals. Nothing compiles that JavaScript, so one unbalanced parenthesis silently kills a
whole ``<script>`` element and the feature it implements while the Python suite stays green.

Payloads are read straight from ``app.ui_registry`` rather than scraped back out of the
rendered page, so every block is checked under the name of the extension that owns it and a
failure points at the module to fix. Both surfaces are covered: the ordinary server
composition, and the desktop composition whose two extra surfaces are capability-gated.

Assets served from ``/ui/`` carry byte-identical payloads to the ones checked here; that
equivalence is asserted by ``tests/test_ui_registry.py::test_inline_and_asset_renderers_never_drift``.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# A browser ends a script element at the first `</script`, so extraction here matches what
# actually executes rather than what the Python source intended to emit.
_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>",
    re.DOTALL | re.IGNORECASE,
)
_MINIMUM_EXPECTED_BLOCKS = 25


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def inline_scripts(html: str) -> list[str]:
    """Return the body of every inline ``<script>`` in *html*, skipping empty ones."""

    return [block for block in _SCRIPT_RE.findall(html) if block.strip()]


def rendered_page() -> str:
    """Return the fully inline composition of the browser client."""

    sys.path.insert(0, str(_repository_root()))
    from app.server import _index_html

    return _index_html()


def collect_blocks() -> list[tuple[str, str]]:
    """Return every ``(label, javascript)`` block the browser executes."""

    root = _repository_root()
    sys.path.insert(0, str(root))

    from app import (
        config as _config,  # noqa: F401 - import registers the composition
        ui_registry,
    )
    from app.ui_composition import DESKTOP_CAPABILITY, compose

    compose()
    blocks: list[tuple[str, str]] = []

    index_html = (root / "web" / "index.html").read_text(encoding="utf-8")
    for position, script in enumerate(_SCRIPT_RE.findall(index_html)):
        if script.strip():
            blocks.append((f"web/index.html inline block {position}", script))

    # The desktop set is a superset of the server set, so iterating it once covers both.
    for extension in ui_registry.extensions({DESKTOP_CAPABILITY}):
        label = extension.extension_id
        if extension.capability:
            label = f"{label} [{extension.capability}]"
        if extension.javascript.strip():
            blocks.append((label, extension.javascript))
    return blocks


def check(node: str, blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="inferbridge-js-") as directory:
        for index, (label, script) in enumerate(blocks):
            path = Path(directory) / f"block-{index:03d}.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                failures.append((label, (result.stderr or result.stdout).strip()))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node",
        default="",
        help="Path to the Node.js executable (default: node on PATH)",
    )
    arguments = parser.parse_args(argv)

    node = arguments.node or shutil.which("node") or ""
    if not node:
        print("Node.js is required to syntax-check the injected browser JavaScript.")
        return 2

    blocks = collect_blocks()
    if len(blocks) < _MINIMUM_EXPECTED_BLOCKS:
        print(
            f"Only {len(blocks)} JavaScript block(s) were collected. The composition or the "
            "collection contract changed; refusing to report success."
        )
        return 2

    failures = check(node, blocks)
    for label, detail in failures:
        print(f"--- {label} failed to parse ---")
        print(detail)
    if failures:
        print(f"{len(failures)} of {len(blocks)} JavaScript block(s) failed to parse.")
        return 1

    print(f"All {len(blocks)} JavaScript block(s) parse cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
