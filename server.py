import asyncio, json, os, secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

import uvicorn
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import agent
from agent import (
    get_technical_signal, get_sentiment_signal, get_momentum_signal,
    get_depth_signal, get_volatility_signal, get_macro_signal, reason_with_qwen,
    apply_risk_rules, execute_trade, close_open_position, save_log,
    set_credentials, credentials_set, mark_credentials_disabled,
    set_symbol, set_manual_balance,
    get_futures_balance, get_open_position, get_trade_history, get_decision_history,
    get_paper_account,
    normalize_symbol,
    execution_mode, LOOP_SECS, EXEC_COOLDOWN, MAX_DAILY_TRADES, EXEC_MAX_PCT,
    MIN_CONFIDENCE, MAX_SIZE_PCT,
)

AUTH_ERROR_CODES = {"40001", "40002", "40003", "40004", "40005", "40031"}
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# Thread pool for blocking I/O (requests, openai SDK) — keeps the event loop free
_executor = ThreadPoolExecutor(max_workers=8)

_SIG_DEFAULTS = {
    "technical":  {"signal": "NEUTRAL", "rsi": 50, "stoch_rsi": 50,
                   "trend": "FLAT", "macd": "FLAT", "bb_pct": 0.5, "price": 0.0},
    "sentiment":  {"signal": "NEUTRAL", "funding_rate": 0, "long_short_ratio": 1,
                   "fear_greed": 50, "fear_greed_label": "Neutral", "note": ""},
    "macro":      {"signal": "NEUTRAL", "btc_dominance": 50, "mcap_change_24h": 0, "note": ""},
    "momentum":   {"signal": "NEUTRAL", "change_24h_pct": 0, "volume_24h_usd": 0,
                   "range_position": 50, "open_interest": 0},
    "depth":      {"signal": "NEUTRAL", "imbalance": 0.0, "spread_pct": 0.0},
    "volatility": {"signal": "NEUTRAL", "regime": "UNKNOWN", "atr_pct": 0.0},
}


def _quick_public_price(symbol: str) -> float:
    try:
        data = agent.public_get(
            "/api/v2/mix/market/ticker",
            {"symbol": symbol.upper(), "productType": "USDT-FUTURES"},
        )
        ticker = data[0] if isinstance(data, list) and data else data
        return float((ticker or {}).get("lastPr", 0) or 0)
    except Exception as e:
        print(f"[ticker:{symbol}] {e}")
        return 0.0


def _switch_placeholder(symbol: str, price: float) -> dict:
    signals = {k: dict(v) for k, v in _SIG_DEFAULTS.items()}
    signals["technical"]["price"] = round(price, 8)
    return {
        "type":      "update",
        "cycle":     0,
        "ts":        datetime.now(timezone.utc).isoformat(),
        "symbol":    symbol,
        "signals":   signals,
        "decision": {
            "direction":    "FLAT",
            "confidence":   0,
            "size_pct":     0,
            "entry_price":  price,
            "stop_loss":    0,
            "take_profit":  0,
            "signal_votes": {k: "NEUTRAL" for k in _SIG_DEFAULTS},
            "reasoning":    "Collecting fresh market signals for this asset.",
            "risk_note":    "Analysis will update automatically after the next agent cycle completes.",
        },
        "execution": {"executed": False, "action": "SYNC", "detail": "Collecting fresh signals..."},
        "price":     round(price, 8),
        "balance":   round(_balance, 2),
        "position":  None,
        "sim_pnl":   0.0,
        "uptime":    int((datetime.now() - _start).total_seconds()),
        "history":   [],
        "risk_config": _risk_config(),
    }


def _safe_signal(key: str, fn):
    try:
        return fn()
    except Exception as e:
        print(f"[signal:{key}] {e}")
        return _SIG_DEFAULTS[key]


def _auth_required() -> bool:
    return bool(ADMIN_TOKEN)


def _check_admin_token(x_admin_token: str | None) -> tuple[bool, dict | None]:
    if not _auth_required():
        return True, None
    if x_admin_token and secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        return True, None
    return False, {"ok": False, "error": "Operator unlock required."}


