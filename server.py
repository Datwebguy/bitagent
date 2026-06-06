import asyncio, json, os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import agent
from agent import (
    get_technical_signal, get_sentiment_signal, get_momentum_signal,
    get_depth_signal, get_volatility_signal, reason_with_qwen,
    apply_risk_rules, execute_trade, save_log,
    set_credentials, credentials_set, set_symbol, set_manual_balance,
    get_futures_balance, get_open_position, get_trade_history,
    LOOP_SECS, EXEC_COOLDOWN, MAX_DAILY_TRADES, EXEC_MAX_PCT,
)

AUTH_ERROR_CODES = {"40001", "40002", "40003", "40004", "40005", "40031"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_task
    _agent_task = asyncio.create_task(agent_loop())
    yield
    _agent_task.cancel()


app = FastAPI(title="BitAgent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_clients:           Set[WebSocket] = set()
_latest:            dict           = {}
_history:           list           = []
_cycle:             int            = 0
_sim_pnl:           float          = 0.0
_balance:           float          = 0.0
_last_price:        float          = 0.0
_last_dir:          str            = "FLAT"
_agent_task                        = None
_consecutive_errors: int           = 0

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
    global _cycle, _sim_pnl, _last_price, _last_dir, _balance, _consecutive_errors

    while True:
        if not credentials_set():
            await asyncio.sleep(2)
            continue

        _cycle += 1
        try:
            signals  = {
                "technical":  get_technical_signal(),
                "sentiment":  get_sentiment_signal(),
                "momentum":   get_momentum_signal(),
                "depth":      get_depth_signal(),
                "volatility": get_volatility_signal(),
            }
            decision  = apply_risk_rules(reason_with_qwen(signals))
            execution = execute_trade(decision, signals)
            price     = signals["technical"]["price"]
            _balance  = get_futures_balance()

            # Session PnL tracker — resets on restart; see trade journal for history
            if _last_dir == "LONG" and _last_price > 0:
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

            pos   = get_open_position()
            state = {
                "type":       "update",
                "cycle":      _cycle,
                "ts":         datetime.now(timezone.utc).isoformat(),
                "symbol":     agent.SYMBOL,
                "signals":    signals,
                "decision":   decision,
                "execution":  execution,
                "price":      price,
                "balance":    round(_balance, 2),
                "position":   pos,
                "sim_pnl":    round(_sim_pnl, 3),
                "uptime":     int((datetime.now() - _start).total_seconds()),
                "history":    list(_history),
                "risk_config": {
                    "cooldown_secs": EXEC_COOLDOWN,
                    "max_daily":     MAX_DAILY_TRADES,
                    "size_pct":      EXEC_MAX_PCT,
                },
            }
            _latest.update(state)
            await _broadcast(state)

        except Exception as e:
            _consecutive_errors += 1
            print(f"[agent] {e}")
            await _broadcast({"type": "error", "msg": str(e), "cycle": _cycle})
            # Back off on repeated failures to avoid hammering the Bitget rate limit
            if _consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                await asyncio.sleep(LOOP_SECS * 2)
                continue

        await asyncio.sleep(LOOP_SECS)


# ─── ROUTES ───────────────────────────────────────────────────────────────────
class ConnectRequest(BaseModel):
    api_key:    str
    secret_key: str
    passphrase: str
    symbol:     str   = "BTCUSDT"
    budget:     float = 0.0


@app.post("/api/connect")
async def connect(req: ConnectRequest):
    if not req.api_key or not req.secret_key or not req.passphrase:
        return {"ok": False, "error": "All three credential fields are required."}

    sym = req.symbol.strip().upper()
    set_symbol(sym)
    set_credentials(req.api_key.strip(), req.secret_key.strip(), req.passphrase.strip())
    if req.budget > 0:
        set_manual_balance(req.budget)

    balance = get_futures_balance()
    if balance == 0.0:
        try:
            r    = agent._auth_get_raw("/api/v2/spot/account/assets", {"coin": "USDT"})
            code = r.get("code", "")
            # Auth error codes indicate bad credentials; 40404/40085 = valid key, wrong endpoint
            if code in AUTH_ERROR_CODES:
                set_credentials("", "", "")
                return {"ok": False, "error": f"Invalid API credentials: {r.get('msg')}"}
        except Exception as e:
            set_credentials("", "", "")
            return {"ok": False, "error": f"Could not reach Bitget: {e}"}

    pos = get_open_position()
    await _broadcast({"type": "connected", "balance": balance, "symbol": sym, "position": pos})
    return {"ok": True, "balance": balance, "symbol": sym, "position": pos}


@app.post("/api/disconnect")
async def disconnect():
    global _cycle, _sim_pnl, _last_price, _last_dir, _history, _balance
    set_credentials("", "", "")
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
async def set_budget_route(req: BudgetRequest):
    set_manual_balance(req.budget)
    bal = get_futures_balance()
    await _broadcast({"type": "balance_update", "balance": round(bal, 2)})
    return {"ok": True, "balance": round(bal, 2)}


class SwitchRequest(BaseModel):
    symbol: str


@app.post("/api/switch-symbol")
async def switch_symbol_route(req: SwitchRequest):
    global _cycle, _sim_pnl, _last_price, _last_dir, _history
    if not credentials_set():
        return {"ok": False, "error": "Not connected"}
    sym = req.symbol.strip().upper()
    set_symbol(sym)
    _cycle = 0
    _sim_pnl = 0.0
    _last_price = 0.0
    _last_dir = "FLAT"
    _history = []
    _latest.clear()
    await _broadcast({"type": "symbol_changed", "symbol": sym})
    return {"ok": True, "symbol": sym}


@app.get("/api/status")
def status():
    return {
        "cycles":    _cycle,
        "connected": len(_clients),
        "pnl":       _sim_pnl,
        "balance":   _balance,
        "creds_set": credentials_set(),
        "risk_config": {
            "cooldown_secs": EXEC_COOLDOWN,
            "max_daily":     MAX_DAILY_TRADES,
            "size_pct":      EXEC_MAX_PCT,
        },
    }


@app.get("/api/trades")
def trades(limit: int = 50):
    return JSONResponse(get_trade_history(limit))


@app.get("/api/position")
def position():
    return JSONResponse(get_open_position() or {})


@app.websocket("/ws")
async def ws_route(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    await ws.send_text(json.dumps({
        "type":      "init",
        "creds_set": credentials_set(),
        "latest":    _latest if _latest else None,
    }))
    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
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
