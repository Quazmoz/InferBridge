"""Privacy-safe diagnostics actions for the desktop tray."""

from __future__ import annotations

import logging

from app import __version__
from app.desktop_shell import confirm_dialog, copy_to_clipboard, open_path, show_dialog
from app.diagnostics import DiagnosticsCollector, diagnostics_confirmation_summary
from app.tray_support import APP_TITLE

logger = logging.getLogger("ov-llm.tray")


class TrayDiagnosticsActionsMixin:
    def _diagnostics_collector(self) -> DiagnosticsCollector:
        payload = (
            self.controller.status_payload()
            or self.last_status_payload
            or {
                "application_version": __version__,
                "installation_mode": "portable" if self.paths.portable else "installed",
                "controller_available": True,
                "server_port": self.controller.port,
                "live": False,
                "ready": False,
                "server_status": "stopped",
                "events": [],
            }
        )
        hardware = None
        npu = payload.get("npu_readiness") if isinstance(payload, dict) else None
        if self.controller.running:
            try:
                scan_result = self.controller.run_hardware_scan()
                scan = scan_result.get("scan") if isinstance(scan_result, dict) else None
                hardware = scan.get("hardware") if isinstance(scan, dict) else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Hardware diagnostics collection failed: %s", str(exc)[:180])
        return DiagnosticsCollector(
            paths=self.paths,
            runtime_snapshot=payload,
            effective_configuration={
                "host": "127.0.0.1",
                "port": self.controller.port or self.args.port,
                "device": payload.get("device") if isinstance(payload, dict) else None,
                "api_key_configured": bool(payload.get("api_key_configured"))
                if isinstance(payload, dict)
                else False,
                "cors_configured": False,
            },
            hardware_snapshot=hardware,
            npu_readiness=npu if isinstance(npu, dict) else None,
            benchmark_summaries=[payload.get("benchmark")]
            if isinstance(payload, dict) and isinstance(payload.get("benchmark"), dict)
            else [],
            build_metadata={
                "packaging_version": __version__,
                "artifact_kind": "portable" if self.paths.portable else "installed",
            },
        )

    def copy_diagnostics(self) -> None:
        collector = self._diagnostics_collector()
        summary = collector.support_summary()
        try:
            copy_to_clipboard(summary)
        except (OSError, RuntimeError) as exc:
            logger.warning("Clipboard unavailable for diagnostics: %s", str(exc)[:180])
            # Never leave the user empty-handed: save the same privacy-safe text
            # locally, open its folder and show the path.
            try:
                path = collector.write_support_summary(summary)
            except Exception:  # noqa: BLE001 - fall back to the export guidance
                raise RuntimeError(
                    "Could not copy diagnostics because the clipboard is unavailable or busy, "
                    "and the summary could not be saved. Use the tray's Export Diagnostics "
                    "action instead; review the local ZIP archive before sharing it."
                ) from None
            self.last_diagnostics_path = path
            open_path(path.parent)
            show_dialog(
                APP_TITLE,
                "The clipboard is unavailable, so the privacy-safe diagnostics were saved "
                "to a text file instead. Attach or paste it into the InferBridge feedback "
                "form or a GitHub issue. Nothing was uploaded.\n\n"
                f"{path}",
            )
            return
        show_dialog(
            APP_TITLE,
            "Privacy-safe diagnostics copied. Paste them into the InferBridge feedback form "
            "or a GitHub issue. Nothing was uploaded.",
        )

    def export_diagnostics(self) -> None:
        if not confirm_dialog(APP_TITLE, diagnostics_confirmation_summary()):
            return
        result = self._diagnostics_collector().export()
        self.last_diagnostics_path = result.path
        show_dialog(
            APP_TITLE,
            "Diagnostics ZIP created locally. Review it before attaching it to the InferBridge "
            "feedback form or a GitHub issue.\n\n"
            f"{result.path}",
        )
