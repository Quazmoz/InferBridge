"""Hardware advisor JavaScript, part 3."""

SCRIPT_3 = r"""        return (model.warnings || [])
            .filter(item => ['warning', 'blocking'].includes(item.severity))
            .map(item => item.message);
    }

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
                    if (overlay.classList.contains('visible')) render();
                }, 0);
            }).catch(() => {});
        }
        if (endpoint.sameOrigin && endpoint.path === '/v1/models/load' && method === 'POST' && response.ok) {
            toast('Model load started. A short hardware benchmark will run automatically after it is ready.');
        }
        return response;
    });

    button.addEventListener('click', open);
    closeButton?.addEventListener('click', close);
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && overlay.classList.contains('visible')) close(); });

    syncAutoSelection();
    void refresh(false);
})();
"""
