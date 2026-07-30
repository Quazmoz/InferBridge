"""Dependency-free About and Updates UI extension."""

from app.brand import DISPLAY_NAME

RELEASE_EXTENSION_JS = r"""
(() => {
  if (document.getElementById('ovllm-release-button')) return;
  const state = { status: null, result: null };
  const css = document.createElement('style');
  css.textContent = `
    #ovllm-release-button{position:fixed;left:16px;bottom:16px;z-index:9997;border:1px solid #526277;background:#111820;color:#f4f7fb;border-radius:999px;padding:9px 14px;font:600 13px system-ui;cursor:pointer}
    #ovllm-release-modal{position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.68);display:none;align-items:center;justify-content:center;padding:20px}
    #ovllm-release-card{width:min(620px,100%);max-height:88vh;overflow:auto;background:#111820;color:#f4f7fb;border:1px solid #526277;border-radius:16px;padding:22px;box-shadow:0 18px 60px rgba(0,0,0,.45);font:14px/1.45 system-ui}
    #ovllm-release-card h2{margin:0 0 6px;font-size:22px} #ovllm-release-card h3{margin:20px 0 8px;font-size:16px}
    #ovllm-release-card .muted{color:#b9c4d2} #ovllm-release-card .warning{padding:10px;border-radius:9px;background:#3b2e13;color:#ffe3a0}
    #ovllm-release-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px} #ovllm-release-actions button,#ovllm-release-actions a{border:1px solid #526277;border-radius:9px;padding:9px 12px;background:#1a2633;color:#fff;text-decoration:none;cursor:pointer}
    #ovllm-release-actions .primary{background:#2563eb;border-color:#2563eb} #ovllm-release-close{float:right;border:0;background:transparent;color:#fff;font-size:24px;cursor:pointer}
  `;
  document.head.appendChild(css);

  const button = document.createElement('button');
  button.id = 'ovllm-release-button';
  button.type = 'button';
  button.textContent = 'About & Updates';
  button.setAttribute('aria-haspopup', 'dialog');
  document.body.appendChild(button);

  const modal = document.createElement('div');
  modal.id = 'ovllm-release-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = '<div id="ovllm-release-card"><button id="ovllm-release-close" aria-label="Close">×</button><div id="ovllm-release-content">Loading…</div><div id="ovllm-release-actions"></div></div>';
  document.body.appendChild(modal);
  const content = modal.querySelector('#ovllm-release-content');
  const actions = modal.querySelector('#ovllm-release-actions');

  const el = (tag, text, className) => { const node = document.createElement(tag); if (text) node.textContent = text; if (className) node.className = className; return node; };
  const api = async (url, options={}) => {
    const response = await fetch(url, { ...options, headers: { 'Content-Type':'application/json', 'X-OV-LLM-UI':'1', ...(options.headers||{}) } });
    if (!response.ok) throw new Error('Update service returned ' + response.status);
    return response.json();
  };
  const saveSettings = async (settings) => {
    state.status.update_checks = await api('/desktop/release/settings', { method:'PUT', body:JSON.stringify(settings) });
    render();
  };
  const addAction = (label, handler, primary=false) => {
    const node = el('button', label, primary ? 'primary' : ''); node.type='button'; node.addEventListener('click', handler); actions.appendChild(node); return node;
  };
  const render = () => {
    content.replaceChildren(); actions.replaceChildren();
    const status = state.status; const result = state.result;
    if (!status) { content.textContent='Release information is unavailable.'; return; }
    const build = status.build;
    content.append(el('h2', 'OpenVINO Windows LLM'));
    content.append(el('div', `Version ${build.application_version} · ${build.build_channel} · ${status.installation_mode}`, 'muted'));
    content.append(el('div', `Build commit: ${build.source_commit}`, 'muted'));
    content.append(el('div', `Data schema: ${status.data_schema_version}`, 'muted'));
    content.append(el('h3', 'Updates'));
    content.append(el('p', status.update_checks.enabled ? `Channel: ${status.update_checks.channel}. Checks occur only when this local UI requests one.` : 'Update checks are disabled.'));
    if (result && result.status === 'available' && result.manifest) {
      const manifest = result.manifest;
      content.append(el('h3', `Version ${manifest.version} is available`));
      const published = new Date(manifest.published_at).toLocaleDateString();
      content.append(el('div', `Channel: ${manifest.channel} · Published: ${published}`, 'muted'));
      content.append(el('p', manifest.summary));
      if (manifest.highlights && manifest.highlights.length) {
        const list=el('ul'); manifest.highlights.forEach(item => list.append(el('li', item))); content.append(list);
      }
      if (result.compatibility_warning) content.append(el('p', result.compatibility_warning, 'warning'));
      const wanted = manifest.artifacts.find(a => a.type === result.selected_artifact_type);
      if (wanted) {
        content.append(el('p', `SHA-256: ${wanted.sha256}`, 'muted'));
        content.append(el('p', wanted.signed && wanted.signature_verified ? 'Signature: published metadata reports a verified installer signature. Confirm Authenticode after download.' : (wanted.contained_launcher_signed && wanted.contained_launcher_signature_verified ? 'Signature: the ZIP is unsigned. Published metadata reports a verified launcher signature; confirm it after extraction and verify the ZIP SHA-256 checksum.' : 'Signature: unsigned. Verify the published SHA-256 checksum before use.'));
        const link=el('a', wanted.type === 'installer' ? 'Download Installer' : 'Download Portable ZIP', 'primary'); link.href=wanted.url; link.target='_blank'; link.rel='noreferrer'; actions.append(link);
      }
      const notes=el('a','View Release Notes'); notes.href=manifest.release_notes_url; notes.target='_blank'; notes.rel='noreferrer'; actions.append(notes);
      addAction('Skip This Version', () => saveSettings({...status.update_checks, skipped_versions:[...new Set([...(status.update_checks.skipped_versions||[]), manifest.version])]}));
      addAction('Remind Me Later', () => { modal.style.display='none'; });
    } else if (result && result.status === 'offline') {
      content.append(el('p', 'The update check could not reach GitHub. Local inference remains available.', 'muted'));
    } else if (result && result.status === 'rejected') {
      content.append(el('p', result.compatibility_warning || 'The available release is not compatible with this installation.', 'warning'));
    } else if (result && result.status !== 'disabled') {
      content.append(el('p', 'This installation is current for the selected channel.', 'muted'));
    }
    if (status.update_checks.enabled) {
      addAction('Check Now', async () => { state.result = await api('/desktop/release/check', {method:'POST', body:'{}'}); render(); }, true);
      if (status.update_checks.channel === 'stable') {
        addAction('Opt In to Beta', () => saveSettings({...status.update_checks, channel:'beta'}));
      } else {
        addAction('Use Stable Channel', () => saveSettings({...status.update_checks, channel:'stable'}));
      }
      addAction('Disable Update Checks', () => saveSettings({...status.update_checks, enabled:false}));
    } else {
      addAction('Enable Stable Checks', () => saveSettings({...status.update_checks, enabled:true, channel:'stable'}), true);
    }
    addAction('Close', () => { modal.style.display='none'; });
  };
  const open = async () => {
    modal.style.display='flex'; content.textContent='Loading…'; actions.replaceChildren();
    try {
      state.status = await api('/desktop/release/status');
      render();
      if (state.status.update_checks.enabled && state.status.check_due) {
        state.result = await api('/desktop/release/check', {method:'POST', body:'{}'});
        render();
      }
    } catch (_) { content.textContent='Release information is unavailable. Local inference is unaffected.'; }
  };
  const checkOnOpen = async () => {
    try {
      state.status = await api('/desktop/release/status');
      if (!state.status.update_checks.enabled || !state.status.check_due) return;
      state.result = await api('/desktop/release/check', {method:'POST', body:'{}'});
      if (state.result.status === 'available' && state.result.latest_version) {
        button.textContent = `Update ${state.result.latest_version} Available`;
      }
    } catch (_) {
      // Update discovery never blocks or interrupts local inference.
    }
  };
  button.addEventListener('click', open);
  modal.querySelector('#ovllm-release-close').addEventListener('click', () => { modal.style.display='none'; });
  modal.addEventListener('click', event => { if (event.target === modal) modal.style.display='none'; });
  if (new URLSearchParams(location.search).get('updates') === '1') open();
  else setTimeout(checkOnOpen, 1200);
})();
"""

RELEASE_EXTENSION_JS = RELEASE_EXTENSION_JS.replace("OpenVINO Windows LLM", DISPLAY_NAME)
