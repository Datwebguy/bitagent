/* Symbol switching keeps a local selected symbol so stale WebSocket cycles
   cannot overwrite the user's current market after a reconnect. */

// ══════════════════════════════════════════════════════
// INTRO SCREEN — ambient canvas + skip logic
// ══════════════════════════════════════════════════════
const iCv  = document.getElementById('introBg');
const iCtx = iCv ? iCv.getContext('2d') : null;
let iW = 0, iH = 0;

function _resizeIntroCv() {
  iW = window.innerWidth;
  iH = window.innerHeight;
  if (iCv) { iCv.width = iW; iCv.height = iH; }
}
_resizeIntroCv();
window.addEventListener('resize', _resizeIntroCv);

const STREAM_COUNT = window.innerWidth < 600 ? 32 : 54;
const STREAMS = Array.from({length: STREAM_COUNT}, () => ({
  x:  Math.random() * window.innerWidth,
  y:  Math.random() * window.innerHeight,
  vx: 0.15 + Math.random() * 0.35,
  vy: (Math.random() - 0.5) * 0.08,
  r:  0.8 + Math.random() * 1.8,
  hue: Math.random() > 0.68 ? '158,169,255' : '18,206,174',
}));

function saveOperatorToken(inputId = 'operatorTokenInput') {
  ensureOperatorUI();
  const input = document.getElementById(inputId);
  _operatorToken = (input?.value || '').trim();
  try {
    if (_operatorToken) sessionStorage.setItem('bitagent_operator_token', _operatorToken);
    else sessionStorage.removeItem('bitagent_operator_token');
  } catch(e) {}
  updateOperatorUI();
  if (_operatorToken) hideOperatorUnlock();
  showMToast(_operatorToken ? 'Operator unlocked' : 'Operator token cleared');
}

function ensureOperatorUI() {
  if (!_operatorMode) return;
  if (!document.getElementById('operatorUnlockPop')) {
    const pop = document.createElement('div');
    pop.className = 'operator-unlock-pop';
    pop.id = 'operatorUnlockPop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'false');
    pop.setAttribute('aria-labelledby', 'operatorUnlockTitle');
    pop.innerHTML = `
      <div class="operator-unlock-head">
        <div class="operator-unlock-title" id="operatorUnlockTitle">Operator Unlock Required</div>
        <button class="operator-unlock-close" type="button" onclick="hideOperatorUnlock()" aria-label="Close operator unlock">×</button>
      </div>
      <div class="operator-card">
        <label class="connect-label" for="operatorTokenQuickInput">Operator Token</label>
        <div class="operator-row">
          <input class="connect-input" id="operatorTokenQuickInput" type="password"
            placeholder="Operator token" autocomplete="current-password">
          <button class="operator-btn" type="button" onclick="saveOperatorToken('operatorTokenQuickInput')">Unlock</button>
        </div>
        <div class="operator-hint" id="operatorUnlockHint">Switching assets changes the active agent market and requires operator approval.</div>
      </div>`;
    document.body.appendChild(pop);
  }
  if (!document.getElementById('operatorCard')) {
    const body = document.querySelector('#connectScreen .connect-body');
    if (!body) return;
    const card = document.createElement('div');
    card.className = 'operator-card';
    card.id = 'operatorCard';
    card.innerHTML = `
      <label class="connect-label" for="operatorTokenInput">Operator Unlock</label>
      <div class="operator-row">
        <input class="connect-input" id="operatorTokenInput" type="password"
          placeholder="Operator token" autocomplete="current-password">
        <button class="operator-btn" type="button" onclick="saveOperatorToken()">Unlock</button>
      </div>
      <div class="operator-hint">Internal operator mode only. Public users connect their own account without this token.</div>`;
    body.prepend(card);
  }
}

function updateOperatorUI() {
  ensureOperatorUI();
  const card = document.getElementById('operatorCard');
  const input = document.getElementById('operatorTokenInput');
  const quick = document.getElementById('operatorTokenQuickInput');
  if (card) card.classList.toggle('show', _operatorMode && _authRequired && !_operatorToken);
  if (input && _operatorToken) input.value = _operatorToken;
  if (quick && _operatorToken) quick.value = _operatorToken;
  updateOrderActionControls();
}

function updateSessionInfo(session, scope) {
  if (session) {
    _sessionInfo = {...session, scope: scope || session.scope || 'shared_agent'};
    _sessionConnected = !!session.credentials_set;
    _tradeMode = session.trade_mode === 'live' ? 'live' : 'paper';
    const sessionPaperEquity = Number(session.paper_equity ?? session.paper_balance);
    if (Number.isFinite(sessionPaperEquity) && sessionPaperEquity > 0) {
      if (sessionPaperEquity !== 10000 || !readStoredPaperEquity()) {
        rememberPaperEquity(sessionPaperEquity);
      }
    }
    if (session.account_balance != null) {
      _sessionAccountBalance = Number(session.account_balance);
      if (_tradeMode === 'live') updateBalance(_sessionAccountBalance, 'account');
    } else if (!_sessionConnected) {
      _sessionAccountBalance = null;
      _balanceSource = 'paper';
      updateBalanceLabel('paper');
    }
  }
  const el = document.getElementById('evidenceSession');
  const s = _sessionInfo || {};
  if (el) {
    const label = (s.scope || 'shared_agent').replaceAll('_', ' ');
    el.textContent = label.replace(/\b\w/g, c => c.toUpperCase());
  }
  updateTradeModeUI();
  updateLiveReadinessUI();
  updateOrderActionControls();
  renderHeaderBalance();
}

function updateTradeModeUI() {
  const ui = getUiState();
  const isLive = ui.isLive;
  const paperBtn = document.getElementById('tradeModePaperBtn');
  const liveBtn = document.getElementById('tradeModeLiveBtn');
  paperBtn?.classList.toggle('active', !isLive);
  liveBtn?.classList.toggle('active', isLive);
  const pill = document.getElementById('modePill');
  if (pill) {
    pill.textContent = isLive ? 'LIVE' : 'PAPER';
    pill.className = isLive ? 'mode-pill live' : 'mode-pill';
  }
  syncAccountHeaderState();
  syncModeLabels();
}

function syncModeLabels(data = {}) {
  const ui = getUiState(data);
  const label = ui.isLive
    ? ui.sessionConnected
      ? 'Live Account'
      : 'Live Account Pending'
    : 'Paper Trading';
  const color = ui.isLive
    ? ui.sessionConnected ? 'var(--bull)' : 'var(--teal)'
    : 'var(--gold)';

  const footer = document.getElementById('footerMode');
  if (footer) {
    footer.textContent = label;
    footer.style.color = color;
  }

  const mobile = document.getElementById('mRiskMode');
  if (mobile) mobile.textContent = label;

  const evidence = document.getElementById('evidenceMode');
  if (evidence) evidence.textContent = label;
}

function syncAccountHeaderState() {
  const ui = getUiState();
  const btn = document.getElementById('accountActionBtn');
  const label = document.getElementById('balanceLabel');
  const balance = document.getElementById('hdrBalance');

  if (btn) {
    btn.textContent = ui.connected ? 'Disconnect' : 'Connect Account';
    btn.title = ui.connected ? 'Disconnect account' : 'Connect your Bitget account';
  }

  if (label) {
    label.textContent = ui.isPaper
      ? 'Paper Equity'
      : ui.connected && ui.accountBalance != null
      ? 'Bitget Balance'
      : 'Live Balance';
  }

  if (balance && ui.isLive && !ui.connected) {
    balance.textContent = '$—';
    balance.style.color = 'var(--txt3)';
  }
}

function renderHeaderBalance(data = {}) {
  const el = document.getElementById('hdrBalance');
  const label = document.getElementById('balanceLabel');
  if (!el) return;

  const ui = getUiState(data);
  if (ui.isPaper) rememberPaperEquity(ui.paperEquity);

  if (ui.isPaper) {
    if (label) label.textContent = 'Paper Equity';
    el.textContent = '$' + ui.paperEquity.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    el.style.color = 'var(--teal)';
    return;
  }

  if (ui.sessionConnected && ui.accountBalance != null) {
    if (label) label.textContent = 'Bitget Balance';
    el.textContent = '$' + ui.accountBalance.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    el.style.color = 'var(--teal)';
    return;
  }

  if (label) label.textContent = 'Live Balance';
  el.textContent = '$—';
  el.style.color = 'var(--txt3)';
}

async function setTradeMode(mode) {
  const next = mode === 'live' ? 'live' : 'paper';
  if (next === _tradeMode) return;
  _tradeMode = next;
  updateTradeModeUI();
  updateLiveReadinessUI();
  try {
    const data = await fetchJson('/api/session/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: next}),
    }, 12000);
    if (!data?.ok) {
      showMToast(data?.error || 'Mode switch failed');
      return;
    }
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    showMToast(next === 'live' ? 'Live Account mode selected' : 'Paper Trading mode selected');
    renderHeaderBalance();
  } catch(e) {
    showMToast(e.message || 'Mode switch failed');
  }
}

async function refreshSessionAccount(silent = true) {
  if (!_sessionConnected || _operatorToken) return null;
  try {
    const data = await fetchJson('/api/session/account', {}, 15000);
    if (data?.session) updateSessionInfo(data.session, data.session.scope);
    if (!data?.ok) {
      if (!silent) showMToast(data?.error || 'Could not read Bitget balance');
      return null;
    }
    _sessionAccountBalance = Number(data.balance || 0);
    if (_tradeMode === 'live') updateBalance(_sessionAccountBalance, 'account');
    else renderHeaderBalance();
    return data;
  } catch(e) {
    if (!silent) showMToast(e.message || 'Could not read Bitget balance');
    return null;
  }
}

function updateLiveReadinessUI() {
  const ui = getUiState();
  const dot = document.getElementById('mLiveDot');
  const txt = document.getElementById('mLiveTxt');
  const hint = document.getElementById('mLiveHint');
  const btn = document.getElementById('mLiveBtn');
  if (dot) dot.className = 'm-status-dot ' + (ui.isLive && ui.unlocked ? 'live' : 'off');
  if (txt) txt.textContent = ui.isLive && ui.unlocked ? 'Live ready' : ui.isLive ? 'Live locked' : 'Paper mode';
  if (hint) hint.textContent = ui.isPaper ? 'Simulated execution' : ui.sessionConnected ? (ui.unlocked ? 'Execution gated' : 'Account verified') : 'Connect account first';
  if (btn) {
    btn.textContent = ui.unlocked ? 'Lock' : 'Unlock';
    btn.disabled = ui.isPaper || !ui.sessionConnected;
  }
  updateTradeModeUI();
  updateSessionExecutionUI();
  updateFundingReadiness();
  updateOrderActionControls();
  syncAccountHeaderState();
}

