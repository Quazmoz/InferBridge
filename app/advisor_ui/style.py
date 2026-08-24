"""Hardware Advisor and Benchmark Lab CSS."""

ADVISOR_STYLE = r"""
        #advisor-open-btn { width:36px;height:36px;flex:0 0 36px;display:grid;place-items:center;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);color:var(--text-2);cursor:pointer;transition:.2s ease; }
        #advisor-open-btn:hover { color:var(--text-1);border-color:var(--primary);background:var(--surface-3); }
        #advisor-open-btn.has-warning { color:var(--amber);border-color:color-mix(in srgb,var(--amber) 58%,var(--border)); }
        #advisor-open-btn.auto-active { color:var(--green);border-color:color-mix(in srgb,var(--green) 58%,var(--border));box-shadow:0 0 0 3px var(--green-glow); }
        #advisor-overlay { position:fixed;inset:0;z-index:1200;display:none;align-items:center;justify-content:center;padding:20px;background:rgba(2,6,14,.72);backdrop-filter:blur(9px); }
        #advisor-overlay.visible { display:flex; }
        #advisor-dialog { width:min(1180px,100%);max-height:min(920px,calc(100vh - 40px));overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr);background:var(--surface-1);border:1px solid var(--border);border-radius:18px;box-shadow:var(--shadow-md); }
        .advisor-header { display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid var(--border); }
        .advisor-title { display:flex;align-items:center;gap:12px;min-width:0; }
        .advisor-title-icon { width:38px;height:38px;display:grid;place-items:center;border-radius:11px;color:white;background:var(--accent-grad);box-shadow:0 0 18px var(--primary-glow); }
        .advisor-title h2 { margin:0;font-size:17px;letter-spacing:-.3px; }
        .advisor-title p { margin:3px 0 0;color:var(--text-3);font-size:11px; }
        #advisor-close-btn { width:34px;height:34px;border-radius:9px;border:1px solid var(--border);background:var(--surface-2);color:var(--text-2);cursor:pointer;font-size:20px; }
        #advisor-body { overflow:auto;padding:0 20px 24px; }
        .advisor-view-tabs { position:sticky;top:0;z-index:5;display:flex;gap:4px;margin:0 -20px 16px;padding:10px 20px;border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--surface-1) 94%,transparent);backdrop-filter:blur(10px); }
        .advisor-view-tab { border:0;border-radius:9px;background:transparent;color:var(--text-3);padding:8px 12px;font:inherit;font-size:12px;font-weight:700;cursor:pointer; }
        .advisor-view-tab.active { color:var(--text-1);background:var(--surface-3);box-shadow:inset 0 0 0 1px var(--border); }
        .advisor-view-tab:focus-visible,.advisor-profile-btn:focus-visible,.advisor-primary:focus-visible,.advisor-secondary:focus-visible,.benchmark-model-choice input:focus-visible + .benchmark-model-copy,.benchmark-device-choice input:focus-visible + span,.benchmark-preset input:focus-visible + span { outline:2px solid var(--primary);outline-offset:2px; }
        .advisor-profile-row { display:flex;gap:7px;overflow-x:auto;padding-bottom:4px;margin-bottom:16px; }
        .advisor-profile-btn { flex:0 0 auto;border:1px solid var(--border);border-radius:999px;background:var(--surface-2);color:var(--text-2);padding:7px 11px;font-size:12px;font-weight:600;cursor:pointer; }
        .advisor-profile-btn.active { color:white;border-color:transparent;background:var(--accent-grad);box-shadow:0 0 0 3px var(--primary-glow); }
        .advisor-grid { display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:14px; }
        .advisor-card { border:1px solid var(--border);border-radius:14px;background:var(--surface-2);padding:15px;min-width:0; }
        .advisor-card h3 { margin:0 0 11px;font-size:12px;color:var(--text-2);text-transform:uppercase;letter-spacing:.08em; }
        .advisor-card p { color:var(--text-3);font-size:11px;line-height:1.5; }
        .advisor-recommendation { position:relative;overflow:hidden;background:linear-gradient(145deg,color-mix(in srgb,var(--surface-2) 82%,var(--primary) 18%),var(--surface-2)); }
        .advisor-recommendation::after { content:'';position:absolute;width:180px;height:180px;right:-90px;top:-90px;border-radius:50%;background:var(--primary-glow);filter:blur(8px);pointer-events:none; }
        .advisor-model-name { position:relative;z-index:1;font-size:20px;font-weight:720;letter-spacing:-.45px;margin-bottom:5px; }
        .advisor-reason { position:relative;z-index:1;color:var(--text-2);font-size:12px;line-height:1.55;margin-bottom:13px; }
        .advisor-measured { position:relative;z-index:1;margin:0 0 13px;padding:10px;border:1px solid color-mix(in srgb,var(--green) 35%,var(--border));border-radius:11px;background:color-mix(in srgb,var(--green-glow) 55%,var(--surface-1)); }
        .advisor-measured-label { color:var(--green);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin-bottom:7px; }
        .advisor-measured-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px; }
        .advisor-measured-grid div { min-width:0; }
        .advisor-measured-grid strong { display:block;color:var(--text-1);font-size:12px;overflow-wrap:anywhere; }
        .advisor-measured-grid span { display:block;margin-top:2px;color:var(--text-3);font-size:9px; }
        .advisor-pills { position:relative;z-index:1;display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px; }
        .advisor-pill { border:1px solid var(--border);border-radius:999px;background:color-mix(in srgb,var(--surface-1) 75%,transparent);padding:5px 8px;color:var(--text-2);font-size:11px;font-variant-numeric:tabular-nums; }
        .advisor-actions { position:relative;z-index:1;display:flex;flex-wrap:wrap;gap:8px; }
        .advisor-primary,.advisor-secondary { border-radius:9px;padding:8px 12px;font-size:12px;font-weight:650;cursor:pointer; }
        .advisor-primary { border:0;color:white;background:var(--accent-grad); }
        .advisor-secondary { border:1px solid var(--border);color:var(--text-2);background:var(--surface-1); }
        .advisor-primary:hover:not(:disabled),.advisor-secondary:hover:not(:disabled) { filter:brightness(1.08); }
        .advisor-primary:disabled,.advisor-secondary:disabled { opacity:.45;cursor:not-allowed; }
        .advisor-hardware-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px; }
        .advisor-hw-item { border:1px solid var(--border);border-radius:10px;background:var(--surface-1);padding:10px;min-width:0; }
        .advisor-hw-label { color:var(--text-3);font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px; }
        .advisor-hw-value { color:var(--text-1);font-size:12px;font-weight:650;overflow-wrap:anywhere; }
        .advisor-hw-sub { color:var(--text-3);font-size:10px;line-height:1.4;margin-top:3px; }
        .advisor-notice { margin-top:13px;border:1px solid var(--border);border-radius:10px;padding:9px 10px;color:var(--text-3);background:var(--surface-1);font-size:10px;line-height:1.5;overflow-wrap:anywhere; }
        .advisor-models { margin-top:14px; }
        .advisor-table-wrap { overflow:auto;border:1px solid var(--border);border-radius:12px; }
        .advisor-table { width:100%;border-collapse:collapse;min-width:760px;font-size:11px; }
        .advisor-table th { position:sticky;top:0;z-index:1;text-align:left;color:var(--text-3);background:var(--surface-2);padding:9px 10px;border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:.06em;font-size:9px; }
        .advisor-table td { padding:9px 10px;border-bottom:1px solid var(--border);color:var(--text-2);vertical-align:top; }
        .advisor-table tr:last-child td { border-bottom:0; }
        .advisor-table strong { color:var(--text-1);font-size:11px; }
        .advisor-status { display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:650;text-transform:capitalize; }
        .advisor-status.compatible { color:var(--green);background:var(--green-glow); }
        .advisor-status.caution { color:var(--amber);background:var(--amber-glow); }
        .advisor-status.blocked { color:var(--red);background:rgba(239,68,68,.13); }
        .advisor-warning-list { margin:5px 0 0;padding-left:14px;color:var(--text-3);line-height:1.45; }
        .advisor-warning-list li + li { margin-top:3px; }
        .advisor-empty { padding:30px 16px;text-align:center;color:var(--text-3);font-size:12px; }
        .advisor-spinner { width:20px;height:20px;margin:18px auto;border-radius:50%;border:2px solid var(--border);border-top-color:var(--primary);animation:advisor-spin .8s linear infinite; }
        .lab-sr-only { position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important; }

        .benchmark-lab { display:grid;gap:14px; }
        .benchmark-intro { display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:3px 1px 0; }
        .benchmark-intro h3 { margin:0 0 4px;font-size:16px;letter-spacing:-.25px; }
        .benchmark-intro p { margin:0;color:var(--text-3);font-size:11px;line-height:1.5; }
        .benchmark-local-badge { flex:0 0 auto;border:1px solid color-mix(in srgb,var(--green) 35%,var(--border));border-radius:999px;padding:5px 8px;color:var(--green);background:var(--green-glow);font-size:10px;font-weight:750; }
        .benchmark-config { padding:0;overflow:hidden; }
        .benchmark-config-section { padding:15px; }
        .benchmark-config-section + .benchmark-config-section { border-top:1px solid var(--border); }
        .benchmark-section-head,.benchmark-results-head,.benchmark-history-head { display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px; }
        .benchmark-section-head h3,.benchmark-results-head h3,.benchmark-history-head h3 { margin:0 0 3px; }
        .benchmark-section-head p,.benchmark-results-head p,.benchmark-history-head p { margin:0; }
        .benchmark-section-head > span { color:var(--text-3);font-size:10px;white-space:nowrap; }
        .benchmark-search { display:block;margin-bottom:8px; }
        .benchmark-search input,.benchmark-advanced input,.benchmark-advanced textarea { width:100%;border:1px solid var(--border);border-radius:9px;background:var(--surface-1);color:var(--text-1);padding:8px 9px;font:inherit;font-size:11px;outline:none; }
        .benchmark-search input:focus,.benchmark-advanced input:focus,.benchmark-advanced textarea:focus { border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-glow); }
        .benchmark-model-list { max-height:250px;overflow:auto;border:1px solid var(--border);border-radius:11px;background:var(--surface-1); }
        .benchmark-model-row { display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px;border-bottom:1px solid var(--border); }
        .benchmark-model-row:last-child { border-bottom:0; }
        .benchmark-model-row.needs-preparation { opacity:.82; }
        .benchmark-model-choice { display:flex;align-items:center;gap:9px;min-width:0;cursor:pointer; }
        .benchmark-model-choice input { flex:0 0 auto;accent-color:var(--primary); }
        .benchmark-model-disabled { cursor:default;padding-left:22px; }
        .benchmark-model-copy { min-width:0; }
        .benchmark-model-copy strong { display:block;color:var(--text-1);font-size:11px; }
        .benchmark-model-copy span { display:block;color:var(--text-3);font-size:9px;margin-top:2px;overflow-wrap:anywhere; }
        .benchmark-model-state { display:flex;align-items:center;gap:7px;flex:0 0 auto; }
        .benchmark-state,.benchmark-row-status,.benchmark-stability { display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;font-size:9px;font-weight:750; }
        .benchmark-state.loaded,.benchmark-row-status.measured,.benchmark-stability.stable { color:var(--green);background:var(--green-glow); }
        .benchmark-state.prepared,.benchmark-row-status.synthetic { color:#60a5fa;background:rgba(96,165,250,.13); }
        .benchmark-state.unprepared,.benchmark-stability.moderate { color:var(--amber);background:var(--amber-glow); }
        .benchmark-row-status.failed,.benchmark-stability.variable { color:var(--red);background:rgba(239,68,68,.13); }
        .benchmark-prepare-btn { padding:5px 8px;font-size:10px; }
        .benchmark-device-grid { display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px; }
        .benchmark-device-choice,.benchmark-preset { position:relative;cursor:pointer; }
        .benchmark-device-choice input,.benchmark-preset input { position:absolute;opacity:0;pointer-events:none; }
        .benchmark-device-choice > span,.benchmark-preset > span { display:block;height:100%;border:1px solid var(--border);border-radius:10px;background:var(--surface-1);padding:10px;transition:.15s ease; }
        .benchmark-device-choice input:checked + span,.benchmark-preset input:checked + span { border-color:var(--primary);background:color-mix(in srgb,var(--surface-1) 84%,var(--primary) 16%);box-shadow:0 0 0 2px var(--primary-glow); }
        .benchmark-device-choice strong,.benchmark-preset strong { display:block;color:var(--text-1);font-size:11px; }
        .benchmark-device-choice small,.benchmark-preset small { display:block;color:var(--text-3);font-size:9px;line-height:1.35;margin-top:3px; }
        .benchmark-presets { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px; }
        .benchmark-advanced { border-top:1px solid var(--border);padding:0 15px; }
        .benchmark-advanced summary { padding:12px 0;color:var(--text-2);font-size:11px;font-weight:700;cursor:pointer; }
        .benchmark-advanced-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding-bottom:10px; }
        .benchmark-advanced label > span { display:block;margin-bottom:4px;color:var(--text-3);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em; }
        .benchmark-wide { grid-column:1/-1; }
        .benchmark-inline-input { display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px; }
        .benchmark-advanced-note { margin:0 0 12px!important; }
        .benchmark-run-bar { display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 15px;border-top:1px solid var(--border);background:color-mix(in srgb,var(--surface-1) 55%,var(--surface-2)); }
        .benchmark-run-bar strong { display:block;font-size:11px; }
        .benchmark-run-bar span { display:block;margin-top:2px;color:var(--text-3);font-size:9px; }
        .benchmark-progress { margin:0 15px 15px;border:1px solid var(--border);border-radius:11px;padding:11px;background:var(--surface-1); }
        .benchmark-progress.has-error { border-color:rgba(239,68,68,.35); }
        .benchmark-progress-head { display:flex;justify-content:space-between;gap:10px;font-size:10px; }
        .benchmark-progress-head span { color:var(--text-3); }
        .benchmark-progress-track { height:6px;margin:8px 0;border-radius:999px;background:var(--surface-3);overflow:hidden; }
        .benchmark-progress-track span { display:block;height:100%;border-radius:inherit;background:var(--accent-grad);transition:width .2s ease; }
        .benchmark-progress-message { color:var(--text-2);font-size:10px;line-height:1.45;overflow-wrap:anywhere; }
        .benchmark-resource-note { margin-top:6px;color:var(--text-3);font-size:9px;line-height:1.4; }
        .benchmark-results-empty p { margin:0; }
        .benchmark-result-actions { display:flex;gap:7px;flex-wrap:wrap; }
        .benchmark-synthetic { display:flex;gap:8px;align-items:flex-start;margin-bottom:11px;padding:9px 10px;border:1px solid rgba(96,165,250,.28);border-radius:10px;background:rgba(96,165,250,.08); }
        .benchmark-synthetic strong { color:#60a5fa;font-size:10px;white-space:nowrap; }
        .benchmark-synthetic span { color:var(--text-2);font-size:10px;line-height:1.4; }
        .benchmark-hero-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:11px; }
        .benchmark-hero-card { border:1px solid var(--border);border-radius:11px;background:var(--surface-1);padding:11px;min-width:0; }
        .benchmark-hero-card > span { color:var(--text-3);font-size:9px;text-transform:uppercase;letter-spacing:.06em;font-weight:750; }
        .benchmark-hero-card > strong { display:block;margin-top:4px;color:var(--text-1);font-size:18px;font-variant-numeric:tabular-nums; }
        .benchmark-hero-card small { display:block;margin-top:4px;color:var(--text-3);font-size:9px;line-height:1.45;overflow-wrap:anywhere; }
        .benchmark-metric-explainer { margin-bottom:10px;padding:9px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface-1);color:var(--text-3);font-size:9px;line-height:1.5; }
        .benchmark-metric-explainer strong { color:var(--text-2); }
        .benchmark-table-wrap { overflow:auto;border:1px solid var(--border);border-radius:11px; }
        .benchmark-table { width:100%;min-width:1100px;border-collapse:collapse;font-size:10px;font-variant-numeric:tabular-nums; }
        .benchmark-table th { position:sticky;top:0;z-index:1;padding:8px;text-align:left;color:var(--text-3);background:var(--surface-2);border-bottom:1px solid var(--border);text-transform:uppercase;font-size:8px;letter-spacing:.05em; }
        .benchmark-table td { padding:9px 8px;color:var(--text-2);vertical-align:top;border-bottom:1px solid var(--border); }
        .benchmark-table tbody tr:last-child td { border-bottom:0; }
        .benchmark-table td > strong { color:var(--text-1); }
        .benchmark-speed { font-size:12px; }
        .benchmark-muted { color:var(--text-3); }
        .benchmark-error { color:#fca5a5;line-height:1.4; }
        .benchmark-delta { margin-top:4px;color:var(--text-3);font-size:8px;line-height:1.35; }
        .benchmark-history { padding:0;overflow:hidden; }
        .benchmark-history-head { margin:0;padding:15px;border-bottom:1px solid var(--border); }
        .benchmark-danger { color:#fca5a5;border-color:rgba(239,68,68,.28); }
        .benchmark-history-list { display:grid; }
        .benchmark-history-row { display:grid;grid-template-columns:minmax(0,1fr) minmax(200px,.65fr);gap:12px;align-items:center;width:100%;padding:10px 15px;border:0;border-bottom:1px solid var(--border);background:transparent;color:var(--text-2);text-align:left;cursor:pointer; }
        .benchmark-history-row:last-child { border-bottom:0; }
        .benchmark-history-row:hover,.benchmark-history-row.active { background:var(--surface-3); }
        .benchmark-history-row strong { display:block;color:var(--text-1);font-size:10px; }
        .benchmark-history-row small { display:block;margin-top:2px;color:var(--text-3);font-size:9px; }
        .benchmark-history-row > span:last-child { font-size:9px;text-align:right;line-height:1.4; }
        @keyframes advisor-spin { to { transform:rotate(360deg); } }
        @media (max-width:900px) { .benchmark-device-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .benchmark-hero-grid { grid-template-columns:1fr; } }
        @media (max-width:760px) { #advisor-overlay { padding:0;align-items:stretch; } #advisor-dialog { max-height:100vh;border-radius:0; } .advisor-grid { grid-template-columns:1fr; } .advisor-hardware-grid { grid-template-columns:1fr 1fr; } .benchmark-intro,.benchmark-run-bar,.benchmark-section-head,.benchmark-results-head,.benchmark-history-head { align-items:stretch;flex-direction:column; } .benchmark-presets { grid-template-columns:1fr; } .benchmark-history-row { grid-template-columns:1fr; } .benchmark-history-row > span:last-child { text-align:left; } }
        @media (max-width:460px) { .advisor-hardware-grid,.advisor-measured-grid,.benchmark-device-grid,.benchmark-advanced-grid { grid-template-columns:1fr; } .advisor-header,#advisor-body { padding-left:14px;padding-right:14px; } .advisor-view-tabs { margin-left:-14px;margin-right:-14px;padding-left:14px;padding-right:14px; } .benchmark-model-row { align-items:flex-start;flex-direction:column; } .benchmark-model-state { width:100%;justify-content:flex-end; } }
    """
