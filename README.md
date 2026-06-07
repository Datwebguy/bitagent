# BitAgent — Autonomous AI Trading Platform

> Built for the **Bitget AI Base Camp Hackathon S1** (May 27 – June 25, 2026)

BitAgent is a production-grade autonomous trading system that perceives live market conditions across six signal dimensions, reasons over them with an LLM, and executes real perpetual futures orders on Bitget — all with a professional real-time cockpit UI.

---

## Architecture

```
Browser (WebSocket) ──► FastAPI Server ──► Bitget REST API
                              │
                         Agent Loop (60s)
                              │
              ┌───────────────┼───────────────┐
          Signals          Reason           Execute
        (6 sources)      (Qwen LLM)    (v2 / v3 API)
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
With `QWEN_API_KEY` set, decisions are handed to `qwen3.6-plus` via the Bitget hackathon endpoint — the model receives all six signal payloads and returns a structured JSON decision including entry, stop-loss, take-profit, confidence, and reasoning.

**Minimum confidence to trade:** 60%  
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
- SQLite trade journal for full audit trail
- Exponential backoff on API errors (up to 3 retries)
- Automatic 5-minute back-off if the balance endpoint is unreachable

---

## Running Locally

```bash
pip install -r requirements.txt

export BITGET_API_KEY=your_key
export BITGET_SECRET_KEY=your_secret
export BITGET_PASSPHRASE=your_passphrase
export QWEN_API_KEY=your_qwen_key   # optional — falls back to rule-based

python server.py
# Open http://localhost:8000
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

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BITGET_API_KEY` | Yes | Bitget API key |
| `BITGET_SECRET_KEY` | Yes | Bitget secret key |
| `BITGET_PASSPHRASE` | Yes | Bitget passphrase |
| `QWEN_API_KEY` | No | Qwen LLM key (hackathon endpoint) |
| `EXEC_ENABLED` | No | Set `false` to disable live execution (default: `true`) |
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
├── Procfile          # Railway entry point
├── fly.toml          # Fly.io deployment config
├── Dockerfile
└── data/             # Runtime (gitignored)
    ├── position.json # Disk-persistent open position
    └── trades.db     # SQLite trade journal
```

---

## Hackathon

**Bitget AI Base Camp — Season 1**  
Prize pool: $50,000 USDT  
Submission deadline: June 25, 2026  

Built by [@Datwebguy](https://github.com/Datwebguy)
