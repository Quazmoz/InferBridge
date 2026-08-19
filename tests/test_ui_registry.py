"""Contract checks for how the browser client is composed.

The composition used to be emergent: each feature module rebound
``app.ui_extension.inject_multimodal_ui`` to a closure wrapping the previous value, so
document order was a side effect of import order and nothing asserted it. These tests make
the order, the ordering requirements, and the two renderers' agreement explicit.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app import (
    config,  # noqa: F401 - importing registers the browser composition
    ui_registry,
)
from app.ui_composition import COMPOSITION, DESKTOP_CAPABILITY, compose, expected_order
from app.ui_registry import UiExtension
from app.ui_runtime import RUNTIME_EXTENSION_ID

_BASE_PAGE = "<html><head></head><body></body></html>"
_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def restored_registry():
    """Rebuild the process-wide composition after a test mutates it.

    The registry is process state, so a test that registers a probe would otherwise leak it
    into every later test's view of the document.
    """

    capabilities = ui_registry.active_capabilities()
    yield
    ui_registry.reset()
    compose()
    ui_registry.activate(*capabilities)


# The document, declared. A change here should be a deliberate decision about what the
# browser loads and in what order, not a surprise from a moved import.
SERVER_ORDER = (
    "inferbridge-runtime",
    "ovllm-vision-extension",
    "ovllm-hardware-advisor-extension",
    "ovllm-model-lifecycle-extension",
    "ovllm-release-extension",
    "ovllm-hf-search-extension",
    "ovllm-responsive-extension",
    "ovllm-generation-state-extension",
    "ovllm-branding-extension",
    "ovllm-chat-context-extension",
    "ovllm-context-budget-extension",
    "ovllm-chat-queue-extension",
    "ovllm-chat-guard-extension",
    "ovllm-conversation-management-extension",
    "ovllm-ui-polish-extension",
    "ovllm-ui-quality-extension",
    "ovllm-system-doctor-extension",
    "ovllm-header-overflow-extension",
    "ovllm-status-split-extension",
    "ovllm-progress-operation-extension",
    "ovllm-model-cancellation-extension",
    "ovllm-model-recovery-extension",
    "ovllm-operation-queue-extension",
    "ovllm-model-progress-extension",
    "ovllm-desktop-onboarding-extension",
    "ovllm-model-library-extension",
    "inferbridge-huggingface-access-extension",
    "ovllm-desktop-operations-extension",
    "ovllm-connection-hub-extension",
    "ovllm-gui-stability-extension",
)

DESKTOP_ONLY_ORDER = (
    "ovllm-desktop-network-extension",
    "ovllm-desktop-browser-auth-extension",
    "ovllm-storage-manager-extension",
    "ovllm-runtime-health-extension",
)


def _desktop_page() -> str:
    ui_registry.activate(DESKTOP_CAPABILITY)
    return ui_registry.render_inline(_BASE_PAGE, {DESKTOP_CAPABILITY})


def test_the_runtime_is_the_first_extension_in_the_document():
    """The runtime takes over window.fetch, so it must load before anything using it.

    Vision and the hardware advisor call ``InferBridge.chain()`` as they load. If the
    runtime were placed later, both would throw and silently lose their features.
    """

    compose()
    order = [item.extension_id for item in ui_registry.extensions()]
    assert order[0] == RUNTIME_EXTENSION_ID

    page = ui_registry.render_inline(_BASE_PAGE)
    runtime_at = page.index(f'<script id="{RUNTIME_EXTENSION_ID}"')
    for consumer in ("ovllm-vision-extension", "ovllm-hardware-advisor-extension"):
        assert runtime_at < page.index(f'<script id="{consumer}"')


def test_composition_order_is_the_declared_order():
    compose()
    assert expected_order() == SERVER_ORDER
    assert expected_order(desktop=True) == SERVER_ORDER + DESKTOP_ONLY_ORDER


def test_rendered_document_follows_the_declared_order():
    compose()
    page = ui_registry.render_inline(_BASE_PAGE)
    positions = [page.index(f'<script id="{name}"') for name in SERVER_ORDER]
    assert positions == sorted(positions)


def test_ordering_requirements_put_dependants_before_the_progress_controller():
    """The five layers that must precede the progress controller still do.

    This was previously achieved by each layer searching the half-built page for another
    layer's ``<script>`` tag. It is now declared with ``before=`` and checked here.
    """

    compose()
    page = ui_registry.render_inline(_BASE_PAGE)
    controller = page.index('<script id="ovllm-model-progress-extension"')
    for dependant in (
        "ovllm-status-split-extension",
        "ovllm-progress-operation-extension",
        "ovllm-model-cancellation-extension",
        "ovllm-model-recovery-extension",
        "ovllm-operation-queue-extension",
    ):
        assert page.index(f'<script id="{dependant}"') < controller
    # Split polling additionally has to precede operation reconciliation.
    assert page.index('<script id="ovllm-status-split-extension"') < page.index(
        '<script id="ovllm-progress-operation-extension"'
    )


def test_rendering_is_idempotent():
    compose()
    once = ui_registry.render_inline(_BASE_PAGE)
    assert ui_registry.render_inline(once) == once
    for name in SERVER_ORDER:
        assert once.count(f'<script id="{name}"') == 1


def test_every_extension_appears_exactly_once():
    compose()
    page = _desktop_page()
    for name in SERVER_ORDER + DESKTOP_ONLY_ORDER:
        assert page.count(f'id="{name}"') == 1, name


def test_desktop_surfaces_are_always_registered_but_render_only_when_activated():
    """Registration no longer depends on which entry point imported the module.

    These surfaces used to exist only if ``app.desktop_server`` was the importer, which is
    why they lacked browser coverage. They are now always registered and gated by
    capability, so they can be composed, syntax-checked, and driven in a test.
    """

    compose()
    registered = {item.extension_id for item in ui_registry.registered()}
    for name in DESKTOP_ONLY_ORDER:
        assert name in registered

    server_page = ui_registry.render_inline(_BASE_PAGE, frozenset())
    for name in DESKTOP_ONLY_ORDER:
        assert name not in server_page

    desktop_page = ui_registry.render_inline(_BASE_PAGE, {DESKTOP_CAPABILITY})
    for name in DESKTOP_ONLY_ORDER:
        assert f'id="{name}"' in desktop_page


def test_inline_and_asset_renderers_never_drift():
    """Tests read payloads out of the inline render; browsers load the asset render.

    Both come from one registry, and this asserts they carry the same extensions in the
    same order with identical payload bytes.
    """

    compose()
    assert ui_registry.renderer_disagreements() == []
    assert ui_registry.renderer_disagreements({DESKTOP_CAPABILITY}) == []


def test_asset_urls_are_content_addressed():
    compose()
    manifest = ui_registry.asset_manifest()
    assert manifest
    for url, asset in manifest.items():
        assert url.startswith(ui_registry.ASSET_PREFIX)
        assert asset.url == url
        assert asset.body == asset.content.encode("utf-8")
        assert len(asset.gzip_body) < len(asset.body)
        # <prefix><id>.<digest>.<suffix>, where the digest is taken from the content. That
        # is what makes immutable caching safe: a payload edit is a different URL.
        digest = url.rsplit(".", 2)[1]
        assert re.fullmatch(r"[0-9a-f]{16}", digest), url
        expected = hashlib.sha256(asset.content.encode("utf-8")).hexdigest()[: len(digest)]
        assert digest == expected, url


def test_asset_render_references_every_published_asset():
    compose()
    page = ui_registry.render_document(_BASE_PAGE, "nonce-value")
    for url in ui_registry.asset_manifest():
        assert url in page


def test_asset_render_contributes_no_inline_script():
    """Every payload is an external asset, so composition adds no inline script at all.

    That makes the hardened ``script-src`` a structural property rather than something each
    new surface has to remember to preserve.
    """

    compose()
    page = ui_registry.render_document(_BASE_PAGE, "nonce-value")
    inline = [match.group(0) for match in re.finditer(r"<script(?![^>]*\bsrc\s*=)[^>]*>", page)]
    assert inline == [], inline


def test_asset_render_nonces_the_shell_own_inline_blocks():
    """The static shell's own blocks stay inline, so they must carry the response nonce."""

    compose()
    shell = (_REPO / "web" / "index.html").read_text(encoding="utf-8")
    page = ui_registry.render_document(shell, "nonce-value", {DESKTOP_CAPABILITY})
    inline = [match.group(0) for match in re.finditer(r"<script(?![^>]*\bsrc\s*=)[^>]*>", page)]
    assert inline, "the shell should still have its own inline script"
    for tag in inline:
        assert 'nonce="nonce-value"' in tag, tag
    styles = [match.group(0) for match in re.finditer(r"<style[^>]*>", page)]
    assert styles
    for tag in styles:
        assert 'nonce="nonce-value"' in tag, tag


