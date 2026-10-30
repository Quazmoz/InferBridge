"""Local Connection Hub for InferBridge's dependency-free browser UI."""

from __future__ import annotations

from app import ui_registry
from app.ui_registry import UiExtension

_EXTENSION_ID = "ovllm-connection-hub-extension"

CONNECTION_HUB_CSS = r"""
#connection-hub-entry{padding:12px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}
#connection-hub-entry h4{margin:0 0 5px;color:var(--text-1);font-size:11px}.ch-entry-copy{margin:0 0 9px;color:var(--text-3);font-size:10.5px;line-height:1.45}.ch-open-btn,.ch-btn{min-height:36px;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text-1);font:inherit;font-size:10px;font-weight:700;cursor:pointer}.ch-open-btn{width:100%}.ch-open-btn:hover,.ch-btn:hover:not(:disabled){border-color:var(--primary);background:var(--surface-3)}.ch-open-btn:focus-visible,.ch-btn:focus-visible,.ch-select:focus-visible,.ch-copy:focus-visible,.ch-details summary:focus-visible,.ch-lan summary:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.ch-btn.primary{border-color:transparent;background:var(--primary);color:white}.ch-btn:disabled{opacity:.5;cursor:not-allowed}
#connection-hub-modal .modal-card{width:min(900px,calc(100vw - 24px));max-height:min(880px,calc(100dvh - 24px));overflow:hidden}.ch-head p{margin-top:3px;color:var(--text-3);font-size:10px;text-transform:none;letter-spacing:0}.ch-body{min-height:0;overflow:auto;padding:16px 18px 20px;overscroll-behavior:contain}.ch-section{padding:13px;border:1px solid var(--border);border-radius:11px;background:color-mix(in srgb,var(--surface-2) 72%,transparent)}.ch-section+.ch-section{margin-top:10px}.ch-section h4{margin:0 0 9px;color:var(--text-1);font-size:11px}.ch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ch-field{min-width:0;padding:9px 10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-field-label{display:block;color:var(--text-3);font-size:8px;text-transform:uppercase;letter-spacing:.45px}.ch-field-row{display:flex;align-items:center;gap:7px;margin-top:4px}.ch-field-value{min-width:0;flex:1;color:var(--text-1);font-size:10px;line-height:1.4;overflow-wrap:anywhere}.ch-mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.ch-copy{flex:0 0 auto;min-width:31px;min-height:31px;padding:5px 7px;border:1px solid var(--border);border-radius:7px;background:var(--surface-2);color:var(--text-2);font:inherit;font-size:9px;cursor:pointer}.ch-copy:hover{border-color:var(--primary);color:var(--text-1)}.ch-model-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;align-items:center;margin-top:8px}.ch-select{min-width:0;width:100%;min-height:36px;padding:7px 9px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1);color:var(--text-1);font:inherit;font-size:10px}.ch-hint{margin-top:7px;color:var(--text-3);font-size:9px;line-height:1.45}.ch-runtime.unavailable{color:var(--amber)}
.ch-details{border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-details+.ch-details{margin-top:7px}.ch-details summary{padding:9px 10px;color:var(--text-2);font-size:10px;font-weight:700;cursor:pointer;user-select:none}.ch-details[open] summary{border-bottom:1px solid var(--border);color:var(--text-1)}.ch-code-wrap{position:relative;padding:10px}.ch-code{margin:0;padding:10px 44px 10px 10px;overflow:auto;border-radius:8px;background:var(--code-bg);color:var(--code-text);font:10px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre}.ch-code-wrap>.ch-copy{position:absolute;top:17px;right:17px}.ch-config{display:grid;gap:6px}.ch-config-row{display:grid;grid-template-columns:120px minmax(0,1fr) auto;gap:8px;align-items:center;padding:8px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1)}.ch-config-row>span:first-child{color:var(--text-3);font-size:9px}.ch-config-value{min-width:0;overflow-wrap:anywhere;color:var(--text-1);font:9.5px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace}
.ch-test-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:9px}.ch-test-head h4{margin:0}.ch-tests{display:grid;gap:6px}.ch-test-row{display:grid;grid-template-columns:minmax(145px,.8fr) 92px 74px minmax(0,1.6fr);gap:8px;align-items:center;padding:9px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-1)}.ch-test-name{color:var(--text-1);font-size:9.5px;font-weight:700}.ch-test-status{font-size:9px;font-weight:750}.ch-test-status.passed{color:var(--green)}.ch-test-status.failed{color:var(--red)}.ch-test-status.running{color:var(--primary)}.ch-test-status.skipped{color:var(--amber)}.ch-test-duration{color:var(--text-3);font-size:8.5px;font-variant-numeric:tabular-nums}.ch-test-detail{color:var(--text-2);font-size:9px;line-height:1.4;overflow-wrap:anywhere}.ch-message{min-height:18px;margin-top:8px;color:var(--text-2);font-size:9.5px;line-height:1.4}.ch-message.error{color:var(--red)}
.ch-lan{margin-top:10px}.ch-lan summary{color:var(--text-2);font-size:10px;font-weight:700;cursor:pointer}.ch-lan-body{margin-top:8px;padding:10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-1)}.ch-lan-title{font-size:10px;font-weight:750;color:var(--text-1)}.ch-lan-detail{margin-top:5px;color:var(--text-2);font-size:9px;line-height:1.5}.ch-lan-body.warn{border-color:color-mix(in srgb,var(--amber) 42%,var(--border))}.ch-lan-body.warn .ch-lan-title{color:var(--amber)}
.ch-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 18px calc(11px + env(safe-area-inset-bottom));border-top:1px solid var(--border);background:var(--surface-1)}.ch-footer-note{max-width:650px;color:var(--text-3);font-size:9px;line-height:1.4}.ch-copy-feedback{min-height:16px;color:var(--green);font-size:9px}
.ch-tools{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ch-tool{min-width:0;padding:11px;border:1px solid var(--border);border-radius:10px;background:var(--surface-1)}.ch-tool h5{margin:0 0 6px;color:var(--text-1);font-size:10.5px}.ch-badges{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:6px}.ch-badge{padding:2px 7px;border:1px solid var(--border);border-radius:999px;color:var(--text-2);font-size:8.5px;font-weight:700}.ch-badge.ready{color:var(--green)}.ch-badge.warn{color:var(--amber)}.ch-tool p{margin:0 0 6px;color:var(--text-2);font-size:9.5px;line-height:1.45}.ch-tool ol{margin:6px 0;padding-left:18px;color:var(--text-2);font-size:9.5px;line-height:1.5}.ch-tool .ch-config-row{grid-template-columns:96px minmax(0,1fr) auto}.ch-tool .ch-code-wrap{padding:6px 0}.ch-tool-actions{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:7px}.ch-tool-actions a{color:var(--primary);font-size:9.5px}.ch-tool-warn{color:var(--amber)}.ch-tool-actions a:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
@media(max-width:720px){.ch-tools{grid-template-columns:1fr}}
@media(max-width:720px){#connection-hub-modal .modal-card{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}.ch-body{padding:12px}.ch-grid{grid-template-columns:1fr}.ch-test-head{align-items:stretch;flex-direction:column}.ch-test-row{grid-template-columns:minmax(0,1fr) auto}.ch-test-duration{grid-column:2}.ch-test-detail{grid-column:1/-1}.ch-config-row{grid-template-columns:1fr auto}.ch-config-row>span:first-child{grid-column:1/-1}.ch-footer{align-items:stretch;flex-direction:column;padding:10px 12px calc(10px + env(safe-area-inset-bottom))}.ch-footer .ch-btn{min-height:42px}.ch-model-row{grid-template-columns:1fr}.ch-copy-model{width:100%}}
"""

