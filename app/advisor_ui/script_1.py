"""Hardware advisor JavaScript, part 1."""

SCRIPT_1 = r"""
(() => {
    'use strict';
    if (window.__ovllmHardwareAdvisorInstalled) return;
    window.__ovllmHardwareAdvisorInstalled = true;

    const PROFILE_KEY = 'ovllm.advisor.profile.v1';
    const AUTO_KEY = 'ovllm.advisor.auto.v1';
    const PROFILE_ORDER = ['fastest', 'balanced', 'best-quality', 'lowest-memory', 'lowest-power'];
    const PROFILE_LABELS = {
        fastest: 'Fastest',
        balanced: 'Balanced',
        'best-quality': 'Best quality',
        'lowest-memory': 'Lowest memory',
        'lowest-power': 'Lowest power',
    };
    const upstreamFetch = InferBridge.chain();
    const modelSelect = document.getElementById('model-select');
    const headerRight = document.querySelector('.header-right');
    if (!modelSelect || !headerRight) return;

    let selectedProfile = normalizeProfile(localStorage.getItem(PROFILE_KEY) || 'balanced');
    let autoRoutingProfile = localStorage.getItem(AUTO_KEY) ? normalizeProfile(localStorage.getItem(AUTO_KEY)) : null;
    let autoSelecting = false;
    let latestStatus = null;
    let refreshTimer = null;
    let loading = false;

    const style = document.createElement('style');
    style.textContent = `/*__ADVISOR_STYLE__*/`;
    document.head.appendChild(style);

    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'advisor-open-btn';
    // The compact header overflow menu collects its keyboard-navigable items by the
    // `icon-btn` class. Without it this button is still announced as a menu item but
    // arrow keys skip straight past it. Its own `#advisor-open-btn` rules win on
    // specificity, so the class changes behaviour without changing appearance.
    button.className = 'icon-btn';
    button.title = 'Best model for this PC';
    button.setAttribute('aria-label', 'Open hardware model advisor');
    button.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 1v3m6-3v3M9 20v3m6-3v3M20 9h3m-3 6h3M1 9h3m-3 6h3"/></svg>';
    const divider = headerRight.querySelector('.header-divider');
    if (divider) headerRight.insertBefore(button, divider);
    else headerRight.appendChild(button);

    const overlay = document.createElement('div');
    overlay.id = 'advisor-overlay';
    overlay.innerHTML = `
        <section id="advisor-dialog" role="dialog" aria-modal="true" aria-labelledby="advisor-heading">
            <header class="advisor-header">
                <div class="advisor-title">
                    <div class="advisor-title-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48 2.83-2.83"/><circle cx="12" cy="12" r="4"/></svg></div>
                    <div><h2 id="advisor-heading">Best model for this PC</h2><p>Hardware preflight, measured evidence, and safe model profiles</p></div>
                </div>
                <button id="advisor-close-btn" type="button" aria-label="Close">×</button>
            </header>
            <div id="advisor-body"><div class="advisor-spinner"></div></div>
        </section>`;
    document.body.appendChild(overlay);
    const body = document.getElementById('advisor-body');
    const closeButton = document.getElementById('advisor-close-btn');

    function normalizeProfile(value) {
        const text = String(value || 'balanced').trim().toLowerCase().replaceAll('_', '-').replaceAll(' ', '-');
        return PROFILE_ORDER.includes(text) ? text : 'balanced';
    }

    function apiHeaders(extra = {}) {
        const key = localStorage.getItem('ovllm.apikey.v1') || '';
        return { ...(key ? { Authorization: `Bearer ${key}` } : {}), ...extra };
    }

    function endpointFor(input) {
        const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input?.url || '';
        try {
            const url = new URL(raw, window.location.href);
            return { path: url.pathname, sameOrigin: url.origin === window.location.origin };
        } catch {
            return { path: '', sameOrigin: false };
        }
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
    }

    function formatGb(value) {
        const number = Number(value);
        return Number.isFinite(number) ? `${number.toFixed(number < 1 ? 2 : 1)} GB` : 'Unknown';
    }

    function toast(message) {
        const element = document.getElementById('toast');
        if (!element) return;
        element.textContent = message;
        element.classList.add('show');
        window.setTimeout(() => element.classList.remove('show'), 3200);
    }

    function advisorData() {
        return latestStatus?.metrics?.advisor || null;
    }

    function currentRecommendation() {
        const data = advisorData();
        return data?.profiles?.[selectedProfile] || null;
    }

    function syncAutoSelection() {
        button.classList.toggle('auto-active', Boolean(autoRoutingProfile));
        button.title = autoRoutingProfile
            ? `Automatic routing active · ${PROFILE_LABELS[autoRoutingProfile]}`
            : 'Best model for this PC';
        if (!autoRoutingProfile) return;
        const loaded = advisorData()?.loaded_profiles?.[autoRoutingProfile];
        const option = loaded ? modelSelect.querySelector(`option[value="${CSS.escape(loaded.model_id)}"]`) : null;
        if (!loaded || !option) {
            autoRoutingProfile = null;
            localStorage.removeItem(AUTO_KEY);
            button.classList.remove('auto-active');
            return;
        }
        if (modelSelect.value !== loaded.model_id) {
            autoSelecting = true;
            modelSelect.value = loaded.model_id;
            modelSelect.dispatchEvent(new Event('change', { bubbles: true }));
            autoSelecting = false;
        }
    }

    function deviceSummary(hardware) {
        const devices = Array.isArray(hardware?.devices) ? hardware.devices : [];
        if (!devices.length) return 'No OpenVINO devices detected';
        return devices.map(item => `${escapeHtml(item.device)}${item.driver_version ? ` · ${escapeHtml(item.driver_version)}` : ''}`).join('<br>');
    }

    function recommendationHtml(recommendation) {
        if (!recommendation) {
            return '<div class="advisor-empty">No compatible text-generation recommendation is available. Check the model warnings and free system resources.</div>';
        }
        const warnings = Array.isArray(recommendation.warnings) ? recommendation.warnings : [];
        return `
            <div class="advisor-model-name">${escapeHtml(recommendation.model_name)}</div>
            <div class="advisor-reason">${escapeHtml(recommendation.reason)}</div>
            <div class="advisor-pills">
                <span class="advisor-pill">${escapeHtml(recommendation.device)}</span>
                <span class="advisor-pill">${escapeHtml(String(recommendation.precision).toUpperCase())}</span>
                <span class="advisor-pill">${Number(recommendation.context_length || 0).toLocaleString()} context</span>
                <span class="advisor-pill">${Number(recommendation.output_tokens || 0).toLocaleString()} output</span>
                <span class="advisor-pill">Fit ${Number(recommendation.fit_score || 0).toFixed(0)}/100</span>
            </div>
            ${warnings.length ? `<ul class="advisor-warning-list">${warnings.slice(0, 3).map(item => `<li>${escapeHtml(item.message)}</li>`).join('')}</ul>` : ''}
            <div class="advisor-actions">
                <button class="advisor-primary" id="advisor-use-auto" type="button">Use ${escapeHtml(PROFILE_LABELS[selectedProfile])} auto</button>
                <button class="advisor-secondary" id="advisor-prepare-model" type="button">Prepare recommended model</button>
            </div>`;
    }

    function modelRowsHtml(models) {
        const rows = [...models]
            .filter(model => !String(model.backend || '').includes('embedding'))
            .sort((a, b) => Number(b.fit_score || 0) - Number(a.fit_score || 0));
        if (!rows.length) return '<div class="advisor-empty">No generation models are registered.</div>';
        return `<div class="advisor-table-wrap"><table class="advisor-table"><thead><tr>
            <th>Model</th><th>Fit</th><th>Device</th><th>Download</th><th>Converted</th><th>Runtime</th><th>First load</th><th>Preflight</th>
        </tr></thead><tbody>${rows.map(model => {
            const warnings = Array.isArray(model.warnings) ? model.warnings : [];
            return `<tr>
                <td><strong>${escapeHtml(model.name)}</strong><div class="advisor-hw-sub">${escapeHtml(String(model.precision || '').toUpperCase())} · ${Number(model.parameter_count_b || 0).toFixed(2)}B params</div></td>
                <td><span class="advisor-status ${escapeHtml(model.compatibility)}">${escapeHtml(model.compatibility)}</span><div class="advisor-hw-sub">${Number(model.fit_score || 0).toFixed(0)}/100</div></td>
"""
