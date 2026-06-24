# BitAgent Hackathon Submission Notes

## Project Description

BitAgent is a crypto futures trading agent built for the Bitget AI Base Camp Hackathon.

The strategy uses multi-signal confirmation. Before making a trade decision, BitAgent reads technical indicators, sentiment, macro conditions, momentum, market depth, and volatility. These signals are sent to Qwen through Bitget's hackathon-compatible endpoint using `qwen3.6-plus`.

Qwen returns a structured `LONG`, `SHORT`, or `FLAT` decision with confidence, reasoning, entry price, stop-loss, and take-profit. If Qwen is unavailable, BitAgent can still run through a transparent rule-based fallback.

Risk is managed through paper mode by default, confidence thresholds, position sizing limits, cooldowns, daily trade caps, stop-loss/take-profit fields, session-private trade history, and guarded live execution.

## Progress

The MVP is live on Fly.io.

Completed:

- Live dashboard
- Asset switching across USDT futures pairs
- Six-signal analysis
- Qwen decisioning
- Rule-based fallback
- Paper trading
- Session-private trade history
- Decision Log
- Executed Trades log
- Bitget futures account connection
- Guarded live execution
- Audit endpoints and CSV trade log

Tech used:

- Python
- FastAPI
- WebSockets
- SQLite
- NumPy
- Qwen `qwen3.6-plus`
- Bitget Futures APIs
- Docker
- Fly.io
- HTML/CSS/JavaScript

Bitget tools used:

- Bitget Futures APIs
- Bitget Qwen-compatible endpoint: `https://hackathon.bitgetops.com/v1`

## AI Trading Thoughts

Agentic trading should be transparent. BitAgent separates Decision Log from Executed Trades so users can inspect both what the agent thought and what it actually executed.

## Submission Links

Live demo:

https://bitagent.fly.dev

GitHub repo:

https://github.com/Datwebguy/bitagent

Public paper trading CSV:

https://bitagent.fly.dev/api/audit/trades.csv

Decision log:

https://bitagent.fly.dev/api/decisions?limit=50

Audit evidence:

https://bitagent.fly.dev/api/audit/evidence

Demo video:

PASTE_VIDEO_LINK_HERE

X post/thread:

PASTE_X_POST_LINK_HERE

## Backtest Status

A formal reproducible backtest report is not included yet. The current submission uses live paper trading records and public audit logs as the verifiable usage record.