function setFundingText(id, text, cls = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = `funding-val ${cls}`.trim();
}

function updateFundingReadiness(data = {}) {
  const mode = data.risk_config?.mode || 'paper';
  const ui = getUiState(data);
  const connectedAt = ui.session.connected_at ? new Date(ui.session.connected_at).toLocaleTimeString('en-US', {hour12:false}) : '';

  const status = document.getElementById('fundingStatus');
  const note = document.getElementById('fundingNote');

  if (ui.isLive) {
    if (status) status.textContent = ui.sessionConnected ? (ui.unlocked ? 'Live ready' : 'Account connected') : 'Not connected';
    setFundingText('fundingFunds', ui.sessionConnected ? (ui.accountBalance != null ? `$${fmt(ui.accountBalance)} USDT` : 'Reading Bitget balance') : 'Connect Bitget account', ui.sessionConnected && ui.accountBalance != null ? 'pass' : 'wait');
    setFundingText('fundingSource', ui.sessionConnected ? 'Verified Bitget futures' : 'No account connected', ui.sessionConnected ? 'pass' : 'wait');
    setFundingText('fundingCustody', 'No BitAgent custody', 'pass');
    setFundingText('fundingGate', ui.sessionConnected ? (ui.unlocked ? 'Preview required' : 'Risk unlock required') : 'Connect first', ui.unlocked ? 'pass' : 'wait');
    if (note) {
      note.textContent = !ui.sessionConnected
        ? 'Live Account mode uses your own Bitget futures account. Connect API credentials before unlocking execution.'
        : ui.unlocked
        ? 'Live execution still requires an order preview and exact confirmation before any Bitget order is sent.'
        : `Bitget account verified${connectedAt ? ` at ${connectedAt}` : ''}. Funds stay on Bitget; unlock live risk only when you intend to preview a real futures order.`;
    }
    return;
  }

  if (status) status.textContent = mode === 'paper' ? 'Paper only' : 'Operator live';
  setFundingText('fundingFunds', mode === 'paper' ? `$${fmt(ui.paperEquity)} paper equity` : 'Operator account', mode === 'paper' ? 'wait' : 'pass');
  setFundingText('fundingSource', mode === 'paper' ? 'BitAgent paper wallet' : 'Configured Bitget account', mode === 'paper' ? '' : 'pass');
  setFundingText('fundingCustody', 'User keeps funds on Bitget', 'pass');
  setFundingText('fundingGate', ui.sessionConnected ? 'Session connected' : 'Connect account', ui.sessionConnected ? 'wait' : 'wait');
  if (note) {
    note.textContent = mode === 'paper'
      ? 'Paper mode uses simulated equity. Real users fund Bitget directly; BitAgent reads balance only after API connection.'
      : 'Operator live mode can use real funds and should be handled only with a dedicated Bitget account.';
  }
}

async function toggleLiveUnlock() {
  if (getUiState().isPaper) {
    showMToast('Switch to Live Account mode before unlocking live trading');
    return;
  }
  const unlocked = !!_sessionInfo?.live_unlocked;
  try {
    if (unlocked) {
      const data = await fetchJson('/api/session/live-lock', {method:'POST'}, 12000);
      if (data?.session) updateSessionInfo(data.session, data.session.scope);
      showMToast('Live mode locked');
      return;
    }
    const phrase = prompt('Live trading can lose real money. Type I ACCEPT LIVE RISK to continue.');
    if (!phrase) return;
    const data = await fetchJson('/api/session/live-unlock', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({acknowledge:true, phrase}),
    }, 12000);
    if (!data?.ok) {
      showMToast(data?.error || 'Live unlock failed');
      return;
    }
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    showMToast('Live mode ready for this session');
  } catch(e) {
    showMToast(e.message || 'Live unlock failed');
  }
}

function proposalText(proposal = {}) {
  const dir = String(proposal.direction || 'FLAT').toUpperCase();
  const symbol = proposal.symbol || _currentSymbol || '—';
  const size = Number(proposal.size || 0);
  const price = Number(proposal.entry_price || 0);
  const conf = proposal.confidence ?? 0;
  const sizeTxt = size > 0 ? size : '—';
  const priceTxt = price > 0 ? '$' + fmt(price) : 'market';
  return `${dir} ${symbol} · ${sizeTxt} @ ${priceTxt} · ${conf}%`;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function updateSessionExecutionUI() {
  const ui = getUiState();
  const actions = document.getElementById('sessionExecActions');
  const connectBtn = document.getElementById('connectAccountActionBtn');
  const preview = document.getElementById('previewOrderBtn');
  const place = document.getElementById('placeOrderBtn');
  const resetPaper = document.getElementById('resetPaperBtn');
  const canPreview = ui.isLive && ui.sessionConnected && ui.unlocked;
  const proposalDir = String(_lastSessionProposal?.direction || 'FLAT').toUpperCase();
  const canPlace = canPreview && (proposalDir === 'LONG' || proposalDir === 'SHORT');

  actions?.classList.toggle('connect-needed', ui.isLive && !ui.sessionConnected);
  if (connectBtn) connectBtn.style.display = ui.isLive && !ui.sessionConnected ? '' : 'none';
  if (preview) {
    preview.disabled = !canPreview;
    preview.title = ui.isPaper
      ? 'Switch to Live Account mode before previewing a real order.'
      : ui.sessionConnected
      ? ui.unlocked ? 'Preview the current session order without placing it.' : 'Unlock live readiness first.'
      : 'Connect your Bitget account first.';
  }
  if (place) {
    place.disabled = !canPlace;
    place.title = canPlace
      ? 'Place the last previewed live order after confirmation.'
      : 'Preview a LONG or SHORT order first.';
  }
  if (resetPaper) {
    resetPaper.disabled = !ui.isPaper;
    resetPaper.title = ui.isPaper
      ? 'Reset this browser session paper account and paper trade list.'
      : 'Switch to Paper mode before resetting the paper account.';
  }
  renderSessionExecLog();
}

function appendSessionExecEvent(kind, text, detail = '') {
  _sessionExecEvents.unshift({
    ts: new Date().toLocaleTimeString('en-US', {hour12:false}),
    kind,
    text,
    detail,
  });
  _sessionExecEvents = _sessionExecEvents.slice(0, 4);
  renderSessionExecLog();
}

function renderSessionExecLog() {
  const log = document.getElementById('sessionExecLog');
  if (!log) return;
  if (!_sessionExecEvents.length) {
    const ui = getUiState();
    const msg = ui.isPaper
      ? 'Paper mode is active. Switch to Live Account to preview or place real orders.'
      : !ui.sessionConnected
      ? 'Connect a Bitget futures account before live execution.'
      : !ui.unlocked
        ? 'Live readiness is locked for this session.'
        : _lastSessionProposal
          ? proposalText(_lastSessionProposal)
          : 'Preview the current AI decision before placing an order.';
    log.innerHTML = `<span class="session-exec-muted">${msg}</span>`;
    return;
  }
  log.innerHTML = _sessionExecEvents.map(ev => {
    const cls = ev.kind === 'live' ? 'live' : ev.kind === 'blocked' ? 'blocked' : '';
    return `<div class="session-exec-entry">
      <span class="session-exec-main">${escapeHtml(ev.ts)} · ${escapeHtml(ev.text)}${ev.detail ? `<br><span class="session-exec-muted">${escapeHtml(ev.detail)}</span>` : ''}</span>
      <span class="session-exec-state ${cls}">${ev.kind.toUpperCase()}</span>
    </div>`;
  }).join('');
}

async function previewSessionOrder() {
  if (getUiState().isPaper) {
    showMToast('Switch to Live Account mode before previewing a real order');
    return;
  }
  try {
    appendSessionExecEvent('preview', 'Requesting dry-run proposal');
    const data = await fetchJson('/api/session/execute', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({dry_run:true}),
    }, 30000);
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    if (!data.ok) {
      _lastSessionProposal = data.proposal || null;
      appendSessionExecEvent('blocked', data.error || 'Preview blocked');
      showMToast(data.error || 'Preview blocked');
      return;
    }
    _lastSessionProposal = data.proposal || null;
    appendSessionExecEvent('preview', 'Dry-run ready', proposalText(_lastSessionProposal));
    updateSessionExecutionUI();
    showMToast('Order preview ready');
  } catch(e) {
    appendSessionExecEvent('blocked', e.name === 'AbortError' ? 'Preview timed out' : 'Preview failed');
    showMToast(e.name === 'AbortError' ? 'Preview timed out' : (e.message || 'Preview failed'));
  }
}

async function placeSessionOrder() {
  if (getUiState().isPaper) {
    showMToast('Switch to Live Account mode before placing a real order');
    return;
  }
  if (!_lastSessionProposal) {
    showMToast('Preview an order first');
    return;
  }
  const phrase = prompt('This can place a real Bitget futures order. Type PLACE LIVE ORDER to continue.');
  if (!phrase) return;
  try {
    appendSessionExecEvent('live', 'Submitting live order', proposalText(_lastSessionProposal));
    const data = await fetchJson('/api/session/execute', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({dry_run:false, acknowledge:true, phrase}),
    }, 45000);
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    if (!data.ok || !data.executed) {
      const result = data.result || {};
      const msg = result.reason || data.error || 'Live order was not placed';
      appendSessionExecEvent('blocked', msg, proposalText(data.proposal || _lastSessionProposal));
      showMToast(msg);
      return;
    }
    appendSessionExecEvent('live', 'Live order placed', proposalText(data.proposal || _lastSessionProposal));
    showMToast('Live order placed');
    loadTradeHistory();
  } catch(e) {
    appendSessionExecEvent('blocked', e.name === 'AbortError' ? 'Order timed out' : 'Order failed');
    showMToast(e.name === 'AbortError' ? 'Order timed out' : (e.message || 'Order failed'));
  }
}

function applySessionSymbolState(data) {
  if (!data) return;
  const state = data.state || data;
  if (data.session) updateSessionInfo(data.session, data.scope || state.session_scope);
  if (!state.symbol || state.symbol !== _currentSymbol) {
    _lastSessionProposal = null;
  }
  _isSwitching = false;
  clearTimeout(_switchTimeout);
  if (state.symbol) {
    _currentSymbol = state.symbol;
    updateSymbolLabels(state.symbol);
    rebuildSymbolDrop(state.symbol);
  }
  handleUpdate(state);
  showMToast('Session asset selected');
}

async function requestSessionAnalysis() {
  try {
    const data = await fetchJson('/api/session/analyze', {method: 'POST'}, 60000);
    if (data?.ok) {
      applySessionSymbolState(data);
      showMToast('Session analysis updated');
    } else {
      showMToast(data?.error || 'Session analysis failed');
    }
  } catch(e) {
    showMToast(e.name === 'AbortError' ? 'Session analysis timed out' : 'Session analysis failed');
    console.error('session analysis failed', e);
  }
}

