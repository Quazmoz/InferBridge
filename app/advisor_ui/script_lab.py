"""Benchmark Lab JavaScript integrated with Hardware Advisor."""

SCRIPT_LAB = r"""
    const BENCHMARK_PRESETS = {
        quick: { label: 'Quick', runs: 3, warmups: 1, maxTokens: 32, description: 'Fast comparison · 1 warm-up + 3 measured runs' },
        standard: { label: 'Standard', runs: 5, warmups: 1, maxTokens: 64, description: 'Recommended · 1 warm-up + 5 measured runs' },
        thorough: { label: 'Thorough', runs: 8, warmups: 2, maxTokens: 128, description: 'More stable · 2 warm-ups + 8 measured runs' },
    };

    function benchmarkModels(data) {
        const runtimeModels = new Map((latestStatus?.models?.available || []).map(model => [model.id, model]));
        const mock = Boolean(latestStatus?.device?.mock);
        return (data.models || [])
            .filter(model => !String(model.backend || '').includes('embedding'))
            .map(model => {
                const runtime = runtimeModels.get(model.id) || {};
                return {
                    ...model,
                    runtime,
                    prepared: mock || Boolean(model.downloaded || runtime.is_downloaded || runtime.is_loaded),
                    loaded: Boolean(model.loaded || runtime.is_loaded),
                };
            })
            .sort((a, b) => {
                if (a.prepared !== b.prepared) return a.prepared ? -1 : 1;
                if (a.loaded !== b.loaded) return a.loaded ? -1 : 1;
                return String(a.name || a.id).localeCompare(String(b.name || b.id));
            });
    }

    function benchmarkDevices(data) {
        const raw = data?.hardware?.available_devices || latestStatus?.device?.available || [];
        const direct = [];
        raw.forEach(value => {
            const base = String(value || '').split('.', 1)[0].toUpperCase();
            if (['CPU', 'GPU', 'NPU'].includes(base) && !direct.includes(base)) direct.push(base);
        });
        if (!direct.includes('CPU') && Boolean(latestStatus?.device?.mock)) direct.unshift('CPU');
        return [...direct.map(device => ({ value: device, label: device, routing: false })), { value: 'AUTO', label: 'AUTO', routing: true }];
    }

    function ensureBenchmarkDefaults(data) {
        const models = benchmarkModels(data);
        const devices = benchmarkDevices(data);
        const preparedIds = new Set(models.filter(model => model.prepared).map(model => model.id));
        benchmarkSelectedModels = new Set([...benchmarkSelectedModels].filter(id => preparedIds.has(id)));

        const validDevices = new Set([...devices.map(item => item.value), ...benchmarkAdvancedDevices]);
        benchmarkSelectedDevices = new Set([...benchmarkSelectedDevices].filter(device => validDevices.has(device)));

        if (!benchmarkSelectionsSeeded) {
            const current = modelSelect.value;
            const preferred = models.find(model => model.id === current && model.prepared)
                || models.find(model => model.loaded && model.prepared)
                || models.find(model => model.prepared);
            if (preferred) benchmarkSelectedModels.add(preferred.id);
            devices.filter(item => !item.routing).forEach(item => benchmarkSelectedDevices.add(item.value));
            benchmarkSelectionsSeeded = true;
        }
    }

    function benchmarkPresetConfig() {
        const preset = BENCHMARK_PRESETS[benchmarkPreset];
        if (preset) return preset;
        return {
            label: 'Custom',
            runs: Math.max(1, Math.min(Number(benchmarkCustomRuns) || 5, 10)),
            warmups: Number(benchmarkCustomRuns) <= 1 ? 0 : Number(benchmarkCustomRuns) <= 5 ? 1 : 2,
            maxTokens: Math.max(1, Math.min(Number(benchmarkCustomTokens) || 64, 4096)),
            description: 'Advanced custom methodology',
        };
    }

    function benchmarkModelMeta(model) {
        const parts = [String(model.precision || model.weight_format || '').toUpperCase()];
        if (Number(model.parameter_count_b) > 0) parts.push(`${Number(model.parameter_count_b).toFixed(2)}B params`);
        if (model.architecture_type) parts.push(String(model.architecture_type));
        if (Number(model.active_parameters_b) > 0) parts.push(`${Number(model.active_parameters_b).toFixed(2)}B active/token`);
        return parts.filter(Boolean).join(' · ');
    }

    function benchmarkModelListHtml(data) {
        const models = benchmarkModels(data);
        if (!models.length) return '<div class="advisor-empty">No generation models are registered.</div>';
        return `
            <label class="benchmark-search">
                <span class="lab-sr-only">Filter benchmark models</span>
                <input id="benchmark-model-search" type="search" value="${escapeHtml(benchmarkModelFilter)}" placeholder="Filter prepared models…" autocomplete="off">
            </label>
            <div class="benchmark-model-list" id="benchmark-model-list">
                ${models.map(model => {
                    const selected = benchmarkSelectedModels.has(model.id);
                    const state = model.loaded ? 'Loaded' : model.prepared ? 'Prepared' : 'Needs preparation';
                    const search = `${model.name || ''} ${model.id} ${model.precision || ''}`.toLowerCase();
                    return `<div class="benchmark-model-row ${model.prepared ? '' : 'needs-preparation'}" data-model-search="${escapeHtml(search)}">
                        ${model.prepared ? `
                            <label class="benchmark-model-choice">
                                <input type="checkbox" data-benchmark-model="${escapeHtml(model.id)}" ${selected ? 'checked' : ''}>
                                <span class="benchmark-model-copy"><strong>${escapeHtml(model.name || model.id)}</strong><span>${escapeHtml(benchmarkModelMeta(model))}</span></span>
                            </label>` : `
                            <div class="benchmark-model-choice benchmark-model-disabled">
                                <span class="benchmark-model-copy"><strong>${escapeHtml(model.name || model.id)}</strong><span>${escapeHtml(benchmarkModelMeta(model))}</span></span>
                            </div>`}
                        <div class="benchmark-model-state">
                            <span class="benchmark-state ${model.loaded ? 'loaded' : model.prepared ? 'prepared' : 'unprepared'}">${state}</span>
                            ${!model.prepared ? `<button type="button" class="advisor-secondary benchmark-prepare-btn" data-benchmark-prepare="${escapeHtml(model.id)}">Prepare</button>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>`;
    }

    function benchmarkDevicesHtml(data) {
        const devices = benchmarkDevices(data);
        const all = [...devices, ...benchmarkAdvancedDevices.map(value => ({ value, label: value, routing: true, custom: true }))];
        return `<div class="benchmark-device-grid">
            ${all.map(item => `<label class="benchmark-device-choice ${item.routing ? 'routing' : ''}">
                <input type="checkbox" data-benchmark-device="${escapeHtml(item.value)}" ${benchmarkSelectedDevices.has(item.value) ? 'checked' : ''}>
                <span><strong>${escapeHtml(item.label)}</strong><small>${item.custom ? 'Advanced target' : item.routing ? 'OpenVINO routing' : 'Direct device'}</small></span>
            </label>`).join('')}
        </div>`;
    }

    function benchmarkPresetsHtml() {
        return `<div class="benchmark-presets" role="radiogroup" aria-label="Benchmark thoroughness">
            ${Object.entries(BENCHMARK_PRESETS).map(([id, preset]) => `<label class="benchmark-preset ${benchmarkPreset === id ? 'active' : ''}">
                <input type="radio" name="benchmark-preset" value="${id}" ${benchmarkPreset === id ? 'checked' : ''}>
                <span><strong>${preset.label}${id === 'standard' ? ' · Recommended' : ''}</strong><small>${preset.description}</small></span>
            </label>`).join('')}
        </div>`;
    }

    function benchmarkProgressHtml() {
        if (!benchmarkRunning && !benchmarkError) return '';
        const percent = benchmarkProgress.total > 0
            ? Math.round((benchmarkProgress.current / benchmarkProgress.total) * 100)
            : 0;
        return `<div class="benchmark-progress ${benchmarkError ? 'has-error' : ''}" role="status" aria-live="polite">
            <div class="benchmark-progress-head"><strong>${benchmarkError ? 'Benchmark stopped' : 'Benchmark running'}</strong><span>${benchmarkProgress.total > 0 ? `${benchmarkProgress.current} / ${benchmarkProgress.total} combinations` : 'Starting…'}</span></div>
            <div class="benchmark-progress-track" role="progressbar" aria-label="Benchmark progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div>
            <div id="benchmark-progress-message" class="benchmark-progress-message">${escapeHtml(benchmarkError || benchmarkProgress.message)}</div>
            <div class="benchmark-resource-note">Benchmarking can temporarily use substantial CPU, GPU, NPU, RAM, and power. Normal chat may be slower while it runs.</div>
        </div>`;
    }

    function benchmarkLabHtml(data) {
        ensureBenchmarkDefaults(data);
        const preset = benchmarkPresetConfig();
        const combinations = benchmarkSelectedModels.size * benchmarkSelectedDevices.size;
        const workUnits = combinations * (preset.runs + preset.warmups);
        return `
            <div id="advisor-panel-benchmark" role="tabpanel" aria-labelledby="advisor-tab-benchmark" class="benchmark-lab">
                <section class="benchmark-intro">
                    <div><h3>How fast does this model actually run on my PC?</h3><p>Compare prepared generation models across the OpenVINO devices this machine exposes. Nothing is uploaded.</p></div>
                    <span class="benchmark-local-badge">${latestStatus?.device?.mock ? 'Synthetic mock mode' : 'Local-only measurement'}</span>
                </section>

                <section class="advisor-card benchmark-config">
                    <div class="benchmark-config-section">
                        <div class="benchmark-section-head"><div><h3>1 · Models</h3><p>Select one or more locally prepared generation models. Selecting a model never starts a download.</p></div><span>${benchmarkSelectedModels.size} selected</span></div>
                        ${benchmarkModelListHtml(data)}
                    </div>
                    <div class="benchmark-config-section">
                        <div class="benchmark-section-head"><div><h3>2 · Devices</h3><p>Direct devices come from OpenVINO discovery. Requested and actual execution are always shown separately.</p></div><span>${benchmarkSelectedDevices.size} selected</span></div>
                        ${benchmarkDevicesHtml(data)}
                    </div>
                    <div class="benchmark-config-section">
                        <div class="benchmark-section-head"><div><h3>3 · Thoroughness</h3><p>Warm-ups are excluded from measured statistics.</p></div><span>${escapeHtml(preset.label)}</span></div>
                        ${benchmarkPresetsHtml()}
                    </div>

                    <details class="benchmark-advanced">
                        <summary>Advanced</summary>
                        <div class="benchmark-advanced-grid">
                            <label><span>Measured runs</span><input id="benchmark-custom-runs" type="number" min="1" max="10" value="${preset.runs}"></label>
                            <label><span>Output token target</span><input id="benchmark-custom-tokens" type="number" min="1" max="4096" value="${preset.maxTokens}"></label>
                            <label class="benchmark-wide"><span>Benchmark prompt</span><textarea id="benchmark-custom-prompt" rows="3" placeholder="Use InferBridge's deterministic default prompt">${escapeHtml(benchmarkCustomPrompt)}</textarea></label>
                            <label class="benchmark-wide"><span>Advanced OpenVINO device expression</span><div class="benchmark-inline-input"><input id="benchmark-custom-device" type="text" placeholder="AUTO:NPU,GPU,CPU"><button id="benchmark-add-device" type="button" class="advisor-secondary">Add target</button></div></label>
                        </div>
                        <p class="benchmark-advanced-note">Composite targets are for experienced OpenVINO users. A routing target can execute somewhere different from the requested expression, so the results matrix reports both.</p>
                    </details>

                    <div class="benchmark-run-bar">
                        <div><strong>${combinations || 0} combination${combinations === 1 ? '' : 's'}</strong><span>${workUnits || 0} total warm-up/measured generations · ${preset.maxTokens} output-token target</span></div>
                        <button id="benchmark-run-lab-btn" class="advisor-primary" type="button" ${benchmarkRunning || !combinations ? 'disabled' : ''}>${benchmarkRunning ? 'Benchmark running…' : 'Run benchmark'}</button>
                    </div>
                    ${benchmarkProgressHtml()}
                </section>

                ${benchmarkResultsHtml(benchmarkRun, data)}
                ${benchmarkHistoryHtml(data)}
            </div>`;
    }

    function benchmarkSpeed(row) {
        const decode = Number(row?.decode_tokens_sec);
        if (Number.isFinite(decode) && decode > 0) return { value: decode, legacy: false };
        const legacy = Number(row?.tokens_sec);
        return Number.isFinite(legacy) && legacy > 0 ? { value: legacy, legacy: true } : { value: null, legacy: false };
    }

    function resolveLeader(run, key) {
        const supplied = run?.leaders?.[key];
        if (supplied) return supplied;
        const rows = (run?.results || []).filter(row => row.success);
        if (!rows.length) return null;
        if (key === 'fastest_generation') {
            return rows.reduce((best, row) => benchmarkSpeed(row).value > benchmarkSpeed(best).value ? row : best, rows[0]);
        }
        if (key === 'fastest_first_token') {
            const timed = rows.filter(row => Number.isFinite(Number(row.time_to_first_token_ms)));
            return timed.sort((a, b) => Number(a.time_to_first_token_ms) - Number(b.time_to_first_token_ms))[0] || null;
        }
        return rows.sort((a, b) => Number(b.score || 0) - Number(a.score || 0))[0] || null;
    }

    function modelNameFor(modelId, data) {
        return (data.models || []).find(model => model.id === modelId)?.name || modelId || 'Unknown model';
    }

    function leaderCard(label, leader, data, kind) {
        if (!leader) return `<div class="benchmark-hero-card"><span>${label}</span><strong>Unavailable</strong><small>No successful measurement</small></div>`;
        const speed = benchmarkSpeed(leader);
        const primary = kind === 'ttft' ? formatMs(leader.time_to_first_token_ms) : formatTps(speed.value);
        const secondary = kind === 'ttft'
            ? `${formatTps(speed.value)} generation`
            : `${formatMs(leader.time_to_first_token_ms)} first token`;
        return `<div class="benchmark-hero-card"><span>${label}</span><strong>${escapeHtml(primary)}</strong><small>${escapeHtml(modelNameFor(leader.model_id, data))} · ${escapeHtml(leader.requested_device || '—')} ${leader.actual_device ? `→ ${escapeHtml(leader.actual_device)}` : ''}<br>${escapeHtml(secondary)}</small></div>`;
    }

    function stabilityHtml(row) {
        const stability = row.stability;
        if (!stability) return '<span class="benchmark-muted">—</span>';
        const status = String(stability.status || 'unknown');
        const cv = Number(stability.cv_percent);
        const range = Number.isFinite(Number(stability.min)) && Number.isFinite(Number(stability.max))
            ? `${Number(stability.min).toFixed(1)}–${Number(stability.max).toFixed(1)} tok/s`
            : '';
        return `<span class="benchmark-stability ${escapeHtml(status)}">${escapeHtml(status)}</span><div class="advisor-hw-sub">${Number.isFinite(cv) ? `CV ${cv.toFixed(1)}%` : ''}${range ? ` · ${range}` : ''}</div>`;
    }

    function methodologyCompatible(a, b) {
        return Boolean(
            a?.hardware_fingerprint
            && b?.hardware_fingerprint
            && a.hardware_fingerprint === b.hardware_fingerprint
            && Number(a.methodology_version || 1) === Number(b.methodology_version || 1)
            && Number(a.max_tokens) === Number(b.max_tokens)
            && Number(a.runs_per_combo) === Number(b.runs_per_combo)
            && Number(a.warmup_runs || 0) === Number(b.warmup_runs || 0)
        );
    }

    function previousComparableRow(run, row) {
        if (!run?.hardware_fingerprint) return null;
        for (const historical of benchmarkHistory) {
            if (historical.run_id === run.run_id || historical.automatic || !methodologyCompatible(run, historical)) continue;
            const candidate = (historical.results || []).find(previous => (
                previous.success
                && previous.model_id === row.model_id
                && previous.source_model === row.source_model
                && previous.weight_format === row.weight_format
                && previous.requested_device === row.requested_device
                && (previous.actual_device || '') === (row.actual_device || '')
            ));
            if (candidate) return candidate;
        }
        return null;
    }

    function deltaText(current, previous, label, lowerIsBetter = false) {
        const now = Number(current);
        const before = Number(previous);
        if (!Number.isFinite(now) || !Number.isFinite(before) || before === 0) return '';
        const delta = ((now - before) / before) * 100;
        if (Math.abs(delta) < 0.5) return `${label} essentially unchanged`;
        const arrow = delta > 0 ? '↑' : '↓';
        const improved = lowerIsBetter ? delta < 0 : delta > 0;
        return `${label} ${arrow} ${Math.abs(delta).toFixed(0)}% ${improved ? 'better' : 'worse'}`;
    }

    function comparisonHtml(run, row) {
        const previous = previousComparableRow(run, row);
        if (!previous) return '';
        const speed = benchmarkSpeed(row).value;
        const previousSpeed = benchmarkSpeed(previous).value;
        const parts = [
            deltaText(speed, previousSpeed, 'Generation'),
            deltaText(row.time_to_first_token_ms, previous.time_to_first_token_ms, 'TTFT', true),
        ].filter(Boolean);
        return parts.length ? `<div class="benchmark-delta">${escapeHtml(parts.join(' · '))}</div>` : '';
    }

    function benchmarkResultsHtml(run, data) {
        if (!run) return `<section class="advisor-card benchmark-results-empty"><h3>Results</h3><p>Run a benchmark to compare generation speed, first-token responsiveness, load cost, actual device routing, and stability.</p></section>`;
        const results = Array.isArray(run.results) ? run.results : [];
        const fastestGeneration = resolveLeader(run, 'fastest_generation');
        const fastestFirst = resolveLeader(run, 'fastest_first_token');
        const balanced = resolveLeader(run, 'best_balanced');
        return `
            <section class="advisor-card benchmark-results">
                <div class="benchmark-results-head">
                    <div><h3>Results</h3><p>${escapeHtml(String(run.preset || 'custom').replace(/^./, value => value.toUpperCase()))} · median of ${Number(run.runs_per_combo || 1)} measured run${Number(run.runs_per_combo || 1) === 1 ? '' : 's'} · ${Number(run.warmup_runs || 0)} warm-up${Number(run.warmup_runs || 0) === 1 ? '' : 's'}</p></div>
                    <div class="benchmark-result-actions">
                        <button id="benchmark-copy-results" type="button" class="advisor-secondary">Copy results</button>
                        <button id="benchmark-download-json" type="button" class="advisor-secondary">Download JSON</button>
                    </div>
                </div>
                ${run.mock || run.synthetic ? '<div class="benchmark-synthetic"><strong>Synthetic / mock mode</strong><span>Useful for UI, API, and CI validation only. This is not hardware performance evidence or certification.</span></div>' : ''}
                <div class="benchmark-hero-grid">
                    ${leaderCard('Fastest generation', fastestGeneration, data, 'speed')}
                    ${leaderCard('Fastest first token', fastestFirst, data, 'ttft')}
                    ${leaderCard('Best balanced', balanced, data, 'speed')}
                </div>
                <div class="benchmark-metric-explainer"><strong>Generation speed</strong> is post-first-token decode throughput when measurable. <strong>First token</strong> includes prompt processing plus first-token decode. Prefill-only throughput is shown as unavailable unless the runtime exposes a trustworthy boundary.</div>
                <div class="benchmark-table-wrap" tabindex="0" aria-label="Benchmark model and device comparison matrix">
                    <table class="benchmark-table">
                        <caption class="lab-sr-only">Benchmark comparison by model, precision, requested and actual device, throughput, latency, memory, load time, and stability</caption>
                        <thead><tr><th>Model</th><th>Precision</th><th>Requested → actual</th><th>Status</th><th>Generation speed</th><th>First token</th><th>Prefill</th><th>Total latency</th><th>Peak process RAM</th><th>Load</th><th>Stability</th></tr></thead>
                        <tbody>${results.map(row => {
                            const speed = benchmarkSpeed(row);
                            const device = `${row.requested_device || '—'} → ${row.actual_device || 'unavailable'}`;
                            if (!row.success) {
                                return `<tr class="benchmark-failed-row"><td><strong>${escapeHtml(modelNameFor(row.model_id, data))}</strong></td><td>${escapeHtml(String(row.weight_format || '').toUpperCase())}</td><td>${escapeHtml(device)}</td><td><span class="benchmark-row-status failed">Failed</span></td><td colspan="7"><span class="benchmark-error">${escapeHtml(row.error || 'Benchmark failed for this combination.')}</span></td></tr>`;
                            }
                            return `<tr>
                                <td><strong>${escapeHtml(modelNameFor(row.model_id, data))}</strong>${comparisonHtml(run, row)}</td>
                                <td>${escapeHtml(String(row.weight_format || '').toUpperCase())}</td>
                                <td><strong>${escapeHtml(device)}</strong></td>
                                <td><span class="benchmark-row-status ${row.synthetic ? 'synthetic' : 'measured'}">${row.synthetic ? 'Synthetic' : 'Measured'}</span></td>
                                <td><strong class="benchmark-speed">${formatTps(speed.value)}</strong><div class="advisor-hw-sub">${speed.legacy ? 'Legacy total throughput' : 'Decode throughput'}</div></td>
                                <td>${formatMs(row.time_to_first_token_ms)}</td>
                                <td><span class="benchmark-muted">Unavailable</span></td>
                                <td>${formatMs(row.total_latency_ms)}</td>
                                <td>${Number.isFinite(Number(row.peak_process_ram_mb)) ? `${Number(row.peak_process_ram_mb).toFixed(0)} MB` : '—'}<div class="advisor-hw-sub">Process RSS</div></td>
                                <td>${formatMs(row.load_time_ms)}</td>
                                <td>${stabilityHtml(row)}</td>
                            </tr>`;
                        }).join('')}</tbody>
                    </table>
                </div>
            </section>`;
    }

    function runFastestSummary(run, data) {
        const leader = resolveLeader(run, 'fastest_generation');
        if (!leader) return 'No successful combinations';
        const speed = benchmarkSpeed(leader).value;
        return `${modelNameFor(leader.model_id, data)} · ${leader.requested_device || '—'} · ${formatTps(speed)}`;
    }

    function benchmarkHistoryHtml(data) {
        if (benchmarkHistoryLoading) return '<section class="advisor-card benchmark-history"><h3>Recent benchmark runs</h3><div class="advisor-spinner"></div></section>';
        const runs = benchmarkHistory.filter(run => !run.automatic).slice(0, 5);
        return `<section class="advisor-card benchmark-history">
            <div class="benchmark-history-head"><div><h3>Recent benchmark runs</h3><p>Open a previous local run or compare compatible evidence automatically.</p></div>${runs.length ? '<button id="benchmark-clear-history" type="button" class="advisor-secondary benchmark-danger">Clear history</button>' : ''}</div>
            ${runs.length ? `<div class="benchmark-history-list">${runs.map(run => `<button type="button" class="benchmark-history-row ${benchmarkRun?.run_id === run.run_id ? 'active' : ''}" data-benchmark-history="${escapeHtml(run.run_id)}">
                <span><strong>${escapeHtml(new Date(run.created_at || Date.now()).toLocaleString())}</strong><small>${escapeHtml((run.models || [...new Set((run.results || []).map(row => row.model_id))]).length)} model(s) · ${escapeHtml((run.devices || [...new Set((run.results || []).map(row => row.requested_device))]).join(', '))} · ${escapeHtml(run.preset || 'custom')}</small></span>
                <span>${escapeHtml(runFastestSummary(run, data))}</span>
            </button>`).join('')}</div>` : '<div class="advisor-empty">No manual Benchmark Lab runs saved yet.</div>'}
        </section>`;
    }

    function wireBenchmarkLab() {
        const data = advisorData();
        body.querySelectorAll('[data-benchmark-model]').forEach(input => input.addEventListener('change', () => {
            if (input.checked) benchmarkSelectedModels.add(input.dataset.benchmarkModel);
            else benchmarkSelectedModels.delete(input.dataset.benchmarkModel);
            render();
        }));
        body.querySelectorAll('[data-benchmark-device]').forEach(input => input.addEventListener('change', () => {
            if (input.checked) benchmarkSelectedDevices.add(input.dataset.benchmarkDevice);
            else benchmarkSelectedDevices.delete(input.dataset.benchmarkDevice);
            render();
        }));
        body.querySelectorAll('input[name="benchmark-preset"]').forEach(input => input.addEventListener('change', () => {
            benchmarkPreset = input.value;
            const preset = BENCHMARK_PRESETS[benchmarkPreset];
            benchmarkCustomRuns = preset.runs;
            benchmarkCustomTokens = preset.maxTokens;
            render();
        }));
        body.querySelectorAll('[data-benchmark-prepare]').forEach(button => button.addEventListener('click', () => void prepareBenchmarkModel(button.dataset.benchmarkPrepare)));
        body.querySelectorAll('[data-benchmark-history]').forEach(button => button.addEventListener('click', () => {
            benchmarkRun = benchmarkHistory.find(run => run.run_id === button.dataset.benchmarkHistory) || benchmarkRun;
            render();
        }));
        document.getElementById('benchmark-run-lab-btn')?.addEventListener('click', () => void runBenchmarkLab());
        document.getElementById('benchmark-copy-results')?.addEventListener('click', () => void copyBenchmarkResults());
        document.getElementById('benchmark-download-json')?.addEventListener('click', downloadBenchmarkJson);
        document.getElementById('benchmark-clear-history')?.addEventListener('click', () => void clearBenchmarkHistory());
        document.getElementById('benchmark-add-device')?.addEventListener('click', addAdvancedBenchmarkDevice);

        const search = document.getElementById('benchmark-model-search');
        search?.addEventListener('input', () => {
            benchmarkModelFilter = search.value;
            filterBenchmarkModels();
        });
        filterBenchmarkModels();

        const runsInput = document.getElementById('benchmark-custom-runs');
        const tokensInput = document.getElementById('benchmark-custom-tokens');
        const promptInput = document.getElementById('benchmark-custom-prompt');
        runsInput?.addEventListener('change', () => {
            benchmarkPreset = 'custom';
            benchmarkCustomRuns = Math.max(1, Math.min(Number(runsInput.value) || 5, 10));
        });
        tokensInput?.addEventListener('change', () => {
            benchmarkPreset = 'custom';
            benchmarkCustomTokens = Math.max(1, Math.min(Number(tokensInput.value) || 64, 4096));
        });
        promptInput?.addEventListener('input', () => {
            benchmarkCustomPrompt = promptInput.value;
        });

        if (!benchmarkHistoryLoaded && !benchmarkHistoryLoading) void loadBenchmarkHistory();
        if (benchmarkRunning) startBenchmarkProgressPolling();
        void data;
    }

    function filterBenchmarkModels() {
        const term = benchmarkModelFilter.trim().toLowerCase();
        document.querySelectorAll('#benchmark-model-list [data-model-search]').forEach(row => {
            row.hidden = Boolean(term) && !String(row.dataset.modelSearch || '').includes(term);
        });
    }

    function addAdvancedBenchmarkDevice() {
        const input = document.getElementById('benchmark-custom-device');
        const value = String(input?.value || '').trim().toUpperCase();
        if (!value) return;
        if (!benchmarkAdvancedDevices.includes(value)) benchmarkAdvancedDevices.push(value);
        benchmarkSelectedDevices.add(value);
        render();
    }

    async function prepareBenchmarkModel(modelId) {
        const model = benchmarkModels(advisorData()).find(item => item.id === modelId);
        if (!model) return;
        try {
            const response = await window.fetch('/v1/models/convert', {
                method: 'POST',
                headers: apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    model: model.id,
                    device: model.recommended_device || 'CPU',
                    load_after: false,
                }),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.detail || `Preparation failed (${response.status})`);
            toast(result.message || `${model.name || model.id} preparation started.`);
            window.setTimeout(() => refresh(true), 800);
        } catch (error) {
            toast(error instanceof Error ? error.message : String(error));
        }
    }

    async function runBenchmarkLab() {
        if (benchmarkRunning) return;
        const models = [...benchmarkSelectedModels];
        const devices = [...benchmarkSelectedDevices];
        if (!models.length || !devices.length) {
            toast('Select at least one prepared model and one device.');
            return;
        }

        const preset = benchmarkPresetConfig();
        benchmarkRunning = true;
        benchmarkError = '';
        benchmarkStartedAt = Date.now();
        benchmarkProgress = { message: 'Preparing benchmark matrix…', current: 0, total: models.length * devices.length };
        render();
        startBenchmarkProgressPolling();

        const payload = {
            models,
            devices,
            runs: preset.runs,
            max_tokens: preset.maxTokens,
        };
        if (benchmarkCustomPrompt.trim()) payload.prompt = benchmarkCustomPrompt.trim();

        try {
            const response = await window.fetch('/v1/benchmarks/run', {
                method: 'POST',
                headers: apiHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.detail || `Benchmark failed (${response.status})`);
            benchmarkRun = result;
            benchmarkHistory = [result, ...benchmarkHistory.filter(run => run.run_id !== result.run_id)];
            benchmarkHistoryLoaded = true;
            benchmarkProgress = { message: 'Benchmark complete.', current: devices.length * models.length, total: devices.length * models.length };
            toast(result.mock ? 'Synthetic benchmark complete.' : 'Benchmark complete.');
        } catch (error) {
            benchmarkError = error instanceof Error ? error.message : String(error);
            toast(benchmarkError);
        } finally {
            benchmarkRunning = false;
            stopBenchmarkProgressPolling();
            if (overlay.classList.contains('visible') && activeView === 'benchmark') render();
        }
    }

    function startBenchmarkProgressPolling() {
        if (benchmarkProgressTimer || !benchmarkRunning || !overlay.classList.contains('visible')) return;
        void pollBenchmarkProgress();
        benchmarkProgressTimer = window.setInterval(pollBenchmarkProgress, 1500);
    }

    function stopBenchmarkProgressPolling() {
        if (!benchmarkProgressTimer) return;
        window.clearInterval(benchmarkProgressTimer);
        benchmarkProgressTimer = null;
    }

    async function pollBenchmarkProgress() {
        if (!benchmarkRunning || !overlay.classList.contains('visible')) {
            stopBenchmarkProgressPolling();
            return;
        }
        try {
            const response = await upstreamFetch('/v1/system/status', { headers: apiHeaders() });
            if (!response.ok) return;
            latestStatus = await response.json();
            const cutoff = Math.floor(benchmarkStartedAt / 1000) - 1;
            const events = (latestStatus.events || []).filter(event => Number(event.timestamp || 0) >= cutoff && String(event.message || '').startsWith('Benchmark Lab ·'));
            const latest = events.at(-1);
            if (!latest) return;
            const message = String(latest.message || '');
            const match = message.match(/combination\s+(\d+)\/(\d+)/i);
            benchmarkProgress.message = message;
            if (match) {
                benchmarkProgress.current = Math.max(Number(match[1]) - (message.endsWith('· complete') ? 0 : 1), 0);
                benchmarkProgress.total = Number(match[2]);
                if (message.endsWith('· complete')) benchmarkProgress.current = Number(match[1]);
            }
            const messageNode = document.getElementById('benchmark-progress-message');
            if (messageNode) messageNode.textContent = message;
            const progress = document.querySelector('.benchmark-progress-track');
            if (progress && benchmarkProgress.total > 0) {
                const percent = Math.round((benchmarkProgress.current / benchmarkProgress.total) * 100);
                progress.setAttribute('aria-valuenow', String(percent));
                const fill = progress.querySelector('span');
                if (fill) fill.style.width = `${percent}%`;
            }
            const progressCount = document.querySelector('.benchmark-progress-head span');
            if (progressCount && benchmarkProgress.total > 0) {
                progressCount.textContent = `${benchmarkProgress.current} / ${benchmarkProgress.total} combinations`;
            }
        } catch { /* the benchmark request owns the final error state */ }
    }

    async function loadBenchmarkHistory(force = false) {
        if (benchmarkHistoryLoading || (benchmarkHistoryLoaded && !force)) return;
        benchmarkHistoryLoading = true;
        if (overlay.classList.contains('visible') && activeView === 'benchmark') render();
        try {
            const response = await upstreamFetch('/v1/benchmarks', { headers: apiHeaders() });
            if (!response.ok) throw new Error(`History request failed (${response.status})`);
            const payload = await response.json();
            benchmarkHistory = Array.isArray(payload.data) ? payload.data : [];
            benchmarkHistoryLoaded = true;
            if (!benchmarkRun) benchmarkRun = benchmarkHistory.find(run => !run.automatic) || null;
        } catch (error) {
            toast(error instanceof Error ? error.message : String(error));
        } finally {
            benchmarkHistoryLoading = false;
            if (overlay.classList.contains('visible') && activeView === 'benchmark') render();
        }
    }

    async function clearBenchmarkHistory() {
        if (!window.confirm('Clear all saved benchmark history on this InferBridge instance? This cannot be undone.')) return;
        try {
            const response = await window.fetch('/v1/benchmarks', {
                method: 'DELETE',
                headers: apiHeaders(),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || `Clear failed (${response.status})`);
            benchmarkHistory = [];
            benchmarkRun = null;
            benchmarkHistoryLoaded = true;
            render();
            toast(`Cleared ${Number(payload.deleted_runs || 0)} saved benchmark run(s).`);
        } catch (error) {
            toast(error instanceof Error ? error.message : String(error));
        }
    }

    function safeBenchmarkExport(run) {
        const environment = run.environment || {};
        return {
            title: 'InferBridge Benchmark',
            exported_at: new Date().toISOString(),
            run: {
                run_id: run.run_id,
                created_at: run.created_at,
                finished_at: run.finished_at,
                mock: Boolean(run.mock || run.synthetic),
                preset: run.preset || 'custom',
                methodology_version: run.methodology_version || 1,
                max_tokens: run.max_tokens,
                measured_runs: run.runs_per_combo,
                warmup_runs: run.warmup_runs || 0,
            },
            system: {
                cpu: environment.cpu || advisorData()?.hardware?.cpu?.name || null,
                ram_gb: environment.ram_gb ?? advisorData()?.hardware?.memory?.total_gb ?? null,
                inferbridge: environment.inferbridge || null,
                openvino: environment.openvino || advisorData()?.hardware?.runtime?.openvino || null,
                openvino_genai: environment.openvino_genai || advisorData()?.hardware?.runtime?.openvino_genai || null,
                devices: (environment.devices || advisorData()?.hardware?.devices || []).map(item => ({
                    device: item.device || item.base || null,
                    driver_version: item.driver_version || null,
                })),
            },
            methodology: run.methodology || null,
            results: (run.results || []).map(row => ({
                model_id: row.model_id,
                precision: row.weight_format,
                requested_device: row.requested_device,
                actual_device: row.actual_device,
                success: row.success,
                generation_tokens_sec: row.decode_tokens_sec,
                legacy_total_tokens_sec: row.tokens_sec,
                time_to_first_token_ms: row.time_to_first_token_ms,
                total_latency_ms: row.total_latency_ms,
                load_time_ms: row.load_time_ms,
                peak_process_ram_mb: row.peak_process_ram_mb,
                completion_tokens: row.completion_tokens,
                prompt_tokens: row.prompt_tokens,
                measured_runs: row.runs,
                warmup_runs: row.warmup_runs || 0,
                stability: row.stability || null,
                statistics: row.statistics || null,
                samples: row.samples || [],
                error: row.success ? null : row.error,
            })),
        };
    }

    function benchmarkMarkdown(run) {
        const exported = safeBenchmarkExport(run);
        const system = exported.system;
        const lines = [
            '# InferBridge Benchmark',
            '',
            `Mode: ${exported.run.mock ? 'Synthetic mock / not hardware evidence' : 'Real local hardware'}`,
            `Preset: ${exported.run.preset} · ${exported.run.measured_runs} measured run(s) · ${exported.run.warmup_runs} warm-up run(s)`,
            '',
            '## System',
            '',
            `- CPU: ${system.cpu || 'Unavailable'}`,
            `- RAM: ${system.ram_gb ? `${system.ram_gb} GB` : 'Unavailable'}`,
            `- InferBridge: ${system.inferbridge || 'Unavailable'}`,
            `- OpenVINO: ${system.openvino || 'Unavailable'}`,
            `- OpenVINO GenAI: ${system.openvino_genai || 'Unavailable'}`,
        ];
        system.devices.forEach(device => lines.push(`- ${device.device || 'Device'} driver: ${device.driver_version || 'Unavailable'}`));
        lines.push('', '## Results', '');
        exported.results.forEach(row => {
            lines.push(`- ${row.model_id} · ${row.requested_device} → ${row.actual_device || 'unavailable'}`);
            if (!row.success) {
                lines.push(`  - Status: Failed — ${row.error || 'Benchmark failed'}`);
                return;
            }
            lines.push(`  - Generation: ${row.generation_tokens_sec ? `${Number(row.generation_tokens_sec).toFixed(2)} tok/s` : 'Unavailable'}`);
            lines.push(`  - TTFT: ${row.time_to_first_token_ms != null ? `${Number(row.time_to_first_token_ms).toFixed(1)} ms` : 'Unavailable'}`);
            lines.push(`  - Load: ${row.load_time_ms != null ? `${(Number(row.load_time_ms) / 1000).toFixed(2)} s` : 'Unavailable'}`);
            lines.push(`  - Peak process RAM: ${row.peak_process_ram_mb != null ? `${Number(row.peak_process_ram_mb).toFixed(0)} MB` : 'Unavailable'}`);
            lines.push(`  - Median of ${row.measured_runs} measured run(s); ${row.warmup_runs} warm-up run(s) excluded`);
        });
        lines.push('', '_Results stay local unless you deliberately copy or export this summary._');
        return lines.join('\n');
    }

    async function copyBenchmarkResults() {
        if (!benchmarkRun) return;
        const text = benchmarkMarkdown(benchmarkRun);
        try {
            await navigator.clipboard.writeText(text);
            toast('Benchmark results copied as privacy-safe Markdown.');
        } catch {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
            toast('Benchmark results copied.');
        }
    }

    function downloadBenchmarkJson() {
        if (!benchmarkRun) return;
        const safe = safeBenchmarkExport(benchmarkRun);
        const blob = new Blob([JSON.stringify(safe, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `inferbridge-benchmark-${String(benchmarkRun.run_id || 'result').replace(/[^a-z0-9-]/gi, '-')}.json`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
    }
"""
