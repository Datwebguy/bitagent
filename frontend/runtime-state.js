/* Shared runtime state for BitAgent's classic browser scripts. */

const ENTERED_PLATFORM_KEY = 'bitagent_entered_platform';
const PAPER_EQUITY_KEY = 'bitagent_paper_equity';
let _introSkipped = false;
try {
  const params = new URLSearchParams(window.location.search);
  if (params.get('home') === '1') {
    localStorage.removeItem(ENTERED_PLATFORM_KEY);
    _introSkipped = false;
  } else {
    _introSkipped = localStorage.getItem(ENTERED_PLATFORM_KEY) === '1';
  }
} catch(e) {}
let introRunning  = !_introSkipped;
let _initPayload  = null;
let _isInitPayload = false;
let _isConnected  = false;
let _hasInitialSnapshot = false;
let _authRequired = false;
let _sessionInfo = null;
let _sessionConnected = false;
let _operatorToken = '';
let _lastSessionProposal = null;
let _sessionExecEvents = [];
let _sessionAccountBalance = null;
let _balanceSource = 'paper';
let _operatorMode = false;
let _tradeMode = 'paper';
let _lastPaperEquity = readStoredPaperEquity() || 10000;

try { _operatorToken = sessionStorage.getItem('bitagent_operator_token') || ''; } catch(e) {}
try {
  const params = new URLSearchParams(window.location.search);
  _operatorMode = params.get('operator') === '1' || sessionStorage.getItem('bitagent_operator_mode') === '1';
  if (params.get('operator') === '1') sessionStorage.setItem('bitagent_operator_mode', '1');
} catch(e) {}

function readStoredPaperEquity() {
  try {
    const value = Number(localStorage.getItem(PAPER_EQUITY_KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch(e) {
    return null;
  }
}

function rememberPaperEquity(value) {
  const equity = Number(value);
  if (!Number.isFinite(equity) || equity <= 0) return;
  _lastPaperEquity = equity;
  try { localStorage.setItem(PAPER_EQUITY_KEY, String(equity)); } catch(e) {}
}

function clearStoredPaperEquity() {
  _lastPaperEquity = 10000;
  try { localStorage.removeItem(PAPER_EQUITY_KEY); } catch(e) {}
}

function getUiState(data = {}) {
  const session = data.session || _sessionInfo || {};
  const tradeMode = session.trade_mode === 'live' ? 'live' : _tradeMode === 'live' ? 'live' : 'paper';
  const isLive = tradeMode === 'live';
  const sessionConnected = !!session.credentials_set || !!_sessionConnected;
  const operatorConnected = _operatorMode && !!_operatorToken && !!_isConnected;
  const connected = sessionConnected || operatorConnected;
  const accountBalanceRaw = data.balance_source === 'bitget_futures'
    ? data.balance
    : session.account_balance ?? _sessionAccountBalance;
  const accountBalance = accountBalanceRaw == null ? null : Number(accountBalanceRaw);
  const storedPaper = readStoredPaperEquity();
  const sessionPaper = Number(session.paper_equity ?? session.paper_balance);
  const paperRaw = (Number.isFinite(sessionPaper) && sessionPaper !== 10000 ? sessionPaper : null)
    ?? storedPaper
    ?? (Number.isFinite(sessionPaper) ? sessionPaper : null)
    ?? data.account?.equity
    ?? (_tradeMode === 'paper' && data.balance_source !== 'bitget_futures' ? data.balance : null)
    ?? _lastPaperEquity
    ?? 10000;
  const paperEquity = Number.isFinite(Number(paperRaw)) && Number(paperRaw) > 0
    ? Number(paperRaw)
    : 10000;
  return {
    tradeMode,
    isPaper: !isLive,
    isLive,
    session,
    sessionConnected,
    operatorConnected,
    connected,
    unlocked: !!session.live_unlocked,
    accountBalance: Number.isFinite(accountBalance) ? accountBalance : null,
    paperEquity,
  };
}