function showOperatorUnlock(reason = 'This operator action requires the operator token.') {
  if (!_operatorMode) {
    showMToast('This is an internal operator action. Public sessions use their own account controls.');
    return;
  }
  ensureOperatorUI();
  const pop = document.getElementById('operatorUnlockPop');
  const hint = document.getElementById('operatorUnlockHint');
  if (hint) hint.textContent = reason;
  if (pop) pop.classList.add('show');
  setTimeout(() => document.getElementById('operatorTokenQuickInput')?.focus(), 50);
  showMToast('Operator unlock required');
}

function hideOperatorUnlock() {
  document.getElementById('operatorUnlockPop')?.classList.remove('show');
}

function isOperatorUnlockMessage(msg = '') {
  return String(msg || '').toLowerCase().includes('operator unlock');
}

function hydrateInitialSnapshot(snapshot) {
  if (!snapshot || _hasInitialSnapshot) return;
  _hasInitialSnapshot = true;
  handleUpdate(snapshot);
}

function applyStatusSnapshot(status) {
  if (!status) return;
  const snapshot = status.latest || {};
  const sessionBalance = status.session?.account_balance;
  const data = {
    ...snapshot,
    balance: sessionBalance ?? status.balance ?? snapshot.balance,
    account: status.session_paper_account ?? status.account ?? snapshot.account,
    session_paper_account: status.session_paper_account,
    session_paper_trades: status.session_paper_trades,
    position: snapshot.position,
    risk_config: status.risk_config || snapshot.risk_config || { mode: 'paper' },
    decision: snapshot.decision,
    execution: snapshot.execution,
  };
  updateExecutionHelp(data);
  updateRulesPanel(data);
  syncModeLabels(data);
  syncMobileMetrics();
}

async function hydrateFromStatus() {
  try {
    const status = await fetchJson('/api/status', {}, 8000);
    _initPayload = status || _initPayload;
    _authRequired = !!status?.auth_required;
    updateSessionInfo(status?.session, status?.session_scope);
    _isConnected = !!(status?.creds_set || status?.session?.credentials_set);
    updateOperatorUI();
    const statusSym = status?.session?.selected_symbol || status?.symbol;
    if (statusSym) {
      _currentSymbol = statusSym;
      updateSymbolLabels(statusSym);
      rebuildSymbolDrop(statusSym);
    }
    applyStatusSnapshot(status);
    if (status?.session?.credentials_set) refreshSessionAccount(true);
    if (status?.latest) hydrateInitialSnapshot(status.latest);
    if (_activeTab === 'decisions') loadDecisionHistory();
  } catch(e) {
    console.warn('Initial status hydration failed', e);
  }
}

function rememberEnteredPlatform() {
  try { localStorage.setItem(ENTERED_PLATFORM_KEY, '1'); } catch(e) {}
}

function enterCockpit(options = {}) {
  const persist = options.persist !== false;
  const animate = options.animate !== false;
  _introSkipped = true;
  introRunning  = false;
  if (persist) rememberEnteredPlatform();

  const screen = document.getElementById('introScreen');
  if (screen) {
    if (animate) {
      screen.classList.add('hide');
      setTimeout(() => {
        if (screen.classList.contains('hide')) screen.style.display = 'none';
      }, 950);
    } else {
      screen.classList.add('hide');
      screen.style.display = 'none';
    }
  }

  setTimeout(() => {
    hideConnectScreen();
    document.querySelector('.live-pill')?.style.setProperty('display','');
    if (_initPayload?.latest) {
      _isInitPayload = true;
      try {
        hydrateInitialSnapshot(_initPayload.latest);
      } finally {
        _isInitPayload = false;
      }
    }
    if (!_hasInitialSnapshot) hydrateFromStatus();
  }, animate ? 650 : 0);
}

function skipIntro() {
  if (_introSkipped) return;
  enterCockpit();
}

// The intro is an actual homepage: users choose when to enter.

function drawIntro() {
  if (!introRunning || !iCtx) return;
  iCtx.clearRect(0, 0, iW, iH);

  const cx = iW * 0.5;
  const cy = iH * 0.48;
  const gr = iCtx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(iW, iH) * 0.58);
  gr.addColorStop(0,   'rgba(18,206,174,0.07)');
  gr.addColorStop(0.42,'rgba(17,24,39,0.028)');
  gr.addColorStop(1,   'transparent');
  iCtx.fillStyle = gr;
  iCtx.fillRect(0, 0, iW, iH);

  STREAMS.forEach((n) => {
    n.x += n.vx * 0.55; n.y += n.vy * 0.55;
    if (n.x > iW + 30) {
      n.x = -30;
      n.y = Math.random() * iH;
    }
    if (n.y < -20) n.y = iH + 20;
    if (n.y > iH + 20) n.y = -20;

    const tail = 16 + n.r * 8;
    const grad = iCtx.createLinearGradient(n.x - tail, n.y, n.x + tail, n.y);
    const hue = n.hue === '158,169,255' ? '17,24,39' : '18,206,174';
    grad.addColorStop(0, `rgba(${hue},0)`);
    grad.addColorStop(.55, `rgba(${hue},0.12)`);
    grad.addColorStop(1, `rgba(${hue},0)`);
    iCtx.strokeStyle = grad;
    iCtx.lineWidth = Math.max(.6, n.r * .65);
    iCtx.beginPath();
    iCtx.moveTo(n.x - tail, n.y);
    iCtx.quadraticCurveTo(n.x, n.y + Math.sin(n.x * .01) * 6, n.x + tail, n.y);
    iCtx.stroke();

    const dotGlow = iCtx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 6);
    dotGlow.addColorStop(0,   `rgba(${hue},0.22)`);
    dotGlow.addColorStop(1,   'transparent');
    iCtx.fillStyle = dotGlow;
    iCtx.beginPath(); iCtx.arc(n.x, n.y, n.r * 5, 0, Math.PI*2); iCtx.fill();
  });

  requestAnimationFrame(drawIntro);
}
drawIntro();
if (_introSkipped) enterCockpit({persist:false, animate:false});

// (skipIntro defined above drawIntro)


// ══════════════════════════════════════════════════════
// CANVAS BACKGROUND ANIMATION
// ══════════════════════════════════════════════════════
const canvas = document.getElementById('bg');
const ctx    = canvas.getContext('2d');
let W, H;

function resizeCanvas() {
  W = canvas.width  = window.innerWidth;
  H = canvas.height = window.innerHeight;
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

// Price waves
const WAVES = [
  { y:.25, amp:40, spd:.0008, phase:0,   col:'rgba(0,212,212,0.045)' },
  { y:.45, amp:55, spd:.0005, phase:2.1, col:'rgba(14,203,129,0.030)' },
  { y:.65, amp:35, spd:.0012, phase:1.0, col:'rgba(0,212,212,0.025)' },
  { y:.82, amp:45, spd:.0006, phase:3.5, col:'rgba(246,70,93,0.025)'  },
];

// Floating market terms
const FIN_WORDS = [
  'LONG','SHORT','BUY','SELL','HOLD',
  'RSI','ATR','MACD','SMA','EMA',
  'BULL','BEAR','PERP','USDT','BTC',
  'SIGNAL','TREND','DEPTH','FUNDING','OI',
  'ENTRY','STOP','AGENT','AI','LIVE'
];
const particles = Array.from({length:28}, () => newParticle());

function newParticle(atBottom=true) {
  return {
    x:    Math.random() * (W || 1200),
    y:    atBottom ? (H || 800) + 20 : Math.random() * (H || 800),
    vy:   -(0.18 + Math.random() * 0.22),
    vx:   (Math.random() - 0.5) * 0.08,
    txt:  FIN_WORDS[Math.floor(Math.random() * FIN_WORDS.length)],
    sz:   10 + Math.random() * 3,
    life: 0,
    maxL: 280 + Math.random() * 320,
    op:   0,
    maxOp:0.055 + Math.random() * 0.055,
  };
}

let t = 0;
function drawBg() {
  ctx.clearRect(0, 0, W, H);

  // Grid
  ctx.strokeStyle = 'rgba(30,37,48,0.6)';
  ctx.lineWidth   = 1;
  for (let x = 0; x < W; x += 64) {
    ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H); ctx.stroke();
  }
  for (let y = 0; y < H; y += 64) {
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
  }

  // Price waves
  WAVES.forEach(w => {
    ctx.beginPath();
    ctx.strokeStyle = w.col;
    ctx.lineWidth   = 1.4;
    for (let x = 0; x <= W; x += 3) {
      const y = w.y * H + Math.sin(x * 0.009 + w.phase + t * w.spd * 1000) * w.amp
              + Math.sin(x * 0.023 + w.phase) * (w.amp * 0.3);
      x === 0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
    }
    ctx.stroke();
  });

  // Floating particles
  ctx.font = `500 11px 'JetBrains Mono', monospace`;
  particles.forEach((p,i) => {
    p.life++; p.x += p.vx; p.y += p.vy;
    if (p.life < 40)           p.op = p.maxOp * p.life / 40;
    else if (p.life > p.maxL - 40) p.op = p.maxOp * (p.maxL - p.life) / 40;
    else                       p.op = p.maxOp;
    if (p.life >= p.maxL || p.y < -20) {
      particles[i] = newParticle(true);
      return;
    }
    ctx.fillStyle = `rgba(0,212,212,${p.op})`;
    ctx.fillText(p.txt, p.x, p.y);
  });

  // Scan line
  const scanY = ((t * 0.04) % H);
  const grad  = ctx.createLinearGradient(0, scanY-60, 0, scanY+60);
  grad.addColorStop(0, 'transparent');
  grad.addColorStop(.5,'rgba(0,212,212,0.018)');
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.fillRect(0, scanY-60, W, 120);

  t++;
  requestAnimationFrame(drawBg);
}
drawBg();


// ══════════════════════════════════════════════════════
// TRADE LOG
// ══════════════════════════════════════════════════════
let _knownTs        = new Set();
let _currentSymbol  = 'BTCUSDT';
let _isSwitching    = false;   // true between switchSymbol() call and first matching update
let _switchTimeout  = null;    // retry handle

