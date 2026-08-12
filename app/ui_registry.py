"""Ordered, inspectable composition of the bundled InferBridge browser client.

The browser UI is one static document (``web/index.html``) plus a set of CSS and
JavaScript payloads that live in ``app/*_ui.py`` modules. Every payload declares itself
as a :class:`UiExtension` and registers here exactly once. Composing the page is then a
pure function over an ordered list that a reader can inspect in one place.

This replaces an earlier design in which each payload composed itself by rebinding
``app.ui_extension.inject_multimodal_ui`` to a closure wrapping the previous value.
Document order was an emergent property of module import order, ``app.server`` bound the
composed function at import time (so late installs had to reach into ``sys.modules`` and
clear a cache), and no single place described the page. Here, order is data, the render is
a pure function, and ``inject_multimodal_ui`` is a stable dispatcher.

Two renderers share the one registry:

``render_inline``
    Embeds every payload directly in the document. This reproduces the historical output
    byte for byte and stays the surface that source-level tests and the injected-JavaScript
    syntax gate assert against.
``render_document``
    References content-addressed ``/ui/`` assets and nonces the document's own inline
    blocks. This is what the server serves: a small cacheable document plus immutable
    assets, instead of one uncacheable ~700 KB response.

:func:`renderer_disagreements` proves the two cannot drift apart.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "UiAsset",
    "UiExtension",
    "activate",
    "active_capabilities",
    "asset_manifest",
    "deactivate",
    "extensions",
    "register",
    "registered",
    "render_document",
    "render_inline",
    "render_only",
    "renderer_disagreements",
    "reset",
    "revision",
]

# Content-addressed asset URLs. The hash makes every payload change a new URL, so the
# immutable cache directive on the asset response is always safe.
ASSET_PREFIX = "/ui/"
_ASSET_DIGEST_LENGTH = 16

# Nonce every inline block in the base document so the served page needs no
# 'unsafe-inline' in script-src. Only tags that carry no external reference qualify.
_BASE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)(?![^>]*\bnonce\s*=)([^>]*)>", re.IGNORECASE
)
_BASE_STYLE_RE = re.compile(r"<style(?![^>]*\bnonce\s*=)([^>]*)>", re.IGNORECASE)


@dataclass(frozen=True)
class UiExtension:
    """One CSS/JavaScript payload composed into the browser client.

    ``extension_id`` is both the ``id`` of the emitted ``<script>`` and the key that makes
    composition idempotent: an extension already present in the document is never added a
    second time.

    A payload is always structured: ``javascript``, optionally with ``css``. There is no
    pre-rendered-markup escape hatch, which is what lets every payload be served as an
    external asset and keeps the served document free of inline script.

    ``before`` names extensions this payload must precede, most preferred first. The first
    name already placed decides the position; if none is present the payload goes last.
    That expresses an ordering requirement directly ("split polling must run before the
    progress controller") instead of leaving it implied by import order.
    """

    extension_id: str
    javascript: str = ""
    css: str = ""
    style_id: str = ""
    head_html: str = ""
    head_id: str = ""
    before: tuple[str, ...] = ()
    transform: Callable[[str], str] | None = None
    capability: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.extension_id:
            raise ValueError("A UI extension needs a non-empty extension_id.")
        if self.css and not self.javascript:
            raise ValueError(f"{self.extension_id}: css requires javascript on the same entry.")
        if self.head_html and not self.head_id:
            raise ValueError(f"{self.extension_id}: head_html requires head_id to stay idempotent.")
        if "<script" in self.head_html:
            raise ValueError(
                f"{self.extension_id}: head_html must not carry script. Put JavaScript in "
                "`javascript` so it is served as an asset rather than inlined."
            )
        if not (self.javascript or self.head_html or self.transform):
            raise ValueError(f"{self.extension_id}: the extension has no payload and no transform.")

    @property
    def resolved_style_id(self) -> str:
        """Return the ``<style>`` element id, defaulting to the documented suffix."""

        return self.style_id or f"{self.extension_id}-styles"

    @property
    def serves_assets(self) -> bool:
        """Return whether this payload can be served from a URL rather than inlined."""

        return bool(self.javascript)


@dataclass(frozen=True)
class UiAsset:
    """One immutable, content-addressed browser asset.

    Both encodings are computed when the manifest is built, so serving costs nothing per
    request. Compression is done here rather than by a global response middleware because
    this application streams Server-Sent Events for chat: a middleware that compresses
    every response would buffer those chunks and delay tokens. These assets are static and
    never streamed, so pre-compressing them is free of that hazard.
    """

    url: str
    content: str
    media_type: str
    body: bytes
    gzip_body: bytes

    @classmethod
    def build(cls, url: str, content: str, media_type: str) -> UiAsset:
        body = content.encode("utf-8")
        # mtime=0 keeps the compressed bytes deterministic for a given payload.
        return cls(
            url=url,
            content=content,
            media_type=media_type,
            body=body,
            gzip_body=gzip.compress(body, compresslevel=9, mtime=0),
        )


@dataclass
class _State:
    entries: list[UiExtension] = field(default_factory=list)
    capabilities: set[str] = field(default_factory=set)
    revision: int = 0


_LOCK = threading.RLock()
_STATE = _State()


def register(extension: UiExtension) -> None:
    """Register *extension* once. Re-registering identical content is a no-op.

    Registering a different payload under an id already in use raises: two features
    fighting over one element id is a defect, and the old chain hid it by silently keeping
    whichever module imported first.
    """

    with _LOCK:
        for existing in _STATE.entries:
            if existing.extension_id != extension.extension_id:
                continue
            if existing == extension:
                return
            raise ValueError(
                f"{extension.extension_id} is already registered with a different payload."
            )
        _STATE.entries.append(extension)
        _STATE.revision += 1


def activate(*capabilities: str) -> None:
    """Mark *capabilities* available so their gated extensions render."""

    with _LOCK:
        fresh = {name for name in capabilities if name and name not in _STATE.capabilities}
        if not fresh:
            return
        _STATE.capabilities |= fresh
        _STATE.revision += 1


def deactivate(*capabilities: str) -> None:
    """Turn *capabilities* back off, so their gated extensions stop rendering.

    The symmetric counterpart to :func:`activate`, which lets a test exercise a gated
    surface and then hand the process back its ordinary composition.
    """

    with _LOCK:
        present = {name for name in capabilities if name in _STATE.capabilities}
        if not present:
            return
        _STATE.capabilities -= present
        _STATE.revision += 1


def active_capabilities() -> frozenset[str]:
    """Return the capabilities currently enabled for rendering."""

    with _LOCK:
        return frozenset(_STATE.capabilities)


def revision() -> int:
    """Return a counter that changes whenever the composition changes.

    Callers that cache a rendered page key on this instead of assuming the composition is
    frozen by the time they first render.
    """

    with _LOCK:
        return _STATE.revision


def registered() -> tuple[UiExtension, ...]:
    """Return every registered extension in registration order, ungated and unordered."""

    with _LOCK:
        return tuple(_STATE.entries)


def reset() -> None:
    """Drop all registrations. Intended for tests that compose a surface in isolation."""

    with _LOCK:
        _STATE.entries.clear()
        _STATE.capabilities.clear()
        _STATE.revision += 1


def _snapshot(capabilities: Iterable[str] | None) -> tuple[tuple[UiExtension, ...], frozenset[str]]:
    with _LOCK:
        entries = tuple(_STATE.entries)
        active = frozenset(_STATE.capabilities if capabilities is None else capabilities)
    return entries, active


def _place(entries: Sequence[UiExtension]) -> tuple[UiExtension, ...]:
    """Resolve declared ``before`` requirements into a single document order."""

    placed: list[UiExtension] = []
    for entry in entries:
        position = len(placed)
        for anchor in entry.before:
            found = next(
                (i for i, other in enumerate(placed) if other.extension_id == anchor), None
            )
            if found is not None:
                position = found
                break
        placed.insert(position, entry)
    return tuple(placed)


def extensions(capabilities: Iterable[str] | None = None) -> tuple[UiExtension, ...]:
    """Return the active extensions in the exact order they appear in the document."""

    entries, active = _snapshot(capabilities)
    return _place(
        [entry for entry in entries if not entry.capability or entry.capability in active]
    )


def _insert_before(document: str, needle: str, payload: str) -> str:
    if needle in document:
        return document.replace(needle, f"{payload}{needle}", 1)
    return document + payload


def _inline_body_payload(extension: UiExtension) -> str:
    if not extension.javascript:
        return ""
    script = f'<script id="{extension.extension_id}">\n{extension.javascript}\n</script>\n'
    if not extension.css:
        return f"\n{script}"
    return f'\n<style id="{extension.resolved_style_id}">\n{extension.css}\n</style>\n{script}'


def _apply(document: str, extension: UiExtension, body_payload: str, head_payload: str) -> str:
    # The transform runs unconditionally, exactly as the wrapper chain did: it rewrites
    # static shell text and must not be skipped just because the payload is already there.
    if extension.transform is not None:
        document = extension.transform(document)
    if head_payload and f'id="{extension.head_id}"' not in document:
        document = _insert_before(document, "</head>", head_payload)
    if body_payload and f'id="{extension.extension_id}"' not in document:
        document = _insert_before(document, "</body>", body_payload)
    return document


def render_inline(document: str, capabilities: Iterable[str] | None = None) -> str:
    """Compose *document* with every active payload embedded directly.

    This is the historical ``inject_multimodal_ui`` output, byte for byte. It is also the
    surface that asserts on payload content: source-level tests and the Node syntax gate
    read the JavaScript out of the rendered page.
    """

    for extension in extensions(capabilities):
        document = _apply(
            document,
            extension,
            _inline_body_payload(extension),
            extension.head_html,
        )
    return document


def render_only(document: str, extension_ids: Iterable[str]) -> str:
    """Compose *document* with only the named extensions, in registration order.

    Lets a test exercise one surface without standing up the whole page and without
    monkey-patching the composition itself.
    """

    wanted = tuple(extension_ids)
    by_id = {entry.extension_id: entry for entry in registered()}
    missing = [name for name in wanted if name not in by_id]
    if missing:
        raise LookupError(f"Not registered: {', '.join(missing)}")
    for extension in _place([by_id[name] for name in wanted]):
        document = _apply(
            document,
            extension,
            _inline_body_payload(extension),
            extension.head_html,
        )
    return document


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:_ASSET_DIGEST_LENGTH]


def _asset_url(extension_id: str, content: str, suffix: str) -> str:
    return f"{ASSET_PREFIX}{extension_id}.{_digest(content)}.{suffix}"


def asset_manifest(capabilities: Iterable[str] | None = None) -> dict[str, UiAsset]:
    """Return every servable asset for the active composition, keyed by URL.

    Content addressing means a payload edit produces a new URL, so responses can be
    cached immutably and a stale asset can never be served for fresh markup.
    """

    manifest: dict[str, UiAsset] = {}
    for extension in extensions(capabilities):
        if not extension.serves_assets:
            continue
        url = _asset_url(extension.extension_id, extension.javascript, "js")
        manifest[url] = UiAsset.build(url, extension.javascript, "text/javascript; charset=utf-8")
        if extension.css:
            style_url = _asset_url(extension.resolved_style_id, extension.css, "css")
            manifest[style_url] = UiAsset.build(style_url, extension.css, "text/css; charset=utf-8")
    return manifest


def _linked_body_payload(extension: UiExtension) -> str:
    if not extension.javascript:
        return ""
    script_url = _asset_url(extension.extension_id, extension.javascript, "js")
    # Deliberately not deferred or async. Classic external scripts execute in document order
    # and interleave correctly with the shell's own inline script, so execution order matches
    # the fully inline render exactly. `defer` would run every external payload after every
    # inline one and reorder the composition.
    script = f'<script id="{extension.extension_id}" src="{script_url}"></script>\n'
    if not extension.css:
        return f"\n{script}"
    style_url = _asset_url(extension.resolved_style_id, extension.css, "css")
    return (
        f'\n<link id="{extension.resolved_style_id}" rel="stylesheet" href="{style_url}">\n{script}'
    )


def _nonce_inline_blocks(document: str, nonce: str) -> str:
    """Add *nonce* to inline ``<script>``/``<style>`` tags in the base document.

    Applied to the base document before any payload is inserted, so payload text can never
    be mistaken for markup.
    """

    # The nonce goes after any existing attributes so an element keeps opening with
    # `<script id="...">`, which is how both renderers and their tests locate extensions.
    document = _BASE_SCRIPT_RE.sub(lambda m: f'<script{m.group(1)} nonce="{nonce}">', document)
    return _BASE_STYLE_RE.sub(lambda m: f'<style{m.group(1)} nonce="{nonce}">', document)


def render_document(
    document: str,
    nonce: str,
    capabilities: Iterable[str] | None = None,
) -> str:
    """Compose *document* with payloads referenced as cacheable ``/ui/`` assets.

    ``nonce`` must be a fresh per-response value and must appear in the response's
    ``script-src``. Every inline block in the result carries it, which is what lets the
    policy drop ``'unsafe-inline'``.
    """

    if not nonce:
        raise ValueError("render_document requires a per-response nonce.")
    # Nonce the base document before inserting anything, so payload text can never be
    # mistaken for markup. Payloads themselves become external assets and need no nonce, and
    # `head_html` is validated to carry no script.
    document = _nonce_inline_blocks(document, nonce)
    for extension in extensions(capabilities):
        document = _apply(
            document,
            extension,
            _linked_body_payload(extension),
            extension.head_html,
        )
    return document


def renderer_disagreements(capabilities: Iterable[str] | None = None) -> list[str]:
    """Return the ways the inline and asset renderers disagree about the composition.

    An empty list means both renderers carry the same extensions, in the same order, with
    identical payload bytes -- only the transport differs. A test asserts this stays empty
    so the surface the tests read cannot drift from the surface the browser loads.
    """

    base = "<html><head></head><body></body></html>"
    inline = render_inline(base, capabilities)
    linked = render_document(base, "renderer-comparison", capabilities)
    active = extensions(capabilities)
    manifest = asset_manifest(capabilities)
    problems: list[str] = []

    # Both renderers open every payload with `<script id="..."`, so the tag prefix locates
    # an extension without matching text that merely mentions the id.
    def order(page: str) -> list[str]:
        found = [
            (page.index(f'<script id="{item.extension_id}"'), item.extension_id)
            for item in active
            if f'<script id="{item.extension_id}"' in page
        ]
        return [name for _, name in sorted(found)]

    inline_order, linked_order = order(inline), order(linked)
    expected = [item.extension_id for item in active if item.javascript]
    if inline_order != expected:
        problems.append(f"inline render order {inline_order} != registry order {expected}")
    if linked_order != expected:
        problems.append(f"asset render order {linked_order} != registry order {expected}")

    for extension in active:
        if not extension.serves_assets:
            continue
        if extension.javascript not in inline:
            problems.append(f"{extension.extension_id}: inline render omits the JavaScript")
        url = _asset_url(extension.extension_id, extension.javascript, "js")
        asset = manifest.get(url)
        if asset is None:
            problems.append(f"{extension.extension_id}: no asset published for {url}")
        elif asset.content != extension.javascript:
            problems.append(f"{extension.extension_id}: served JavaScript differs from the payload")
        elif url not in linked:
            problems.append(f"{extension.extension_id}: asset render does not reference {url}")
    return problems
