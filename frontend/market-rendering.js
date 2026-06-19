/* Market, signal, decision, and execution rendering for BitAgent. */

// ══════════════════════════════════════════════════════
// SIGNAL CARDS  (initial render)
// ══════════════════════════════════════════════════════
const SIG_META = {
  technical:  { label:'Technical',  icon:'◈', keys:['rsi','stoch_rsi','trend'] },
  sentiment:  { label:'Sentiment',  icon:'◉', keys:['fear_greed','long_short_ratio','funding_rate'] },
  macro:      { label:'Market Macro', icon:'◐', keys:['btc_dominance','mcap_change_24h'] },
  momentum:   { label:'Momentum',   icon:'◆', keys:['change_24h_pct','range_position'] },
  depth:      { label:'Mkt Depth',  icon:'◇', keys:['imbalance','spread_pct'] },
  volatility: { label:'Volatility', icon:'◎', keys:['regime','atr_pct'] },
};
const SIG_KEYS = Object.keys(SIG_META);

function buildSignalCards() {
  const row = document.getElementById('signalsRow');
  row.innerHTML = SIG_KEYS.map(k => {
    const m = SIG_META[k];
    return `<div class="sig-card neutral loading" id="sc-${k}">
      <div class="scanning"></div>
      <div class="sig-top">
        <span class="sig-name">${m.icon} ${m.label}</span>
        <span class="sig-badge neutral" id="sb-${k}">—</span>
      </div>
      <div class="sig-value neutral" id="sv-${k}">—</div>
      <div class="sig-metrics" id="sm-${k}"></div>
      <div class="sig-bar-wrap"><div class="sig-bar neutral" id="sbar-${k}"></div></div>
    </div>`;
  }).join('');
}
buildSignalCards();

function fmtMetricVal(k, v) {
  if (k === 'funding_rate')    return v + '%';
  if (k === 'change_24h_pct')  return v + '%';
  if (k === 'range_position')  return v + '%';
  if (k === 'atr_pct')         return v + '%';
  if (k === 'spread_pct')      return v + '%';
  if (k === 'btc_dominance')   return v + '%';
  if (k === 'mcap_change_24h') return (v >= 0 ? '+' : '') + v + '%';
  if (k === 'long_short_ratio') return String(v);
  if (typeof v === 'number')   return String(v);
  return String(v).slice(0, 14);
}

function metricLabel(k) {
  const labels = {
    btc_dominance: 'BTC dom market',
    mcap_change_24h: 'Crypto mcap 24h',
  };
  return labels[k] || k.replace(/_/g, ' ');
}

function signalStrength(type, sig, data) {
  // All values derived from real signal data — no random numbers
  let raw = 50;
  switch (type) {
    case 'technical':
      // RSI distance from 50 = signal conviction
      raw = Math.abs((data.rsi || 50) - 50) * 2;
      break;
    case 'sentiment':
      // funding rate magnitude (0.0001 = normal, 0.001 = extreme)
      raw = Math.min(Math.abs(data.funding_rate || 0) / 0.05 * 100, 100);
      break;
    case 'momentum':
      // 24h price change magnitude (3% move = strong)
      raw = Math.min(Math.abs(data.change_24h_pct || 0) / 3 * 100, 100);
      break;
    case 'depth':
      // order book imbalance magnitude (0.5 = very skewed)
      raw = Math.min(Math.abs(data.imbalance || 0) / 0.5 * 100, 100);
      break;
    case 'volatility':
      raw = Math.min((data.atr_pct || 0) / 2 * 100, 100);
      break;
    case 'macro':
      // market cap move magnitude (2% = strong, 5% = extreme)
      raw = Math.min(Math.abs(data.mcap_change_24h || 0) / 5 * 100, 100);
      break;
  }
  return Math.max(15, Math.min(95, raw));
}