function updateLog(history) {
  if (!history || !history.length) return;
  const list  = document.getElementById('logList');

  const newItems = history.filter(h => !_knownTs.has(h.ts));
  if (!newItems.length) return;
  newItems.forEach(h => _knownTs.add(h.ts));
  if (_knownTs.size > 500) {
    const recent = Array.from(_knownTs).slice(-500);
    _knownTs = new Set(recent);
  }

  if (_knownTs.size === newItems.length) list.innerHTML = '';

  const frag = document.createDocumentFragment();
  newItems.forEach(h => {
    const cls = h.direction === 'LONG' ? 'bull' : h.direction === 'SHORT' ? 'bear' : 'neutral';
    const el  = document.createElement('div');
    el.className = 'log-item';
    const execMark = h.executed
      ? `<span style="color:var(--bull);font-size:9px;font-family:var(--mono)">✦</span>`
      : `<span style="color:var(--txt3);font-size:9px">·</span>`;
    el.innerHTML = `
      <span class="log-time">${h.ts}</span>
      <span class="log-dir ${cls}">${h.direction}</span>
      <span class="log-price">$${fmt(h.price)}</span>
      <span class="log-conf">${h.confidence}% ${execMark}</span>`;
    frag.prepend(el);
  });
  list.prepend(frag);

  const count = document.getElementById('tabCount');
  if (count && _activeTab === 'decisions')
    count.textContent = `${history.length} decision${history.length !== 1 ? 's' : ''}`;
}


// ══════════════════════════════════════════════════════
// CONNECT / DISCONNECT
// ══════════════════════════════════════════════════════
function showConnectScreen() {
  const el = document.getElementById('connectScreen');
  el.classList.remove('hide');
  el.setAttribute('aria-hidden', 'false');
}
function hideConnectScreen() {
  const el = document.getElementById('connectScreen');
  el.classList.add('hide');
  el.setAttribute('aria-hidden', 'true');
}

async function doConnect() {
  const btn    = document.getElementById('connectBtn');
  if (btn.disabled) return;
  const err    = document.getElementById('connectError');
  const key    = document.getElementById('inputApiKey').value.trim();
  const sec    = document.getElementById('inputSecretKey').value.trim();
  const pass   = document.getElementById('inputPassphrase').value.trim();
  const budget = parseFloat(document.getElementById('inputBudget').value) || 0;

  if (!key || !sec || !pass) {
    err.textContent = 'Please fill in all three fields.';
    err.classList.add('show');
    return;
  }

  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  btn.textContent = 'Connecting...';
  err.classList.remove('show');

  const symbol = document.getElementById('inputSymbol').value;
  const endpoint = (_operatorMode && _operatorToken) ? '/api/connect' : '/api/session/connect';
  try {
    const data = await fetchJson(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: key, secret_key: sec, passphrase: pass, symbol, budget}),
    }, 20000);
    if (data.ok) {
      _manualBudget = budget;
      _isConnected = true;
      if (data.session) updateSessionInfo(data.session, data.scope || data.session.scope);
      btn.setAttribute('aria-busy', 'false');
      hideConnectScreen();
      document.querySelector('.live-pill')?.style.setProperty('display','');
      if (data.symbol) {
        _currentSymbol = data.symbol;
        updateSymbolLabels(data.symbol);
        rebuildSymbolDrop(data.symbol);
      }
      if (data.balance != null) updateBalance(data.balance, data.balance_source === 'bitget_futures' ? 'account' : 'paper');
      if (data.balance_source === 'bitget_futures') _sessionAccountBalance = Number(data.balance || 0);
      if (data.position !== undefined) updatePosition(data.position);
      if (data.state) applySessionSymbolState(data);
      syncMobileMetrics();
      const bal = data.balance ? `$${Number(data.balance).toFixed(2)} USDT available` : (budget ? `$${budget.toFixed(2)} USDT budget` : '');
      if (bal) document.getElementById('execDetail').textContent = bal;
    } else {
      err.textContent = data.error || 'Connection failed. Check your credentials.';
      err.classList.add('show');
      btn.disabled = false;
      btn.setAttribute('aria-busy', 'false');
      btn.textContent = 'Connect My Account';
    }
  } catch(e) {
    err.textContent = e.name === 'AbortError'
      ? 'Connection timed out. Check Bitget/network status and try again.'
      : (e.message || 'Could not reach server. Is it running?');
    err.classList.add('show');
    btn.disabled = false;
    btn.setAttribute('aria-busy', 'false');
    btn.textContent = 'Connect My Account';
  }
}

async function doDisconnect() {
  if (!_sessionConnected && !(_operatorMode && _operatorToken && _isConnected)) {
    showConnectScreen();
    return;
  }
  if (!confirm('Disconnect account? The agent will stop trading.')) return;
  let data;
  const endpoint = (_operatorMode && _operatorToken && !_sessionConnected) ? '/api/disconnect' : '/api/session/disconnect';
  try {
    data = await fetchJson(endpoint, {method: 'POST'}, 12000);
  } catch(e) {
    if (isOperatorUnlockMessage(e.message)) {
      showMToast('No account is connected in this browser session.');
    } else {
      showMToast(e.name === 'AbortError' ? 'Disconnect timed out' : 'Disconnect failed');
    }
    return;
  }
  if (!data?.ok) {
    if (isOperatorUnlockMessage(data?.error)) {
      showMToast('No account is connected in this browser session.');
    } else {
      showMToast(data?.error || 'Disconnect failed');
    }
    updateOperatorUI();
    return;
  }
  _manualBudget = 0;
  _isConnected = false;
  _sessionConnected = false;
  _sessionAccountBalance = null;
  _balanceSource = 'paper';
  updateBalanceLabel('paper');
  if (data.session) updateSessionInfo(data.session, data.scope || data.session.scope);
  if (_initPayload) _initPayload.creds_set = false;
  _hasInitialSnapshot = false;
  document.querySelector('.live-pill')?.style.setProperty('display','none');
  // Clear live header values
  const hdrB = document.getElementById('hdrBalance');
  if (hdrB) { hdrB.textContent = '$—'; hdrB.style.color = ''; }
  const hdrP = document.getElementById('hdrPrice');
  if (hdrP) hdrP.textContent = '$—';
  updatePosition(null);
  showConnectScreen();
  document.getElementById('inputApiKey').value = '';
  document.getElementById('inputSecretKey').value = '';
  document.getElementById('inputPassphrase').value = '';
  document.getElementById('inputBudget').value = '';
  document.getElementById('connectBtn').disabled = false;
  document.getElementById('connectBtn').setAttribute('aria-busy', 'false');
  document.getElementById('connectBtn').textContent = 'Connect My Account';
  syncAccountHeaderState();
}

function handleAccountAction() {
  if (_sessionConnected || (_operatorMode && _operatorToken && _isConnected)) {
    doDisconnect();
  } else {
    showConnectScreen();
  }
}

// Populate the connect form's symbol select from _PAIRS
function rebuildConnectSelect() {
  const sel = document.getElementById('inputSymbol');
  if (!sel) return;
  const current = sel.value;
  const groups = [
    { label: '── Major',              syms: ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','ADAUSDT','AVAXUSDT','DOTUSDT','LINKUSDT','ATOMUSDT','LTCUSDT','AAVEUSDT'] },
    { label: '── Mid-Cap Alts',       syms: ['NEARUSDT','UNIUSDT','INJUSDT','APTUSDT','SUIUSDT','ARBUSDT','OPUSDT','TIAUSDT','JUPUSDT','WLDUSDT','CRVUSDT','FILUSDT','ICPUSDT','ALGOUSDT','EGLDUSDT','AXSUSDT','LDOUSDT','GRTUSDT','DYDXUSDT','RUNEUSDT','FTMUSDT','APEUSDT','GMTUSDT','ASTRUSDT','FLOWUSDT','KAVAUSDT','XLMUSDT','ETCUSDT','VETUSDT','HBARUSDT'] },
    { label: '── DeFi / Governance',  syms: ['COMPUSDT','MKRUSDT','YFIUSDT','SNXUSDT','SUSHIUSDT','1INCHUSDT','ENSUSDT','RDNTUSDT','MAGICUSDT','MASKUSDT'] },
    { label: '── Gaming / Metaverse', syms: ['SANDUSDT','MANAUSDT','GALAUSDT','CHZUSDT','ENJUSDT','ALICEUSDT','THETAUSDT','CAKEUSDT','TWTUSDT'] },
    { label: '── Infrastructure',     syms: ['ANKRUSDT','ROSEUSDT','CELRUSDT','OCEANUSDT','STORJUSDT','ZILUSDT','KSMUSDT','HOTUSDT','BTTUSDT'] },
    { label: '── Low-Cost (from $0.50)', syms: ['DOGEUSDT','TRXUSDT','SHIBUSDT','PEPEUSDT','WIFUSDT','NOTUSDT','FLOKIUSDT','TURBOUSDT','POPCATUSDT','PNUTUSDT','MOODENGUSDT','SEIUSDT','PYTHUSDT','BLURUSDT','JASMYUSDT'] },
  ];
  const pairMap = Object.fromEntries(_PAIRS.map(([s,b]) => [s, `${b} / USDT Perpetual`]));
  sel.innerHTML = groups.map(g =>
    `<optgroup label="${g.label}">${
      g.syms.filter(s => pairMap[s]).map(s => `<option value="${s}">${pairMap[s]}</option>`).join('')
    }</optgroup>`
  ).join('');
  if (current && sel.querySelector(`option[value="${current}"]`)) sel.value = current;
}
// Allow Enter key to submit
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !document.getElementById('connectScreen').classList.contains('hide')) {
    doConnect();
  }
});

// ── SYMBOL SWITCHER ───────────────────────────────────

