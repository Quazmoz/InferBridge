"""Dedicated WebGUI recovery screen for interrupted model preparation."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-model-recovery-extension"

MODEL_RECOVERY_UI_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmModelRecoveryInstalled) return;
    window.__ovllmModelRecoveryInstalled = true;

    const STATUS_PATHS = new Set(['/v1/system/status', '/v1/models/status']);
    const RECOVERY_PATH = '/v1/models/recovery';
    const ACTION_PATH = '/v1/models/recovery/action';
    const AUTO_OPEN_PREFIX = 'inferbridge.model-recovery.seen.';
    let latestPayload = null;
    let recoveries = [];
    let activeRecovery = null;
    let actionInFlight = '';
    let feedback = null;
    let detailsLoadedFor = '';
    let previousFocus = null;

    const style = document.createElement('style');
    style.textContent = `
        .ovmr-banner{position:fixed;left:50%;bottom:18px;z-index:1900;display:flex;align-items:center;gap:12px;max-width:min(760px,calc(100vw - 28px));padding:11px 13px;border:1px solid color-mix(in srgb,var(--amber,#e7a93b) 52%,var(--border));border-radius:12px;background:color-mix(in srgb,var(--surface-1,#15191f) 94%,var(--amber,#e7a93b) 6%);box-shadow:0 14px 38px rgba(0,0,0,.28);transform:translateX(-50%);color:var(--text-1)}
        .ovmr-banner[hidden]{display:none}
        .ovmr-banner-copy{min-width:0;flex:1}
        .ovmr-banner-title{font-size:12px;font-weight:800;line-height:1.35}
        .ovmr-banner-detail{margin-top:2px;color:var(--text-2);font-size:10.5px;line-height:1.4;overflow-wrap:anywhere}
        .ovmr-button{min-height:34px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:11px;font-weight:750;cursor:pointer}
        .ovmr-button:hover:not(:disabled){background:var(--surface-3)}
        .ovmr-button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
        .ovmr-button:disabled{opacity:.55;cursor:not-allowed}
        .ovmr-button.primary{border-color:color-mix(in srgb,var(--accent) 65%,var(--border));background:color-mix(in srgb,var(--accent) 18%,var(--surface-2));color:var(--text-1)}
        .ovmr-button.danger{border-color:color-mix(in srgb,var(--red) 52%,var(--border));color:var(--red)}
        .ovmr-overlay{position:fixed;inset:0;z-index:2100;display:grid;place-items:center;padding:18px;background:rgba(4,7,11,.72);backdrop-filter:blur(5px)}
        .ovmr-overlay[hidden]{display:none}
        .ovmr-dialog{width:min(720px,100%);max-height:min(780px,calc(100vh - 36px));overflow:auto;border:1px solid var(--border);border-radius:18px;background:var(--surface-1);box-shadow:0 24px 80px rgba(0,0,0,.48);color:var(--text-1)}
        .ovmr-header{display:flex;align-items:flex-start;gap:16px;padding:20px 22px 14px;border-bottom:1px solid var(--border)}
        .ovmr-heading-copy{min-width:0;flex:1}
        .ovmr-eyebrow{color:var(--amber,#e7a93b);font-size:10px;font-weight:850;letter-spacing:.11em;text-transform:uppercase}
        .ovmr-title{margin:5px 0 0;font-size:20px;line-height:1.25}
        .ovmr-subtitle{margin:7px 0 0;color:var(--text-2);font-size:12px;line-height:1.55}
        .ovmr-close{width:34px;height:34px;padding:0;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-2);font:inherit;font-size:20px;cursor:pointer}
        .ovmr-body{padding:18px 22px 22px}
        .ovmr-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
        .ovmr-fact{min-width:0;padding:12px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2)}
        .ovmr-fact-label{color:var(--text-3);font-size:9.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
        .ovmr-fact-value{margin-top:5px;font-size:12px;font-weight:760;line-height:1.45;overflow-wrap:anywhere}
        .ovmr-recommendation{margin-top:12px;padding:13px 14px;border:1px solid color-mix(in srgb,var(--accent) 42%,var(--border));border-radius:11px;background:color-mix(in srgb,var(--accent) 8%,var(--surface-2));font-size:12px;line-height:1.55}
        .ovmr-recommendation strong{display:block;margin-bottom:3px}
        .ovmr-actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:16px}
        .ovmr-feedback{margin-top:13px;padding:10px 12px;border-radius:9px;background:var(--surface-2);color:var(--text-2);font-size:11px;line-height:1.5}
        .ovmr-feedback.error{border:1px solid color-mix(in srgb,var(--red) 46%,var(--border));color:var(--red)}
        .ovmr-details{margin-top:14px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2)}
        .ovmr-details[hidden]{display:none}
        .ovmr-details-title{padding:10px 12px;border-bottom:1px solid var(--border);font-size:11px;font-weight:800}
        .ovmr-details-message{padding:11px 12px;color:var(--text-2);font-size:11px;line-height:1.5;overflow-wrap:anywhere}
        .ovmr-details-log{margin:0;padding:0 12px 12px;max-height:190px;overflow:auto;white-space:pre-wrap;color:var(--text-3);font:10.5px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}
        @media(max-width:620px){.ovmr-grid{grid-template-columns:1fr}.ovmr-header,.ovmr-body{padding-left:16px;padding-right:16px}.ovmr-banner{align-items:stretch;flex-direction:column}.ovmr-banner .ovmr-button{width:100%}.ovmr-actions{display:grid;grid-template-columns:1fr}.ovmr-button{width:100%}}
        @media(prefers-reduced-motion:reduce){.ovmr-overlay{backdrop-filter:none}}
    `;
    document.head.appendChild(style);

    function endpoint(input) {
        const value = typeof input === 'string'
            ? input
            : input instanceof URL
                ? input.href
                : input?.url || '';
        try {
            const url = new URL(value, window.location.href);
            return { path: url.pathname, sameOrigin: url.origin === window.location.origin };
        } catch {
            return { path: '', sameOrigin: false };
        }
    }

    function methodOf(input, init) {
        return String(init?.method || input?.method || 'GET').toUpperCase();
    }

    function requestHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        if (key) headers.Authorization = `Bearer ${key}`;
        return headers;
    }

    function errorMessage(payload, fallback) {
        const detail = payload?.detail;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail && typeof detail.message === 'string' && detail.message.trim()) {
            return detail.message.trim();
        }
        return fallback;
    }

    function statusModels(payload) {
        return Array.isArray(payload?.models?.available) ? payload.models.available : [];
    }

    function collectRecoveries(payload) {
        const selectedId = document.getElementById('model-select')?.value || '';
        const found = statusModels(payload)
            .map(model => model?.recovery)
            .filter(item => item?.available && item.recovery_id);
        found.sort((left, right) => {
            if (left.model_id === selectedId) return -1;
            if (right.model_id === selectedId) return 1;
            return Number(right.interrupted_at || 0) - Number(left.interrupted_at || 0);
        });
        return found;
    }

    function humanState(value) {
        const labels = {
            reusable: 'Reusable',
            not_found: 'Not found',
            unknown: 'Unknown',
            complete: 'Complete',
            incomplete: 'Incomplete',
            missing: 'Not created',
            none: 'None',
            download: 'Download',
            conversion: 'Conversion',
            load: 'Model load',
        };
        return labels[value] || String(value || 'Unknown').replaceAll('_', ' ');
    }

    function actionLabel(action) {
        const labels = {
            resume: 'Resume preparation',
            retry_failed_stage: 'Retry failed stage',
            restart_download: 'Restart from download',
            remove_incomplete_files: 'Remove incomplete files',
        };
        return labels[action] || action;
    }

    function recommendationText(recovery) {
        const action = actionLabel(recovery?.recommended_action);
        if (recovery?.recommended_action === 'resume') {
            return `${action}. Cached model files can be reused while conversion restarts safely.`;
        }
        if (recovery?.recommended_action === 'retry_failed_stage') {
            return `${action}. The completed OpenVINO files can be reused.`;
        }
        return `${action}. Existing cached source files will be removed before a fresh download.`;
    }

    function createElement(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function ensureBanner() {
        let banner = document.getElementById('ov-model-recovery-banner');
        if (banner) return banner;
        banner = createElement('div', 'ovmr-banner');
        banner.id = 'ov-model-recovery-banner';
        banner.hidden = true;
        const copy = createElement('div', 'ovmr-banner-copy');
        copy.append(
            createElement('div', 'ovmr-banner-title'),
            createElement('div', 'ovmr-banner-detail'),
        );
        const button = createElement('button', 'ovmr-button primary', 'Review recovery');
        button.type = 'button';
        button.addEventListener('click', () => {
            if (recoveries[0]) void openRecovery(recoveries[0]);
        });
        banner.append(copy, button);
        document.body.appendChild(banner);
        return banner;
    }

    function renderBanner() {
        const banner = ensureBanner();
        const recovery = recoveries[0];
        banner.hidden = !recovery || !!activeRecovery;
        if (!recovery) return;
        banner.querySelector('.ovmr-banner-title').textContent =
            `${recovery.model_name || recovery.model_id} preparation was interrupted`;
        const count = recoveries.length;
        banner.querySelector('.ovmr-banner-detail').textContent = count > 1
            ? `${count} models have recoverable preparation state.`
            : `Recommended: ${actionLabel(recovery.recommended_action)}.`;
    }

    function ensureDialog() {
        let overlay = document.getElementById('ov-model-recovery-overlay');
        if (overlay) return overlay;
        overlay = createElement('div', 'ovmr-overlay');
        overlay.id = 'ov-model-recovery-overlay';
        overlay.hidden = true;
        overlay.setAttribute('role', 'presentation');

        const dialog = createElement('section', 'ovmr-dialog');
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'ovmr-title');

        const header = createElement('header', 'ovmr-header');
        const heading = createElement('div', 'ovmr-heading-copy');
        heading.append(
            createElement('div', 'ovmr-eyebrow', 'Model preparation recovery'),
            createElement('h2', 'ovmr-title'),
            createElement('p', 'ovmr-subtitle'),
        );
        heading.querySelector('.ovmr-title').id = 'ovmr-title';
        const close = createElement('button', 'ovmr-close', '×');
        close.type = 'button';
        close.setAttribute('aria-label', 'Close recovery screen');
        close.addEventListener('click', closeRecovery);
        header.append(heading, close);

        const body = createElement('div', 'ovmr-body');
        body.append(
            createElement('div', 'ovmr-grid'),
            createElement('div', 'ovmr-recommendation'),
            createElement('div', 'ovmr-actions'),
            createElement('div', 'ovmr-feedback'),
            createElement('div', 'ovmr-details'),
        );
        const details = body.querySelector('.ovmr-details');
        details.hidden = true;
        details.append(
            createElement('div', 'ovmr-details-title', 'Sanitized failure details'),
            createElement('div', 'ovmr-details-message'),
            createElement('pre', 'ovmr-details-log'),
        );
        body.querySelector('.ovmr-feedback').hidden = true;
        dialog.append(header, body);
        overlay.appendChild(dialog);
        overlay.addEventListener('mousedown', event => {
            if (event.target === overlay) closeRecovery();
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    function addFact(grid, label, value) {
        const item = createElement('div', 'ovmr-fact');
        item.append(
            createElement('div', 'ovmr-fact-label', label),
            createElement('div', 'ovmr-fact-value', value),
        );
        grid.appendChild(item);
    }

    function selectedDevice() {
        return document.getElementById('device-select')?.value || undefined;
    }

    function renderFeedback(overlay) {
        const node = overlay.querySelector('.ovmr-feedback');
        if (!feedback) {
            node.hidden = true;
            node.textContent = '';
            node.className = 'ovmr-feedback';
            return;
        }
        node.hidden = false;
        node.className = `ovmr-feedback${feedback.error ? ' error' : ''}`;
        node.textContent = feedback.message;
    }

    function renderDetails(overlay, recovery) {
        const details = overlay.querySelector('.ovmr-details');
        const failure = recovery?.failure_details;
        details.hidden = !failure;
        if (!failure) return;
        details.querySelector('.ovmr-details-message').textContent =
            failure.message || 'No additional failure message was retained.';
        const lines = Array.isArray(failure.log_tail) ? failure.log_tail : [];
        details.querySelector('.ovmr-details-log').textContent = lines.length
            ? lines.join('\n')
            : 'No sanitized converter log lines were retained.';
    }

    function renderDialog() {
        const overlay = ensureDialog();
        const recovery = activeRecovery;
        overlay.hidden = !recovery;
        if (!recovery) return;

        overlay.querySelector('.ovmr-title').textContent =
            `${recovery.model_name || recovery.model_id} preparation was interrupted`;
        overlay.querySelector('.ovmr-subtitle').textContent =
            'InferBridge found recoverable preparation state. Choose an explicit action below.';

        const grid = overlay.querySelector('.ovmr-grid');
        grid.replaceChildren();
        addFact(grid, 'Downloaded files', humanState(recovery.downloaded_files));
        addFact(grid, 'Conversion output', humanState(recovery.conversion_output));
        addFact(grid, 'Last completed stage', humanState(recovery.last_completed_stage));
        addFact(grid, 'Failed stage', humanState(recovery.failed_stage));

        const recommendation = overlay.querySelector('.ovmr-recommendation');
        recommendation.replaceChildren(
            createElement('strong', '', 'Recommended action'),
            document.createTextNode(recommendationText(recovery)),
        );

        const actions = overlay.querySelector('.ovmr-actions');
        actions.replaceChildren();
        const orderedActions = [
            'resume',
            'retry_failed_stage',
            'restart_download',
            'remove_incomplete_files',
        ];
        for (const action of orderedActions) {
            if (recovery.actions?.[action] !== true) continue;
            const recommended = action === recovery.recommended_action;
            const danger = action === 'restart_download' || action === 'remove_incomplete_files';
            const className = `ovmr-button${recommended ? ' primary' : ''}${danger ? ' danger' : ''}`;
            const button = createElement('button', className, actionLabel(action));
            button.type = 'button';
            button.disabled = !!actionInFlight;
            button.setAttribute('data-action', action);
            button.addEventListener('click', () => void applyAction(action));
            actions.appendChild(button);
        }
        const detailsButton = createElement(
            'button',
            'ovmr-button',
            detailsLoadedFor === recovery.recovery_id ? 'Hide failure details' : 'View sanitized failure details',
        );
        detailsButton.type = 'button';
        detailsButton.disabled = !!actionInFlight;
        detailsButton.addEventListener('click', () => void toggleDetails());
        actions.appendChild(detailsButton);

        renderFeedback(overlay);
        renderDetails(
            overlay,
            detailsLoadedFor === recovery.recovery_id ? recovery : null,
        );
    }

    async function refreshStatus() {
        try {
            const response = await window.fetch('/v1/models/status', {
                headers: requestHeaders(),
                cache: 'no-store',
            });
            if (response.ok) await response.json();
        } catch {
            // Existing connectivity UI owns persistent error reporting.
        }
    }

    async function openRecovery(summary) {
        if (!summary) return;
        previousFocus = document.activeElement;
        activeRecovery = summary;
        feedback = null;
        detailsLoadedFor = '';
        renderBanner();
        renderDialog();
        ensureDialog().querySelector('.ovmr-close')?.focus();
    }

    function closeRecovery() {
        activeRecovery = null;
        actionInFlight = '';
        feedback = null;
        detailsLoadedFor = '';
        renderDialog();
        renderBanner();
        if (previousFocus instanceof HTMLElement) previousFocus.focus();
        previousFocus = null;
    }

    async function toggleDetails() {
        if (!activeRecovery || actionInFlight) return;
        if (detailsLoadedFor === activeRecovery.recovery_id) {
            detailsLoadedFor = '';
            renderDialog();
            return;
        }
        actionInFlight = 'details';
        feedback = { message: 'Loading sanitized failure details…', error: false };
        renderDialog();
        try {
            const response = await window.fetch(
                `${RECOVERY_PATH}/${encodeURIComponent(activeRecovery.model_id)}`,
                { headers: requestHeaders(), cache: 'no-store' },
            );
            let payload = null;
            try { payload = await response.json(); } catch { payload = null; }
            if (!response.ok) {
                throw new Error(errorMessage(payload, `Recovery details failed with HTTP ${response.status}.`));
            }
            activeRecovery = payload;
            detailsLoadedFor = payload.recovery_id;
            feedback = null;
        } catch (error) {
            feedback = {
                message: error instanceof Error ? error.message : 'Recovery details could not be loaded.',
                error: true,
            };
        } finally {
            actionInFlight = '';
            renderDialog();
        }
    }

    function confirmationText(action, recovery) {
        if (action === 'restart_download') {
            return `Restart ${recovery.model_name || recovery.model_id} from download? Cached source files and incomplete conversion output will be removed.`;
        }
        if (action === 'remove_incomplete_files') {
            return `Remove incomplete conversion files for ${recovery.model_name || recovery.model_id}? Reusable downloaded source files will be kept.`;
        }
        return '';
    }

    async function applyAction(action) {
        if (!activeRecovery || actionInFlight) return;
        const confirmation = confirmationText(action, activeRecovery);
        if (confirmation && !window.confirm(confirmation)) return;

        actionInFlight = action;
        feedback = { message: `${actionLabel(action)}…`, error: false };
        renderDialog();
        try {
            const response = await window.fetch(ACTION_PATH, {
                method: 'POST',
                headers: requestHeaders(),
                body: JSON.stringify({
                    model: activeRecovery.model_id,
                    recovery_id: activeRecovery.recovery_id,
                    action,
                    device: selectedDevice(),
                }),
            });
            let payload = null;
            try { payload = await response.json(); } catch { payload = null; }
            if (!response.ok) {
                throw new Error(errorMessage(payload, `Recovery action failed with HTTP ${response.status}.`));
            }
            window.dispatchEvent(new CustomEvent('inferbridge:model-recovery-action', {
                detail: {
                    model: activeRecovery.model_id,
                    action,
                    status: payload?.status || '',
                },
            }));
            if (payload?.status === 'cleaned' && payload?.recovery) {
                activeRecovery = payload.recovery;
                feedback = { message: payload.message || 'Incomplete files removed.', error: false };
                detailsLoadedFor = '';
            } else {
                closeRecovery();
            }
            await refreshStatus();
        } catch (error) {
            feedback = {
                message: error instanceof Error ? error.message : 'Recovery action failed.',
                error: true,
            };
        } finally {
            actionInFlight = '';
            renderDialog();
            renderBanner();
        }
    }

    function applyStatusPayload(payload) {
        latestPayload = payload;
        recoveries = collectRecoveries(payload);
        if (activeRecovery) {
            const refreshed = recoveries.find(
                item => item.recovery_id === activeRecovery.recovery_id,
            );
            if (refreshed && detailsLoadedFor !== activeRecovery.recovery_id) {
                activeRecovery = refreshed;
            }
        }
        renderBanner();
        renderDialog();

        const first = recoveries[0];
        if (!activeRecovery && first) {
            const key = `${AUTO_OPEN_PREFIX}${first.recovery_id}`;
            if (sessionStorage.getItem(key) !== '1') {
                sessionStorage.setItem(key, '1');
                void openRecovery(first);
            }
        }
    }

    const previousFetch = InferBridge.chain();
    InferBridge.use(async function recoveryAwareFetch(input, init = {}) {
        const target = endpoint(input);
        const method = methodOf(input, init);
        const response = await previousFetch(input, init);
        if (
            target.sameOrigin
            && STATUS_PATHS.has(target.path)
            && method === 'GET'
            && response.ok
        ) {
            response.clone().json().then(applyStatusPayload).catch(() => {});
        }
        return response;
    });

    document.getElementById('model-select')?.addEventListener('change', () => {
        if (latestPayload) applyStatusPayload(latestPayload);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && activeRecovery) closeRecovery();
    });
    ensureBanner();
    ensureDialog();
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=MODEL_RECOVERY_UI_JS,
    before=("ovllm-model-progress-extension",),
    description="Model preparation recovery actions and reusable-cache status.",
)


def install_model_recovery_ui_extension() -> None:
    """Register the model recovery screen."""

    ui_registry.register(EXTENSION)


__all__ = ["MODEL_RECOVERY_UI_JS", "install_model_recovery_ui_extension"]
