# BitAgent

BitAgent is an AI trading risk cockpit that perceives live market conditions across six signal dimensions, reasons over them with Qwen, and runs an execution loop with explicit risk controls. The public deployment defaults to paper trading for safe, verifiable evaluation; live Bitget futures execution is available only when deliberately enabled by an operator.

BitAgent is not financial advice and does not guarantee profit. Paper mode uses simulated funds; live mode can lose real money.

---

## Architecture

```
Browser (WebSocket) ──► FastAPI Server ──► Bitget REST API
                              │
                         Agent Loop (60s)
                              │
              ┌───────────────┼───────────────┐
          Signals          Reason           Execute
        (6 sources)      (Qwen LLM)    (Paper / Bitget API)
```

**Stack:** Python 3.11 · FastAPI · WebSocket · NumPy · SQLite · Single-file HTML frontend

---

## Signal Pipeline

| Signal | Source | What it measures |
|--------|--------|-----------------|
| **Technical** | 15m candles | RSI(14), Bollinger Bands %B, Stochastic RSI, MACD direction |
| **Sentiment** | Bitget + alternative.me | Funding rate, Fear & Greed index, Long/Short ratio |
| **Macro** | CoinGecko global | BTC dominance, total market cap 24h change |
| **Momentum** | 24h ticker + OI | Price change, range position, open interest |
| **Market Depth** | Order book top 10 | Bid/ask volume imbalance |
| **Volatility** | 1H candles | ATR(14) regime — expanding vs. contracting |

Priority order: Technical → Sentiment → Macro → Momentum → Depth → Volatility. Four or more signals in strong agreement triggers a high-confidence trade; three signals give 60% confidence.

---

## Decision Engine

Without a Qwen API key, the agent runs a transparent rule-based majority vote.  
With `QWEN_API_KEY` set, decisions are handed to `qwen3.6-plus` through the configured Qwen-compatible endpoint. The model receives all six signal payloads and returns a structured JSON decision including entry, stop-loss, take-profit, confidence, and reasoning.

**Minimum confidence to trade:** 60%  
**Mode:** paper by default; set `EXEC_MODE=live` only for controlled live trading  
**Max trade size:** 1% of balance per cycle (configurable)  
**Execution cooldown:** 300 seconds between trades  
**Daily cap:** 10 trades per UTC day

---

## Bitget Account Compatibility

BitAgent supports both Classic and Unified (One-Account) modes:

- **Unified Account** — uses v3 endpoints: `/api/v3/account/assets`, `/api/v3/position/current-position`, `/api/v3/trade/place-order`
- **Classic Account** — uses v2 endpoints: `/api/v2/mix/...`

The server auto-detects the account type on first connection and routes all subsequent calls accordingly.

---

## Risk Management

- Hard stop-loss and take-profit on every order
- Per-asset minimum lot size enforcement
- Disk-persistent position tracking — survives server restarts
- SQLite trade journal for paper/live execution evidence
- JSONL decision journal for every perception → decision cycle
- Exponential backoff on API errors (up to 3 retries)
- Automatic 5-minute back-off if the balance endpoint is unreachable

Paper equity, realized P&L, and open P&L are tracked separately from decision logs. Decision logs show every agent cycle; trade history shows only executed paper/live fills.

Public users can connect their own Bitget futures API key in a browser session without changing the shared operator account. Session live execution is gated by three steps: account verification, the exact `I ACCEPT LIVE RISK` unlock phrase, and a dry-run preview before the exact `PLACE LIVE ORDER` confirmation can submit a real order.

Users fund their Bitget futures account directly; BitAgent does not custody deposits. After a user connects API credentials, the session reads the available USDT futures balance from Bitget and sizes orders from that balance.

---

## Running Locally

```bash
pip install -r requirements.txt

export BITGET_API_KEY=your_key
export BITGET_SECRET_KEY=your_secret
export BITGET_PASSPHRASE=your_passphrase
export QWEN_API_KEY=your_qwen_key   # optional — falls back to rule-based
export EXEC_MODE=paper              # default; use live only with care
export PAPER_BALANCE=10000
export ADMIN_TOKEN=strong_operator_token

python server.py
# Open http://localhost:8000
```

### Smoke Test

Run one full perception-to-decision cycle without storing credentials in the
repo:

```bash
python smoke_test.py
```

The smoke test reads Bitget and Qwen credentials only from environment
variables. If `QWEN_API_KEY` is unset, it verifies the rule-based fallback.

To verify a running server's public endpoints:

```bash
python api_smoke.py http://127.0.0.1:8000
python api_smoke.py https://bitagent.fly.dev
```

---

## Deployment (Fly.io)

```bash
# Install CLI
powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"

fly auth login
fly apps create bitagent
fly secrets set BITGET_API_KEY=... BITGET_SECRET_KEY=... BITGET_PASSPHRASE=...
fly deploy
```

Live at: **https://bitagent.fly.dev**

### Audit Endpoints

- `/api/status` — current agent state and latest decision
- `/api/decisions?limit=50` — verifiable decision-cycle JSONL history
- `/api/trades?limit=50` — paper/live execution journal
- `/api/evidence` — compact system evidence summary
- `/api/audit/evidence` — full audit pack with normalized trade log fields
- `/api/audit/trades.csv` — CSV trade log: timestamp, pair, direction, price, quantity, balance change

### Product Walkthrough

1. Open `https://bitagent.fly.dev`.
2. Enter the platform and confirm the mode is Paper Trading.
3. Review six live signal cards and the Agent Decision Engine.
4. Compare Decision Log against Executed Trades.
5. Open Settings → Audit Center and inspect the live JSON endpoints.
6. Confirm paper equity, realized P&L, open P&L, and strategy rules update from live cycles.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BITGET_API_KEY` | Yes | Bitget API key |
| `BITGET_SECRET_KEY` | Yes | Bitget secret key |
| `BITGET_PASSPHRASE` | Yes | Bitget passphrase |
| `QWEN_API_KEY` | No | Qwen-compatible LLM key |
| `EXEC_MODE` | No | `paper` by default; set `live` for real Bitget orders |
| `EXEC_ENABLED` | No | Set `false` to disable live execution (default: `true`) |
| `PAPER_BALANCE` | No | Simulated USDT balance for paper trading (default: `10000`) |
| `ADMIN_TOKEN` | Recommended | Protects state-changing operator actions |
| `PUBLIC_PAPER_SYMBOL_SWITCH` | No | Allows public asset switching in paper mode when `true` (default); live mode still requires the operator token |
| `EXEC_MAX_PCT` | No | Max position size as % of balance (default: `1.0`) |
| `EXEC_COOLDOWN` | No | Seconds between trades (default: `300`) |
| `MAX_DAILY_TRADES` | No | Daily trade cap per UTC day (default: `10`) |
| `PORT` | No | Server port — injected automatically by Fly.io / Railway |

---

## Project Structure

```
bitagent/
├── agent.py          # Signal collection, LLM reasoning, order execution
├── server.py         # FastAPI server, WebSocket broadcast, REST endpoints
├── frontend/
│   └── index.html    # Single-file trading cockpit UI
├── requirements.txt
├── fly.toml          # Fly.io deployment config
├── Dockerfile
└── data/             # Runtime (gitignored)
    ├── position.json # Disk-persistent open position
    └── trades.db     # SQLite trade journal
```

---

## Ownership

Built by [@Datwebguy](https://github.com/Datwebguy)