def test_asset_render_requires_a_nonce():
    compose()
    with pytest.raises(ValueError):
        ui_registry.render_document(_BASE_PAGE, "")


def test_registering_a_conflicting_payload_is_rejected():
    """Two features claiming one element id is a defect the old chain hid."""

    compose()
    with pytest.raises(ValueError, match="already registered"):
        ui_registry.register(
            UiExtension(extension_id=RUNTIME_EXTENSION_ID, javascript="// different")
        )
    # Re-registering the identical entry stays a no-op, so install_* remains idempotent.
    before = ui_registry.revision()
    for extension in COMPOSITION:
        ui_registry.register(extension)
    assert ui_registry.revision() == before


def test_revision_changes_when_the_composition_changes(restored_registry):
    """Cached renders key on this, so a late registration cannot serve a stale page."""

    compose()
    before = ui_registry.revision()
    ui_registry.register(UiExtension(extension_id="test-revision-probe", javascript="//"))
    assert ui_registry.revision() > before

    activation = ui_registry.revision()
    ui_registry.activate("test-capability-probe")
    assert ui_registry.revision() > activation


def test_render_only_composes_a_single_surface():
    compose()
    page = ui_registry.render_only(_BASE_PAGE, ["ovllm-system-doctor-extension"])
    assert 'id="ovllm-system-doctor-extension"' in page
    assert 'id="ovllm-vision-extension"' not in page
    with pytest.raises(LookupError):
        ui_registry.render_only(_BASE_PAGE, ["not-a-real-extension"])


def test_declared_payload_shapes_are_validated():
    with pytest.raises(ValueError):
        UiExtension(extension_id="")
    with pytest.raises(ValueError, match="must not carry script"):
        UiExtension(extension_id="x", javascript="//", head_html="<script></script>", head_id="h")
    with pytest.raises(ValueError, match="css requires javascript"):
        UiExtension(extension_id="x", css="a{}")
    with pytest.raises(ValueError, match="head_id"):
        UiExtension(extension_id="x", javascript="//", head_html="<link>")
    with pytest.raises(ValueError, match="no payload"):
        UiExtension(extension_id="x")
