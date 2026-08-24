"""Hardware advisor JavaScript, part 2."""

SCRIPT_2 = r"""
    function advisorTabsHtml() {
        return `
            <div class="advisor-view-tabs" role="tablist" aria-label="Hardware performance tools">
                <button id="advisor-tab-advisor" class="advisor-view-tab ${activeView === 'advisor' ? 'active' : ''}" type="button" role="tab" aria-selected="${activeView === 'advisor'}" aria-controls="advisor-panel-advisor" tabindex="${activeView === 'advisor' ? '0' : '-1'}" data-advisor-view="advisor">Advisor</button>
                <button id="advisor-tab-benchmark" class="advisor-view-tab ${activeView === 'benchmark' ? 'active' : ''}" type="button" role="tab" aria-selected="${activeView === 'benchmark'}" aria-controls="advisor-panel-benchmark" tabindex="${activeView === 'benchmark' ? '0' : '-1'}" data-advisor-view="benchmark">Benchmark Lab</button>
            </div>`;
    }

    function advisorViewHtml(data) {
        const hardware = data.hardware || {};
        const memory = hardware.memory || {};
        const disk = hardware.disk || {};
        const cpu = hardware.cpu || {};
        const recommendation = currentRecommendation();
        return `
            <div id="advisor-panel-advisor" role="tabpanel" aria-labelledby="advisor-tab-advisor">
                <div class="advisor-profile-row">${PROFILE_ORDER.map(profile => `<button type="button" class="advisor-profile-btn ${profile === selectedProfile ? 'active' : ''}" data-profile="${profile}">${escapeHtml(PROFILE_LABELS[profile])}</button>`).join('')}</div>
                <div class="advisor-grid">
                    <section class="advisor-card advisor-recommendation"><h3>${escapeHtml(PROFILE_LABELS[selectedProfile])} recommendation</h3>${recommendationHtml(recommendation)}</section>
                    <section class="advisor-card"><h3>Hardware preflight</h3><div class="advisor-hardware-grid">
                        <div class="advisor-hw-item"><div class="advisor-hw-label">CPU</div><div class="advisor-hw-value">${escapeHtml(cpu.name || 'Unknown CPU')}</div><div class="advisor-hw-sub">${Number(cpu.physical_cores || 0)} physical · ${Number(cpu.logical_cores || 0)} logical cores</div></div>
                        <div class="advisor-hw-item"><div class="advisor-hw-label">RAM</div><div class="advisor-hw-value">${formatGb(memory.available_gb)} available</div><div class="advisor-hw-sub">${formatGb(memory.total_gb)} installed · ${Number(memory.used_percent || 0).toFixed(0)}% used</div></div>
                        <div class="advisor-hw-item"><div class="advisor-hw-label">Disk</div><div class="advisor-hw-value">${formatGb(disk.free_gb)} free</div><div class="advisor-hw-sub">${formatGb(latestStatus?.disk?.models_gb ?? disk.models_gb)} currently used by models</div></div>
                        <div class="advisor-hw-item"><div class="advisor-hw-label">OpenVINO devices / drivers</div><div class="advisor-hw-value">${deviceSummary(hardware)}</div><div class="advisor-hw-sub">Runtime ${escapeHtml(hardware.runtime?.openvino_genai || hardware.runtime?.openvino || 'unknown')}</div></div>
                    </div><div class="advisor-notice">${escapeHtml(data.estimates_notice || '')}<br>Hardware fingerprint: ${escapeHtml(hardware.fingerprint || 'unknown')}</div></section>
                </div>
                <section class="advisor-card advisor-models"><h3>Model compatibility before download</h3>${modelRowsHtml(data.models || [])}</section>
            </div>`;
    }

    function render() {
        if (!body) return;
        const data = advisorData();
        if (!data) {
            body.innerHTML = '<div class="advisor-empty">Hardware advisor data is not available from this server version.</div>';
            return;
        }
        const warningCount = (data.models || []).filter(item => item.compatibility === 'blocked').length;
        button.classList.toggle('has-warning', warningCount > 0);
        body.innerHTML = `
            ${advisorTabsHtml()}
            ${activeView === 'benchmark' ? benchmarkLabHtml(data) : advisorViewHtml(data)}`;
        wireAdvisorTabs();
        if (activeView === 'advisor') {
            body.querySelectorAll('[data-profile]').forEach(profileButton => profileButton.addEventListener('click', () => selectProfile(profileButton.dataset.profile)));
            document.getElementById('advisor-use-auto')?.addEventListener('click', useAutoProfile);
            document.getElementById('advisor-prepare-model')?.addEventListener('click', prepareRecommendation);
        } else {
            wireBenchmarkLab();
        }
        syncAutoSelection();
    }

    function wireAdvisorTabs() {
        const tabs = [...body.querySelectorAll('[role="tab"][data-advisor-view]')];
        tabs.forEach((tab, index) => {
            tab.addEventListener('click', () => selectAdvisorView(tab.dataset.advisorView, false));
            tab.addEventListener('keydown', event => {
                let target = null;
                if (event.key === 'ArrowRight') target = tabs[(index + 1) % tabs.length];
                if (event.key === 'ArrowLeft') target = tabs[(index - 1 + tabs.length) % tabs.length];
                if (event.key === 'Home') target = tabs[0];
                if (event.key === 'End') target = tabs[tabs.length - 1];
                if (!target) return;
                event.preventDefault();
                selectAdvisorView(target.dataset.advisorView, true);
            });
        });
    }

    function selectAdvisorView(view, focusTab = true) {
        activeView = view === 'benchmark' ? 'benchmark' : 'advisor';
        render();
        if (activeView === 'benchmark') {
            void loadBenchmarkHistory();
            if (benchmarkRunning) startBenchmarkProgressPolling();
        }
        if (focusTab) {
            window.setTimeout(() => document.getElementById(`advisor-tab-${activeView}`)?.focus(), 0);
        }
    }

    function selectProfile(profile) {
        selectedProfile = normalizeProfile(profile);
        localStorage.setItem(PROFILE_KEY, selectedProfile);
        syncAutoSelection();
        render();
    }

    function useAutoProfile() {
        const loaded = advisorData()?.loaded_profiles?.[selectedProfile];
        const option = loaded ? modelSelect.querySelector(`option[value="${CSS.escape(loaded.model_id)}"]`) : null;
        if (!loaded || !option) {
            toast('Load a compatible generation model before using automatic routing.');
            return;
        }
        autoRoutingProfile = selectedProfile;
        localStorage.setItem(AUTO_KEY, autoRoutingProfile);
        syncAutoSelection();
        close();
        toast(`${PROFILE_LABELS[selectedProfile]} automatic model routing selected.`);
    }

    async function prepareRecommendation() {
        const recommendation = currentRecommendation();
        const model = (latestStatus?.models?.available || []).find(item => item.id === recommendation?.model_id);
        if (!recommendation || !model || loading) return;
        loading = true;
        render();
        try {
            const endpoint = model.is_downloaded ? '/v1/models/load' : '/v1/models/convert';
            const payload = model.is_downloaded
                ? { model: model.id, device: recommendation.device }
                : { model: model.id, device: recommendation.device, load_after: true };
            const response = await window.fetch(endpoint, {
                method: 'POST',
                headers: apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`);
            toast(result.message || 'Recommended model preparation started.');
            window.setTimeout(() => refresh(true), 800);
        } catch (error) {
            toast(error instanceof Error ? error.message : String(error));
        } finally {
            loading = false;
            render();
        }
    }

    async function refresh(forceRender = false) {
        try {
            const response = await upstreamFetch('/v1/system/status', { headers: apiHeaders() });
            if (!response.ok) return;
            latestStatus = await response.json();
            syncAutoSelection();
            if (forceRender || (overlay.classList.contains('visible') && activeView === 'advisor')) render();
        } catch { /* normal server polling remains authoritative */ }
    }

    function open() {
        overlay.classList.add('visible');
        document.body.style.overflow = 'hidden';
        render();
        void refresh(true);
        if (!refreshTimer) refreshTimer = window.setInterval(() => refresh(false), 5000);
        if (activeView === 'benchmark' && benchmarkRunning) startBenchmarkProgressPolling();
        window.setTimeout(() => document.getElementById(`advisor-tab-${activeView}`)?.focus(), 0);
    }

    function close() {
        overlay.classList.remove('visible');
        document.body.style.overflow = '';
        if (refreshTimer) {
            window.clearInterval(refreshTimer);
            refreshTimer = null;
        }
        stopBenchmarkProgressPolling();
        button.focus();
    }

    function customEstimate(bodyData) {
        const text = `${bodyData.model_id || ''} ${bodyData.name || ''} ${bodyData.source_model || ''}`.toLowerCase();
        const matchB = [...text.matchAll(/(?:^|[^a-z0-9])([0-9]+(?:\.[0-9]+)?)\s*b(?:[^a-z]|$)/g)].at(-1);
        const matchM = [...text.matchAll(/(?:^|[^a-z0-9])([0-9]+(?:\.[0-9]+)?)\s*m(?:[^a-z]|$)/g)].at(-1);
        const params = matchB ? Number(matchB[1]) : matchM ? Number(matchM[1]) / 1000 : 1;
        const precision = String(bodyData.weight_format || 'fp16').toLowerCase();
        const bytes = precision === 'int4' ? .58 : precision === 'int8' ? 1.05 : 2;
        const converted = Math.max(params * bytes * 1.08, .04);
        const download = Math.max(params * 2.1, .06);
        const available = Number(advisorData()?.hardware?.disk?.free_gb || 0);
        const warnings = [];
        if (params >= 7) warnings.push(`This appears to be a ${params.toFixed(1)}B model and may take substantial RAM and compilation time.`);
        if (available && available < download + converted + 1) warnings.push(`Estimated preparation needs ${(download + converted + 1).toFixed(1)} GB, but only ${available.toFixed(1)} GB is free.`);
        if (String(bodyData.recommended_device || '').toUpperCase().includes('NPU') && params > 4.5) warnings.push('Large-model NPU support varies by Intel platform and driver. CPU or GPU is safer until benchmarked.');
        return warnings;
    }

    function preflightWarnings(path, requestBody) {
        const data = advisorData();
        if (!data) return [];
        if (path === '/v1/models/download-custom') return customEstimate(requestBody);
        const modelId = requestBody.model || requestBody.model_id;
        const model = (data.models || []).find(item => item.id === modelId);
        if (!model) return [];
        return (model.warnings || [])
            .filter(item => ['warning', 'blocking'].includes(item.severity))
            .map(item => item.message);
    }
"""