function updateSignalCard(k, data) {
  const sig   = (data.signal || 'NEUTRAL').toUpperCase();
  const cls   = sig === 'BULLISH' ? 'bull' : sig === 'BEARISH' ? 'bear' : 'neutral';
  const card  = document.getElementById(`sc-${k}`);
  const badge = document.getElementById(`sb-${k}`);
  const val   = document.getElementById(`sv-${k}`);
  const met   = document.getElementById(`sm-${k}`);
  const bar   = document.getElementById(`sbar-${k}`);
  if (!card) return;

  card.classList.remove('loading');
  card.className = `sig-card ${cls}`;
  badge.className = `sig-badge ${cls}`;
  badge.textContent = sig;
  val.className  = `sig-value ${cls}`;
  val.textContent = sig;

  const keys = SIG_META[k].keys;
  met.innerHTML = keys.filter(m => data[m] !== undefined).map(m =>
    `<div class="sig-metric"><span class="mk">${metricLabel(m)}</span>
     <span class="mv">${fmtMetricVal(m, data[m])}</span></div>`
  ).join('');

  const strength = signalStrength(k, sig, data);
  bar.style.width = strength + '%';
  bar.className   = `sig-bar ${cls}`;
  flash(card);
}


// ══════════════════════════════════════════════════════
// TICKER TAPE
// ══════════════════════════════════════════════════════
function buildTicker(data) {
  const base  = (data.symbol || _currentSymbol).replace('USDT', '');
  const pair  = `${base}/USDT`;
  const items = [
    [pair,         `$${fmt(data.price)}`,   'teal'],
    ['24h Change', `${data.signals?.momentum?.change_24h_pct ?? '—'}%`,
        (data.signals?.momentum?.change_24h_pct ?? 0) >= 0 ? 'bull' : 'bear'],
    ['Funding',    `${data.signals?.sentiment?.funding_rate ?? '—'}%`, ''],
    ['Open Int',   `${data.signals?.momentum?.open_interest ?? '—'} ${base}`, ''],
    ['Cycle',      `#${data.cycle}`, ''],
    ['Sim P&L',    `${data.sim_pnl >= 0 ? '+' : ''}${data.sim_pnl}%`,
        data.sim_pnl >= 0 ? 'bull' : 'bear'],
    ['Signal',     data.decision?.direction ?? '—',
        data.decision?.direction === 'LONG' ? 'bull' : data.decision?.direction === 'SHORT' ? 'bear' : ''],
    ['Confidence', `${data.decision?.confidence ?? '—'}%`, ''],
    ['RSI',        `${data.signals?.technical?.rsi ?? '—'}`, ''],
    ['ATR',        `${data.signals?.volatility?.atr_pct ?? '—'}%`, ''],
    ['Exchange',   'Bitget Futures', 'teal'],
  ];
  const html = items.map(([l,v,c]) =>
    `<span class="t-item"><span class="lbl">${l}</span><span class="val ${c}">${v}</span></span>`
  ).join('');
  const track = document.getElementById('tickerTrack');
  track.innerHTML = html + html; // duplicate for seamless loop
}


// ══════════════════════════════════════════════════════
// DECISION UPDATE
// ══════════════════════════════════════════════════════
const GAUGE_LEN = 353.4;
let _reasoningDebounce = null;