def _risk_config(decision: dict | None = None, execution: dict | None = None) -> dict:
    decision = decision or {}
    execution = execution or {}
    confidence = int(decision.get("confidence") or 0)
    direction = str(decision.get("direction") or "WAITING").upper()
    execution_detail = execution.get("detail") or ""
    return {
        "mode":                execution_mode(),
        "confidence_min":      MIN_CONFIDENCE,
        "confidence":          confidence,
        "confidence_pass":     confidence >= MIN_CONFIDENCE,
        "decision_direction":  direction,
        "is_directional":      direction in ("LONG", "SHORT"),
        "cooldown_secs":       EXEC_COOLDOWN,
        "cooldown_blocked":    "cooldown" in execution_detail.lower(),
        "max_daily":           MAX_DAILY_TRADES,
        "size_pct":            EXEC_MAX_PCT,
        "llm_size_cap_pct":    MAX_SIZE_PCT,
        "loop_secs":           LOOP_SECS,
        "execution_detail":    execution_detail,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_task, _wake_loop
    _wake_loop = asyncio.Event()
    _agent_task = asyncio.create_task(agent_loop())
    yield
    _agent_task.cancel()


app = FastAPI(title="BitAgent", lifespan=lifespan)
_cors_origins = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "https://bitagent.fly.dev,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"]
)

_clients:           Set[WebSocket] = set()
_latest:            dict           = {}
_history:           list           = []
_cycle:             int            = 0
_sim_pnl:           float          = 0.0
_balance:           float          = get_futures_balance() if execution_mode() == "paper" else 0.0
_last_price:        float          = 0.0
_last_dir:          str            = "FLAT"
_agent_task                        = None
_consecutive_errors: int           = 0
_wake_loop:          asyncio.Event = None
_last_error:         str           = ""

MAX_CONSECUTIVE_ERRORS = 5
_start = datetime.now()


async def _broadcast(data: dict):
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_text(json.dumps(data))
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def agent_loop():
    global _cycle, _sim_pnl, _last_price, _last_dir, _balance, _consecutive_errors, _last_error

    while True:
        if not credentials_set():
            await asyncio.sleep(2)
            continue

        _cycle += 1
        try:
            loop = asyncio.get_running_loop()
            # Snapshot symbol — prevents a mid-cycle switch from tagging wrong symbol
            sym = agent.SYMBOL

            # Run all 6 signals in parallel inside a thread pool.
            # requests.get() is blocking — running it in threads keeps the event loop
            # free so WebSocket pings and reconnects are never starved.
            # Outer timeout guards against a thread hanging beyond its individual timeout.
            sig_results = await asyncio.wait_for(asyncio.gather(
                loop.run_in_executor(_executor, _safe_signal, "technical",  get_technical_signal),
                loop.run_in_executor(_executor, _safe_signal, "sentiment",  get_sentiment_signal),
                loop.run_in_executor(_executor, _safe_signal, "macro",      get_macro_signal),
                loop.run_in_executor(_executor, _safe_signal, "momentum",   get_momentum_signal),
                loop.run_in_executor(_executor, _safe_signal, "depth",      get_depth_signal),
                loop.run_in_executor(_executor, _safe_signal, "volatility", get_volatility_signal),
            ), timeout=45.0)
            signals = dict(zip(
                ("technical", "sentiment", "macro", "momentum", "depth", "volatility"),
                sig_results,
            ))
            if sym != agent.SYMBOL:
                print(f"[agent] discarded stale signal cycle for {sym}; current symbol is {agent.SYMBOL}")
                continue

            # Qwen SDK is also blocking — run in thread pool
            decision_raw = await loop.run_in_executor(_executor, reason_with_qwen, signals)
            decision  = apply_risk_rules(decision_raw)
            if sym != agent.SYMBOL:
                print(f"[agent] discarded stale decision cycle for {sym}; current symbol is {agent.SYMBOL}")
                continue

            execution = await loop.run_in_executor(_executor, execute_trade, decision, signals)
            if sym != agent.SYMBOL:
                print(f"[agent] discarded stale execution result for {sym}; current symbol is {agent.SYMBOL}")
                continue

            price     = signals["technical"]["price"]
            paper_account = await loop.run_in_executor(_executor, get_paper_account, price) if execution_mode() == "paper" else None
            _balance  = paper_account["equity"] if paper_account else await loop.run_in_executor(_executor, get_futures_balance)

            # Session/Paper P&L. In paper mode this is account return, not a raw signal move.
            if paper_account and paper_account["initial"] > 0:
                _sim_pnl = ((_balance - paper_account["initial"]) / paper_account["initial"]) * 100
            elif _last_dir == "LONG" and _last_price > 0:
                _sim_pnl += (price - _last_price) / _last_price * 100
            elif _last_dir == "SHORT" and _last_price > 0:
                _sim_pnl += (_last_price - price) / _last_price * 100
            _last_price = price
            _last_dir   = decision["direction"]

            _history.append({
                "ts":          datetime.now().strftime("%H:%M:%S"),
                "direction":   decision["direction"],
                "confidence":  decision["confidence"],
                "price":       price,
                "size_pct":    decision["size_pct"],
                "executed":    execution.get("executed", False),
                "exec_action": execution.get("action", ""),
            })
            if len(_history) > 20:
                _history.pop(0)

            save_log(_cycle, signals, decision)
            _consecutive_errors = 0
            _last_error = ""

            pos = await loop.run_in_executor(_executor, get_open_position)
            state = {
                "type":       "update",
                "cycle":      _cycle,
                "ts":         datetime.now(timezone.utc).isoformat(),
                "symbol":     sym,
                "signals":    signals,
                "decision":   decision,
                "execution":  execution,
                "price":      price,
                "balance":    round(_balance, 2),
                "account":    paper_account,
                "position":   pos,
                "sim_pnl":    round(_sim_pnl, 3),
                "uptime":     int((datetime.now() - _start).total_seconds()),
                "history":    list(_history),
                "risk_config": _risk_config(decision, execution),
            }
            _latest.update(state)
            await _broadcast(state)

        except Exception as e:
            _consecutive_errors += 1
            _last_error = str(e)
            print(f"[agent] {e}")
            await _broadcast({"type": "error", "msg": str(e), "cycle": _cycle})
            # Back off on repeated failures to avoid hammering the Bitget rate limit
            if _consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                await asyncio.sleep(LOOP_SECS * 2)
                continue

        # Sleep LOOP_SECS, but wake immediately if a symbol switch fires.
        # IMPORTANT: check is_set() BEFORE clearing — a switch that fires during
        # the active cycle sets the event; clearing first would lose that signal
        # and force a full 60s sleep before the new symbol's cycle starts.
        if not _wake_loop.is_set():
            try:
                await asyncio.wait_for(_wake_loop.wait(), timeout=LOOP_SECS)
            except asyncio.TimeoutError:
                pass
        _wake_loop.clear()


