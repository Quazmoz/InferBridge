"""Guard the composed browser client that no other test compiles or parses.

The page the server serves is assembled from ``web/index.html`` plus JavaScript, CSS, and
markup held in Python string literals across many ``*_ui`` modules. Nothing validates the
result, so a single unbalanced parenthesis or missing close tag reaches users while the
rest of the suite stays green.
"""

from __future__ import annotations

import collections
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.server import _index_html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_injected_javascript import (  # noqa: E402
    check,
    inline_scripts,
    main,
    rendered_page,
)

_BROKEN_SCRIPT = "<script>alert('unbalanced'</script>"
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _StructureScanner(HTMLParser):
    """Report unmatched close tags, unclosed elements, and duplicate ids.

    ``script`` and ``style`` bodies are skipped by ``HTMLParser``'s CDATA handling, so
    markup inside a JavaScript string is not mistaken for real elements.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open_elements: list[tuple[str, int]] = []
        self.problems: list[str] = []
        self.ids: collections.Counter[str] = collections.Counter()
        self.body_children: list[str] = []
        self._body_depth: int | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag in _VOID_ELEMENTS:
            return
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids[attributes["id"]] += 1
        if tag == "body":
            self._body_depth = len(self.open_elements)
        elif self._body_depth is not None and len(self.open_elements) == self._body_depth + 1:
            self.body_children.append(attributes.get("id") or tag)
        self.open_elements.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag) -> None:
        if tag in _VOID_ELEMENTS:
            return
        if not self.open_elements:
            self.problems.append(f"stray </{tag}> at line {self.getpos()[0]}")
            return
        top_tag, top_line = self.open_elements[-1]
        if top_tag == tag:
            self.open_elements.pop()
            return
        self.problems.append(
            f"</{tag}> at line {self.getpos()[0]} closed while <{top_tag}> "
            f"opened on line {top_line} was still open"
        )
        for index in range(len(self.open_elements) - 1, -1, -1):
            if self.open_elements[index][0] == tag:
                del self.open_elements[index:]
                return


def _scan(html: str) -> _StructureScanner:
    scanner = _StructureScanner()
    scanner.feed(html)
    return scanner


# --- markup structure ------------------------------------------------------


def test_static_client_markup_is_balanced():
    scanner = _scan((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
    assert scanner.problems == []
    assert scanner.open_elements == []


def test_composed_page_markup_is_balanced():
    scanner = _scan(_index_html())
    assert scanner.problems == []
    assert scanner.open_elements == []


def test_composed_page_has_no_duplicate_element_ids():
    duplicates = sorted(name for name, count in _scan(_index_html()).ids.items() if count > 1)
    assert duplicates == []


def test_injected_surfaces_attach_directly_to_body():
    # An unclosed div in the static markup previously nested every injected script and
    # style inside a hidden, aria-hidden modal overlay instead of the document body.
    children = _scan(_index_html()).body_children
    assert "custom-model-modal" in children
    assert children.index("custom-model-modal") < len(children) - 1
    assert len(children) > 20


# --- injected JavaScript ---------------------------------------------------


def test_every_injected_surface_reaches_the_page():
    # A hand-maintained module list is what let a broken release-UI script ship. Assert on
    # the composed page instead so a newly injected surface is covered automatically.
    assert len(inline_scripts(rendered_page())) > 20


def test_extraction_ignores_external_and_empty_scripts():
    html = "<script src='x.js'></script><script>  </script><script>const a=1;</script>"
    assert inline_scripts(html) == ["const a=1;"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_composed_browser_javascript_parses():
    assert main([]) == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_checker_reports_a_syntax_error():
    # Blocks are labeled with the extension that owns them, so a failure names the module
    # to fix rather than an anonymous index.
    blocks = [("probe", script) for script in inline_scripts(_BROKEN_SCRIPT)]
    failures = check(shutil.which("node"), blocks)
    assert [label for label, _ in failures] == ["probe"]
