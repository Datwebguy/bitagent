# BitAgent

BitAgent is an AI trading risk cockpit for crypto futures, built for the Bitget AI Base Camp Hackathon.

The app demonstrates an agentic trading loop: read live market conditions, reason over multiple signal groups, produce a structured decision, and execute through paper trading or guarded live Bitget futures trading.

Live demo: https://bitagent.fly.dev

> BitAgent is not financial advice. Paper mode uses simulated funds. Live mode can lose real money.

## What It Does

- Tracks live USDT futures market conditions.
- Analyzes six signal groups: technicals, sentiment, macro, momentum, market depth, and volatility.
- Uses Qwen through Bitget's hackathon-compatible endpoint for AI-assisted decisions.
- Returns `LONG`, `SHORT`, or `FLAT` decisions with confidence, reasoning, entry, stop-loss, and take-profit.
- Supports session-private paper trading by default.
- Lets users connect their own Bitget futures account for guarded live execution.
- Keeps decision logs separate from executed trade history.
- Exposes public audit endpoints and CSV trade logs for review.

## Strategy Logic

BitAgent does not depend on a single indicator. It uses multi-signal confirmation:

| Signal Group | Examples |
|---|---|
| Technicals | RSI, Bollinger Bands, Stochastic RSI, MACD |
| Sentiment | Funding rate, Fear & Greed, long/short ratio |
| Macro | BTC dominance, total crypto market cap change |
| Momentum | 24h price change, range position, open interest |
| Market Depth | Bid/ask imbalance, spread |
| Volatility | ATR regime |

The signal payload is sent to Qwen. The configured model is:

```text
qwen3.6-plus
```

The configured Qwen-compatible base URL is:

```text
https://hackathon.bitgetops.com/v1
```

If Qwen is unavailable, BitAgent falls back to a transparent rule-based decision engine so the demo remains runnable.

## Risk Controls

- Paper trading is the default mode.
- Minimum confidence threshold: 60%.
- Position sizing is capped by account equity.
- Execution cooldown limits repeated entries.
- Daily trade cap limits overtrading.
- Stop-loss and take-profit fields are part of each decision.
- Live trading requires account connection, live mode, risk unlock, order preview, and final confirmation.
- Normal user trade history is session-private.

## Live Trading Flow

Users connect their own Bitget futures account with:

- API key
- Secret key
- Passphrase

BitAgent does not custody deposits. Users fund their Bitget futures account directly. After connection, BitAgent reads the available USDT futures balance and uses it for sizing.

Before any live order is submitted, the user must:

1. Connect a Bitget futures account.
2. Switch to Live Account mode.
3. Unlock live risk.
4. Review the order preview.
5. Confirm the final live order phrase.

## Public Audit Links

Live demo:

```text
https://bitagent.fly.dev
```

Decision log:

```text
https://bitagent.fly.dev/api/decisions?limit=50
```

Public paper trading CSV:

```text
https://bitagent.fly.dev/api/audit/trades.csv
```

Audit evidence:

```text
https://bitagent.fly.dev/api/audit/evidence
```

Note: `/api/trades` is session-scoped for user privacy. Fresh visitors may see an empty list there. The public review log is `/api/audit/trades.csv`.

## Project Structure

```text
agent.py                  Signal collection, reasoning, paper/live execution
server.py                 FastAPI app, sessions, REST API, WebSocket updates
bitget_auth.py            Bitget request signing and credential validation
session_store.py          SQLite session persistence
frontend/                 Browser trading cockpit
docs/                     Public hackathon evidence and submission notes
requirements.txt          Python dependencies
Dockerfile                Container build
fly.toml                  Fly.io deployment config
```

## Running Locally

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Then open:

```text
http://127.0.0.1:8000
```

Optional environment variables:

```text
BITGET_API_KEY
BITGET_SECRET_KEY
BITGET_PASSPHRASE
QWEN_API_KEY
EXEC_MODE=paper
PAPER_BALANCE=10000
ADMIN_TOKEN
```

## Tests

```bash
.\.venv\Scripts\python.exe -m unittest discover -v
```

Current local suite covers:

- Bitget credential validation
- Session storage without saving secrets
- Session-private paper trade history
- Guarded live execution requirements
- Paper account calculations

## Hackathon Status

Completed:

- Live deployed demo
- Qwen-backed decision engine
- Bitget futures integration
- Paper trading and paper trade log
- Session-private user trade history
- Audit endpoints and CSV log
- Demo video recorded

Not yet included:

- Formal backtest report
- On-chain/news signal modules
- User strategy configuration