function updateDecision(d) {
  const dir = (d.direction || 'FLAT').toUpperCase();
  const cls = dir === 'LONG' ? 'bull' : dir === 'SHORT' ? 'bear' : 'neutral';

  const wrap  = document.getElementById('dirWrap');
  const arrow = document.getElementById('dirArrow');
  const name  = document.getElementById('dirName');
  wrap.className  = `dir-arrow-wrap ${cls}`;
  arrow.className = `dir-arrow ${cls}`;
  name.className  = `dir-name ${cls}`;
  arrow.textContent = dir === 'LONG' ? '▲' : dir === 'SHORT' ? '▼' : '—';
  name.textContent  = dir;

  // Gauge — 270° speedometer arc
  const pct    = d.confidence || 0;
  const offset = GAUGE_LEN - (pct / 100 * GAUGE_LEN);
  const arcColor = pct === 0 ? '#1A2030'
                 : pct >= 70 ? '#0ECB81'
                 : pct >= 50 ? '#F0B90B'
                 : '#F6465D';
  const arc    = document.getElementById('gaugeArc');
  const glow   = document.getElementById('gaugeGlow');
  const dot    = document.getElementById('gaugeDot');
  const gPct   = document.getElementById('gaugePct');
  arc.style.strokeDashoffset  = offset;
  arc.style.stroke             = arcColor;
  if (glow) { glow.style.strokeDashoffset = offset; glow.style.stroke = arcColor; }
  if (dot) {
    // Arc: center(100,100) r=75, starts at 135° clockwise, spans 270°
    // Tip angle = 135° + (pct/100)*270°
    const tipAngle = (135 + pct * 2.7) * Math.PI / 180;
    dot.setAttribute('cx', (100 + 75 * Math.cos(tipAngle)).toFixed(2));
    dot.setAttribute('cy', (100 + 75 * Math.sin(tipAngle)).toFixed(2));
    dot.style.opacity = pct > 0 ? '1' : '0';
    dot.style.fill    = arcColor;
  }
  gPct.textContent = pct === 0 ? '—' : pct + '%';
  const announcer = document.getElementById('a11yAnnouncer');
  if (announcer) announcer.textContent = `Agent decision: ${dir}, confidence ${pct}%`;

  // Params — only show SL/TP/Entry when there is an active directional signal
  if (dir === 'FLAT') {
    setEl('pEntry', '—'); setEl('pSL', '—'); setEl('pTP', '—');
    setEl('pSize', '—');
  } else {
    setEl('pEntry', '$' + fmt(d.entry_price));
    setEl('pSL',    '$' + fmt(d.stop_loss));
    setEl('pTP',    '$' + fmt(d.take_profit));
    setEl('pSize',  (d.size_pct || 0) + '%');
    // SL is above entry for SHORT, below for LONG — label accordingly
    const slEl = document.getElementById('pSL');
    if (slEl) slEl.className = 'param-val bear';
    const tpEl = document.getElementById('pTP');
    if (tpEl) tpEl.className = 'param-val bull';
  }

  // Reasoning
  clearTimeout(_reasoningDebounce);
  _reasoningDebounce = setTimeout(() => {
    typeWriter('reasoningTxt', d.reasoning || '—');
  }, 120);

  // Risk note
  const rn = document.getElementById('riskNote');
  if (d.risk_note) {
    rn.textContent = '⚠ ' + d.risk_note;
    rn.className   = 'risk-note show';
  } else {
    rn.className   = 'risk-note';
  }
}

function updateExecution(exec) {
  if (!exec) return;
  const bar    = document.getElementById('execBar');
  const dot    = document.getElementById('execDot');
  const action = document.getElementById('execAction');
  const detNorm= document.getElementById('execDetailNormal');
  const badge  = document.getElementById('execBadge');
  const detail = exec.detail || '—';
  const isCooldown = detail.includes('cooldown');

  if (exec.executed) {
    const a = (exec.action || '').toUpperCase();
    const isBear = a.includes('SHORT');
    const isClose = a.includes('CLOSE');
    const isPaper = a.includes('PAPER');
    dot.className      = 'exec-bar-dot on';
    action.className   = isBear ? 'exec-bar-action bear' : isClose ? 'exec-bar-action off' : 'exec-bar-action on';
    action.textContent = a || 'EXECUTED';
    bar.classList.remove('fired');
    void bar.offsetWidth;
    bar.classList.add('fired');
    const base = (exec.detail||'').match(/([A-Z]+)\s@/)?.[1] || 'ASSET';
    badge.textContent       = exec.size ? `${exec.size} ${base}` : isPaper ? 'PAPER FILL' : 'ORDER SENT';
    badge.style.color       = 'var(--bull)';
    badge.style.borderColor = 'rgba(14,203,129,0.3)';
    _showCooldown(false);
    if (detNorm) detNorm.textContent = detail;
    if (_cooldownTimer) clearInterval(_cooldownTimer);
    // Auto-refresh trade history if tab is open
    if (_activeTab === 'trades') loadTradeHistory();
  } else {
    dot.className      = 'exec-bar-dot off';
    action.className   = 'exec-bar-action off';
    action.textContent = 'SKIP';
    bar.classList.remove('fired');
    badge.textContent       = '—';
    badge.style.color       = '';
    badge.style.borderColor = '';

    if (isCooldown) {
      if (!_isInitPayload) {
        startCooldownDisplay(detail);
      } else {
        _showCooldown(false);
        if (detNorm) detNorm.textContent = detail;
      }
    } else {
      _showCooldown(false);
      if (detNorm) detNorm.textContent = detail;
    }
  }
}

