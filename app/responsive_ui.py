"""Dependency-free browser resizing and compact-layout hardening."""

RESPONSIVE_EXTENSION_JS = r"""
(() => {
  if (document.getElementById('ovllm-responsive-style')) return;

  const root = document.documentElement;
  const app = document.getElementById('app');
  const header = app?.querySelector(':scope > header');
  const mainBody = document.querySelector('.main-body');
  const chatAreaElement = document.getElementById('chat-area');
  const inputAreaElement = document.getElementById('input-area');
  const chatsSidebarElement = document.getElementById('chats-sidebar');
  const settingsSidebarElement = document.getElementById('settings-sidebar');
  const chatsToggleElement = document.getElementById('chats-toggle-btn');
  const settingsToggleElement = document.getElementById('settings-toggle-btn');
  if (
    !app ||
    !header ||
    !mainBody ||
    !chatAreaElement ||
    !inputAreaElement ||
    !chatsSidebarElement ||
    !settingsSidebarElement
  ) return;

  const style = document.createElement('style');
  style.id = 'ovllm-responsive-style';
  style.textContent = `
    html,body{min-width:280px;min-height:0;overscroll-behavior:none}
    body{overflow:hidden}
    #app{width:100%;min-width:0;min-height:0;height:100vh;height:100dvh;max-height:var(--ovllm-viewport-height,100dvh);overflow:hidden}
    header,.header-left,.header-right,.logo,.model-select-wrap,.device-select-wrap,.main-body,.chat-column,#chat-area,#input-area,.chat-inner,.input-row,.footer-meta,.footer-right{min-width:0}
    header{max-width:100%;overflow:visible}
    .main-body{width:100%;isolation:isolate}
    .chat-column{width:0;max-width:100%}
    #chat-area{overscroll-behavior:contain}
    .chat-inner,.input-row,.footer-meta{width:min(100%,820px)}
    .bubble,.bubble pre,.bubble table,.tool-call-card,.tool-call-args{max-width:100%}
    .bubble pre,.tool-call-args{overflow-x:auto;overscroll-behavior-inline:contain}
    #chats-sidebar,#settings-sidebar{max-height:100%;overscroll-behavior:contain}
    .modal-overlay,#ovllm-model-risk-modal{padding:clamp(8px,2vw,20px)!important}
    .modal-card,#ovllm-model-risk-card{max-height:calc(var(--ovllm-viewport-height,100dvh) - 16px)!important}
    #ovllm-panel-scrim{position:absolute;inset:0;z-index:3;border:0;background:rgba(2,6,23,.56);backdrop-filter:blur(2px);cursor:pointer;opacity:1;transition:opacity .18s ease}
    #ovllm-panel-scrim[hidden]{display:none!important;opacity:0;pointer-events:none}

    @media (min-width:951px) and (max-width:1240px){
      header{flex-wrap:wrap;align-content:center}
      .header-left{flex:0 0 auto}
      .header-right{flex:1 1 680px;flex-wrap:wrap;justify-content:flex-end}
      .model-select-wrap{flex:1 1 260px}
      #model-select{width:100%;max-width:none}
      .device-select-wrap{flex:0 1 170px}
      #device-select,#device-select.has-advanced-value{width:100%;max-width:170px}
    }

    @media (max-width:950px){
      header{display:grid;grid-template-columns:minmax(0,1fr);align-items:stretch;padding:10px 12px;gap:8px}
      .header-left{width:100%;justify-content:space-between}
      .header-right{display:flex!important;flex-wrap:wrap;align-items:center;justify-content:flex-start;width:100%;gap:6px}
      .model-select-wrap{order:0;flex:1 0 100%}
      #model-select{width:100%;max-width:none}
      .device-select-wrap{order:1;flex:1 1 150px;max-width:260px}
      #device-select,#device-select.has-advanced-value{width:100%;max-width:none}
      #device-chip{order:2;flex:1 1 120px;max-width:220px;overflow:hidden}
      #device-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .btn-group{order:3;display:inline-flex!important;flex:0 0 auto}
      .header-right>.icon-btn{order:4;flex:0 0 auto}
      #chats-sidebar,#settings-sidebar{position:absolute;inset-block:0;width:min(340px,92vw);height:100%;box-shadow:var(--shadow-md)}
      #chats-sidebar{inset-inline:0 auto}
      #settings-sidebar{inset-inline:auto 0}
      #chats-sidebar.collapsed{margin-left:min(-340px,-92vw)}
      #settings-sidebar.closed{margin-right:min(-340px,-92vw)}
      .device-warning,.device-info{width:min(360px,calc(100vw - 24px));max-height:min(240px,45vh);overflow:auto}
    }

    @media (max-width:620px){
      header{padding:8px 10px;gap:7px}
      .logo{flex:1;overflow:hidden}
      .logo>div:last-child{min-width:0}
      .logo-text{display:block;overflow:hidden;text-overflow:ellipsis}
      .header-right{gap:6px}
      .device-select-wrap{flex:1 1 132px;max-width:none}
      #device-chip{flex:1 1 112px;max-width:none}
      .header-right>.icon-btn{flex:1 0 40px;max-width:52px}
      .btn-group{flex:0 0 auto}
      .chat-inner{padding-inline:10px}
      #input-area{padding-inline:10px}
      .footer-meta{gap:5px}
      .footer-right{width:100%;flex-wrap:wrap;gap:5px 10px}
      #model-status{flex:1 1 100%}
      #token-counter{margin-left:auto}
      .modal-card{width:min(100%,520px)}
      #ovllm-model-risk-card{width:100%!important;padding:18px!important}
      #ovllm-release-button{left:10px!important;bottom:calc(102px + env(safe-area-inset-bottom))!important;max-width:calc(100vw - 20px)}
    }

    @media (max-width:420px){
      .header-right>.icon-btn{max-width:none}
      .device-select-wrap,#device-chip{flex-basis:100%;max-width:none}
      .btn-group{flex:1 0 auto}
      .btn-group .icon-btn{flex:1 1 44px}
      .footer-right{align-items:flex-start}
      #token-counter{margin-left:0}
      .modal-header,.modal-form,.modal-panel{padding-inline:14px}
    }

    @media (max-height:720px){
      header{padding-block:7px;gap:6px}
      .logo-sub{display:none}
      #chat-area{padding-block:10px}
      #input-area{padding-top:8px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
      #user-input{max-height:min(110px,26vh)}
      .chat-inner{gap:16px}
      .empty-state{padding:24px 16px 16px;gap:10px}
      .empty-icon svg{width:42px;height:42px}
      .model-action-card{margin-top:10px;padding:16px;gap:10px}
      .footer-meta{margin-top:4px}
    }

    @media (max-height:540px){
      #chat-area{padding-block:6px}
      .chat-inner{gap:12px}
      .empty-state{padding-block:12px}
      .empty-state .suggestion-chips{display:none}
      .footer-meta>span:first-child{display:none}
      #user-input{max-height:min(88px,22vh)}
      #ovllm-release-button{display:none!important}
    }

    @media (prefers-reduced-motion:reduce){
      #ovllm-panel-scrim{transition:none}
    }

    @media (forced-colors:active){
      #ovllm-panel-scrim{background:Canvas;border:1px solid CanvasText;opacity:.72}
    }
  `;
  document.head.appendChild(style);

  const scrim = document.createElement('button');
  scrim.id = 'ovllm-panel-scrim';
  scrim.type = 'button';
  scrim.hidden = true;
  scrim.setAttribute('aria-label', 'Close open side panel');
  mainBody.insertBefore(scrim, mainBody.firstChild);

  const compactQuery = window.matchMedia('(max-width: 950px)');
  const narrowQuery = window.matchMedia('(max-width: 620px)');
  let wasCompact = compactQuery.matches;
  let desktopChatsOpen = localStorage.getItem('ovllm.chatlist.v1') !== 'closed';
  let desktopSettingsOpen = false;
  let resizeFrame = 0;

  const setChatsOpenDirect = (open) => {
    chatsSidebarElement.classList.toggle('collapsed', !open);
    chatsSidebarElement.setAttribute('aria-hidden', String(!open));
    chatsSidebarElement.inert = !open;
    chatsToggleElement?.classList.toggle('active', open);
    chatsToggleElement?.setAttribute('aria-expanded', String(open));
  };

  const setSettingsOpenDirect = (open) => {
    settingsSidebarElement.classList.toggle('closed', !open);
    settingsSidebarElement.setAttribute('aria-hidden', String(!open));
    settingsSidebarElement.inert = !open;
    settingsToggleElement?.classList.toggle('active', open);
    settingsToggleElement?.setAttribute('aria-expanded', String(open));
  };

  const syncScrim = () => {
    const compact = compactQuery.matches;
    const panelOpen = compact && (
      !chatsSidebarElement.classList.contains('collapsed') ||
      !settingsSidebarElement.classList.contains('closed')
    );
    scrim.hidden = !panelOpen;
    mainBody.classList.toggle('ovllm-panel-open', panelOpen);
  };

  const updateViewportMetrics = () => {
    const viewportHeight = Math.max(
      320,
      Math.round(window.visualViewport?.height || window.innerHeight || 0),
    );
    root.style.setProperty('--ovllm-viewport-height', `${viewportHeight}px`);
    root.dataset.ovllmViewport = narrowQuery.matches
      ? 'narrow'
      : compactQuery.matches
        ? 'compact'
        : 'wide';
    root.dataset.ovllmHeight = viewportHeight <= 540
      ? 'very-short'
      : viewportHeight <= 720
        ? 'short'
        : 'normal';
  };

  const applyResponsiveState = () => {
    const compact = compactQuery.matches;
    if (compact && !wasCompact) {
      desktopChatsOpen = !chatsSidebarElement.classList.contains('collapsed');
      desktopSettingsOpen = !settingsSidebarElement.classList.contains('closed');
      setChatsOpenDirect(false);
      setSettingsOpenDirect(false);
    } else if (!compact && wasCompact) {
      setChatsOpenDirect(desktopChatsOpen);
      setSettingsOpenDirect(desktopSettingsOpen);
    }
    wasCompact = compact;
    updateViewportMetrics();
    syncScrim();
    if (typeof autoResize === 'function') autoResize();
  };

  const scheduleResponsiveState = () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(applyResponsiveState);
  };

  scrim.addEventListener('click', () => {
    if (typeof setChatsSidebarCollapsed === 'function') setChatsSidebarCollapsed(true);
    else setChatsOpenDirect(false);
    if (typeof setSettingsSidebarOpen === 'function') setSettingsSidebarOpen(false, true);
    else setSettingsOpenDirect(false);
    syncScrim();
  });

  const panelObserver = new MutationObserver(() => {
    if (!compactQuery.matches) {
      desktopChatsOpen = !chatsSidebarElement.classList.contains('collapsed');
      desktopSettingsOpen = !settingsSidebarElement.classList.contains('closed');
    }
    syncScrim();
  });
  panelObserver.observe(chatsSidebarElement, { attributes: true, attributeFilter: ['class'] });
  panelObserver.observe(settingsSidebarElement, { attributes: true, attributeFilter: ['class'] });

  const headerObserver = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => {
        root.style.setProperty('--ovllm-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
      })
    : null;
  headerObserver?.observe(header);

  compactQuery.addEventListener?.('change', scheduleResponsiveState);
  narrowQuery.addEventListener?.('change', scheduleResponsiveState);
  window.addEventListener('resize', scheduleResponsiveState, { passive: true });
  window.visualViewport?.addEventListener('resize', scheduleResponsiveState, { passive: true });
  window.visualViewport?.addEventListener('scroll', scheduleResponsiveState, { passive: true });

  updateViewportMetrics();
  syncScrim();
  scheduleResponsiveState();
})();
"""

__all__ = ["RESPONSIVE_EXTENSION_JS"]
