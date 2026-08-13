"""The one place that says what the InferBridge browser client is made of, and in what order.

Read :data:`COMPOSITION` top to bottom and you have the document: the base payloads from
:mod:`app.ui_extension`, then every feature surface, in the order the browser receives them.

Ordering used to be emergent. Each feature module rebound
``app.ui_extension.inject_multimodal_ui`` to a closure wrapping the previous value, so the
document was whatever sequence of imports happened to run, and five surfaces additionally
searched the half-built page for another feature's ``<script>`` tag to insert themselves
before. Nothing stated the intended order and nothing checked it.

Here the order is a list, and a requirement like "split polling must execute before the
progress controller" is declared on the extension itself via ``before=``.

Surfaces that only exist when the desktop launcher provides their APIs carry
``capability="desktop"``. They register unconditionally so they are always present, testable,
and syntax-checked; the desktop entry point activates the capability that makes them render.
That replaces the previous arrangement, in which those two surfaces existed only if
``app.desktop_server`` happened to be the module that imported them.
"""

from __future__ import annotations

import os

from app import (
    branding_ui,
    cancellation_ui,
    chat_context_ui,
    chat_guard_ui,
    chat_queue_ui,
    connection_hub_ui,
    context_budget_ui,
    conversation_management_ui,
    desktop_operations_ui,
    doctor_ui,
    gui_stability,
    header_overflow_ui,
    huggingface_access_ui,
    model_library_ui,
    model_recovery_ui,
    onboarding_ui,
    operation_queue_ui,
    progress_operation_ui,
    progress_reliability,
    runtime_health_ui,
    status_split_ui,
    storage_manager_ui,
    ui_extension,
    ui_polish,
    ui_quality,
    ui_registry,
    ui_runtime,
)
from app.ui_registry import UiExtension

__all__ = [
    "CAPABILITIES_ENV_VAR",
    "COMPOSITION",
    "DESKTOP_CAPABILITY",
    "compose",
    "expected_order",
]

#: Capability naming the surfaces that need desktop-only backend routes.
DESKTOP_CAPABILITY = "desktop"

#: Comma-separated capabilities to activate, so the development server and the browser
#: tests can render desktop surfaces without launching the desktop entry point. Those
#: surfaces were previously unreachable outside ``app.desktop_server``, which is why
#: neither had browser coverage.
CAPABILITIES_ENV_VAR = "INFERBRIDGE_UI_CAPABILITIES"

#: Every extension in the browser client, in registration order.
COMPOSITION: tuple[UiExtension, ...] = (
    # The runtime must come first: it takes over window.fetch once and every later layer
    # registers into its stack rather than reassigning window.fetch again.
    ui_runtime.EXTENSION,
    *ui_extension.BASE_EXTENSIONS,
    # Branding rewrites static shell text, so it runs once the base payloads are in place.
    branding_ui.EXTENSION,
    # Chat behavior: per-chat isolation, then budget preflight, queueing, and safety checks.
    chat_context_ui.EXTENSION,
    context_budget_ui.EXTENSION,
    chat_queue_ui.EXTENSION,
    chat_guard_ui.EXTENSION,
    # Conversation management wraps the completed chat stack; visual polish then decorates
    # its replacement list renderer without owning persistence behavior.
    conversation_management_ui.EXTENSION,
    ui_polish.EXTENSION,
    ui_quality.EXTENSION,
    doctor_ui.EXTENSION,
    header_overflow_ui.EXTENSION,
    # The progress controller is declared before the surfaces that must precede it, because
    # each of those names it in `before=`. Document order ends up: split status, operation
    # reconciliation, cancellation, recovery, queue, then the controller itself.
    progress_reliability.EXTENSION,
    progress_operation_ui.EXTENSION,
    cancellation_ui.EXTENSION,
    model_recovery_ui.EXTENSION,
    status_split_ui.EXTENSION,
    operation_queue_ui.EXTENSION,
    onboarding_ui.EXTENSION,
    model_library_ui.EXTENSION,
    huggingface_access_ui.EXTENSION,
    desktop_operations_ui.EXTENSION,
    connection_hub_ui.EXTENSION,
    gui_stability.EXTENSION,
    # Desktop-only surfaces. Registered always, rendered only when the capability is active.
    storage_manager_ui.EXTENSION,
    runtime_health_ui.EXTENSION,
)


def compose() -> None:
    """Register the whole browser composition. Safe to call any number of times."""

    for extension in COMPOSITION:
        ui_registry.register(extension)
    requested = os.environ.get(CAPABILITIES_ENV_VAR, "")
    ui_registry.activate(*(name.strip() for name in requested.split(",") if name.strip()))


def expected_order(*, desktop: bool = False) -> tuple[str, ...]:
    """Return the extension ids in the order the browser should receive them.

    Derived from :data:`COMPOSITION` the same way the renderers derive it, so a test can
    compare a rendered page against this and catch any reordering.
    """

    capabilities = frozenset({DESKTOP_CAPABILITY}) if desktop else frozenset()
    return tuple(
        item.extension_id for item in ui_registry.extensions(capabilities) if item.javascript
    )