// Single source of truth for all available trading pairs
const _PAIRS = [
  // Major
  ['BTCUSDT','BTC'],['ETHUSDT','ETH'],['SOLUSDT','SOL'],['BNBUSDT','BNB'],
  ['XRPUSDT','XRP'],['ADAUSDT','ADA'],['AVAXUSDT','AVAX'],['DOTUSDT','DOT'],
  ['LINKUSDT','LINK'],['ATOMUSDT','ATOM'],['LTCUSDT','LTC'],['AAVEUSDT','AAVE'],
  // Mid-cap Alts
  ['NEARUSDT','NEAR'],['UNIUSDT','UNI'],['INJUSDT','INJ'],['APTUSDT','APT'],
  ['SUIUSDT','SUI'],['ARBUSDT','ARB'],['OPUSDT','OP'],['TIAUSDT','TIA'],
  ['JUPUSDT','JUP'],['WLDUSDT','WLD'],['CRVUSDT','CRV'],['FILUSDT','FIL'],
  ['ICPUSDT','ICP'],['ALGOUSDT','ALGO'],['EGLDUSDT','EGLD'],['AXSUSDT','AXS'],
  ['LDOUSDT','LDO'],['GRTUSDT','GRT'],['DYDXUSDT','DYDX'],['RUNEUSDT','RUNE'],
  ['FTMUSDT','FTM'],['APEUSDT','APE'],['GMTUSDT','GMT'],['ASTRUSDT','ASTR'],
  ['FLOWUSDT','FLOW'],['KAVAUSDT','KAVA'],['XLMUSDT','XLM'],['ETCUSDT','ETC'],
  ['VETUSDT','VET'],['HBARUSDT','HBAR'],
  // DeFi / Governance
  ['COMPUSDT','COMP'],['MKRUSDT','MKR'],['YFIUSDT','YFI'],['SNXUSDT','SNX'],
  ['SUSHIUSDT','SUSHI'],['1INCHUSDT','1INCH'],['ENSUSDT','ENS'],
  ['RDNTUSDT','RDNT'],['MAGICUSDT','MAGIC'],['MASKUSDT','MASK'],
  // Gaming / Metaverse
  ['SANDUSDT','SAND'],['MANAUSDT','MANA'],['GALAUSDT','GALA'],['CHZUSDT','CHZ'],
  ['ENJUSDT','ENJ'],['ALICEUSDT','ALICE'],['THETAUSDT','THETA'],
  ['CAKEUSDT','CAKE'],['TWTUSDT','TWT'],
  // Infrastructure / Other
  ['ANKRUSDT','ANKR'],['ROSEUSDT','ROSE'],['CELRUSDT','CELR'],['OCEANUSDT','OCEAN'],
  ['STORJUSDT','STORJ'],['ZILUSDT','ZIL'],['KSMUSDT','KSM'],
  ['HOTUSDT','HOT'],['BTTUSDT','BTT'],
  // Low-Cost / Meme
  ['DOGEUSDT','DOGE'],['TRXUSDT','TRX'],['SHIBUSDT','SHIB'],['PEPEUSDT','PEPE'],
  ['WIFUSDT','WIF'],['NOTUSDT','NOT'],['FLOKIUSDT','FLOKI'],['TURBOUSDT','TURBO'],
  ['POPCATUSDT','POPCAT'],['PNUTUSDT','PNUT'],['MOODENGUSDT','MOODENG'],
  ['SEIUSDT','SEI'],['PYTHUSDT','PYTH'],['BLURUSDT','BLUR'],['JASMYUSDT','JASMY'],
];

// ── Single canonical symbol formatter ─────────────────
function formatSymbol(sym) {
  if (!sym) return '— / USDT PERP';
  return sym.replace(/USDT$/, '') + '/USDT PERP';
}

// ── Update every symbol label in the UI ────────────────
function updateSymbolLabels(sym) {
  const displayText = formatSymbol(sym);
  const symVal = document.getElementById('symbolVal');
  if (symVal) symVal.textContent = displayText;
  // Desktop dropdown items
  document.querySelectorAll('.sym-item').forEach(el => {
    el.classList.toggle('active', el.dataset.s === sym);
  });
  // Mobile settings grid pills
  document.querySelectorAll('.m-sym-pill').forEach(el => {
    const base = el.textContent.trim();
    el.classList.toggle('active', base + 'USDT' === sym);
  });
  // Mobile settings status sym
  const mStatusSym = document.getElementById('mStatusSym');
  if (mStatusSym && mStatusSym.textContent !== '—') mStatusSym.textContent = displayText;
}

// Alias kept so any residual call-sites still work
function setSymbolDisplay(sym) { updateSymbolLabels(sym); }

// ── Reset all signal cards to neutral/loading state ────
function setAllSignalsLoading() {
  const sigRow = document.getElementById('signalsRow');
  if (sigRow) { sigRow.innerHTML = ''; buildSignalCards(); }
}

// ── Clear every live data element to "—" / loading ────
function clearAllDisplayValues() {
  // Price
  const priceEl = document.getElementById('hdrPrice');
  if (priceEl) priceEl.textContent = '$—';
  const cycleEl = document.getElementById('hdrCycle');
  if (cycleEl) cycleEl.textContent = '#0';
  const mPrc = document.getElementById('mPrice');
  if (mPrc) mPrc.textContent = '$—';
  const mCyc = document.getElementById('mCycle');
  if (mCyc) mCyc.textContent = '#0';

  // Header timestamp
  const lastTs = document.getElementById('lastTs');
  if (lastTs) lastTs.textContent = 'Switching...';

  // Direction circle
  const wrap = document.getElementById('dirWrap');
  if (wrap) wrap.className = 'dir-arrow-wrap neutral';
  const arrow = document.getElementById('dirArrow');
  if (arrow) { arrow.className = 'dir-arrow neutral'; arrow.textContent = '—'; }
  const dirName = document.getElementById('dirName');
  if (dirName) { dirName.className = 'dir-name neutral'; dirName.textContent = 'SWITCHING'; }

  // Confidence gauge
  const arc = document.getElementById('gaugeArc');
  if (arc) { arc.style.strokeDashoffset = GAUGE_LEN; arc.style.stroke = '#1A2030'; }
  const glow = document.getElementById('gaugeGlow');
  if (glow) { glow.style.strokeDashoffset = GAUGE_LEN; glow.style.stroke = '#1A2030'; }
  const dot = document.getElementById('gaugeDot');
  if (dot) { dot.style.opacity = '0'; dot.setAttribute('cx','47'); dot.setAttribute('cy','153'); }
  const gPct = document.getElementById('gaugePct');
  if (gPct) gPct.textContent = '—';

  // Trade params
  ['pEntry','pSL','pTP','pSize'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '—';
  });

  // Reasoning
  const rtxt = document.getElementById('reasoningTxt');
  if (rtxt) rtxt.textContent = 'Switching asset — collecting fresh market signals...';
  const rn = document.getElementById('riskNote');
  if (rn) rn.className = 'risk-note';

  // Execution bar
  const execAction = document.getElementById('execAction');
  if (execAction) { execAction.textContent = 'IDLE'; execAction.className = 'exec-bar-action off'; }
  const execDot = document.getElementById('execDot');
  if (execDot) execDot.className = 'exec-bar-dot off';
  const execDetailNormal = document.getElementById('execDetailNormal');
  if (execDetailNormal) execDetailNormal.textContent = 'Waiting for first agent cycle...';
  const execBadge = document.getElementById('execBadge');
  if (execBadge) { execBadge.textContent = '—'; execBadge.style.color = ''; execBadge.style.borderColor = ''; }
  const cooldownWrap = document.getElementById('cooldownWrap');
  if (cooldownWrap) cooldownWrap.style.display = 'none';
  // Cancel any running cooldown timer
  if (typeof _cooldownTimer !== 'undefined' && _cooldownTimer) {
    clearInterval(_cooldownTimer); _cooldownTimer = null;
  }

  // Ticker tape — reset to new symbol immediately so stale BTC data is never
  // visible after a switch. buildTicker uses _currentSymbol which is already
  // updated by the time clearAllDisplayValues() is called.
  buildTicker({ symbol: _currentSymbol, price: 0, cycle: 0, sim_pnl: 0, signals: {}, decision: {} });

  // Agent log
  _knownTs.clear();
  const logList = document.getElementById('logList');
  if (logList) logList.innerHTML = `<div class="log-empty">
    <span style="font-size:22px;opacity:.3">◈</span>
    <span>Switching asset...</span>
    <span style="color:var(--txt3)">Fresh price loads immediately; full analysis follows</span>
  </div>`;
}

function toggleSymbolDrop() {
  const drop  = document.getElementById('symbolDrop');
  const chip  = document.getElementById('symbolChip');
  const isOpen = !drop.classList.contains('hide');

  if (!isOpen) {
    const r = chip.getBoundingClientRect();
    drop.style.top   = (r.bottom + 6) + 'px';
    drop.style.right = (window.innerWidth - r.right) + 'px';
    drop.style.left  = 'auto';
    // Reset search filter when opening
    filterSymbolItems('');
    setTimeout(() => {
      const s = document.getElementById('symSearch');
      if (s) { s.value = ''; s.focus(); }
    }, 40);
  }

  drop.classList.toggle('hide', isOpen);
  chip.classList.toggle('open', !isOpen);
}

function switchSymbol(el) {
  const sym = el.dataset.s;
  if (!sym) return;

  // Guard: no-op if already on this symbol or a switch is in flight
  if (_isSwitching || _currentSymbol === sym) {
    document.getElementById('symbolDrop')?.classList.add('hide');
    document.getElementById('symbolChip')?.classList.remove('open');
    return;
  }

  clearTimeout(_switchTimeout);
  _isSwitching = true;

  // 1. Update _currentSymbol IMMEDIATELY — this makes the stale-update guard
  //    correctly reject any in-flight update from the previous symbol.
  _currentSymbol = sym;

  // 2. Update every symbol label in the UI right now
  updateSymbolLabels(sym);

  // 3. Clear all stale data values
  clearAllDisplayValues();

  // 4. Reset signal cards to neutral loading state
  setAllSignalsLoading();

  // 5. Close dropdown immediately
  document.getElementById('symbolDrop')?.classList.add('hide');
  document.getElementById('symbolChip')?.classList.remove('open');

  // 6. POST to backend (WS symbol_changed → update will arrive shortly)
  fetchJson('/api/switch-symbol', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({symbol: sym}),
  }, 12000)
  .then(data => {
    if (!data.ok) {
      _isSwitching = false;
      if (isOperatorUnlockMessage(data.error)) {
        showOperatorUnlock('Switching assets changes the active agent market and requires operator approval. Enter the operator token, then choose the asset again.');
      } else {
        showMToast(data.error || 'Symbol switch failed');
      }
      console.error('Symbol switch failed:', data.error);
      return;
    }
    if (data.scope === 'session_preview') {
      applySessionSymbolState(data);
      if (data.analysis_available) requestSessionAnalysis();
    }
    // On success: stay in switching state until WS update arrives
  })
  .catch(err => {
    _isSwitching = false;
    if (isOperatorUnlockMessage(err.message)) {
      showOperatorUnlock('Switching assets changes the active agent market and requires operator approval. Enter the operator token, then choose the asset again.');
    } else {
      showMToast(err.name === 'AbortError' ? 'Symbol switch timed out' : 'Symbol switch failed');
    }
    console.error('Symbol switch error:', err);
  });

  // 7. Safety net: full analysis can take a minute because it fans out to market,
  // exchange, and reasoning calls. Do not retry early and reset the UI.
  _switchTimeout = setTimeout(() => {
    if (_isSwitching) {
      console.warn('Symbol switch analysis is still pending');
      _isSwitching = false;
      showMToast('Still collecting fresh signals...');
    }
  }, 90000);
}

function rebuildSymbolDrop(activeSym) {
  const drop = document.getElementById('symbolDrop');
  drop.innerHTML =
    `<div class="sym-search-wrap">
      <input class="sym-search" id="symSearch" type="text"
        placeholder="Search ${_PAIRS.length} pairs…"
        oninput="filterSymbolItems(this.value)"
        onclick="event.stopPropagation()"
        autocomplete="off" spellcheck="false">
    </div>
    <div id="symItemList"></div>`;
  _symActiveSym = activeSym;
  filterSymbolItems('');
}

