"""Hardware advisor JavaScript, part 3."""

SCRIPT_3 = r"""
    const benchmarkBaseFormatMs = formatMs;
    formatMs = function formatOptionalMs(value) {
        if (value === null || value === undefined || value === '') return '—';
        return benchmarkBaseFormatMs(value);
    };

    const benchmarkExportWithCapturedEnvironment = safeBenchmarkExport;
    safeBenchmarkExport = function capturedBenchmarkExport(run) {
        const safe = benchmarkExportWithCapturedEnvironment(run);
        const environment = run?.environment || {};
        const devices = Array.isArray(environment.devices) ? environment.devices : [];
        safe.system = {
            cpu: environment.cpu || null,
            ram_gb: environment.ram_gb ?? null,
            inferbridge: environment.inferbridge || null,
            openvino: environment.openvino || null,
            openvino_genai: environment.openvino_genai || null,
            devices: devices.map(item => ({
                device: item?.device || item?.base || null,
                driver_version: item?.driver_version || null,
            })),
        };
        return safe;
    };

    modelSelect.addEventListener('change', event => {
        if (event.isTrusted && !autoSelecting && autoRoutingProfile) {
            autoRoutingProfile = null;
            localStorage.removeItem(AUTO_KEY);
            syncAutoSelection();
            toast('Automatic model routing disabled for this manual selection.');
        }
    });

    InferBridge.use(async function hardwareAdvisorFetch(input, init = {}) {
        const endpoint = endpointFor(input);
        const method = String(init?.method || (typeof input !== 'string' && input?.method) || 'GET').toUpperCase();
        if (
            endpoint.sameOrigin
            && method === 'POST'
            && autoRoutingProfile
            && ['/v1/chat/completions', '/v1/responses'].includes(endpoint.path)
            && !document.getElementById('vision-attach-btn')?.classList.contains('has-images')
        ) {
            try {
                const bodyData = JSON.parse(String(init.body || '{}'));
                const loaded = advisorData()?.loaded_profiles?.[autoRoutingProfile];
                if (loaded && bodyData.model === loaded.model_id) {
                    bodyData.model = `auto:${autoRoutingProfile}`;
                    init = { ...init, body: JSON.stringify(bodyData) };
                }
            } catch { /* existing request validation handles malformed payloads */ }
        }
        if (
            endpoint.sameOrigin
            && method === 'POST'
            && ['/v1/models/convert', '/v1/models/download-custom'].includes(endpoint.path)
            && !new Headers(init.headers || {}).has('X-Advisor-Confirmed')
        ) {
            try {
                const requestBody = JSON.parse(String(init.body || '{}'));
                const warnings = preflightWarnings(endpoint.path, requestBody);
                if (warnings.length) {
                    const accepted = window.confirm(`Hardware compatibility warning:\n\n• ${warnings.join('\n• ')}\n\nContinue with the download and conversion?`);
                    if (!accepted) {
                        return new Response(JSON.stringify({ detail: 'Model preparation cancelled after hardware preflight.' }), {
                            status: 409,
                            headers: { 'Content-Type': 'application/json' },
                        });
                    }
                    const headers = new Headers(init.headers || {});
                    headers.set('X-Advisor-Confirmed', '1');
                    init = { ...init, headers };
                }
            } catch { /* existing request validation handles malformed payloads */ }
        }

        const response = await upstreamFetch(input, init);
        if (endpoint.sameOrigin && endpoint.path === '/v1/system/status' && response.ok) {
            response.clone().json().then(data => {
                latestStatus = data;
                window.setTimeout(() => {
                    syncAutoSelection();
                    if (overlay.classList.contains('visible') && activeView === 'advisor') render();
                }, 0);
            }).catch(() => {});
        }
        if (endpoint.sameOrigin && endpoint.path === '/v1/models/load' && method === 'POST' && response.ok) {
            toast('Model load started. A short hardware benchmark will run automatically after it is ready.');
        }
        return response;
    });

    function trapAdvisorFocus(event) {
        if (event.key !== 'Tab' || !overlay.classList.contains('visible')) return;
        const focusable = [...document.querySelectorAll(
            '#advisor-dialog button:not([disabled]), #advisor-dialog input:not([disabled]), #advisor-dialog textarea:not([disabled]), #advisor-dialog select:not([disabled]), #advisor-dialog summary, #advisor-dialog [tabindex]:not([tabindex="-1"])'
        )].filter(element => !element.hidden && element.getClientRects().length > 0);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    button.addEventListener('click', open);
    closeButton?.addEventListener('click', close);
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    document.addEventListener('keydown', event => {
        if (!overlay.classList.contains('visible')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
            return;
        }
        trapAdvisorFocus(event);
    });

    syncAutoSelection();
    void refresh(false);
})();
"""
