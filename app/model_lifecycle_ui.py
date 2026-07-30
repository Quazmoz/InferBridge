"""Dependency-free model lifecycle status and memory guidance UI."""

MODEL_LIFECYCLE_EXTENSION_JS = r"""
(() => {
  if (document.getElementById('ovllm-model-lifecycle-style')) return;
  if (
    typeof availableModels === 'undefined' ||
    typeof requestModelLoad !== 'function' ||
    typeof requestModelConvert !== 'function'
  ) return;

  const modelSelectElement = document.getElementById('model-select');
  const modelSelectWrap = modelSelectElement?.closest('.model-select-wrap');
  const chatFormElement = document.getElementById('chat-form');
  if (!modelSelectElement || !modelSelectWrap) return;

  const MODEL_STATES = {
    loaded: {
      label: 'Loaded',
      icon: '●',
      color: '#86efac',
      background: '#10271d',
    },
    ready: {
      label: 'Converted and ready',
      icon: '●',
      color: '#7dd3fc',
      background: '#102332',
    },
    working: {
      label: 'Preparing',
      icon: '◐',
      color: '#c4b5fd',
      background: '#201b38',
    },
    unavailable: {
      label: 'Not converted',
      icon: '●',
      color: '#fcd34d',
      background: '#30240f',
    },
    cancelled: {
      label: 'Conversion cancelled',
      icon: '■',
      color: '#fdba74',
      background: '#302015',
    },
    error: {
      label: 'Needs attention',
      icon: '●',
      color: '#fca5a5',
      background: '#321719',
    },
  };

  const style = document.createElement('style');
  style.id = 'ovllm-model-lifecycle-style';
  style.textContent = `
    #model-select.ovllm-model-state-loaded{color:#86efac;border-color:rgba(34,197,94,.58);box-shadow:0 0 0 3px rgba(34,197,94,.12)}
    #model-select.ovllm-model-state-ready{color:#7dd3fc;border-color:rgba(14,165,233,.58);box-shadow:0 0 0 3px rgba(14,165,233,.12)}
    #model-select.ovllm-model-state-working{color:#c4b5fd;border-color:rgba(139,92,246,.58);box-shadow:0 0 0 3px rgba(139,92,246,.12)}
    #model-select.ovllm-model-state-unavailable{color:#fcd34d;border-color:rgba(245,158,11,.62);box-shadow:0 0 0 3px rgba(245,158,11,.12)}
    #model-select.ovllm-model-state-cancelled{color:#fdba74;border-color:rgba(249,115,22,.58);box-shadow:0 0 0 3px rgba(249,115,22,.12)}
    #model-select.ovllm-model-state-error{color:#fca5a5;border-color:rgba(239,68,68,.62);box-shadow:0 0 0 3px rgba(239,68,68,.12)}
    [data-theme="light"] #model-select.ovllm-model-state-loaded{color:#166534}
    [data-theme="light"] #model-select.ovllm-model-state-ready{color:#0369a1}
    [data-theme="light"] #model-select.ovllm-model-state-working{color:#6d28d9}
    [data-theme="light"] #model-select.ovllm-model-state-unavailable{color:#a16207}
    [data-theme="light"] #model-select.ovllm-model-state-cancelled{color:#c2410c}
    [data-theme="light"] #model-select.ovllm-model-state-error{color:#b91c1c}
    #ovllm-model-status-legend{display:none;position:absolute;top:calc(100% + 8px);left:0;z-index:40;width:min(350px,calc(100vw - 24px));padding:9px 10px;border:1px solid var(--border-hover);border-radius:var(--radius-sm);background:color-mix(in srgb,var(--surface-1) 96%,transparent);box-shadow:var(--shadow-md);font-size:11px;line-height:1.45;color:var(--text-2);pointer-events:none}
    .model-select-wrap:hover #ovllm-model-status-legend,.model-select-wrap:focus-within #ovllm-model-status-legend{display:flex;gap:10px;flex-wrap:wrap}
    #ovllm-model-status-legend span{white-space:nowrap;font-weight:600}
    #ovllm-model-status-legend .loaded{color:#86efac}
    #ovllm-model-status-legend .ready{color:#7dd3fc}
    #ovllm-model-status-legend .working{color:#c4b5fd}
    #ovllm-model-status-legend .unavailable{color:#fcd34d}
    #ovllm-model-status-legend .error{color:#fca5a5}
    #ovllm-multi-model-notice{position:fixed;top:70px;right:16px;z-index:9995;width:min(450px,calc(100vw - 32px));padding:14px 15px;border:1px solid rgba(245,158,11,.5);border-radius:12px;background:color-mix(in srgb,var(--surface-1) 94%,#f59e0b 6%);color:var(--text-1);box-shadow:var(--shadow-md);font:12px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif}
    #ovllm-multi-model-notice[hidden]{display:none!important}
    #ovllm-multi-model-notice strong{color:#fcd34d}
    #ovllm-multi-model-notice p{margin:5px 0 0;color:var(--text-2)}
    .ovllm-model-notice-title{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.4px}
    .ovllm-model-notice-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin-top:11px}
    .ovllm-model-notice-actions button,.ovllm-model-risk-actions button{border:1px solid var(--border-hover);border-radius:999px;padding:8px 12px;background:var(--surface-2);color:var(--text-1);font:600 12px system-ui;cursor:pointer}
    .ovllm-model-notice-actions button:hover,.ovllm-model-risk-actions button:hover{background:var(--surface-3)}
    .ovllm-model-notice-actions .recommended,.ovllm-model-risk-actions .recommended{border-color:rgba(14,165,233,.65);background:#075985;color:#fff}
    .ovllm-model-notice-actions button:disabled,.ovllm-model-risk-actions button:disabled{opacity:.55;cursor:not-allowed}
    #ovllm-model-risk-modal{position:fixed;inset:0;z-index:10020;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(2,6,23,.78);backdrop-filter:blur(8px)}
    #ovllm-model-risk-modal[hidden]{display:none!important}
    #ovllm-model-risk-card{width:min(560px,100%);max-height:90vh;overflow:auto;border:1px solid rgba(245,158,11,.48);border-radius:16px;background:var(--surface-1);color:var(--text-1);box-shadow:0 20px 70px rgba(0,0,0,.52);padding:22px;font:13px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif}
    #ovllm-model-risk-card h2{margin:0;font-size:19px;line-height:1.25}
    #ovllm-model-risk-card p{margin:10px 0;color:var(--text-2)}
    #ovllm-model-risk-card .warning{padding:11px 12px;border-radius:10px;background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.28);color:#fcd34d}
    #ovllm-model-risk-list{margin:10px 0 0;padding-left:20px;color:var(--text-1)}
    #ovllm-model-risk-list li+li{margin-top:4px}
    #ovllm-model-risk-error{margin-top:10px;color:#fca5a5;font-weight:600}
    #ovllm-model-risk-error:empty{display:none}
    .ovllm-model-risk-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin-top:18px}
    .ovllm-model-risk-actions .advanced{border-color:rgba(245,158,11,.5);color:#fcd34d}
    @media (max-width:700px){
      #ovllm-multi-model-notice{top:auto;right:12px;bottom:12px;left:12px;width:auto}
      .ovllm-model-risk-actions button,.ovllm-model-notice-actions button{flex:1 1 auto}
    }
    @media (forced-colors:active){
      #model-select[class*='ovllm-model-state-']{border:2px solid CanvasText;box-shadow:none}
      #ovllm-multi-model-notice,#ovllm-model-risk-card{border:2px solid CanvasText}
    }
  `;
  document.head.appendChild(style);

  const legend = document.createElement('div');
  legend.id = 'ovllm-model-status-legend';
  legend.setAttribute('role', 'note');
  legend.innerHTML = '<span class="loaded">● Loaded</span><span class="ready">● Converted</span><span class="working">◐ Preparing</span><span class="unavailable">● Not converted</span><span class="error">● Error</span>';
  modelSelectWrap.appendChild(legend);
  modelSelectElement.setAttribute('aria-describedby', legend.id);

  const notice = document.createElement('aside');
  notice.id = 'ovllm-multi-model-notice';
  notice.hidden = true;
  notice.setAttribute('role', 'status');
  notice.setAttribute('aria-live', 'polite');
  notice.innerHTML = `
    <div class="ovllm-model-notice-title"><span aria-hidden="true">⚠</span><span>Multiple-model memory advisory</span></div>
    <p id="ovllm-model-notice-copy"></p>
    <div class="ovllm-model-notice-actions">
      <button type="button" id="ovllm-model-notice-hide">Hide</button>
      <button type="button" id="ovllm-model-notice-unload" class="recommended">Unload other model</button>
    </div>
  `;
  document.body.appendChild(notice);

  const modal = document.createElement('div');
  modal.id = 'ovllm-model-risk-modal';
  modal.hidden = true;
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'ovllm-model-risk-title');
  modal.innerHTML = `
    <div id="ovllm-model-risk-card">
      <h2 id="ovllm-model-risk-title">Load another model?</h2>
      <p id="ovllm-model-risk-copy"></p>
      <div class="warning">Keeping multiple model pipelines loaded can increase system RAM and accelerator memory use, reduce generation speed, or make the app less responsive. High-memory systems may handle this, but capacity is not assumed.</div>
      <ul id="ovllm-model-risk-list"></ul>
      <div id="ovllm-model-risk-error" role="alert"></div>
      <div class="ovllm-model-risk-actions">
        <button type="button" id="ovllm-model-risk-cancel">Cancel</button>
        <button type="button" id="ovllm-model-risk-continue" class="advanced">Keep loaded and continue</button>
        <button type="button" id="ovllm-model-risk-unload" class="recommended">Unload others and continue</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const noticeCopy = notice.querySelector('#ovllm-model-notice-copy');
  const noticeHideButton = notice.querySelector('#ovllm-model-notice-hide');
  const noticeUnloadButton = notice.querySelector('#ovllm-model-notice-unload');
  const modalCard = modal.querySelector('#ovllm-model-risk-card');
  const modalCopy = modal.querySelector('#ovllm-model-risk-copy');
  const modalList = modal.querySelector('#ovllm-model-risk-list');
  const modalError = modal.querySelector('#ovllm-model-risk-error');
  const modalCancelButton = modal.querySelector('#ovllm-model-risk-cancel');
  const modalContinueButton = modal.querySelector('#ovllm-model-risk-continue');
  const modalUnloadButton = modal.querySelector('#ovllm-model-risk-unload');

  const modelState = (model) => {
    if (!model) return 'unavailable';
    if (model.is_loaded) return 'loaded';
    if (model.status === 'error') return 'error';
    if (model.status === 'cancelled') return 'cancelled';
    if (model.is_loading) return 'working';
    if (model.status === 'ready_to_load' || model.is_downloaded) return 'ready';
    return 'unavailable';
  };

  const loadedPeersFor = (modelId) => Array.from(availableModels.values())
    .filter((model) => model.is_loaded && model.id !== modelId);

  const cleanOptionLabel = (label) => String(label || '').replace(/^[●◐■]\s+/, '');

  let dismissedForModelId = '';
  let previousSelectedModelId = modelSelectElement.value;
  let pendingDecision = null;
  let guardBypass = false;
  let lastFocusedElement = null;

  const applyModelStatusStyles = () => {
    Array.from(modelSelectElement.options).forEach((option) => {
      const model = availableModels.get(option.value);
      if (!model) return;
      const stateName = modelState(model);
      const state = MODEL_STATES[stateName];
      option.dataset.modelState = stateName;
      option.textContent = `${state.icon} ${cleanOptionLabel(option.textContent)}`;
      option.style.color = state.color;
      option.style.backgroundColor = state.background;
      option.title = `${state.label}. ${model.description || model.status_label || ''}`.trim();
    });

    const selectedModel = availableModels.get(modelSelectElement.value);
    const selectedStateName = modelState(selectedModel);
    const selectedState = MODEL_STATES[selectedStateName];
    Array.from(modelSelectElement.classList)
      .filter((name) => name.startsWith('ovllm-model-state-'))
      .forEach((name) => modelSelectElement.classList.remove(name));
    modelSelectElement.classList.add(`ovllm-model-state-${selectedStateName}`);
    if (selectedModel) {
      modelSelectElement.setAttribute(
        'aria-label',
        `Model: ${selectedModel.name}. Status: ${selectedState.label}.`,
      );
    }
    updateMemoryNotice();
  };

  const updateMemoryNotice = () => {
    const selectedModel = availableModels.get(modelSelectElement.value);
    if (!selectedModel || selectedModel.is_loaded) {
      notice.hidden = true;
      return;
    }
    const peers = loadedPeersFor(selectedModel.id);
    if (!peers.length || dismissedForModelId === selectedModel.id) {
      notice.hidden = true;
      return;
    }
    const names = peers.map((model) => model.name).join(', ');
    const countLabel = peers.length === 1 ? 'One model is' : `${peers.length} models are`;
    noticeCopy.innerHTML = `<strong>${countLabel} already loaded:</strong> ${escapeHtml(names)}. Loading ${escapeHtml(selectedModel.name)} too can reduce performance unless your hardware has enough memory for every active model.`;
    noticeUnloadButton.textContent = peers.length === 1
      ? 'Unload other model'
      : `Unload ${peers.length} other models`;
    notice.hidden = false;
  };

  const setDecisionBusy = (busy, errorMessage = '') => {
    modalCancelButton.disabled = busy;
    modalContinueButton.disabled = busy;
    modalUnloadButton.disabled = busy;
    modalError.textContent = busy ? '' : errorMessage;
  };

  const closeDecision = (result) => {
    const decision = pendingDecision;
    pendingDecision = null;
    modal.hidden = true;
    document.body.style.removeProperty('overflow');
    setDecisionBusy(false);
    if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
      lastFocusedElement.focus();
    }
    lastFocusedElement = null;
    if (decision) decision.resolve(result);
  };

  const openDecision = (targetModel, peers, originalAction, args, actionKind) =>
    new Promise((resolve) => {
      pendingDecision = { targetModel, peers, originalAction, args, actionKind, resolve };
      lastFocusedElement = document.activeElement;
      modalCopy.textContent = `${targetModel.name} will be ${
        actionKind === 'convert' ? 'converted and then loaded' : 'loaded'
      } while ${
        peers.length === 1 ? 'another model remains' : `${peers.length} other models remain`
      } in memory. Unloading the other ${
        peers.length === 1 ? 'model' : 'models'
      } first is recommended for most systems.`;
      modalList.replaceChildren(...peers.map((peer) => {
        const item = document.createElement('li');
        item.textContent = `${peer.name}${peer.device ? ` on ${peer.device}` : ''}`;
        return item;
      }));
      modalUnloadButton.textContent = peers.length === 1
        ? 'Unload other and continue'
        : `Unload ${peers.length} others and continue`;
      modal.hidden = false;
      document.body.style.overflow = 'hidden';
      setDecisionBusy(false);
      modalCancelButton.focus();
    });

  const parseResponseBody = async (response) => {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  };

  const unloadModels = async (peers) => {
    for (const peer of peers) {
      const response = await fetch('/v1/models/unload', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ model: peer.id }),
      });
      const data = await parseResponseBody(response);
      if (!response.ok) throw new Error(data.detail || `Could not unload ${peer.name}.`);
    }
    await updateStatus();
  };

  const runPendingDecision = async (unloadFirst) => {
    const decision = pendingDecision;
    if (!decision) return;
    setDecisionBusy(true);
    try {
      if (unloadFirst) {
        await unloadModels(decision.peers);
        showToast(
          decision.peers.length === 1 ? 'Other model unloaded' : 'Other models unloaded',
        );
      }
      guardBypass = true;
      const result = await decision.originalAction(...decision.args);
      closeDecision(result);
      applyModelStatusStyles();
    } catch (error) {
      setDecisionBusy(
        false,
        error && error.message ? error.message : 'Could not continue.',
      );
    } finally {
      guardBypass = false;
    }
  };

  const guardedAction = (originalAction, args, actionKind) => {
    if (guardBypass) return originalAction(...args);
    const modelId = args[0];
    const targetModel = availableModels.get(modelId);
    if (!targetModel || targetModel.is_loaded) return originalAction(...args);
    const peers = loadedPeersFor(modelId);
    if (!peers.length) return originalAction(...args);
    return openDecision(targetModel, peers, originalAction, args, actionKind);
  };

  const originalRequestModelLoad = requestModelLoad;
  const originalRequestModelConvert = requestModelConvert;
  requestModelLoad = function (...args) {
    return guardedAction(originalRequestModelLoad, args, 'load');
  };
  requestModelConvert = function (...args) {
    return guardedAction(originalRequestModelConvert, args, 'convert');
  };

  if (chatFormElement) {
    chatFormElement.addEventListener('submit', (event) => {
      if (guardBypass) return;
      const targetModel = availableModels.get(modelSelectElement.value);
      if (
        !targetModel ||
        targetModel.is_loaded ||
        !(targetModel.can_load || targetModel.can_convert)
      ) return;
      const peers = loadedPeersFor(targetModel.id);
      if (!peers.length) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      void openDecision(
        targetModel,
        peers,
        () => sendMessage(),
        [],
        targetModel.can_convert ? 'convert' : 'load',
      );
    }, true);
  }

  noticeHideButton.addEventListener('click', () => {
    dismissedForModelId = modelSelectElement.value;
    notice.hidden = true;
  });

  noticeUnloadButton.addEventListener('click', async () => {
    const selectedModel = availableModels.get(modelSelectElement.value);
    const peers = selectedModel ? loadedPeersFor(selectedModel.id) : [];
    if (!peers.length) {
      updateMemoryNotice();
      return;
    }
    noticeUnloadButton.disabled = true;
    noticeUnloadButton.textContent = 'Unloading…';
    try {
      await unloadModels(peers);
      showToast(peers.length === 1 ? 'Other model unloaded' : 'Other models unloaded');
      dismissedForModelId = '';
    } catch (error) {
      showToast(error && error.message ? error.message : 'Could not unload other models');
    } finally {
      noticeUnloadButton.disabled = false;
      applyModelStatusStyles();
    }
  });

  modalCancelButton.addEventListener('click', () => closeDecision(undefined));
  modalContinueButton.addEventListener('click', () => runPendingDecision(false));
  modalUnloadButton.addEventListener('click', () => runPendingDecision(true));
  modal.addEventListener('click', (event) => {
    if (
      event.target === modal &&
      pendingDecision &&
      !modalCancelButton.disabled
    ) closeDecision(undefined);
  });
  modalCard.addEventListener('click', (event) => event.stopPropagation());
  document.addEventListener('keydown', (event) => {
    if (
      event.key === 'Escape' &&
      !modal.hidden &&
      pendingDecision &&
      !modalCancelButton.disabled
    ) {
      event.preventDefault();
      closeDecision(undefined);
    }
  });

  modelSelectElement.addEventListener('change', () => {
    if (previousSelectedModelId !== modelSelectElement.value) {
      dismissedForModelId = '';
    }
    previousSelectedModelId = modelSelectElement.value;
    queueMicrotask(applyModelStatusStyles);
  });

  const optionObserver = new MutationObserver(() =>
    queueMicrotask(applyModelStatusStyles));
  optionObserver.observe(modelSelectElement, { childList: true });
  applyModelStatusStyles();
})();
"""

__all__ = ["MODEL_LIFECYCLE_EXTENSION_JS"]