let _symActiveSym = 'BTCUSDT';

function filterSymbolItems(filter) {
  const list = document.getElementById('symItemList');
  if (!list) return;
  const lf = filter.toLowerCase().replace('/', '').replace(' ', '');
  const filtered = lf
    ? _PAIRS.filter(([s, b]) => b.toLowerCase().includes(lf) || s.toLowerCase().includes(lf))
    : _PAIRS;
  if (!filtered.length) {
    list.innerHTML = `<div class="sym-no-results">No matches for "${filter}"</div>`;
    return;
  }
  list.innerHTML = filtered.map(([s, b]) =>
    `<div class="sym-item${s === _symActiveSym ? ' active' : ''}" data-s="${s}" onclick="switchSymbol(this)">${b} / USDT Perp</div>`
  ).join('');
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
  const wrap = document.querySelector('.symbol-wrap');
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById('symbolDrop')?.classList.add('hide');
    document.getElementById('symbolChip')?.classList.remove('open');
  }
});


// ══════════════════════════════════════════════════════
// WEBSOCKET
// ══════════════════════════════════════════════════════
let _wsRetryDelay = 1000;
const WS_MAX_DELAY = 30000;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws    = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    _wsRetryDelay = 1000;
    document.getElementById('overlay').classList.add('hide');
  };

  ws.onmessage = e => {
    const data = JSON.parse(e.data);

    // Init message — store payload, NEVER touch the intro screen here.
    // skipIntro() (user click or auto-timer) will read _initPayload when ready.
    if (data.type === 'init') {
      _initPayload = data;
      _isConnected = !!(data.creds_set || data.session?.credentials_set);
      if ('auth_required' in data) {
        _authRequired = !!data.auth_required;
        updateSessionInfo(data.session, data.session_scope);
        updateOperatorUI();
      }
      if (data.creds_set || data.session?.credentials_set) {
        _isInitPayload = true;
        try {
          hydrateInitialSnapshot(data.latest);
        } finally {
          _isInitPayload = false;
        }
        if (!data.latest) hydrateFromStatus();
        refreshSessionAccount(true);
      }
      // If user already skipped intro before WS connected, act immediately
      if (_introSkipped) {
        enterCockpit({persist:false, animate:false});
        if (data.latest) {
          _isInitPayload = true;
          try {
            hydrateInitialSnapshot(data.latest);
          } finally {
            _isInitPayload = false;
          }
        }
      }
      // Sync symbol: prefer data.symbol (= agent.SYMBOL, always authoritative) over
      // data.latest.symbol which may be from the last completed cycle and could be a
      // stale old symbol if the WS reconnects mid-switch. Reversing this precedence was
      // the root cause of "SWITCHING forever" after a WS reconnect during a symbol switch.
      const initSym = data.session?.selected_symbol || data.symbol || data.latest?.symbol;
      if (initSym) {
        _currentSymbol = initSym;
        updateSymbolLabels(initSym);
        rebuildSymbolDrop(initSym);
      }
      return;
    }

    if (data.type === 'connected') {
      _isConnected = true;
      hideConnectScreen();
      document.querySelector('.live-pill')?.style.setProperty('display','');
      if (data.symbol) {
        _currentSymbol = data.symbol;
        updateSymbolLabels(data.symbol);
        rebuildSymbolDrop(data.symbol);
      }
      if (data.balance != null) updateBalance(data.balance, data.balance_source === 'bitget_futures' ? 'account' : 'live');
      if ('position' in data)   updatePosition(data.position);
      hydrateFromStatus();
      return;
    }
    if (data.type === 'disconnected') {
      _isConnected = false;
      document.querySelector('.live-pill')?.style.setProperty('display','none');
      showConnectScreen();
      return;
    }
    if (data.type === 'symbol_changed') {
      // Ensure consistency (switchSymbol() may have already set these, but handle
      // the case where the change originates from another client / server restart)
      _currentSymbol = data.symbol;
      updateSymbolLabels(data.symbol);
      rebuildSymbolDrop(data.symbol);

      // Belt-and-suspenders clear in case switchSymbol() wasn't the trigger
      clearAllDisplayValues();
      setAllSignalsLoading();

      // Fetch current price immediately — don't wait up to 60s for next cycle
      (async () => {
        try {
          const sym = data.symbol;
          const d = await fetchJson(`/api/ticker/${sym}`, {}, 8000);
          if (d.price > 0 && _currentSymbol === sym) {
            document.getElementById('hdrPrice').textContent = '$' + fmt(d.price);
            const mPrc = document.getElementById('mPrice');
            if (mPrc) mPrc.textContent = '$' + fmt(d.price);
          }
        } catch(e) {}
      })();

      buildTicker({ symbol: data.symbol, price: 0, cycle: 0, sim_pnl: 0, signals: {}, decision: {} });
      return;
    }

    if (data.type === 'session_symbol_changed') {
      applySessionSymbolState(data);
      return;
    }

    if (data.type === 'balance_update') {
      if (_tradeMode === 'paper') {
        renderHeaderBalance({balance:data.balance});
      } else if (_sessionConnected && !_operatorToken && _sessionAccountBalance != null) {
        updateBalance(_sessionAccountBalance, 'account');
      } else {
        updateBalance(data.balance, 'live');
      }
      hydrateFromStatus();
      return;
    }

    if (data.type === 'error') {
      // Cycle failed — show in exec bar so user knows why it's taking long
      const action = document.getElementById('execAction');
      const det    = document.getElementById('execDetailNormal');
      if (action) { action.textContent = 'ERROR'; action.className = 'exec-bar-action bear'; }
      if (det)    det.textContent = (data.msg || 'Signal fetch failed') + ' — retrying...';
      const lastTs = document.getElementById('lastTs');
      if (lastTs && _isSwitching) lastTs.textContent = 'Retrying...';
      return;
    }

    if (data.type === 'update') handleUpdate(data);
  };

  ws.onclose = () => {
    document.getElementById('overlay').classList.remove('hide');
    document.querySelector('.overlay-txt').textContent = 'RECONNECTING...';
    const delay = _wsRetryDelay;
    setTimeout(connect, delay);
    _wsRetryDelay = Math.min(_wsRetryDelay * 2, WS_MAX_DELAY);
  };
}

function handleUpdate(data) {
  document.getElementById('overlay').classList.add('hide');

  // Discard stale updates from a previous symbol — only if symbol field is present AND wrong.
  // Do NOT require _currentSymbol to be truthy: if it's somehow empty, let updates through.
  if (data.symbol && data.symbol !== _currentSymbol) {
    return;
  }

  // First matching update after a symbol switch — clear the switching flag
  if (_isSwitching) {
    _isSwitching = false;
    clearTimeout(_switchTimeout);
  }

  // Sync server uptime so page refreshes don't reset to 00:00
  if (data.uptime != null) _uptimeSec = data.uptime;

  // Header stats
  const priceEl = document.getElementById('hdrPrice');
  if (data.price && data.price > 0) {
    priceEl.textContent = '$' + fmt(data.price);
    flash(priceEl);
  }
  setEl('hdrCycle', '#' + data.cycle);
  if (_tradeMode === 'paper') {
    renderHeaderBalance(data);
  } else if (_sessionConnected && _sessionAccountBalance != null) {
    updateBalance(_sessionAccountBalance, 'account');
  } else if (data.balance != null) {
    updateBalance(data.balance, 'live');
  }
  if ('position' in data)   updatePosition(data.position);
  updateExecutionHelp(data);
  updateRulesPanel(data);
  syncMobileMetrics();

  // Timestamp
  const d = new Date(data.ts);
  document.getElementById('lastTs').textContent =
    d.toLocaleTimeString('en-US',{hour12:false});

  // Signals
  if (data.signals) {
    Object.keys(data.signals).forEach(k => updateSignalCard(k, data.signals[k]));
  }

  // Decision
  if (data.decision) updateDecision(data.decision);

  // Execution
  if (data.execution) {
    updateExecution(data.execution);
  }

  // History
  updateLog(data.history);

  // Ticker
  buildTicker(data);

  // Settings panel risk config
  if (data.risk_config) {
    const rc = data.risk_config;
    const pct = document.getElementById('mRiskPct');
    if (pct) pct.textContent = rc.size_pct + '%';
    const cd  = document.getElementById('mRiskLev');
    if (cd)  cd.textContent  = `${rc.daily_trades || 0}/${rc.max_daily || 10} opens · ${rc.cooldown_secs || 300}s cooldown`;
    syncModeLabels(data);
  }
}

connect();


// ══════════════════════════════════════════════════════
// LLM REASONING TOGGLE
// ══════════════════════════════════════════════════════
function toggleReasoning(hdr) {
  const body  = document.getElementById('reasoningBody');
  const arrow = document.getElementById('reasoningArrow');
  if (!body) return;
  const open = body.classList.toggle('expanded');
  if (arrow) arrow.style.transform = open ? '' : 'rotate(-90deg)';
}


// ══════════════════════════════════════════════════════
// UPTIME CLOCK
// ══════════════════════════════════════════════════════
let _uptimeSec = 0;
setInterval(() => {
  _uptimeSec++;
  const h = String(Math.floor(_uptimeSec/3600)).padStart(2,'0');
  const m = String(Math.floor((_uptimeSec%3600)/60)).padStart(2,'0');
  const s = String(_uptimeSec%60).padStart(2,'0');
  const el = document.getElementById('uptime');
  if (el) el.textContent = `${h}:${m}:${s}`;
}, 1000);


// ══════════════════════════════════════════════════════
// UTILITIES
// ══════════════════════════════════════════════════════
function fmt(n) {
  if (n == null || isNaN(n)) return '—';
  const num = Number(n);
  if (!isFinite(num)) return '—';
  const abs = Math.abs(num);
  const dp = abs === 0        ? 2
           : abs >= 1000      ? 2
           : abs >= 10        ? 3
           : abs >= 1         ? 4
           : abs >= 0.1       ? 5
           : abs >= 0.01      ? 6
           : abs >= 0.001     ? 7
           : abs >= 0.00001   ? 8
           :                    10;
  return num.toLocaleString('en-US',{minimumFractionDigits:dp,maximumFractionDigits:dp});
}

function setEl(id, txt) {
  const el = document.getElementById(id);
  if (el) { el.textContent = txt; flash(el); }
}

function flash(el) {
  if (!el) return;
  el.classList.remove('flash');
  void el.offsetWidth;
  el.classList.add('flash');
}

