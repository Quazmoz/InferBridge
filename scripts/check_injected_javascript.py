#!/usr/bin/env python3
"""Syntax-check every inline script the composed browser UI actually serves.

The browser client is assembled by injecting JavaScript that lives inside Python
string literals across many ``*_ui`` modules. Nothing compiles that JavaScript, so a
single unbalanced parenthesis silently kills one whole ``<script>`` element and the
feature it implements, while the Python test suite stays green.

Checking the rendered page instead of a hand-maintained module list means a newly
injected surface is covered the moment it reaches the page.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The browser ends a script element at the first `</script`, so extraction here matches
# what actually executes rather than what the Python source intended to emit.
_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>",
    re.DOTALL | re.IGNORECASE,
)
_MINIMUM_EXPECTED_BLOCKS = 5


def rendered_page() -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.server import _index_html

    return _index_html()


def inline_scripts(html: str) -> list[str]:
    return [block for block in _SCRIPT_RE.findall(html) if block.strip()]


def check(node: str, blocks: list[str]) -> list[tuple[int, str]]:
    failures: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="inferbridge-js-") as directory:
        for index, block in enumerate(blocks):
            path = Path(directory) / f"inline-script-{index:02d}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                failures.append((index, detail))
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

    blocks = inline_scripts(rendered_page())
    if len(blocks) < _MINIMUM_EXPECTED_BLOCKS:
        print(
            f"Only {len(blocks)} inline script block(s) were found in the composed UI. "
            "The page or the extraction contract changed; refusing to report success."
        )
        return 2

    failures = check(node, blocks)
    for index, detail in failures:
        print(f"--- inline script block {index} failed to parse ---")
        print(detail)
    if failures:
        print(f"{len(failures)} of {len(blocks)} inline script block(s) failed to parse.")
        return 1

    print(f"All {len(blocks)} inline script block(s) in the composed UI parse cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