function updateExecutionHelp(data = {}) {
  const mode = data.risk_config?.mode || 'paper';
  const ui = getUiState(data);
  const decision = (data.decision?.direction || 'WAITING').toUpperCase();
  const confidence = data.decision?.confidence;
  const exec = data.execution || {};
  const pos = data.position;
  const account = ui.isPaper ? (data.session_paper_account || data.session?.paper_account || data.account || {}) : (data.account || {});
  const balance = Number(data.balance || 0);
  const equity = ui.paperEquity;
  if (Number.isFinite(equity) && equity > 0 && ui.isPaper) rememberPaperEquity(equity);

  const pill = document.getElementById('modePill');
  if (pill) {
    pill.textContent = ui.isLive ? 'LIVE' : 'PAPER';
    pill.className = ui.isLive ? 'mode-pill live' : 'mode-pill';
  }

  const wallet = document.getElementById('helpWallet');
  if (wallet) {
    const sessionBal = ui.accountBalance != null ? ui.accountBalance : balance;
    const free = Number(account.free_equity);
    const used = Number(account.used_margin);
    wallet.textContent = ui.isLive && ui.sessionConnected
      ? `Bitget balance $${fmt(sessionBal)}`
      : ui.isPaper
        ? Number.isFinite(used) && used > 0
          ? `Equity $${fmt(equity)} · free $${fmt(free)}`
          : `Paper equity $${fmt(equity)}`
        : `Live $${fmt(balance)}`;
    wallet.title = ui.isPaper && Number.isFinite(used) && used > 0
      ? `Used simulated margin: $${fmt(used)}. Equity changes with realized and open P&L.`
      : '';
    wallet.className = ui.isLive ? 'execution-help-val bull' : 'execution-help-val gold';
  }

  const balLbl = document.getElementById('balanceLabel');
  if (balLbl) balLbl.textContent = ui.isLive && ui.sessionConnected ? 'Bitget Balance' : ui.isPaper ? 'Paper Equity' : 'Live Balance';

  const dec = document.getElementById('helpDecision');
  if (dec) {
    dec.textContent = confidence != null ? `${decision} ${confidence}%` : decision;
    dec.className = decision === 'LONG'
      ? 'execution-help-val bull'
      : decision === 'SHORT' ? 'execution-help-val bear' : 'execution-help-val';
  }

  const ex = document.getElementById('helpExecution');
  if (ex) {
    const action = (exec.action || 'WAITING').toUpperCase();
    ex.textContent = exec.executed ? action : `SKIP: ${exec.detail || 'waiting'}`;
    ex.title = exec.detail || action;
    ex.className = exec.executed
      ? (action.includes('SHORT') ? 'execution-help-val bear' : 'execution-help-val bull')
      : 'execution-help-val';
  }

  const p = document.getElementById('helpPosition');
  if (p) {
    const side = (pos?.holdSide || 'flat').toUpperCase();
    const size = pos ? ` ${pos.total || pos.available || ''}` : '';
    p.textContent = pos ? `${side}${size}` : 'FLAT';
    p.className = side === 'LONG'
      ? 'execution-help-val bull'
      : side === 'SHORT' ? 'execution-help-val bear' : 'execution-help-val';
  }

  const realized = document.getElementById('helpRealized');
  if (realized) {
    const val = Number(account.realized || 0);
    realized.textContent = `${val >= 0 ? '+' : '-'}$${fmt(Math.abs(val))}`;
    realized.className = val > 0 ? 'execution-help-val bull'
      : val < 0 ? 'execution-help-val bear' : 'execution-help-val';
  }

  const unrealized = document.getElementById('helpUnrealized');
  if (unrealized) {
    const val = Number(account.unrealized || 0);
    unrealized.textContent = `${val >= 0 ? '+' : '-'}$${fmt(Math.abs(val))}`;
    unrealized.className = val > 0 ? 'execution-help-val bull'
      : val < 0 ? 'execution-help-val bear' : 'execution-help-val';
  }

  const note = document.getElementById('helpNote');
  if (note) {
    if (ui.isLive && ui.sessionConnected) {
      note.innerHTML = '<strong>Connected account:</strong> balance is read from your Bitget futures wallet. Orders still require live unlock, preview, and confirmation.';
    } else if (ui.isPaper) {
      note.innerHTML = '<strong>Paper mode:</strong> futures simulation keeps starting equity visible, tracks used margin/free equity, and changes equity through realized and open P&L only.';
    } else {
      note.innerHTML = '<strong>Live Account mode:</strong> connect your Bitget account, unlock live risk, preview, then confirm before any real order is sent.';
    }
  }
  updateFundingReadiness(data);
  renderHeaderBalance(data);
}