let _twTimer = null;
function typeWriter(id, text, speed=6) {
  const el = document.getElementById(id);
  if (!el) return;
  if (_twTimer) clearInterval(_twTimer);
  if ((text || '').length > 260) {
    el.textContent = text;
    return;
  }
  el.innerHTML = '';
  let i = 0;
  _twTimer = setInterval(() => {
    if (i < text.length) {
      el.innerHTML = text.slice(0,++i) + '<span class="cursor"></span>';
    } else {
      clearInterval(_twTimer);
      el.textContent = text;
    }
  }, speed);
}


// ══════════════════════════════════════════════════════
// BALANCE
// ══════════════════════════════════════════════════════
let _manualBudget = 0;

function toggleBudgetPopover() {
  const pop  = document.getElementById('budgetPop');
  const chip = document.getElementById('balanceChip');
  if (pop.style.display === 'none') {
    const r = chip.getBoundingClientRect();
    pop.style.top  = (r.bottom + 6) + 'px';
    pop.style.left = Math.max(4, r.right - 220) + 'px';
    pop.style.display = 'block';
    document.getElementById('budgetInput').value = _manualBudget || '';
    document.getElementById('budgetInput').focus();
  } else {
    pop.style.display = 'none';
  }
}

async function applyBudget() {
  const v = parseFloat(document.getElementById('budgetInput').value) || 0;
  document.getElementById('budgetPop').style.display = 'none';
  await setPaperBudget(v);
}

async function setPaperBudget(v) {
  const endpoint = (_operatorMode && _operatorToken) ? '/api/set-budget' : '/api/session/paper-budget';
  try {
    const data = await fetchJson(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({budget: v}),
    });
    if (!data?.ok) {
      if (isOperatorUnlockMessage(data?.error)) {
        showOperatorUnlock('Changing the paper budget requires the operator token.');
      } else {
        showMToast(data?.error || 'Failed to set budget');
      }
      return;
    }
    _manualBudget = v;
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    if (v > 0) {
      rememberPaperEquity(data.balance != null ? data.balance : v);
    } else {
      clearStoredPaperEquity();
    }
    renderHeaderBalance(data);
    showMToast(v > 0 ? `Budget set: $${v}` : 'Budget cleared');
  } catch(e) {
    if (isOperatorUnlockMessage(e.message)) {
      showOperatorUnlock('Changing the paper budget requires the operator token.');
    } else {
      showMToast('Failed to set budget');
    }
    console.error('set-budget failed', e);
  }
}

async function resetPaperAccount() {
  if (!getUiState().isPaper) {
    showMToast('Switch to Paper mode before resetting the paper account');
    return;
  }
  if (!confirm('Reset this paper account? This clears this session paper trade list and returns equity to the paper budget.')) return;
  try {
    const data = await fetchJson('/api/session/paper-reset', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({budget: _manualBudget || 0}),
    }, 12000);
    if (!data?.ok) {
      showMToast(data?.error || 'Paper reset failed');
      return;
    }
    if (data.session) updateSessionInfo(data.session, data.session.scope);
    rememberPaperEquity(data.balance || data.session?.paper_equity || 10000);
    renderHeaderBalance({session: data.session, balance: data.balance});
    renderTrades([]);
    showMToast('Paper account reset');
  } catch(e) {
    showMToast(e.name === 'AbortError' ? 'Paper reset timed out' : (e.message || 'Paper reset failed'));
  }
}

// Close popover on outside click
document.addEventListener('click', e => {
  const pop  = document.getElementById('budgetPop');
  const chip = document.getElementById('balanceChip');
  if (pop && pop.style.display !== 'none' && !pop.contains(e.target) && !chip.contains(e.target)) {
    pop.style.display = 'none';
  }
});

// Allow Enter key in budget input
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('budgetPop')?.style.display !== 'none') {
    applyBudget();
  }
});

function updateBalanceLabel(source = _balanceSource) {
  const lbl = document.getElementById('balanceLabel');
  if (!lbl) return;
  const ui = getUiState();
  if (ui.isLive && !ui.connected) {
    lbl.textContent = 'Live Balance';
    return;
  }
  lbl.textContent = source === 'account' ? 'Bitget Balance' : source === 'live' ? 'Balance' : 'Paper Equity';
}

function updateBalance(bal, source = 'auto') {
  if (bal == null) return;
  const el = document.getElementById('hdrBalance');
  if (!el) return;
  const ui = getUiState();
  if (source === 'auto') {
    source = (ui.sessionConnected && !_operatorToken) ? 'account' : 'paper';
  }
  if (ui.isPaper && source === 'account') {
    renderHeaderBalance();
    return;
  }
  if (ui.isLive && source === 'paper' && !ui.sessionConnected) {
    _balanceSource = 'live_pending';
    updateBalanceLabel('live_pending');
    el.textContent = '$—';
    el.style.color = 'var(--txt3)';
    syncAccountHeaderState();
    return;
  }
  _balanceSource = source;
  updateBalanceLabel(source);
  if (!bal || bal === 0) {
    el.textContent = source === 'account' ? '$0.00' : 'N/A';
    el.style.color = source === 'account' ? 'var(--teal)' : 'var(--txt3)';
  } else {
    const fmt = '$' + Number(bal).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    el.textContent = source === 'paper' && _manualBudget > 0 && bal <= _manualBudget ? fmt + ' (budget)' : fmt;
    el.style.color = 'var(--teal)';
    flash(el);
  }
  syncAccountHeaderState();
  if (ui.isPaper) renderHeaderBalance();
}


// ══════════════════════════════════════════════════════
// EXECUTION BAR — cooldown display
// ══════════════════════════════════════════════════════
let _cooldownTotal = 300;  // seconds, matches server default
let _cooldownTimer = null;
let _cooldownRemaining = 0;

function startCooldownDisplay(remainingStr) {
  // Parse "cooldown — 285s remaining"
  const match = (remainingStr || '').match(/(\d+)s/);
  if (!match) return;
  _cooldownRemaining = parseInt(match[1]);
  _cooldownTotal     = Math.max(_cooldownTotal, _cooldownRemaining);

  if (_cooldownTimer) clearInterval(_cooldownTimer);
  _showCooldown(true);

  _cooldownTimer = setInterval(() => {
    _cooldownRemaining = Math.max(0, _cooldownRemaining - 1);
    const pct = ((_cooldownTotal - _cooldownRemaining) / _cooldownTotal * 100).toFixed(1);
    const fill = document.getElementById('cooldownFill');
    const det  = document.getElementById('execDetail');
    if (fill) fill.style.width = pct + '%';
    if (det)  det.textContent  = `cooldown — ${_cooldownRemaining}s`;
    if (_cooldownRemaining === 0) {
      clearInterval(_cooldownTimer);
      _showCooldown(false);
    }
  }, 1000);
}

function _showCooldown(on) {
  const wrap   = document.getElementById('cooldownWrap');
  const normal = document.getElementById('execDetailNormal');
  if (wrap)   wrap.style.display   = on ? '' : 'none';
  if (normal) normal.style.display = on ? 'none' : '';
}

// ══════════════════════════════════════════════════════
// MOBILE: UPTIME SYNC
// ══════════════════════════════════════════════════════
setInterval(() => {
  const el = document.getElementById('uptime');
  const mel = document.getElementById('mUptime');
  if (el && mel) mel.textContent = el.textContent;
}, 1000);


// ══════════════════════════════════════════════════════
// MOBILE: TAB NAVIGATION
// ══════════════════════════════════════════════════════
let _mActiveTab = 'overview';

function switchMobileTab(tab) {
  if (window.innerWidth > 767) return;

  _mActiveTab = tab;
  const app = document.getElementById('app');

  if (tab === 'settings') {
    document.getElementById('mtab-settings')?.classList.add('active');
    document.getElementById('tab-settings')?.classList.add('active');
    app.removeAttribute('data-active-tab');
    populateMSettings();
  } else {
    document.getElementById('mtab-settings')?.classList.remove('active');
    document.getElementById('tab-settings')?.classList.remove('active');
    app.setAttribute('data-active-tab', tab);
  }

  document.querySelectorAll('.m-tab').forEach(btn => {
    const t = btn.id.replace('mtab-', '');
    btn.classList.toggle('active', t === tab);
    btn.setAttribute('aria-selected', t === tab ? 'true' : 'false');
  });

  if (tab === 'history') {
    loadTradeHistory();
  }
}


// ══════════════════════════════════════════════════════
// MOBILE: METRICS ROW SYNC
// ══════════════════════════════════════════════════════
function syncMobileMetrics() {
  const hdrBal = document.getElementById('hdrBalance');
  const hdrPos = document.getElementById('hdrPosVal');
  const hdrCyc = document.getElementById('hdrCycle');
  const hdrPrc = document.getElementById('hdrPrice');

  const mBal = document.getElementById('mBalance');
  const mPos = document.getElementById('mPos');
  const mCyc = document.getElementById('mCycle');
  const mPrc = document.getElementById('mPrice');

  if (hdrBal && mBal) mBal.textContent = hdrBal.textContent;
  if (hdrPos && mPos) {
    mPos.textContent = hdrPos.textContent;
    mPos.className = 'mv ' + (
      hdrPos.textContent === 'LONG'  ? 'bull' :
      hdrPos.textContent === 'SHORT' ? 'bear' : ''
    );
  }
  if (hdrCyc && mCyc) mCyc.textContent = hdrCyc.textContent;
  if (hdrPrc && mPrc) mPrc.textContent = hdrPrc.textContent;
}


// ══════════════════════════════════════════════════════
// MOBILE: CLOSE-POSITION BOTTOM SHEET
// ══════════════════════════════════════════════════════
let _posSnapshot = null;

function updatePosition(pos) {
  const badge  = document.getElementById('posBadge');
  const rows   = document.getElementById('posRows');
  const hdrVal = document.getElementById('hdrPosVal');
  const closeBtn = document.getElementById('posCloseBtn');

  if (!pos || !pos.holdSide) {
    if (badge)    { badge.className = 'pos-badge flat'; badge.textContent = 'FLAT'; }
    if (hdrVal)   { hdrVal.textContent = 'FLAT'; hdrVal.style.color = 'var(--txt3)'; }
    if (rows)     rows.innerHTML = '<div class="pos-empty" style="grid-column:1/-1">No open position</div>';
    if (closeBtn) closeBtn.disabled = true;
    _posSnapshot = null;
    updateOrderActionControls();
    syncMobileMetrics();
    return;
  }

  _posSnapshot = pos;
  const side   = pos.holdSide.toLowerCase();
  const isBull = side === 'long';
  if (badge)  { badge.className = `pos-badge ${isBull ? 'long' : 'short'}`; badge.textContent = side.toUpperCase(); }
  if (hdrVal) { hdrVal.textContent = side.toUpperCase(); hdrVal.style.color = isBull ? 'var(--bull)' : 'var(--bear)'; }

  const base    = (pos.symbol || 'BTCUSDT').replace('USDT','');
  const size    = parseFloat(pos.total || pos.available || 0);
  const entry   = parseFloat(pos.entryPrice || 0);
  const openedAt = pos.openedAt ? new Date(pos.openedAt).toLocaleTimeString('en-US',{hour12:false}) : '—';

  if (rows) rows.innerHTML = `
    <div class="pos-row"><span class="pos-key">Side</span><span class="pos-val ${isBull?'bull':'bear'}">${side.toUpperCase()}</span></div>
    <div class="pos-row"><span class="pos-key">Size</span><span class="pos-val">${size} ${base}</span></div>
    ${entry > 0 ? `<div class="pos-row"><span class="pos-key">Entry</span><span class="pos-val">$${fmt(entry)}</span></div>` : ''}
    <div class="pos-row"><span class="pos-key">Opened</span><span class="pos-val">${openedAt}</span></div>
  `;
  updateOrderActionControls();
  syncMobileMetrics();
}