# ─── ROUTES ───────────────────────────────────────────────────────────────────
class ConnectRequest(BaseModel):
    api_key:    str
    secret_key: str
    passphrase: str
    symbol:     str   = "BTCUSDT"
    budget:     float = 0.0


@app.post("/api/connect")
async def connect(req: ConnectRequest, x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    if not req.api_key or not req.secret_key or not req.passphrase:
        return {"ok": False, "error": "All three credential fields are required."}

    try:
        sym = normalize_symbol(req.symbol)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    set_symbol(sym)
    set_credentials(req.api_key.strip(), req.secret_key.strip(), req.passphrase.strip())
    if req.budget > 0:
        set_manual_balance(req.budget)

    loop = asyncio.get_running_loop()

    # Always probe auth — budget > 0 used to skip this, allowing bad credentials through
    try:
        r    = await loop.run_in_executor(None, lambda: agent._auth_get_raw(
                   "/api/v2/spot/account/assets", {"coin": "USDT"}))
        code = r.get("code", "")
        # Auth error codes indicate bad credentials; 40404/40085 = valid key, wrong endpoint
        if code in AUTH_ERROR_CODES:
            set_credentials("", "", "")
            return {"ok": False, "error": f"Invalid API credentials: {r.get('msg')}"}
    except Exception as e:
        set_credentials("", "", "")
        return {"ok": False, "error": f"Could not reach Bitget: {e}"}

    balance = await loop.run_in_executor(None, get_futures_balance)
    pos     = await loop.run_in_executor(None, get_open_position)
    await _broadcast({"type": "connected", "balance": balance, "symbol": sym, "position": pos})
    return {"ok": True, "balance": balance, "symbol": sym, "position": pos}


@app.post("/api/disconnect")
async def disconnect(x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    global _cycle, _sim_pnl, _last_price, _last_dir, _history, _balance
    set_credentials("", "", "")
    mark_credentials_disabled()
    _cycle = 0
    _sim_pnl = 0.0
    _last_price = 0.0
    _last_dir = "FLAT"
    _history = []
    _balance = 0.0
    _latest.clear()
    await _broadcast({"type": "disconnected"})
    return {"ok": True}


class BudgetRequest(BaseModel):
    budget: float = 0.0


@app.post("/api/set-budget")
async def set_budget_route(req: BudgetRequest, x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    set_manual_balance(req.budget)
    loop = asyncio.get_running_loop()
    bal  = await loop.run_in_executor(None, get_futures_balance)
    await _broadcast({"type": "balance_update", "balance": round(bal, 2)})
    return {"ok": True, "balance": round(bal, 2)}


class SwitchRequest(BaseModel):
    symbol: str


@app.post("/api/switch-symbol")
async def switch_symbol_route(req: SwitchRequest, x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    global _cycle, _sim_pnl, _last_price, _last_dir, _history, _consecutive_errors
    if not credentials_set():
        return {"ok": False, "error": "Not connected"}
    try:
        sym = normalize_symbol(req.symbol)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    pos = await asyncio.get_running_loop().run_in_executor(_executor, get_open_position)
    if pos and pos.get("holdSide"):
        pos_symbol = (pos.get("symbol") or agent.SYMBOL).upper()
        if pos_symbol != sym:
            return {
                "ok": False,
                "error": f"Close the open {pos_symbol} position before switching assets.",
            }
    set_symbol(sym)
    _cycle = 0
    _sim_pnl = 0.0
    _last_price = 0.0
    _last_dir = "FLAT"
    _history = []
    _consecutive_errors = 0
    _latest.clear()
    await _broadcast({"type": "symbol_changed", "symbol": sym})
    loop = asyncio.get_running_loop()
    price = await loop.run_in_executor(_executor, _quick_public_price, sym)
    placeholder = _switch_placeholder(sym, price)
    _latest.update(placeholder)
    await _broadcast(placeholder)
    # Interrupt the sleep so the agent loop runs immediately for the new symbol
    if _wake_loop:
        _wake_loop.set()
    return {"ok": True, "symbol": sym, "price": price}


@app.get("/api/status")
def status():
    account = get_paper_account() if execution_mode() == "paper" else None
    return {
        "cycles":    _cycle,
        "connected": len(_clients),
        "pnl":       _sim_pnl,
        "balance":   account["equity"] if account else _balance,
        "creds_set": credentials_set(),
        "symbol":    agent.SYMBOL,
        "auth_required": _auth_required(),
        "last_error": _last_error,
        "uptime":    int((datetime.now() - _start).total_seconds()),
        "latest":    _latest if _latest else None,
        "account":   account,
        "risk_config": _risk_config((_latest or {}).get("decision"), (_latest or {}).get("execution")),
    }


@app.get("/api/trades")
def trades(limit: int = 50):
    return JSONResponse(get_trade_history(limit))


@app.get("/api/decisions")
def decisions(limit: int = 50):
    return JSONResponse(get_decision_history(limit))


@app.get("/api/evidence")
def evidence():
    account = get_paper_account() if execution_mode() == "paper" else None
    recent_decisions = get_decision_history(50)
    recent_trades = get_trade_history(50)
    return {
        "project": "BitAgent",
        "agent_type": "Trading Agent",
        "demo_url": "https://bitagent.fly.dev",
        "mode": execution_mode(),
        "symbol": agent.SYMBOL,
        "cycles": _cycle,
        "decision_count": len(recent_decisions),
        "executed_trade_count": len(recent_trades),
        "paper_account": account,
        "risk_config": _risk_config((_latest or {}).get("decision"), (_latest or {}).get("execution")),
        "endpoints": {
            "status": "/api/status",
            "decisions": "/api/decisions?limit=50",
            "trades": "/api/trades?limit=50",
            "evidence": "/api/evidence",
        },
        "notes": [
            "Decision Log records every perception to decision cycle.",
            "Executed Trades records only paper or live fills.",
            "Paper mode uses simulated funds and does not create real profit or loss.",
            "Live mode is operator controlled and can lose real money.",
        ],
    }


@app.get("/api/ticker/{symbol}")
def ticker(symbol: str):
    """Quick public price fetch — called immediately after a symbol switch."""
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return {"price": 0}
    return {"price": _quick_public_price(sym)}


@app.get("/api/position")
def position():
    return JSONResponse(get_open_position() or {})


@app.post("/api/close-position")
async def close_position_route(x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    if not credentials_set():
        return {"ok": False, "error": "Not connected"}
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, close_open_position)
    if result["executed"]:
        pos = await loop.run_in_executor(None, get_open_position)
        await _broadcast({"type": "update", "symbol": agent.SYMBOL,
                          "position": pos, "execution": result})
    return {"ok": result["executed"], "detail": result["detail"],
            "error": None if result["executed"] else result["detail"]}


@app.websocket("/ws")
async def ws_route(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    await ws.send_text(json.dumps({
        "type":      "init",
        "creds_set": credentials_set(),
        "auth_required": _auth_required(),
        "symbol":    agent.SYMBOL,          # always send current symbol so frontend can sync
        "latest":    _latest if _latest else None,
    }))
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=30)
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        _clients.discard(ws)
    except Exception:
        _clients.discard(ws)


@app.get("/")
def root():
    html = (Path(__file__).parent / "frontend" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"\nBitAgent  ->  http://localhost:{port}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False, log_level="warning")
