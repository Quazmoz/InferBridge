"""Reliable, persistent model-preparation progress for the browser client.

One controller owns optimistic request feedback and server reconciliation for model
loads, conversions, and custom-model downloads. It coalesces overlapping status polls,
keeps operation selection stable, and updates progress surfaces without resetting user
interaction state.
"""

from __future__ import annotations

from app import ui_extension

_EXTENSION_ID = "ovllm-model-progress-extension"

PROGRESS_RELIABILITY_JS = r"""
(() => {
    'use strict';
    if (window.__ovllmReliableProgressInstalled) return;
    window.__ovllmReliableProgressInstalled = true;

    const STATUS_PATH = '/v1/system/status';
    const PREPARATION_PATHS = new Set([
        '/v1/models/load',
        '/v1/models/convert',
        '/v1/models/download-custom',
    ]);
    const OPTIMISTIC_TTL_MS = 30000;
    const PHASES = {
        idle: ['Waiting', -1],
        queued: ['Queued', 0],
        resolving: ['Resolving files', 0],
        downloading: ['Downloading', 0],
        converting: ['Converting', 1],
        finalizing: ['Finalizing', 1],
        loading: ['Loading runtime', 2],
        ready: ['Ready', 3],
        cancelled: ['Cancelled', -1],
        error: ['Failed', -1],
    };

    const modelState = new Map();
    const optimistic = new Map();
    let latestStatus = null;
    let latestStatusRevision = 0;
    let nextStatusRevision = 0;
    let expanded = false;
    let renderTimer = null;
    let renderTicker = null;
    let activeModelId = '';
    let lastAnnouncement = '';
    let sharedStatusRequest = null;

    const style = document.createElement('style');
    style.textContent = `
        #ov-reliable-progress{display:none;flex:0 0 auto;margin:10px 14px 0;border:1px solid color-mix(in srgb,var(--primary) 36%,var(--border));border-radius:12px;background:color-mix(in srgb,var(--surface-1) 92%,transparent);box-shadow:var(--shadow-md);backdrop-filter:blur(14px);overflow:hidden}
        #ov-reliable-progress.visible{display:block}#ov-reliable-progress.error{border-color:color-mix(in srgb,var(--red) 48%,var(--border))}#ov-reliable-progress.cancelled{border-color:color-mix(in srgb,var(--amber) 48%,var(--border))}
        .ovrp-main{width:100%;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:7px 10px;align-items:center;padding:11px 12px;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer;font:inherit}
        .ovrp-main:focus-visible{outline:2px solid var(--primary);outline-offset:-2px}.ovrp-spinner{width:16px;height:16px;border:2px solid var(--surface-3);border-top-color:var(--primary);border-radius:50%;animation:spin .8s linear infinite}
        .terminal .ovrp-spinner{display:grid;place-items:center;border:0;animation:none;font-size:13px;font-weight:800}.error .ovrp-spinner:before{content:'!'}.cancelled .ovrp-spinner:before{content:'■';font-size:10px}.error .ovrp-spinner{color:var(--red)}.cancelled .ovrp-spinner{color:var(--amber)}
        .ovrp-copy{min-width:0}.ovrp-title{display:block;font-size:11.5px;font-weight:750;color:var(--text-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ovrp-message{display:block;margin-top:2px;font-size:10.5px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ovrp-value{font-size:11.5px;font-weight:750;color:var(--primary);font-variant-numeric:tabular-nums;white-space:nowrap}.error .ovrp-value{color:var(--red)}.cancelled .ovrp-value{color:var(--amber)}
        .ovrp-track{grid-column:2/4;position:relative;height:7px;overflow:hidden;border-radius:999px;background:var(--surface-3);box-shadow:inset 0 0 0 1px var(--border)}
        .ovrp-fill{position:absolute;inset:0 auto 0 0;width:0;border-radius:inherit;background:var(--accent-grad);transition:width .35s ease}.error .ovrp-fill{background:var(--red)}.cancelled .ovrp-fill{background:var(--amber)}
        .ovrp-scan{display:none;position:absolute;top:0;bottom:0;width:18%;border-radius:inherit;background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--primary) 75%,white),transparent);opacity:.72;animation:ovrp-scan 1.35s ease-in-out infinite}
        .ovrp-track.indeterminate .ovrp-scan{display:block}@keyframes ovrp-scan{from{transform:translateX(-120%)}to{transform:translateX(650%)}}
        .ovrp-detail{display:none;padding:0 12px 12px 38px;border-top:1px solid color-mix(in srgb,var(--border) 75%,transparent)}.expanded .ovrp-detail{display:block}
        .ovrp-steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:10px}.ovrp-step{display:flex;align-items:center;gap:6px;min-width:0;padding:6px 8px;border:1px solid var(--border);border-radius:8px;color:var(--text-3);font-size:10.5px}
        .ovrp-step:before{content:'';width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:var(--text-3)}.ovrp-step.active{color:var(--text-1);border-color:color-mix(in srgb,var(--primary) 40%,var(--border))}
        .ovrp-step.active:before{background:var(--primary);box-shadow:0 0 8px var(--primary-glow);animation:dot-pulse 1.3s ease infinite}.ovrp-step.done{color:var(--green)}.ovrp-step.done:before{background:var(--green);box-shadow:0 0 6px var(--green-glow)}
        .ovrp-meta{display:flex;flex-wrap:wrap;gap:5px 12px;margin-top:9px;color:var(--text-3);font-size:10.5px;font-variant-numeric:tabular-nums}.ovrp-meta .warning{color:var(--amber)}.ovrp-meta .danger{color:var(--red)}
        .ovrp-log{margin-top:9px}.ovrp-log summary{cursor:pointer;color:var(--text-3);font-size:10.5px;user-select:none}.ovrp-log pre{max-height:130px;overflow:auto;margin:7px 0 0;padding:8px;border-radius:8px;background:var(--code-bg);color:var(--code-text);font:10px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
        .ovrp-inline{flex:1 0 100%;width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 10px;align-items:center;margin-top:7px}.ovrp-inline .ovrp-track{grid-column:1/-1;height:7px}
        .ovrp-inline-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:var(--text-2)}.ovrp-inline-value{font-size:11px;font-weight:750;color:var(--primary);font-variant-numeric:tabular-nums;white-space:nowrap}
        .ovrp-live{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
        @media(max-width:640px){#ov-reliable-progress{margin:8px 10px 0}.ovrp-detail{padding-left:12px}.ovrp-steps{grid-template-columns:1fr}.ovrp-message{display:none}}
        @media(prefers-reduced-motion:reduce){.ovrp-spinner,.ovrp-step.active:before,.ovrp-scan{animation:none}.ovrp-track.indeterminate .ovrp-scan{left:72%;display:block}}
    `;
    document.head.appendChild(style);

    const chatColumn = document.querySelector('.chat-column');
    const chatArea = document.getElementById('chat-area');
    const dock = document.createElement('section');
    dock.id = 'ov-reliable-progress';
    dock.setAttribute('role', 'region');
    dock.setAttribute('aria-label', 'Model preparation status');
    dock.innerHTML = `
        <button type="button" class="ovrp-main" aria-expanded="false" aria-label="Show model preparation details">
            <span class="ovrp-spinner" aria-hidden="true"></span>
            <span class="ovrp-copy"><span class="ovrp-title"></span><span class="ovrp-message"></span></span>
            <span class="ovrp-value"></span>
            <span class="ovrp-track" role="progressbar" aria-valuemin="0" aria-valuemax="100">
                <span class="ovrp-fill"></span><span class="ovrp-scan"></span>
            </span>
        </button>
        <div class="ovrp-detail"></div>
        <span class="ovrp-live" role="status" aria-live="polite" aria-atomic="true"></span>`;
    if (chatColumn && chatArea) chatColumn.insertBefore(dock, chatArea);

    const main = dock.querySelector('.ovrp-main');
    const liveRegion = dock.querySelector('.ovrp-live');
    main?.addEventListener('click', () => {
        expanded = !expanded;
        dock.classList.toggle('expanded', expanded);
        main.setAttribute('aria-expanded', String(expanded));
        main.setAttribute(
            'aria-label',
            expanded ? 'Hide model preparation details' : 'Show model preparation details'
        );
    });

    function strictPercent(value) {
        if (value === null || value === undefined || value === '') return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : null;
    }

    function aggregateDownloadPercent(progress) {
        const files = Array.isArray(progress?.files) ? progress.files : [];
        const values = files
            .map(file => Number(file?.percent))
            .filter(value => Number.isFinite(value));
        if (!values.length) return null;
        const average = values.reduce((sum, value) => sum + Math.max(0, Math.min(100, value)), 0) / values.length;
        return Math.round(average);
    }

    function progressCount(progress) {
        const completed = Number(progress?.completed);
        const total = Number(progress?.total);
        if (!Number.isInteger(completed) || !Number.isInteger(total)) return null;
        if (completed < 0 || total <= 0 || completed > total) return null;
        return { completed, total };
    }

    function baseName(model) {
        return String(model?.name || model?.id || 'Model').split(' — ')[0];
    }

    function normalizedPhase(model, progress) {
        const value = String(progress?.phase || model?.status || 'idle').toLowerCase();
        if (value === 'queued_convert') return 'queued';
        return value;
    }

    function phaseInfo(phase) {
        const [label, stage] = PHASES[phase] || ['Preparing', 0];
        return { label, stage };
    }

    function progressInfo(model) {
        const progress = model?.progress || {};
        const phase = normalizedPhase(model, progress);
        const meta = phaseInfo(phase);
        const count = progressCount(progress);
        const phasePercent = count
            ? strictPercent((count.completed / count.total) * 100)
            : strictPercent(progress.percent);
        const overallPercent = strictPercent(progress.overall_percent);
        const aggregatePercent = aggregateDownloadPercent(progress) ?? strictPercent(progress.percent);
        const reportedStart = Number(progress.started_at) || 0;
        const operationId = typeof progress.operation_id === 'string'
            ? progress.operation_id
            : '';
        const previous = modelState.get(model.id);
        const newOperation = !previous
            || (operationId && previous.operationId && operationId !== previous.operationId)
            || (reportedStart > 0 && previous.startedAt > 0 && reportedStart !== previous.startedAt)
            || (previous.terminal && !['ready', 'error', 'cancelled'].includes(phase));
        const prior = newOperation ? {
            operationId,
            startedAt: reportedStart || Math.floor(Date.now() / 1000),
            targetDevice: null,
            terminal: false,
            overall: 0,
        } : previous;

        // A phase without any reported number stays indeterminate: the track animates
        // instead of claiming a misleading 0%.
        const raw = overallPercent ?? phasePercent ?? aggregatePercent ?? null;
        let trackPercent = raw;
        let progressScope = overallPercent !== null ? 'overall' : 'phase';
        let determinate = raw !== null;
        // Overall progress is a monotonic floor built from the completed stage base plus
        // whatever the current stage reports, so a new phase never rewinds the bar.
        const metaStart = meta.stage >= 0 ? meta.stage * 33 : 0;
        let overall = Math.max(prior.overall ?? 0, metaStart, raw ?? 0);
        if (phase === 'ready') {
            trackPercent = 100;
            progressScope = 'overall';
            determinate = true;
            overall = 100;
        } else if (phase === 'error' || phase === 'cancelled') {
            trackPercent = null;
            determinate = false;
        }

        const now = Math.floor(Date.now() / 1000);
        const startedAt = reportedStart || prior.startedAt || now;
        const updatedAt = Number(progress.updated_at) || now;
        const targetDevice = model.device
            || prior.targetDevice
            || document.getElementById('device-select')?.value
            || null;
        modelState.set(model.id, {
            operationId: operationId || prior.operationId || '',
            startedAt,
            targetDevice,
            terminal: ['ready', 'error', 'cancelled'].includes(phase),
            overall,
        });

        return {
            model,
            progress,
            phase,
            meta,
            count,
            phasePercent,
            overallPercent,
            raw,
            trackPercent,
            progressScope,
            determinate,
            targetDevice,
            elapsed: Math.max(0, now - startedAt),
            staleFor: Math.max(0, now - updatedAt),
            overall,
        };
    }

    function duration(seconds) {
        const total = Math.max(0, Math.floor(seconds || 0));
        if (total < 60) return `${total}s`;
        const minutes = Math.floor(total / 60);
        if (minutes < 60) return `${minutes}m ${String(total % 60).padStart(2, '0')}s`;
        return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    }

    function valueLabel(info) {
        if (info.phase === 'error') return 'Failed';
        if (info.phase === 'cancelled') return 'Cancelled';
        if (info.phase === 'ready') return '100%';
        if (info.raw === null) return info.meta.stage >= 0 && info.meta.stage < 3 ? `Stage ${info.meta.stage + 1} of 3` : 'Working…';
        if (info.overallPercent !== null) return `${Math.round(info.overallPercent)}%`;
        if (info.count) return `${info.count.completed} of ${info.count.total}`;
        if (info.phasePercent !== null) return `${Math.round(info.phasePercent)}%`;
        return info.meta.stage >= 0 && info.meta.stage < 3
            ? `Stage ${info.meta.stage + 1} of 3`
            : 'Working…';
    }

    function progressSummary(info) {
        if (info.phase === 'error' || info.phase === 'cancelled') return info.meta.label;
        if (info.phase === 'ready') return 'Ready · 100% complete';
        if (info.overallPercent !== null) {
            return `${info.meta.label} · ${Math.round(info.overallPercent)}% overall`;
        }
        if (info.count) {
            return `${info.meta.label} · ${info.count.completed} of ${info.count.total}`
                + ` (${Math.round(info.phasePercent)}% of current phase)`;
        }
        if (info.phasePercent !== null) {
            return `${info.meta.label} · ${Math.round(info.phasePercent)}% of current phase`;
        }
        return `${info.meta.label} · progress is not measurable for this phase`;
    }

    function updateTrack(track, info) {
        if (!track) return;
        const fill = track.querySelector('.ovrp-fill');
        if (fill) fill.style.width = `${info.trackPercent ?? 0}%`;
        const terminal = ['error', 'cancelled'].includes(info.phase);
        track.classList.toggle('indeterminate', !info.determinate && !terminal);
        if (info.determinate) {
            track.setAttribute('aria-valuenow', String(Math.round(info.trackPercent)));
        } else {
            track.removeAttribute('aria-valuenow');
        }
        track.setAttribute('aria-valuetext', progressSummary(info));
        track.setAttribute(
            'aria-label',
            info.progressScope === 'overall'
                ? 'Overall model preparation progress'
                : `${info.meta.label} phase progress`,
        );
    }

    function detailInteractionState() {
        const disclosure = dock.querySelector('.ovrp-log');
        const output = disclosure?.querySelector('pre');
        return {
            logOpen: !!disclosure?.open,
            logScrollTop: output?.scrollTop || 0,
            logFocused: !!disclosure?.contains(document.activeElement),
        };
    }

    function buildDetail(info, operationCount) {
        const detail = document.createDocumentFragment();
        const steps = document.createElement('div');
        steps.className = 'ovrp-steps';
        ['1. Download', '2. Convert', '3. Load'].forEach((label, index) => {
            const step = document.createElement('div');
            step.className = 'ovrp-step';
            if (info.phase === 'ready' || index < info.meta.stage) step.classList.add('done');
            if (!['error', 'cancelled'].includes(info.phase) && index === info.meta.stage) {
                step.classList.add('active');
            }
            step.textContent = label;
            steps.appendChild(step);
        });
        detail.appendChild(steps);

        const status = document.createElement('div');
        status.className = 'ovrp-meta';
        const values = [
            `Elapsed ${duration(info.elapsed)}`,
            progressSummary(info),
            info.targetDevice ? `Device ${info.targetDevice}` : null,
            operationCount > 1 ? `${operationCount} model operations active` : null,
        ].filter(Boolean);
        values.forEach(text => {
            const span = document.createElement('span');
            span.textContent = text;
            status.appendChild(span);
        });
        if (info.staleFor >= 120 && !['error', 'ready', 'cancelled'].includes(info.phase)) {
            const stale = document.createElement('span');
            stale.className = 'danger';
            stale.textContent = `Taking longer than usual · last update ${duration(info.staleFor)} ago`;
            status.appendChild(stale);
        } else if (info.staleFor >= 30 && !['error', 'ready', 'cancelled'].includes(info.phase)) {
            const stale = document.createElement('span');
            stale.className = 'warning';
            stale.textContent = `No recent progress update for ${duration(info.staleFor)} · still running`;
            status.appendChild(stale);
        } else if (info.progress.updated_at) {
            const updated = document.createElement('span');
            updated.textContent = `Updated ${duration(info.staleFor)} ago`;
            status.appendChild(updated);
        }
        detail.appendChild(status);

        const logs = Array.isArray(info.progress.log_tail)
            ? info.progress.log_tail.filter(Boolean).slice(-8)
            : [];
        if (logs.length) {
            const disclosure = document.createElement('details');
            disclosure.className = 'ovrp-log';
            const summary = document.createElement('summary');
            summary.textContent = `Recent preparation activity (${logs.length})`;
            const output = document.createElement('pre');
            output.textContent = logs.join('\n');
            disclosure.append(summary, output);
            detail.appendChild(disclosure);
        }
        return detail;
    }

    function restoreDetailInteraction(state) {
        const disclosure = dock.querySelector('.ovrp-log');
        const output = disclosure?.querySelector('pre');
        if (disclosure) disclosure.open = state.logOpen;
        if (output) output.scrollTop = state.logScrollTop;
        if (state.logFocused) disclosure?.querySelector('summary')?.focus({ preventScroll: true });
    }

    function announce(info) {
        const text = `${baseName(info.model)}: ${info.meta.label}`;
        if (text === lastAnnouncement || !liveRegion) return;
        lastAnnouncement = text;
        liveRegion.textContent = text;
    }

    function renderDock(model, operationCount = 0) {
        const displayable = model && (model.is_loading || ['error', 'cancelled'].includes(model.status));
        if (!displayable) {
            dock.classList.remove('visible', 'error', 'cancelled', 'terminal');
            lastAnnouncement = '';
            return null;
        }
        const info = progressInfo(model);
        const interaction = detailInteractionState();
        dock.classList.add('visible');
        dock.classList.toggle('error', info.phase === 'error');
        dock.classList.toggle('cancelled', info.phase === 'cancelled');
        dock.classList.toggle('terminal', ['error', 'cancelled'].includes(info.phase));
        const operationSuffix = operationCount > 1 ? ` · ${operationCount} active` : '';
        dock.querySelector('.ovrp-title').textContent = `${info.meta.label} ${baseName(model)}${operationSuffix}`;
        dock.querySelector('.ovrp-message').textContent = String(
            info.progress.message || model.status_label || `${info.meta.label} model…`
        );
        dock.querySelector('.ovrp-value').textContent = valueLabel(info);
        updateTrack(dock.querySelector('.ovrp-track'), info);
        dock.querySelector('.ovrp-detail').replaceChildren(buildDetail(info, operationCount));
        restoreDetailInteraction(interaction);
        announce(info);
        return info;
    }

    function currentWaitingModelId() {
        return typeof waitingForModelId === 'undefined' ? null : waitingForModelId;
    }

    function loaderHostFor(modelId) {
        const waitingId = currentWaitingModelId();
        if (waitingId && waitingId !== modelId) return null;
        const hosts = Array.from(document.querySelectorAll('.model-loader-status'))
            .filter(element => element.isConnected);
        const visible = hosts.filter(element => element.getClientRects().length > 0);
        return visible[visible.length - 1] || hosts[hosts.length - 1] || null;
    }

    function renderInline(model, info) {
        if (!model?.is_loading || !info) {
            document.querySelectorAll('.ovrp-inline').forEach(element => element.remove());
            return;
        }
        const host = loaderHostFor(model.id);
        if (!host) {
            document.querySelectorAll('.ovrp-inline').forEach(element => element.remove());
            return;
        }
        host.style.flexWrap = 'wrap';
        let inline = Array.from(host.querySelectorAll('.ovrp-inline'))
            .find(element => element.dataset.modelId === model.id) || null;
        document.querySelectorAll('.ovrp-inline').forEach(element => {
            if (element !== inline) element.remove();
        });
        if (!inline) {
            inline = document.createElement('div');
            inline.className = 'ovrp-inline';
            inline.dataset.modelId = model.id;
            inline.innerHTML = `
                <div class="ovrp-inline-label"></div>
                <div class="ovrp-inline-value"></div>
                <div class="ovrp-track"><span class="ovrp-fill"></span><span class="ovrp-scan"></span></div>`;
            host.appendChild(inline);
        }
        inline.querySelector('.ovrp-inline-label').textContent = String(
            info.progress.message || model.status_label || `${info.meta.label} model…`
        );
        inline.querySelector('.ovrp-inline-value').textContent = valueLabel(info);
        updateTrack(inline.querySelector('.ovrp-track'), info);
    }

    function renderFooter(model, info) {
        if (!model || !info) return;
        const footer = document.getElementById('model-status');
        if (!footer) return;
        footer.textContent = `${baseName(model)}: ${progressSummary(info)} · elapsed ${duration(info.elapsed)}`;
        footer.className = info.phase === 'error'
            ? 'error'
            : (info.phase === 'cancelled' ? 'cancelled' : 'loading');
        footer.title = String(info.progress.message || model.status_label || footer.textContent);
    }

    function mergeOptimistic(source) {
        const now = Date.now();
        const models = source.map(model => {
            const pending = optimistic.get(model.id);
            if (!pending) return model;
            if (model.is_loading || model.is_loaded || ['error', 'cancelled'].includes(model.status)) {
                optimistic.delete(model.id);
                return model;
            }
            if (now - pending.createdAt > OPTIMISTIC_TTL_MS) {
                optimistic.delete(model.id);
                return model;
            }
            return pending.model;
        });

        const known = new Set(models.map(model => model.id));
        for (const [modelId, pending] of optimistic.entries()) {
            if (known.has(modelId)) continue;
            if (now - pending.createdAt > OPTIMISTIC_TTL_MS) {
                optimistic.delete(modelId);
                continue;
            }
            models.push(pending.model);
        }
        return models;
    }

    function chooseActiveModel(models, selectedId) {
        const loading = models.filter(model => model.is_loading);
        const waitingId = currentWaitingModelId();
        const selected = models.find(model => model.id === selectedId) || null;
        if (selected?.is_loading) activeModelId = selected.id;

        const waiting = waitingId ? loading.find(model => model.id === waitingId) : null;
        if (waiting) activeModelId = waiting.id;

        const retained = loading.find(model => model.id === activeModelId);
        if (retained) return retained;
        if (loading.length) {
            activeModelId = loading[0].id;
            return loading[0];
        }
        activeModelId = '';
        return selected && ['error', 'cancelled'].includes(selected.status) ? selected : null;
    }

    function setRenderTicker(enabled) {
        if (enabled && !renderTicker) {
            renderTicker = window.setInterval(() => {
                if (latestStatus) renderStatus(latestStatus, latestStatusRevision);
            }, 1000);
        } else if (!enabled && renderTicker) {
            window.clearInterval(renderTicker);
            renderTicker = null;
        }
    }

    function renderStatus(data, revision = latestStatusRevision) {
        if (revision < latestStatusRevision) return;
        const source = data?.models?.available;
        if (!Array.isArray(source)) return;
        latestStatusRevision = revision;
        latestStatus = data;
        const models = mergeOptimistic(source);
        const selectedId = document.getElementById('model-select')?.value;
        const active = chooseActiveModel(models, selectedId);
        const operationCount = models.filter(model => model.is_loading).length;
        const info = renderDock(active, operationCount);
        renderInline(active, info);
        renderFooter(active, info);
        setRenderTicker(!!active?.is_loading);

        const retained = new Set(
            models
                .filter(model => model.is_loading || ['error', 'cancelled'].includes(model.status))
                .map(model => model.id)
        );
        for (const modelId of modelState.keys()) {
            if (!retained.has(modelId)) modelState.delete(modelId);
        }
    }

    function scheduleRender(data, revision = ++nextStatusRevision) {
        if (revision < latestStatusRevision) return;
        latestStatus = data;
        window.clearTimeout(renderTimer);
        renderTimer = window.setTimeout(() => renderStatus(data, revision), 0);
    }

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

    function requestMethod(input, init) {
        return String(init?.method || input?.method || 'GET').toUpperCase();
    }

    function statusRequestKey(input, init) {
        const headers = new Headers(input instanceof Request ? input.headers : undefined);
        if (init?.headers) {
            new Headers(init.headers).forEach((value, key) => headers.set(key, value));
        }
        return headers.get('authorization') || '';
    }

    async function requestBody(input, init) {
        if (typeof init?.body === 'string') {
            try {
                return JSON.parse(init.body);
            } catch {
                return {};
            }
        }
        if (input instanceof Request) {
            try {
                return await input.clone().json();
            } catch {
                return {};
            }
        }
        return {};
    }

    function optimisticIdentity(path, body) {
        const modelId = String(body.model || body.model_id || '').trim();
        if (!modelId) return null;
        return {
            modelId,
            device: body.device || body.recommended_device || null,
            converting: path !== '/v1/models/load',
        };
    }

    function renderOptimistic(path, body) {
        const identity = optimisticIdentity(path, body);
        if (!identity) return null;
        const { modelId, device, converting } = identity;
        modelState.delete(modelId);
        activeModelId = modelId;

        const catalog = latestStatus?.models?.available || [];
        const base = catalog.find(model => model.id === modelId) || {
            id: modelId,
            name: body.name || modelId,
            status_label: 'Preparing model…',
        };
        const now = Math.floor(Date.now() / 1000);
        const message = converting
            ? `Queued ${baseName(base)} for download and conversion…`
            : `Queued ${baseName(base)} to load on ${device || 'the selected device'}…`;
        const model = {
            ...base,
            device: device || base.device || null,
            is_loaded: false,
            is_loading: true,
            status: converting ? 'converting' : 'queued',
            status_label: message,
            progress: {
                phase: 'queued',
                message,
                percent: null,
                started_at: now,
                updated_at: now,
                log_tail: [],
            },
        };
        optimistic.set(modelId, { model, createdAt: Date.now() });
        const info = renderDock(model, 1);
        renderInline(model, info);
        renderFooter(model, info);
        setRenderTicker(true);
        return modelId;
    }

    function clearOptimistic(modelId) {
        if (!modelId) return;
        optimistic.delete(modelId);
        modelState.delete(modelId);
        if (activeModelId === modelId) activeModelId = '';
        if (latestStatus) scheduleRender(latestStatus);
        else {
            renderDock(null);
            renderInline(null, null);
            setRenderTicker(false);
        }
    }

    function mergeReturnedModel(payload) {
        if (!payload?.model) return;
        const current = latestStatus || { models: { available: [] } };
        const models = Array.isArray(current.models?.available)
            ? [...current.models.available]
            : [];
        const index = models.findIndex(model => model.id === payload.model.id);
        if (index >= 0) models[index] = payload.model;
        else models.push(payload.model);
        scheduleRender({
            ...current,
            models: { ...(current.models || {}), available: models },
        });
    }

    const previousFetch = window.fetch.bind(window);

    async function sharedStatusFetch(input, init) {
        const key = statusRequestKey(input, init);
        if (!sharedStatusRequest || sharedStatusRequest.key !== key) {
            const holder = {
                key,
                revision: ++nextStatusRevision,
                promise: previousFetch(input, init),
            };
            sharedStatusRequest = holder;
            void holder.promise.then(
                () => { if (sharedStatusRequest === holder) sharedStatusRequest = null; },
                () => { if (sharedStatusRequest === holder) sharedStatusRequest = null; },
            );
        }
        const holder = sharedStatusRequest;
        const response = await holder.promise;
        return { response: response.clone(), revision: holder.revision };
    }

    window.fetch = async function reliableProgressFetch(input, init = {}) {
        const target = endpoint(input);
        const method = requestMethod(input, init);
        const isStatus = target.sameOrigin && target.path === STATUS_PATH && method === 'GET';
        const isPreparation = target.sameOrigin
            && method === 'POST'
            && PREPARATION_PATHS.has(target.path);
        let optimisticModelId = null;

        if (isPreparation) {
            const body = await requestBody(input, init);
            optimisticModelId = renderOptimistic(target.path, body);
        }

        let response;
        let revision = null;
        try {
            if (isStatus) {
                const shared = await sharedStatusFetch(input, init);
                response = shared.response;
                revision = shared.revision;
            } else {
                response = await previousFetch(input, init);
            }
        } catch (error) {
            clearOptimistic(optimisticModelId);
            throw error;
        }

        if (isStatus && response.ok) {
            response.clone().json().then(data => scheduleRender(data, revision)).catch(() => {});
        } else if (isPreparation) {
            if (!response.ok) {
                clearOptimistic(optimisticModelId);
            } else {
                response.clone().json().then(payload => {
                    optimistic.delete(optimisticModelId);
                    mergeReturnedModel(payload);
                }).catch(() => {
                    clearOptimistic(optimisticModelId);
                });
            }
        }
        return response;
    };

    document.getElementById('model-select')?.addEventListener('change', event => {
        const selected = latestStatus?.models?.available?.find(model => model.id === event.target.value);
        if (selected?.is_loading) activeModelId = selected.id;
        if (latestStatus) scheduleRender(latestStatus);
    });

    async function initialStatus() {
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        const headers = key ? { Authorization: `Bearer ${key}` } : {};
        try {
            await window.fetch(STATUS_PATH, { headers });
        } catch {
            // The base UI owns connectivity errors.
        }
    }
    void initialStatus();
})();
"""


def install_progress_ui_extension() -> None:
    """Install the progress controller after all other browser extensions."""

    if getattr(ui_extension, "_MODEL_PROGRESS_EXTENSION_INSTALLED", False):
        return
    previous_inject = ui_extension.inject_multimodal_ui

    def inject_with_reliable_progress(html: str) -> str:
        html = previous_inject(html)
        if f'id="{_EXTENSION_ID}"' in html:
            return html
        script = f'\n<script id="{_EXTENSION_ID}">\n{PROGRESS_RELIABILITY_JS}\n</script>\n'
        if "</body>" in html:
            return html.replace("</body>", f"{script}</body>", 1)
        return html + script

    ui_extension.inject_multimodal_ui = inject_with_reliable_progress
    ui_extension._MODEL_PROGRESS_EXTENSION_INSTALLED = True