function updateOrderActionControls() {
  const cancelBtn = document.getElementById('cancelOrdersBtn');
  const closeBtn = document.getElementById('posCloseBtn');
  const ui = getUiState();
  const canManageExchange = ui.isLive && (ui.sessionConnected || ui.operatorConnected);
  const canClosePosition = canManageExchange && !!_posSnapshot;
  if (cancelBtn) {
    cancelBtn.disabled = !canManageExchange;
    cancelBtn.title = ui.isPaper
      ? 'Paper mode has no resting exchange orders to cancel.'
      : canManageExchange
      ? 'Cancel unfilled open orders for the selected symbol.'
      : 'Connect your Bitget account before canceling exchange orders.';
  }
  if (closeBtn) {
    closeBtn.disabled = !canClosePosition;
    closeBtn.title = ui.isPaper
      ? 'Paper positions are simulated and close through the paper engine.'
      : !_posSnapshot
      ? 'No open position to close.'
      : canManageExchange
      ? 'Close the current Bitget futures position.'
      : 'Connect your Bitget account before closing positions.';
  }
}

function isPaperUiMode() {
  return getUiState().isPaper;
}

function openCloseSheet() {
  if (!_posSnapshot) return;
  const pos  = _posSnapshot;
  const base = (pos.symbol || 'BTCUSDT').replace('USDT','');
  const size = parseFloat(pos.total || pos.available || 0);
  const entry = parseFloat(pos.entryPrice || 0);

  document.getElementById('sheetSym').textContent   = pos.symbol || '—';
  document.getElementById('sheetSide').textContent  = (pos.holdSide || '—').toUpperCase();
  document.getElementById('sheetSize').textContent  = `${size} ${base}`;
  document.getElementById('sheetEntry').textContent = entry > 0 ? `$${fmt(entry)}` : '—';
  document.getElementById('sheetPnl').textContent   = pos.unrealisedPl != null
    ? `$${fmt(pos.unrealisedPl)}` : '—';

  document.getElementById('sheetBackdrop').classList.add('open');
  document.getElementById('closePosSheet').classList.add('open');
}

function closeSheet() {
  document.getElementById('sheetBackdrop').classList.remove('open');
  document.getElementById('closePosSheet').classList.remove('open');
}

async function confirmClosePosition() {
  closeSheet();
  const ui = getUiState();
  if (ui.isPaper) {
    showMToast('Paper mode is simulated; exchange close orders are only available in Live Account mode.');
    return;
  }
  if (!ui.sessionConnected && !ui.operatorConnected) {
    showMToast('Connect your Bitget account before closing positions.');
    return;
  }
  if (!_posSnapshot) {
    showMToast('No open position to close.');
    return;
  }
  const endpoint = (ui.operatorConnected && !_sessionConnected) ? '/api/close-position' : '/api/session/close-position';
  try {
    const data = await fetchJson(endpoint, {method: 'POST'}, 20000);
    if (!data.ok && _operatorMode && isOperatorUnlockMessage(data.error)) {
      showOperatorUnlock('Closing an operator position requires the operator token.');
    } else {
      showMToast(data.ok ? 'Position closed' : (data.error || 'Close failed'));
      if (data.position !== undefined) updatePosition(data.position);
    }
  } catch(e) {
    if (_operatorMode && isOperatorUnlockMessage(e.message)) {
      showOperatorUnlock('Closing an operator position requires the operator token.');
    } else {
      showMToast(e.name === 'AbortError' ? 'Close request timed out' : 'Request failed');
    }
  }
}

async function confirmCancelOrders() {
  const symbol = formatSymbol(_currentSymbol || 'BTCUSDT');
  const ui = getUiState();
  if (ui.isPaper) {
    showMToast('Paper mode has no resting exchange orders to cancel.');
    return;
  }
  if (!ui.sessionConnected && !ui.operatorConnected) {
    showMToast('Connect your Bitget account before canceling exchange orders.');
    return;
  }
  if (!confirm(`Cancel all open orders for ${symbol}? This does not close an active position.`)) return;
  const endpoint = (ui.operatorConnected && !_sessionConnected) ? '/api/cancel-orders' : '/api/session/cancel-orders';
  try {
    const data = await fetchJson(endpoint, {method: 'POST'}, 20000);
    if (!data.ok && _operatorMode && isOperatorUnlockMessage(data.error)) {
      showOperatorUnlock('Canceling operator orders requires the operator token.');
      return;
    }
    showMToast(data.ok ? (data.detail || 'Open orders canceled') : (data.error || data.detail || 'Cancel failed'));
  } catch(e) {
    if (_operatorMode && isOperatorUnlockMessage(e.message)) {
      showOperatorUnlock('Canceling operator orders requires the operator token.');
    } else {
      showMToast(e.name === 'AbortError' ? 'Cancel request timed out' : (e.message || 'Cancel failed'));
    }
  }
}


// ══════════════════════════════════════════════════════
// MOBILE: SETTINGS PANEL
// ══════════════════════════════════════════════════════
function populateMSettings() {
  const ui = getUiState(_initPayload || {});
  const connected = ui.connected;
  const displaySym = formatSymbol(_currentSymbol);

  const dot  = document.getElementById('mStatusDot');
  const txt  = document.getElementById('mStatusTxt');
  const symEl = document.getElementById('mStatusSym');
  const discBtn = document.getElementById('mDisconnectBtn');

  if (dot)    { dot.className = 'm-status-dot ' + (connected ? 'live' : 'off'); }
  if (txt)    txt.textContent = connected ? 'Connected' : 'Not connected';
  if (symEl)  symEl.textContent = connected ? displaySym : '—';
  if (discBtn) discBtn.style.display = connected ? '' : 'none';

  const mode  = document.getElementById('footerMode')?.textContent || '—';
  const mMode = document.getElementById('mRiskMode');
  if (mMode) mMode.textContent = mode;
  const eMode = document.getElementById('evidenceMode');
  if (eMode) eMode.textContent = mode || 'Paper';
  updateSessionInfo();
  updateLiveReadinessUI();

  const bal = parseFloat(document.getElementById('hdrBalance')?.textContent?.replace(/[^0-9.]/g,'')) || 0;
  const mBudgetInput = document.getElementById('mBudgetInput');
  if (mBudgetInput && bal > 0) mBudgetInput.placeholder = `Current: $${bal.toFixed(0)}`;

  // Pass the raw symbol string (e.g. "BTCUSDT"), not the formatted display
  buildMSymGrid(_currentSymbol);
}

function buildMSymGrid(activeSym) {
  // activeSym must be the raw symbol string e.g. "BTCUSDT", not the display string
  const grid = document.getElementById('mSymGrid');
  if (!grid) return;
  const top12 = _PAIRS.slice(0, 12);
  grid.innerHTML = top12.map(([s, b]) =>
    `<button class="m-sym-pill${s === activeSym ? ' active' : ''}"
      onclick="mSwitchSymbol('${s}')">${b}</button>`
  ).join('');
}

function mSwitchSymbol(sym) {
  switchSymbol({ dataset: { s: sym } });
  document.querySelectorAll('.m-sym-pill').forEach(p => {
    p.classList.toggle('active', p.textContent === sym.replace('USDT',''));
  });
  showMToast(`Switching to ${sym.replace('USDT','')}...`);
  setTimeout(() => switchMobileTab('overview'), 400);
}

async function mSetBudget() {
  const input = document.getElementById('mBudgetInput');
  const v = parseFloat(input.value) || 0;
  input.value = '';
  await setPaperBudget(v);
}


// ══════════════════════════════════════════════════════
// MOBILE: TOAST
// ══════════════════════════════════════════════════════
let _toastTimer = null;
function showMToast(msg, dur = 2800) {
  const el = document.getElementById('mToast');
  if (!el) return;
  if (_toastTimer) clearTimeout(_toastTimer);
  el.textContent = msg;
  el.classList.add('show');
  _toastTimer = setTimeout(() => el.classList.remove('show'), dur);
}

function auditUrl(path) {
  return new URL(path, window.location.origin).toString();
}

function stampAuditChecked() {
  const el = document.getElementById('auditLastChecked');
  if (!el) return;
  el.textContent = new Date().toLocaleTimeString('en-US', {hour12:false});
}

function openAuditPath(path) {
  stampAuditChecked();
  window.open(auditUrl(path), '_blank', 'noopener');
}

async function copyAuditPath(path) {
  const url = auditUrl(path);
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const input = document.createElement('input');
      input.value = url;
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      input.remove();
    }
    stampAuditChecked();
    showMToast('Audit link copied');
  } catch(e) {
    showMToast('Could not copy link');
  }
}


// ══════════════════════════════════════════════════════
// MOBILE: SWIPE-DOWN SHEET DISMISS
// ══════════════════════════════════════════════════════
(function() {
  let _sy = 0;
  const sheet = () => document.getElementById('closePosSheet');
  document.addEventListener('touchstart', e => {
    if (sheet()?.classList.contains('open')) _sy = e.touches[0].clientY;
  }, {passive:true});
  document.addEventListener('touchend', e => {
    if (!sheet()?.classList.contains('open')) return;
    if (e.changedTouches[0].clientY - _sy > 60) closeSheet();
  }, {passive:true});
})();


// ══════════════════════════════════════════════════════
// MOBILE: INIT — set default tab on mobile
// ══════════════════════════════════════════════════════
(function initMobile() {
  if (window.innerWidth > 767) return;
  const app = document.getElementById('app');
  app.setAttribute('data-active-tab', 'overview');
  document.getElementById('tab-settings')?.classList.remove('active');
})();


// Populate connect form select from _PAIRS (called after _PAIRS is defined above)
rebuildConnectSelect();