TOOL_SETUP_JS = r"""
(() => {
'use strict';
// Pure "Connect a tool" builders, mirrored by the native ToolSetup.cs. Values
// come from Connection Hub metadata; snippets only ever carry the placeholder.
if (window.__ovllmToolSetup) return;
const MANAGED = 'InferBridge (local)';
const START = '# >>> InferBridge (local): managed by InferBridge, Undo in InferBridge restores your backup';
const END = '# <<< InferBridge (local)';
const clients = [
    {id:'continue', title:'Continue (VS Code \u00b7 JetBrains)', native:true, summary:'Adds one model named \u201cInferBridge (local)\u201d to Continue\u2019s config.yaml.', doc:'https://docs.continue.dev/reference', docLabel:'Continue config.yaml reference'},
    {id:'copilot-cli', title:'GitHub Copilot CLI', native:true, summary:'Sets the Copilot CLI bring-your-own-model environment variables.', doc:'https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models', docLabel:'GitHub Docs: Copilot CLI models'},
    {id:'vscode-copilot', title:'VS Code \u00b7 GitHub Copilot Chat', native:false, summary:'Add InferBridge as a Custom Endpoint in VS Code\u2019s Language Models editor.', doc:'https://code.visualstudio.com/docs/agent-customization/language-models', docLabel:'VS Code: AI language models'},
    {id:'copilot-app', title:'GitHub Copilot app', native:false, summary:'Add InferBridge as an OpenAI-compatible model provider (public preview).', doc:'https://docs.github.com/en/copilot/how-tos/github-copilot-app/use-byok-models', docLabel:'GitHub Docs: Copilot app models'},
    {id:'cline', title:'Cline', native:false, summary:'Choose the OpenAI Compatible API provider in Cline settings.', doc:'https://docs.cline.bot/provider-config/openai-compatible', docLabel:'Cline: OpenAI Compatible'},
    {id:'roo', title:'Roo Code / Zoo Code', native:false, summary:'Roo Code was discontinued on 15 May 2026; its community fork Zoo Code keeps the same OpenAI Compatible provider.', doc:'https://github.com/Zoo-Code-Org/Zoo-Code-Docs/blob/main/docs/providers/openai-compatible.md', docLabel:'Zoo Code: OpenAI Compatible'},
    {id:'generic', title:'Other apps (Open WebUI, n8n, scripts)', native:false, summary:'Any client with a custom OpenAI-compatible Base URL.', doc:'https://github.com/Quazmoz/InferBridge/blob/main/docs/INTEGRATIONS.md', docLabel:'InferBridge integrations guide'},
];
function values(metadata, modelId) {
    const model = (metadata?.models || []).find(item => item.id === modelId && item.loaded && item.generation_capable);
    if (!metadata?.base_url || !model) return null;
    const auth = Boolean(metadata.authentication?.required);
    const context = Number.isInteger(model.context_tokens) ? model.context_tokens : null;
    const output = Number.isInteger(model.max_output_tokens) && context && model.max_output_tokens < context ? model.max_output_tokens : null;
    return {base:metadata.base_url, model:model.id, auth, key:auth ? 'YOUR_INFERBRIDGE_API_KEY' : 'not-required', context, output, input:context ? context - (output || 0) : null};
}
function fields(id, v) {
    const rows = [['Base URL', v.base], ['Model ID', v.model], ['API key', v.key]];
    if (['cline','roo','generic'].includes(id) && v.context) {
        rows.push(['Context window', String(v.context)]);
        if (v.output) rows.push(['Max output tokens', String(v.output)]);
    }
    return rows;
}
function steps(id, v) {
    const key = v.auth ? 'Authentication is on: replace YOUR_INFERBRIDGE_API_KEY with your own key. InferBridge never writes it.' : 'Authentication is off, so the API key value is not-required.';
    return ({
        'continue': ['One-click setup with backup and Undo is in the InferBridge Windows app (Local API page). In the browser, copy the entry below.', 'Open Continue\u2019s config selector, select the gear next to Local Config to open config.yaml.', 'Paste the entry under models: and save. Continue reloads automatically.', key],
        'copilot-cli': ['One-click setup with Undo is in the InferBridge Windows app (Local API page). In the browser, copy the commands below.', 'Run them in PowerShell, then open a new terminal and run copilot.', v.auth ? 'In that terminal run $env:COPILOT_PROVIDER_API_KEY = "<your key>". InferBridge never stores it.' : 'Authentication is off, so no API key variable is needed.'],
        'vscode-copilot': ['In VS Code run Chat: Manage Language Models from the Command Palette.', 'Select Add Models \u2192 Custom Endpoint and enter a group name such as InferBridge.', 'Enter a display name and the API key value below, and choose the Chat Completions API type.', 'VS Code opens chatLanguageModels.json: paste the entry below and save.', 'Pick the model in the chat model picker. Restart VS Code if it does not appear.'],
        'copilot-app': ['Open the GitHub Copilot app settings and select Model providers.', 'Select Add provider and choose OpenAI-compatible (any OpenAI-compatible HTTP endpoint).', 'Paste the Base URL and API key below, then select Add provider.', 'Choose the model ID below in a new session.'],
        'cline': ['Open Cline settings and set API Provider to OpenAI Compatible.', 'Paste the Base URL, API key and Model ID below.', 'Under Model Configuration set Context Window and Max Output Tokens to the values below.', 'Use Verify or start a task to confirm the connection.'],
        'roo': ['Install Zoo Code (Roo Code no longer receives updates).', 'Open its settings and set API Provider to OpenAI Compatible.', 'Paste the Base URL, API key and Model ID below.', 'Under Model Configuration set Context Window and Max Output Tokens to the values below.'],
    })[id] || ['In the app choose an OpenAI-compatible or custom OpenAI provider.', 'Paste the Base URL, API key and Model ID below.', 'Use Test connection here to confirm the engine answers.'];
}
const q = value => JSON.stringify(value);
const ps = value => `'${String(value).replace(/'/g, "''")}'`;
function continueSnippet(v) {
    const lines = ['models:', `  ${START}`, `  - name: ${q(MANAGED)}`, '    provider: openai', `    model: ${q(v.model)}`, `    apiBase: ${q(v.base)}`, `    apiKey: ${q(v.key)}`, '    roles:', '      - chat', '      - edit', '      - apply'];
    if (v.context) { lines.push('    defaultCompletionOptions:', `      contextLength: ${v.context}`); if (v.output) lines.push(`      maxTokens: ${v.output}`); }
    lines.push(`  ${END}`);
    return lines.join('\n') + '\n';
}
function copilotEnv(v) {
    const plan = [['COPILOT_PROVIDER_BASE_URL', v.base], ['COPILOT_PROVIDER_TYPE', 'openai'], ['COPILOT_MODEL', v.model]];
    if (v.input) plan.push(['COPILOT_PROVIDER_MAX_PROMPT_TOKENS', String(v.input)]);
    if (v.output) plan.push(['COPILOT_PROVIDER_MAX_OUTPUT_TOKENS', String(v.output)]);
    return plan;
}
function copilotCli(v) {
    let text = '# Run in PowerShell, then open a new terminal.\n';
    for (const [name, value] of copilotEnv(v)) text += `[Environment]::SetEnvironmentVariable(${ps(name)}, ${ps(value)}, 'User')\n`;
    if (v.auth) text += "# In each terminal: $env:COPILOT_PROVIDER_API_KEY = 'YOUR_INFERBRIDGE_API_KEY'\n";
    return text;
}
function limits(model, v) { if (v.input) model.maxInputTokens = v.input; if (v.output) model.maxOutputTokens = v.output; return model; }
function vscodeCustomEndpoint(v) {
    const model = limits({id:v.model, name:`InferBridge \u00b7 ${v.model}`, url:`${v.base}/chat/completions`, toolCalling:true, vision:false}, v);
    return JSON.stringify([{name:'InferBridge', vendor:'customendpoint', apiKey:v.auth ? '${input:inferbridgeApiKey}' : 'not-required', apiType:'chat-completions', models:[model]}], null, 2) + '\n';
}
function vscodeDeprecated(v) {
    const model = limits({name:`InferBridge \u00b7 ${v.model}`, url:`${v.base}/chat/completions`, toolCalling:true, vision:false}, v);
    model.requiresAPIKey = v.auth;
    return '// Deprecated: github.copilot.chat.customOAIModels. Prefer Custom Endpoint.\n"github.copilot.chat.customOAIModels": ' + JSON.stringify({[v.model]: model}, null, 2) + '\n';
}
function snippets(id, v) {
    if (id === 'continue') return [['config.yaml entry', continueSnippet(v)]];
    if (id === 'copilot-cli') return [['PowerShell commands', copilotCli(v)]];
    if (id === 'vscode-copilot') return [['chatLanguageModels.json entry (Custom Endpoint)', vscodeCustomEndpoint(v)], ['Deprecated: settings.json customOAIModels (older VS Code only)', vscodeDeprecated(v)]];
    return [];
}
function agenticNote(context) {
    const n = Number.isInteger(context) ? context.toLocaleString('en-US') : '';
    if (!n) return {warning:true, text:'Context window unknown. Agentic coding tools need tool calling and a large context window; check the model before relying on agent mode.'};
    if (context < 32768) return {warning:true, text:`Small context window (${n} tokens). Agentic coding tools need tool calling and much larger context; expect truncated or failed agent tasks. Chat and short edits work best.`};
    if (context < 131072) return {warning:true, text:`Context window ${n} tokens. Fine for chat; agentic tools also need tool calling, and GitHub recommends at least 128k tokens for Copilot CLI.`};
    return {warning:false, text:`Context window ${n} tokens. Agentic tools also need a model that supports tool calling.`};
}
window.__ovllmToolSetup = Object.freeze({clients, values, fields, steps, snippets, copilotEnv, agenticNote});
})();
"""