function updateRulesPanel(data = {}) {
  const rc = data.risk_config || {};
  const decision = data.decision || {};
  const execution = data.execution || {};
  const confidence = Number(rc.confidence ?? decision.confidence ?? 0);
  const min = Number(rc.confidence_min || 60);
  const dir = String(rc.decision_direction || decision.direction || 'WAITING').toUpperCase();
  const detail = String(rc.execution_detail || execution.detail || '');
  const detailLc = detail.toLowerCase();
  const cooldownBlocked = !!rc.cooldown_blocked || detailLc.includes('cooldown');
  const dailyBlocked = !!rc.daily_limit_blocked || detailLc.includes('daily limit');
  const orderBlocked = !!rc.order_size_blocked
    || detailLc.includes('budget too small')
    || detailLc.includes('insufficient balance')
    || detailLc.includes('min lot');

  const confEl = document.getElementById('ruleConfidence');
  if (confEl) {
    confEl.textContent = confidence ? `${confidence}% / min ${min}%` : `min ${min}%`;
    confEl.className = confidence >= min ? 'rule-val pass' : confidence > 0 ? 'rule-val fail' : 'rule-val wait';
  }

  const dirEl = document.getElementById('ruleDirection');
  if (dirEl) {
    dirEl.textContent = dir === 'LONG' || dir === 'SHORT' ? `${dir} signal` : dir === 'FLAT' ? 'FLAT / wait' : 'Waiting';
    dirEl.className = dir === 'LONG' ? 'rule-val pass'
      : dir === 'SHORT' ? 'rule-val fail'
      : dir === 'FLAT' ? 'rule-val wait' : 'rule-val wait';
  }

  const cdEl = document.getElementById('ruleCooldown');
  if (cdEl) {
    cdEl.textContent = cooldownBlocked ? detail : `${rc.cooldown_secs || 300}s clear`;
    cdEl.title = detail;
    cdEl.className = cooldownBlocked ? 'rule-val wait' : 'rule-val pass';
  }

  const dailyEl = document.getElementById('ruleDaily');
  if (dailyEl) {
    const used = Number(rc.daily_trades || 0);
    const max = Number(rc.max_daily || 10);
    dailyEl.textContent = dailyBlocked ? `${used}/${max} reached` : `${used}/${max} opens`;
    dailyEl.title = dailyBlocked ? detail : `${max} opens/day`;
    dailyEl.className = dailyBlocked ? 'rule-val wait' : 'rule-val pass';
  }

  const sizeEl = document.getElementById('ruleSize');
  if (sizeEl) {
    sizeEl.textContent = orderBlocked ? detail : `${rc.size_pct || 1}% equity`;
    sizeEl.title = detail;
    sizeEl.className = orderBlocked ? 'rule-val fail' : 'rule-val pass';
  }

  const cycleEl = document.getElementById('ruleCycle');
  if (cycleEl) cycleEl.textContent = `${rc.loop_secs || 60}s`;

  const note = document.getElementById('rulesNote');
  if (note) {
    const action = String(execution.action || '').toUpperCase();
    if (execution.executed) {
      if (action.includes('CLOSE')) {
        note.textContent = `Position closed because the agent moved to FLAT. Action: ${action.replaceAll('_', ' ')}.`;
      } else if (action.includes('PAPER')) {
        note.textContent = `Paper fill recorded after the decision passed the strategy gates. Action: ${action.replaceAll('_', ' ')}.`;
      } else {
        note.textContent = `Live order sent after the decision passed the strategy gates. Action: ${action.replaceAll('_', ' ')}.`;
      }
    } else if (dir === 'FLAT' && confidence > 0 && confidence < min) {
      note.textContent = `Skipped because confidence is below the ${min}% threshold and the agent is staying FLAT.`;
    } else if (detail) {
      note.textContent = `Skipped because: ${detail}`;
    } else {
      note.textContent = 'A trade can execute only when confidence passes the threshold, direction is LONG/SHORT, cooldown is clear, and daily/order limits allow it.';
    }
  }
}
