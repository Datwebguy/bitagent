// Decision and trade history panel.
// Depends on globals from runtime-state.js and api-client.js.

let _activeTab = 'decisions';

function showTab(tab) {
  _activeTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab)
  );
  document.getElementById('logList').style.display = tab === 'decisions' ? '' : 'none';
  document.getElementById('tradeList').style.display = tab === 'trades' ? '' : 'none';
  if (tab === 'decisions') loadDecisionHistory();
  if (tab === 'trades') loadTradeHistory();
}

async function loadDecisionHistory() {
  try {
    const decisions = await fetchJson('/api/decisions?limit=30');
    renderDecisionHistory(decisions);
  } catch (e) {
    showMToast('Could not load decision history');
    console.error('[decisions]', e);
  }
}

function normalizeDecisionRecord(row = {}) {
  const decision = row.decision || row;
  const signals = row.signals || {};
  const price = decision.entry_price
    ?? signals.technical?.price
    ?? signals.momentum?.price
    ?? row.price
    ?? 0;
  return {
    ts: row.ts || decision.ts || '',
    direction: String(decision.direction || row.direction || 'FLAT').toUpperCase(),
    confidence: Number(decision.confidence ?? row.confidence ?? 0),
    price: Number(price || 0),
    executed: !!row.executed,
  };
}

function renderDecisionHistory(rows) {
  const list = document.getElementById('logList');
  const count = document.getElementById('tabCount');
  if (!list) return;

  if (!rows || !rows.length) {
    list.innerHTML = `<div class="log-empty">
      <span style="font-size:22px;opacity:.3">◇</span>
      <span>No decision cycles recorded yet</span>
      <span style="color:var(--txt3)">The first signal cycle usually appears within 60 seconds.</span>
    </div>`;
    if (count && _activeTab === 'decisions') count.textContent = '0 decisions';
    return;
  }

  const normalized = rows.map(normalizeDecisionRecord).reverse();
  list.innerHTML = normalized.map(h => {
    const cls = h.direction === 'LONG' ? 'bull' : h.direction === 'SHORT' ? 'bear' : 'neutral';
    const ts = h.ts ? new Date(h.ts).toLocaleTimeString('en-US', {hour12:false}) : '—';
    const execMark = h.executed
      ? `<span style="color:var(--bull);font-size:9px;font-family:var(--mono)">FILLED</span>`
      : `<span style="color:var(--txt3);font-size:9px">SKIP</span>`;
    return `<div class="log-item">
      <span class="log-time">${ts}</span>
      <span class="log-dir ${cls}">${h.direction}</span>
      <span class="log-price">${h.price > 0 ? '$' + fmt(h.price) : '—'}</span>
      <span class="log-conf">${h.confidence}% ${execMark}</span>
    </div>`;
  }).join('');
  if (count && _activeTab === 'decisions') {
    count.textContent = `${rows.length} decision${rows.length !== 1 ? 's' : ''}`;
  }
}

async function loadTradeHistory() {
  try {
    const trades = await fetchJson('/api/trades?limit=30');
    renderTrades(trades);
  } catch (e) {
    showMToast('Could not load trade history');
    console.error('[trades]', e);
  }
}

function renderTrades(trades) {
  const list = document.getElementById('tradeList');
  const count = document.getElementById('tabCount');
  if (!list) return;

  if (!trades || !trades.length) {
    const rc = _initPayload?.risk_config || {};
    const latest = _initPayload?.latest || {};
    const decision = latest.decision || {};
    const execution = latest.execution || {};
    const detail = execution.detail || rc.execution_detail || 'No fill has passed the strategy gates yet.';
    const confidence = Number(decision.confidence ?? rc.confidence ?? 0);
    const min = Number(rc.confidence_min || 60);
    list.innerHTML = `<div class="log-empty">
      <span style="font-size:22px;opacity:.3">◈</span>
      <span>No executed trades yet</span>
      <span style="color:var(--txt3)">Latest gate: ${detail}${confidence ? ` (${confidence}% / min ${min}%)` : ''}</span>
    </div>`;
    if (count && _activeTab === 'trades') count.textContent = '0 trades';
    return;
  }

  list.innerHTML = trades.map(t => {
    const a = (t.action || '').toUpperCase();
    const cls = a.includes('LONG') ? 'ta-ol' :
                a.includes('SHORT') ? 'ta-os' :
                a.includes('CLOSE') ? 'ta-cl' : 'ta-fail';
    const ts = new Date(t.ts).toLocaleTimeString('en-US', {hour12:false});
    const base = (t.symbol || 'BTCUSDT').replace('USDT', '');
    const sizeStr = t.size ? `${t.size} ${base}` : '—';
    const pnl = Number(t.pnl || 0);
    const hasPnl = t.pnl !== null && t.pnl !== undefined;
    const pnlText = hasPnl ? `${pnl >= 0 ? '+' : '-'}$${fmt(Math.abs(pnl))}` : (t.price ? '$' + fmt(t.price) : '—');
    const pnlCls = hasPnl ? (pnl >= 0 ? 'trade-pnl bull' : 'trade-pnl bear') : 'trade-price';
    const entry = t.entry_price ? '$' + fmt(t.entry_price) : null;
    const exit = t.exit_price ? '$' + fmt(t.exit_price) : null;
    const meta = t.audit || (entry && exit ? `${entry} → ${exit}` : entry ? `Entry ${entry}` : '');
    return `<div class="trade-item">
      <span class="log-time">${ts}</span>
      <span class="trade-action ${cls}">${a.replace('OPEN_','')}</span>
      <span class="trade-size">${sizeStr}</span>
      <span class="${pnlCls}">${pnlText}</span>
      <span class="trade-meta" title="${meta}">${meta}</span>
    </div>`;
  }).join('');

  if (count && _activeTab === 'trades') {
    count.textContent = `${trades.length} trade${trades.length !== 1 ? 's' : ''}`;
  }
}