CONNECTION_HUB_JS = r"""
(() => {
'use strict';
if (window.__ovllmConnectionHubInstalled) return;
window.__ovllmConnectionHubInstalled = true;
const META_PATH = '/internal/connection-hub';
const TEST_PATH = '/internal/connection-hub/self-test';
const TESTS = [
    ['models', 'Model listing'],
    ['non_streaming', 'Non-streaming generation'],
    ['streaming', 'Streaming generation'],
    ['cancellation', 'Cancellation'],
    ['authentication', 'Authentication'],
];
let metadata = null;
let selectedModel = '';
let returnFocus = null;
let activeController = null;
let running = false;
let toolCopies = [];

function browserApiKey() {
    const fieldValue = String(document.getElementById('settings-api-key')?.value || '').trim();
    if (fieldValue) return fieldValue;
    try { return String(localStorage.getItem('ovllm.apikey.v1') || '').trim(); } catch { return ''; }
}
function uiHeaders(json = false, includeAuth = false) {
    const key = includeAuth ? browserApiKey() : '';
    return {
        'X-OV-LLM-UI': '1',
        ...(key ? {Authorization: `Bearer ${key}`} : {}),
        ...(json ? {'Content-Type': 'application/json'} : {}),
    };
}
function safeText(value) { return String(value ?? ''); }
function apiError(body, status) {
    const detail = body?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail.message === 'string' && detail.message.trim()) return detail.message.trim();
    return `Connection Hub request failed (HTTP ${status}).`;
}
async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            ...uiHeaders(Boolean(options.body), path === TEST_PATH),
            ...(options.headers || {}),
        },
    });
    let body = null;
    try { body = await response.json(); } catch {}
    if (!response.ok) throw new Error(apiError(body, response.status));
    return body;
}

const apiKeyGroup = document.getElementById('settings-api-key')?.closest('.setting-group');
if (apiKeyGroup && !document.getElementById('connection-hub-entry')) {
    const entry = document.createElement('div');
    entry.className = 'setting-group';
    entry.id = 'connection-hub-entry';
    entry.innerHTML = '<h4>Local API</h4><p class="ch-entry-copy">Copy the active endpoint and model ID, then verify OpenAI-compatible connectivity.</p><button type="button" class="ch-open-btn" id="connection-hub-open">Open Connection Hub</button>';
    apiKeyGroup.insertAdjacentElement('afterend', entry);
}

const modal = document.createElement('div');
modal.className = 'modal-overlay hidden';
modal.id = 'connection-hub-modal';
modal.setAttribute('aria-hidden', 'true');
modal.innerHTML = `
<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="ch-title" aria-describedby="ch-description">
  <div class="modal-header"><div class="ch-head"><h3 id="ch-title">Local Connection Hub</h3><p id="ch-description">Endpoint, model, authentication, and live API checks</p></div><button type="button" class="close-btn" id="ch-close" aria-label="Close Connection Hub">&times;</button></div>
  <div class="ch-body">
    <section class="ch-section" aria-labelledby="ch-connection-heading">
      <h4 id="ch-connection-heading">Connection</h4>
      <div class="ch-grid">
        <div class="ch-field"><span class="ch-field-label">Base URL</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-base-url">Checking...</span><button type="button" class="ch-copy" data-copy-target="base" aria-label="Copy Base URL">Copy</button></div></div>
        <div class="ch-field"><span class="ch-field-label">Authentication</span><div class="ch-field-row"><span class="ch-field-value" id="ch-auth">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Listener</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-listener">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Runtime</span><div class="ch-field-row"><span class="ch-field-value ch-runtime" id="ch-runtime">Checking...</span></div></div>
        <div class="ch-field"><span class="ch-field-label">Loaded models</span><div class="ch-field-row"><span class="ch-field-value ch-mono" id="ch-loaded-models">Checking...</span></div></div>
      </div>
      <div class="ch-model-row"><select class="ch-select" id="ch-model-select" aria-label="Model ID to copy and self-test"><option value="">Checking loaded models...</option></select><button type="button" class="ch-btn ch-copy-model" id="ch-copy-model">Copy model ID</button></div>
      <p class="ch-hint" id="ch-model-hint">Only loaded generation-capable models can run generation self-tests.</p>
    </section>

    <section class="ch-section" aria-labelledby="ch-quick-heading">
      <h4 id="ch-quick-heading">Quick configuration</h4>
      <details class="ch-details"><summary>OpenAI Python SDK</summary><div class="ch-code-wrap"><pre class="ch-code" id="ch-python"></pre><button type="button" class="ch-copy" data-copy-target="python" aria-label="Copy OpenAI Python SDK example">Copy</button></div></details>
      <details class="ch-details"><summary>curl</summary><div class="ch-code-wrap"><pre class="ch-code" id="ch-curl"></pre><button type="button" class="ch-copy" data-copy-target="curl" aria-label="Copy curl example">Copy</button></div></details>
      <details class="ch-details" open><summary>Connection values</summary><div class="ch-code-wrap"><div class="ch-config">
        <div class="ch-config-row"><span>Base URL</span><span class="ch-config-value" id="ch-value-base">-</span><button type="button" class="ch-copy" data-copy-target="base" aria-label="Copy Base URL configuration value">Copy</button></div>
        <div class="ch-config-row"><span>API key</span><span class="ch-config-value" id="ch-value-key">-</span><button type="button" class="ch-copy" data-copy-target="key" aria-label="Copy API key placeholder">Copy</button></div>
        <div class="ch-config-row"><span>Model ID</span><span class="ch-config-value" id="ch-value-model">Select a loaded model</span><button type="button" class="ch-copy" data-copy-target="model" aria-label="Copy Model ID configuration value">Copy</button></div>
      </div></div></details>
    </section>

    <section class="ch-section" aria-labelledby="ch-tools-heading">
      <h4 id="ch-tools-heading">Connect a tool</h4>
      <p class="ch-hint" id="ch-tools-hint">Refresh and select a loaded chat model to get ready-to-use settings for coding assistants and apps.</p>
      <div class="ch-tools" id="ch-tools" role="list" aria-label="Tool setup cards"></div>
    </section>

    <section class="ch-section" aria-labelledby="ch-test-heading">
      <div class="ch-test-head"><h4 id="ch-test-heading">Connection self-test</h4><button type="button" class="ch-btn primary" id="ch-run-test">Run connection self-test</button></div>
      <div class="ch-tests" id="ch-tests"></div>
      <div class="ch-message" id="ch-message" role="status" aria-live="polite"></div>
    </section>

    <details class="ch-lan"><summary>Advanced LAN access</summary><div class="ch-lan-body" id="ch-lan-body"><div class="ch-lan-title" id="ch-lan-title">Checking listener...</div><div class="ch-lan-detail" id="ch-lan-detail"></div></div></details>
  </div>
  <div class="ch-footer"><div><div class="ch-footer-note">The Hub never returns the configured server secret. When authentication is enabled, the self-test proves access with the API key already entered in this browser.</div><div class="ch-copy-feedback" id="ch-copy-feedback" role="status" aria-live="polite"></div></div><button type="button" class="ch-btn primary" id="ch-done">Done</button></div>
</div>`;
document.body.appendChild(modal);

const $ = selector => modal.querySelector(selector);
const openButton = document.getElementById('connection-hub-open');
const modelSelect = $('#ch-model-select');
const runButton = $('#ch-run-test');
const message = $('#ch-message');
const copyFeedback = $('#ch-copy-feedback');

function statusLabel(status) {
    return ({not_run:'Not run', running:'Running', passed:'Passed', failed:'Failed', skipped:'Skipped'})[status] || safeText(status);
}
function renderTestRows(results = null) {
    const byId = new Map((results || []).map(item => [item.id, item]));
    $('#ch-tests').innerHTML = '';
    for (const [id, label] of TESTS) {
        const item = byId.get(id) || {id, label, status:'not_run', duration_ms:null, detail:'Not run yet.'};
        const row = document.createElement('div');
        row.className = 'ch-test-row';
        row.dataset.testId = id;
        const name = document.createElement('span'); name.className = 'ch-test-name'; name.textContent = label;
        const status = document.createElement('span'); status.className = `ch-test-status ${item.status}`; status.textContent = statusLabel(item.status); status.setAttribute('aria-label', `${label}: ${statusLabel(item.status)}`);
        const duration = document.createElement('span'); duration.className = 'ch-test-duration'; duration.textContent = Number.isFinite(item.duration_ms) ? `${item.duration_ms} ms` : '—';
        const detail = document.createElement('span'); detail.className = 'ch-test-detail'; detail.textContent = safeText(item.detail || '');
        row.append(name, status, duration, detail);
        $('#ch-tests').appendChild(row);
    }
}
function setAllRunning() {
    renderTestRows(TESTS.map(([id, label]) => ({id, label, status:'running', duration_ms:null, detail:'Running through the local API...'})));
}
function setMessage(text = '', isError = false) {
    message.textContent = safeText(text);
    message.className = `ch-message${isError ? ' error' : ''}`;
}
function setRunning(value) {
    running = Boolean(value);
    runButton.disabled = running;
    runButton.setAttribute('aria-busy', running ? 'true' : 'false');
    modelSelect.disabled = running || !usableModels().length;
    modal.querySelectorAll('[data-tool-test]').forEach(button => { button.disabled = running || !window.__ovllmToolSetup?.values(metadata, selectedModel); });
}
function usableModels() {
    return Array.isArray(metadata?.models) ? metadata.models.filter(item => item.loaded && item.generation_capable) : [];
}
function chooseModel() {
    const usable = usableModels();
    const current = document.getElementById('model-select')?.value || '';
    if (usable.some(item => item.id === selectedModel)) return selectedModel;
    if (usable.some(item => item.id === current)) return current;
    return usable.length === 1 ? usable[0].id : '';
}
function renderModelOptions() {
    selectedModel = chooseModel();
    modelSelect.replaceChildren();
    const usable = usableModels();
    if (!usable.length) {
        const option = new Option('No loaded generation model', '', true, true); option.disabled = true; modelSelect.add(option);
        modelSelect.disabled = true;
        $('#ch-model-hint').textContent = 'Load a chat model before running generation checks.';
    } else {
        modelSelect.disabled = running;
        if (usable.length > 1) modelSelect.add(new Option('Select a loaded model...', ''));
        usable.forEach(item => modelSelect.add(new Option(`${item.name} (${item.id})${item.busy ? ' · busy' : ''}`, item.id)));
        modelSelect.value = selectedModel;
        $('#ch-model-hint').textContent = usable.length > 1 && !selectedModel
            ? 'Multiple generation-capable models are loaded. Select the model ID to copy and test.'
            : 'The self-test never unloads, reloads, converts, or changes the selected model.';
    }
    $('#ch-copy-model').disabled = !selectedModel;
    updateSnippets();
}
function pythonSnippet() {
    const base = metadata?.base_url || 'http://127.0.0.1:8000/v1';
    const key = metadata?.authentication?.required ? 'YOUR_INFERBRIDGE_API_KEY' : 'not-required';
    const model = selectedModel || 'MODEL_ID';
    return `from openai import OpenAI\n\nclient = OpenAI(\n    base_url=${JSON.stringify(base)},\n    api_key=${JSON.stringify(key)},\n)\n\nresponse = client.chat.completions.create(\n    model=${JSON.stringify(model)},\n    messages=[{"role": "user", "content": "Hello"}],\n)\n\nprint(response.choices[0].message.content)`;
}
function curlSnippet() {
    const base = metadata?.base_url || 'http://127.0.0.1:8000/v1';
    const auth = metadata?.authentication?.required ? ' -H "Authorization: Bearer YOUR_INFERBRIDGE_API_KEY"' : '';
    return `curl.exe "${base}/models"${auth}`;
}
function copyValue(kind) {
    if (kind === 'base') return metadata?.base_url || '';
    if (kind === 'key') return metadata?.authentication?.api_key_placeholder || '';
    if (kind === 'model') return selectedModel;
    if (kind === 'python') return pythonSnippet();
    if (kind === 'curl') return curlSnippet();
    return '';
}
function renderTools() {
    const setup = window.__ovllmToolSetup;
    const container = $('#ch-tools');
    if (!setup || !container) return;
    const v = setup.values(metadata, selectedModel);
    toolCopies = [];
    container.replaceChildren();
    $('#ch-tools-hint').textContent = v
        ? `Ready. Settings use ${v.model} at ${v.base}. One-click Continue and Copilot CLI setup (with preview and Undo) is in the InferBridge Windows app.`
        : 'Not ready: the engine must be available and a loaded chat model selected above.';
    const text = (tag, value, className = '') => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
    const copyButton = (value, label) => {
        const index = toolCopies.push([value, label]) - 1;
        const button = text('button', 'Copy', 'ch-copy');
        button.type = 'button'; button.dataset.toolCopy = String(index); button.setAttribute('aria-label', `Copy ${label}`);
        return button;
    };
    setup.clients.forEach((client, position) => {
        const card = document.createElement('article');
        card.className = 'ch-tool'; card.setAttribute('role', 'listitem');
        const headingId = `ch-tool-${position}`;
        card.setAttribute('aria-labelledby', headingId);
        const heading = text('h5', client.title); heading.id = headingId; card.appendChild(heading);
        const badges = document.createElement('div'); badges.className = 'ch-badges';
        badges.append(text('span', v ? 'Ready' : 'Not ready', `ch-badge ${v ? 'ready' : 'warn'}`), text('span', client.native ? 'One-click in the Windows app' : 'Guided setup', 'ch-badge'));
        card.append(badges, text('p', client.summary));
        if (v) {
            if (client.id !== 'generic') { const note = setup.agenticNote(v.context); card.appendChild(text('p', note.text, note.warning ? 'ch-tool-warn' : '')); }
            const config = document.createElement('div'); config.className = 'ch-config';
            for (const [label, value] of setup.fields(client.id, v)) {
                const row = document.createElement('div'); row.className = 'ch-config-row';
                row.append(text('span', label), text('span', value, 'ch-config-value'), copyButton(value, `${label} for ${client.title}`));
                config.appendChild(row);
            }
            card.appendChild(config);
            const list = document.createElement('ol'); list.setAttribute('aria-label', `${client.title} setup steps`);
            setup.steps(client.id, v).forEach(step => list.appendChild(text('li', step)));
            card.appendChild(list);
            for (const [title, code] of setup.snippets(client.id, v)) {
                card.appendChild(text('p', title));
                const wrap = document.createElement('div'); wrap.className = 'ch-code-wrap';
                wrap.append(text('pre', code, 'ch-code'), copyButton(code, `${title} for ${client.title}`));
                card.appendChild(wrap);
            }
        }
        const actions = document.createElement('div'); actions.className = 'ch-tool-actions';
        const test = text('button', 'Test connection', 'ch-btn'); test.type = 'button'; test.dataset.toolTest = '1'; test.disabled = !v || running;
        test.setAttribute('aria-label', `Test connection for ${client.title}`);
        const link = text('a', client.docLabel); link.href = client.doc; link.target = '_blank'; link.rel = 'noopener noreferrer';
        actions.append(test, link);
        card.appendChild(actions);
        container.appendChild(card);
    });
}
function updateSnippets() {
    $('#ch-python').textContent = pythonSnippet();
    $('#ch-curl').textContent = curlSnippet();
    $('#ch-value-base').textContent = metadata?.base_url || '-';
    $('#ch-value-key').textContent = metadata?.authentication?.api_key_placeholder || '-';
    $('#ch-value-model').textContent = selectedModel || 'Select a loaded model';
    renderTools();
}
async function writeClipboard(text) {
    if (!text) throw new Error('Nothing is available to copy yet.');
    if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(text); return; } catch {}
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand?.('copy') === true;
    textarea.remove();
    if (!copied) throw new Error('Clipboard access was blocked. Select the visible value and copy it manually.');
}
async function copy(kind, label, explicit = null) {
    try {
        await writeClipboard(explicit ?? copyValue(kind));
        copyFeedback.textContent = `${label} copied.`;
    } catch (error) {
        copyFeedback.textContent = error instanceof Error ? error.message : 'Clipboard access failed.';
    }
}
function renderMetadata() {
    $('#ch-base-url').textContent = metadata?.base_url || 'Unavailable';
    $('#ch-auth').textContent = metadata?.authentication?.label || 'Unavailable';
    $('#ch-listener').textContent = metadata ? `${metadata.listen_host}:${metadata.port} · ${metadata.api_root}` : 'Unavailable';
    const runtime = $('#ch-runtime');
    const state = metadata?.runtime_state || 'unavailable';
    runtime.textContent = state === 'available' ? 'Available' : state === 'shutting_down' ? 'Shutting down' : 'Unavailable';
    runtime.className = `ch-field-value ch-runtime ${state === 'available' ? '' : 'unavailable'}`;
    const loaded = Array.isArray(metadata?.loaded_model_ids) ? metadata.loaded_model_ids : [];
    $('#ch-loaded-models').textContent = loaded.length ? loaded.join(', ') : 'None';
    const lan = metadata?.lan;
    $('#ch-lan-title').textContent = lan?.label || 'LAN state unavailable';
    $('#ch-lan-detail').textContent = lan ? `${lan.detail}${lan.url ? ` LAN Base URL: ${lan.url}` : ''}` : 'Refresh the Hub after the server is available.';
    $('#ch-lan-body').classList.toggle('warn', Boolean(lan?.enabled && lan?.security_attention));
    renderModelOptions();
}
async function loadMetadata(preserveMessage = false) {
    activeController?.abort();
    activeController = new AbortController();
    if (!preserveMessage) setMessage();
    copyFeedback.textContent = '';
    try {
        metadata = await api(META_PATH, {signal: activeController.signal});
        renderMetadata();
    } catch (error) {
        if (error?.name === 'AbortError') return;
        metadata = null; selectedModel = ''; renderMetadata();
        setMessage(error instanceof Error ? error.message : 'Connection metadata is unavailable.', true);
    }
}
async function runSelfTest() {
    if (running) return;
    if (metadata?.authentication?.required && !browserApiKey()) {
        setMessage('Authentication is enabled. Close the Hub, enter the InferBridge API key in Generation Settings, then run the self-test again.', true);
        return;
    }
    setRunning(true); setAllRunning(); setMessage('Testing the local API with synthetic input...');
    try {
        const payload = await api(TEST_PATH, {method:'POST', body:JSON.stringify({model_id:selectedModel || null})});
        renderTestRows(payload?.tests || []);
        setMessage('Self-test finished. Review each check independently.');
        await loadMetadata(true);
    } catch (error) {
        renderTestRows(TESTS.map(([id,label]) => ({id,label,status:'failed',duration_ms:null,detail:'The self-test coordinator did not return a result.'})));
        setMessage(error instanceof Error ? error.message : 'The self-test could not run.', true);
    } finally {
        setRunning(false);
    }
}
function focusables() {
    return Array.from(modal.querySelectorAll('button:not(:disabled),select:not(:disabled),summary,[href],input:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')).filter(item => item.offsetParent !== null);
}
async function openHub(source) {
    returnFocus = source || document.activeElement;
    modal.classList.remove('hidden'); modal.setAttribute('aria-hidden','false');
    document.getElementById('app')?.setAttribute('inert','');
    $('#ch-close').focus();
    await loadMetadata();
}
function closeHub() {
    modal.classList.add('hidden'); modal.setAttribute('aria-hidden','true');
    activeController?.abort();
    document.getElementById('app')?.removeAttribute('inert');
    returnFocus?.focus?.();
}

renderTestRows();
openButton?.addEventListener('click', event => void openHub(event.currentTarget));
$('#ch-close').addEventListener('click', closeHub); $('#ch-done').addEventListener('click', closeHub);
runButton.addEventListener('click', () => void runSelfTest());
modelSelect.addEventListener('change', () => { selectedModel = modelSelect.value; $('#ch-copy-model').disabled = !selectedModel; updateSnippets(); });
$('#ch-copy-model').addEventListener('click', () => void copy('model', 'Model ID'));
modal.addEventListener('click', event => {
    const toolCopy = event.target.closest('[data-tool-copy]');
    if (toolCopy) { const [value, label] = toolCopies[Number(toolCopy.dataset.toolCopy)] || ['', 'Value']; void copy(null, label, value); return; }
    if (event.target.closest('[data-tool-test]')) { void runSelfTest(); return; }
});
modal.addEventListener('click', event => { const button = event.target.closest('[data-copy-target]'); if (button) { const kind = button.dataset.copyTarget; const labels = {base:'Base URL',key:'API key placeholder',model:'Model ID',python:'Python example',curl:'curl example'}; void copy(kind, labels[kind] || 'Value'); return; } if (event.target === modal) closeHub(); });
modal.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); closeHub(); return; }
    if (event.key !== 'Tab') return;
    const items = focusables(); if (!items.length) return;
    const first = items[0]; const last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
})();
"""


EXTENSION = UiExtension(
    extension_id=_EXTENSION_ID,
    javascript=TOOL_SETUP_JS + CONNECTION_HUB_JS,
    css=CONNECTION_HUB_CSS,
    description="Local Connection Hub client connection examples.",
)


def install_connection_hub_ui_extension() -> None:
    """Register the Local Connection Hub."""

    ui_registry.register(EXTENSION)


__all__ = [
    "CONNECTION_HUB_CSS",
    "CONNECTION_HUB_JS",
    "TOOL_SETUP_JS",
    "install_connection_hub_ui_extension",
]
