"""Guard the injected browser JavaScript that no Python test would otherwise compile."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_injected_javascript import (  # noqa: E402
    check,
    inline_scripts,
    main,
    rendered_page,
)

_BROKEN_SCRIPT = "<script>alert('unbalanced'</script>"


def test_every_injected_surface_reaches_the_page():
    blocks = inline_scripts(rendered_page())
    # A hand-maintained module list is what let a broken release-UI script ship. Assert on
    # the composed page instead so a newly injected surface is covered automatically.
    assert len(blocks) > 20


def test_extraction_ignores_external_and_empty_scripts():
    html = "<script src='x.js'></script><script>  </script><script>const a=1;</script>"
    assert inline_scripts(html) == ["const a=1;"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_composed_browser_javascript_parses():
    assert main([]) == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_checker_reports_a_syntax_error():
    failures = check(shutil.which("node"), inline_scripts(_BROKEN_SCRIPT))
    assert [index for index, _ in failures] == [0]
