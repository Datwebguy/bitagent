import asyncio, base64, csv, hashlib, hmac, io, json, os, secrets, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Set
from urllib.parse import urlencode

import requests
import uvicorn
from fastapi import Cookie, FastAPI, Header, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import session_store
from bitget_auth import extract_usdt, validate_credentials
from agent import (
    get_technical_signal, get_sentiment_signal, get_momentum_signal,
    get_depth_signal, get_volatility_signal, get_macro_signal, reason_with_qwen,
    apply_risk_rules, execute_trade, close_open_position, cancel_open_orders, save_log,
    set_credentials, credentials_set, mark_credentials_disabled,
    set_symbol, set_manual_balance,
    get_futures_balance, get_open_position, get_trade_history, get_decision_history,
    get_paper_account, get_today_trade_count,
    normalize_symbol,
    execution_mode, LOOP_SECS, EXEC_COOLDOWN, MAX_DAILY_TRADES, EXEC_MAX_PCT,
    MIN_CONFIDENCE, MAX_SIZE_PCT, PAPER_BALANCE,
)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
PUBLIC_PAPER_SYMBOL_SWITCH = os.getenv("PUBLIC_PAPER_SYMBOL_SWITCH", "true").strip().lower() == "true"
SESSION_COOKIE = "bitagent_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
SESSION_ID_BYTES = 24
SESSION_AUTH_TIMEOUT = 8.0

# Thread pool for blocking I/O (requests, openai SDK) — keeps the event loop free
_executor = ThreadPoolExecutor(max_workers=8)
_sessions: dict[str, dict] = {}

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


def _session_switch_placeholder(symbol: str, price: float) -> dict:
    state = _switch_placeholder(symbol, price)
    state["type"] = "session_symbol_changed"
    state["session_scope"] = "session_preview"
    state["decision"]["reasoning"] = "Session asset selected. Shared market feed remains isolated from other users."
    state["decision"]["risk_note"] = "Per-user live analysis and execution are separated from the shared agent."
    state["execution"] = {"executed": False, "action": "SESSION_PREVIEW", "detail": "Session asset selected"}
    return state


async def _analyze_session_symbol(symbol: str) -> dict:
    loop = asyncio.get_running_loop()
    async with _symbol_analysis_lock:
        prev_symbol = agent.SYMBOL
        set_symbol(symbol)
        try:
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
            decision_raw = await loop.run_in_executor(_executor, reason_with_qwen, signals)
            decision = apply_risk_rules(decision_raw)
        finally:
            set_symbol(prev_symbol)

    price = float(signals.get("technical", {}).get("price") or 0)
    return {
        "type":       "session_symbol_changed",
        "session_scope": "session_preview",
        "cycle":      0,
        "ts":         datetime.now(timezone.utc).isoformat(),
        "symbol":     symbol,
        "signals":    signals,
        "decision":   decision,
        "execution":  {"executed": False, "action": "SESSION_ANALYSIS", "detail": "Session analysis only"},
        "price":      price,
        "balance":    round(_balance, 2),
        "position":   None,
        "sim_pnl":    0.0,
        "uptime":     int((datetime.now() - _start).total_seconds()),
        "history":    [],
        "risk_config": _risk_config(decision, {"detail": "Session analysis only"}),
    }


def _safe_signal(key: str, fn):
    try:
        return fn()
    except Exception as e:
        print(f"[signal:{key}] {e}")
        return _SIG_DEFAULTS[key]


def _session_auth_headers(api_key: str, secret_key: str, passphrase: str,
                          method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(
        hmac.new(
            secret_key.encode(),
            (ts + method + path + body).encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    return {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sig,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def _session_auth_get_raw(api_key: str, secret_key: str, passphrase: str,
                          path: str, params: dict | None = None) -> dict:
    qs = urlencode(params or {})
    full_path = f"{path}?{qs}" if qs else path
    r = requests.get(
        agent.BITGET_BASE + full_path,
        headers=_session_auth_headers(api_key, secret_key, passphrase, "GET", full_path),
        timeout=SESSION_AUTH_TIMEOUT,
    )
    if not r.text.strip():
        return {"code": "00000", "data": {}}
    return r.json()


def _session_auth_post(creds: dict, path: str, body: dict) -> dict:
    body_str = json.dumps(body, separators=(",", ":"))
    r = requests.post(
        agent.BITGET_BASE + path,
        data=body_str,
        headers=_session_auth_headers(
            creds["api_key"], creds["secret_key"], creds["passphrase"],
            "POST", path, body_str,
        ),
        timeout=SESSION_AUTH_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _session_validate_credentials(creds: dict) -> tuple[bool, float, str]:
    return validate_credentials(creds, _session_auth_get_raw, agent.PRODUCT)


def _session_futures_balance(creds: dict) -> float:
    candidates = [
        ("/api/v3/account/assets", {}),
        ("/api/v2/mix/account/accounts", {"productType": agent.PRODUCT}),
        ("/api/v2/account/all-account-balance", {"coin": "USDT"}),
        ("/api/v2/spot/account/assets", {"coin": "USDT"}),
    ]
    for path, params in candidates:
        try:
            r = _session_auth_get_raw(
                creds["api_key"], creds["secret_key"], creds["passphrase"],
                path, params,
            )
            if r.get("code") == "00000":
                v = extract_usdt(r.get("data"))
                if v is not None:
                    return max(0.0, float(v))
        except Exception:
            continue
    return 0.0


def _session_min_lot(symbol: str) -> float:
    return agent.MIN_LOT_SIZES.get(symbol.replace("USDT", ""), 0.01)


def _session_order_body(symbol: str, direction: str, size: float, decision: dict) -> dict:
    side = "buy" if direction == "LONG" else "sell"
    body = {
        "symbol": symbol,
        "productType": agent.PRODUCT,
        "marginMode": "isolated",
        "marginCoin": "USDT",
        "size": str(size),
        "side": side,
        "tradeSide": "open",
        "orderType": "market",
    }
    sl = float(decision.get("stop_loss") or 0)
    tp = float(decision.get("take_profit") or 0)
    if sl > 0:
        body["presetStopLossPrice"] = str(round(sl, agent._price_dp(sl)))
    if tp > 0:
        body["presetTakeProfitPrice"] = str(round(tp, agent._price_dp(tp)))
    return body


def _session_place_order(creds: dict, symbol: str, decision: dict, balance: float) -> dict:
    direction = str(decision.get("direction") or "FLAT").upper()
    confidence = int(decision.get("confidence") or 0)
    price = float(decision.get("entry_price") or 0)
    if direction not in ("LONG", "SHORT"):
        return {"executed": False, "action": "SKIP", "detail": "decision is not directional"}
    if confidence < MIN_CONFIDENCE:
        return {"executed": False, "action": "SKIP", "detail": f"confidence below {MIN_CONFIDENCE}%"}
    if price <= 0:
        return {"executed": False, "action": "SKIP", "detail": "market price unavailable"}
    if balance < 0.5:
        return {"executed": False, "action": "SKIP", "detail": f"insufficient balance (${balance:.2f} USDT)"}

    budget_usdt = balance * (EXEC_MAX_PCT / 100.0)
    min_lot = _session_min_lot(symbol)
    if budget_usdt < min_lot * price:
        return {
            "executed": False,
            "action": "SKIP",
            "detail": f"budget too small for min lot: need ~${min_lot * price:.2f}",
        }
    size = round(budget_usdt / price, 4)
    if size < min_lot:
        size = min_lot
    body = _session_order_body(symbol, direction, size, decision)
    r = _session_auth_post(creds, "/api/v2/mix/order/place-order", body)
    if r.get("code") == "00000":
        order_id = (r.get("data") or {}).get("orderId", "")
        return {
            "executed": True,
            "action": f"OPEN_{direction}_SESSION",
            "size": size,
            "order_id": order_id,
            "detail": f"{size} {symbol.replace('USDT', '')} @ ~${price:,.6g} | {order_id}",
        }
    return {"executed": False, "action": "SKIP", "detail": r.get("msg", f"order failed ({r.get('code', '?')})")}


def _session_cancel_open_orders(creds: dict, symbol: str) -> dict:
    candidates = [
        {"symbol": symbol, "productType": agent.PRODUCT, "marginCoin": "USDT"},
        {"productType": agent.PRODUCT, "marginCoin": "USDT"},
    ]
    last = {}
    for body in candidates:
        try:
            r = _session_auth_post(creds, "/api/v2/mix/order/cancel-all-orders", body)
        except Exception as e:
            r = {"code": "ERROR", "msg": str(e)}
        last = r
        if r.get("code") == "00000":
            return {"ok": True, "action": "CANCEL_ORDERS_SESSION", "detail": "open orders canceled", "response": r}
    return {
        "ok": False,
        "action": "CANCEL_ORDERS_SESSION",
        "detail": last.get("msg", f"cancel failed ({last.get('code', '?')})"),
        "response": last,
    }


def _session_get_open_position(creds: dict, symbol: str) -> dict | None:
    candidates = [
        ("/api/v2/mix/position/all-position", {"productType": agent.PRODUCT, "marginCoin": "USDT"}),
        ("/api/v3/position/current-position", {"category": agent.PRODUCT, "symbol": symbol}),
    ]
    for path, params in candidates:
        try:
            r = _session_auth_get_raw(
                creds["api_key"], creds["secret_key"], creds["passphrase"],
                path, params,
            )
            if r.get("code") != "00000":
                continue
            data = r.get("data") or []
            positions = data if isinstance(data, list) else (data.get("list") or [])
            for p in positions:
                pos_symbol = str(p.get("symbol") or "").upper()
                if pos_symbol and pos_symbol != symbol:
                    continue
                size = float(p.get("total") or p.get("available") or p.get("holdVol") or p.get("qty") or 0)
                if size <= 0:
                    continue
                side = str(p.get("holdSide") or p.get("posSide") or p.get("side") or "").lower()
                if side not in ("long", "short"):
                    continue
                return {
                    "symbol": symbol,
                    "holdSide": side,
                    "total": str(size),
                    "available": str(float(p.get("available") or size)),
                    "entryPrice": p.get("openPriceAvg") or p.get("entryPrice") or p.get("avgPrice") or 0,
                    "unrealisedPl": p.get("unrealizedPL") or p.get("unrealisedPl") or 0,
                }
        except Exception:
            continue
    return None


def _session_close_position(creds: dict, symbol: str) -> dict:
    pos = _session_get_open_position(creds, symbol)
    if not pos:
        return {"executed": False, "action": "SKIP", "detail": "no open position"}
    side = str(pos.get("holdSide") or "").lower()
    size = str(pos.get("available") or pos.get("total") or "0")
    close_side = "sell" if side == "long" else "buy"
    body = {
        "symbol": symbol,
        "productType": agent.PRODUCT,
        "marginMode": "isolated",
        "marginCoin": "USDT",
        "size": size,
        "side": close_side,
        "tradeSide": "close",
        "orderType": "market",
    }
    try:
        r = _session_auth_post(creds, "/api/v2/mix/order/place-order", body)
    except Exception as e:
        return {"executed": False, "action": "SKIP", "detail": str(e)}
    if r.get("code") == "00000":
        order_id = (r.get("data") or {}).get("orderId", "")
        return {"executed": True, "action": "CLOSE_SESSION", "detail": f"closed {side} {size}", "order_id": order_id}
    return {"executed": False, "action": "SKIP", "detail": r.get("msg", f"close failed ({r.get('code', '?')})")}


def _store_session_credentials(session: dict, api_key: str, secret_key: str, passphrase: str):
    session["credentials"] = {
        "api_key": api_key,
        "secret_key": secret_key,
        "passphrase": passphrase,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    session["mode"] = "session_connected"
    session["live_unlocked"] = False


def _basic_credential_shape_ok(api_key: str, secret_key: str, passphrase: str) -> bool:
    return len(api_key) >= 8 and len(secret_key) >= 16 and len(passphrase) >= 4


def _clear_session_credentials(session: dict):
    session.pop("credentials", None)
    session["mode"] = "shared_agent"
    session["trade_mode"] = "paper"
    session["live_unlocked"] = False


def _auth_required() -> bool:
    return bool(ADMIN_TOKEN)


def _check_admin_token(x_admin_token: str | None) -> tuple[bool, dict | None]:
    if not _auth_required():
        return True, None
    if x_admin_token and secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        return True, None
    return False, {"ok": False, "error": "Operator unlock required."}


def _admin_token_valid(x_admin_token: str | None) -> bool:
    return bool(_auth_required() and x_admin_token and secrets.compare_digest(x_admin_token, ADMIN_TOKEN))


def _new_session_id() -> str:
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def _get_session(session_id: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    session = _sessions.get(session_id)
    if session is None:
        session = session_store.default_store.load(session_id, agent.SYMBOL, PAPER_BALANCE)
    if session is None:
        session = session_store.default_session(session_id, agent.SYMBOL, PAPER_BALANCE)
    _sessions[session_id] = session
    session["last_seen"] = now
    _persist_session(session)
    return session


def _persist_session(session: dict):
    try:
        session_store.default_store.save(session)
    except Exception as e:
        print(f"[session-store] save failed: {e}")


def _session_paper_balance(session: dict) -> float:
    try:
        value = float(session.get("paper_balance", PAPER_BALANCE))
    except (TypeError, ValueError):
        value = PAPER_BALANCE
    return value if value > 0 else PAPER_BALANCE


def _session_paper_account(session: dict, mark_price: float | None = None) -> dict:
    initial = _session_paper_balance(session)
    realized = float(session.get("paper_realized") or 0)
    pos = session.get("paper_open_position") or {}
    mark = float(mark_price or pos.get("mark") or pos.get("entry") or 0)
    side = str(pos.get("side") or "flat").lower()
    size = float(pos.get("size") or 0)
    entry = float(pos.get("entry") or 0)
    unrealized = 0.0
    if side in ("long", "short") and size > 0 and entry > 0 and mark > 0:
        unrealized = (mark - entry) * size if side == "long" else (entry - mark) * size
    equity = initial + realized + unrealized
    notional = size * mark if size > 0 and mark > 0 else size * entry
    used_margin = notional if notional > 0 else 0.0
    free_equity = max(0.0, equity - used_margin)
    return {
        "mode": "session_paper",
        "initial": round(initial, 2),
        "realized": round(realized, 4),
        "unrealized": round(unrealized, 4),
        "equity": round(equity, 2),
        "used_margin": round(used_margin, 4),
        "free_equity": round(free_equity, 2),
        "notional": round(notional, 4),
        "open_side": side if side in ("long", "short") else "flat",
        "open_size": round(size, 8),
        "entry": round(entry, 8),
        "mark": round(mark, 8),
        "trade_count": len(session.get("paper_trades") or []),
    }


def _session_paper_trades(session: dict, limit: int = 50) -> list:
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    return list(reversed(session.get("paper_trades") or []))[:limit]


def _reset_session_paper(session: dict, budget: float | None = None):
    if budget is not None:
        session["paper_balance"] = budget if budget > 0 else PAPER_BALANCE
    session["paper_realized"] = 0.0
    session["paper_open_position"] = None
    session["paper_trades"] = []
    _persist_session(session)


def _session_payload(session: dict) -> dict:
    paper_balance = _session_paper_balance(session)
    paper_account = _session_paper_account(session)
    payload = {
        "id": session["id"],
        "mode": session.get("mode", "shared_agent"),
        "scope": "shared_agent",
        "trade_mode": session.get("trade_mode", "paper"),
        "selected_symbol": session.get("selected_symbol", agent.SYMBOL),
        "paper_balance": round(paper_balance, 2),
        "paper_equity": paper_account["equity"],
        "paper_account": paper_account,
        "paper_trade_count": paper_account["trade_count"],
        "credentials_set": bool(session.get("credentials")),
        "live_unlocked": bool(session.get("live_unlocked")),
        "can_use_live": bool(session.get("credentials") and session.get("live_unlocked")),
    }
    if session.get("credentials"):
        payload["connected_at"] = session["credentials"].get("connected_at")
    if session.get("account_balance") is not None:
        payload["account_balance"] = round(float(session.get("account_balance") or 0), 2)
        payload["balance_source"] = "bitget_futures"
    return payload


def _paper_position_payload(account: dict, symbol: str, opened_at: str | None = None) -> dict | None:
    side = str(account.get("open_side") or "flat").lower()
    size = float(account.get("open_size") or 0)
    if side not in ("long", "short") or size <= 0:
        return None
    return {
        "holdSide": side,
        "total": str(size),
        "available": str(size),
        "symbol": symbol,
        "entryPrice": str(account.get("entry") or 0),
        "unrealisedPl": account.get("unrealized", 0),
        "openedAt": opened_at or datetime.now(timezone.utc).isoformat(),
    }


def _session_status_snapshot(session: dict) -> dict:
    symbol = normalize_symbol(session.get("selected_symbol") or agent.SYMBOL)
    latest = _latest if isinstance(_latest, dict) else {}
    latest_symbol = normalize_symbol(latest.get("symbol") or agent.SYMBOL) if latest else ""
    if latest and latest_symbol == symbol:
        state = dict(latest)
    elif session.get("last_analysis") and session["last_analysis"].get("symbol") == symbol:
        state = dict(session["last_analysis"])
    else:
        price = _quick_public_price(symbol)
        state = _session_switch_placeholder(symbol, price)

    account = _session_paper_account(session, state.get("price") or None)
    trades = session.get("paper_trades") or []
    opened_at = None
    if trades:
        opened_at = trades[-1].get("ts") if isinstance(trades[-1], dict) else None
    state["symbol"] = symbol
    state["session_scope"] = "session_preview"
    state["account"] = account
    state["session_paper_account"] = account
    state["position"] = _paper_position_payload(account, symbol, opened_at)
    state["balance"] = account["equity"]
    return state


def _ensure_session(response: Response, session_id: str | None) -> dict:
    sid = session_id or _new_session_id()
    session = _get_session(sid)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        secure=False,
        samesite="lax",
    )
    return session


def _session_from_websocket(ws: WebSocket) -> dict:
    sid = ws.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = _new_session_id()
    return _get_session(sid)


def _symbol_switch_requires_auth() -> bool:
    return execution_mode() != "paper" or not PUBLIC_PAPER_SYMBOL_SWITCH


def _check_symbol_switch_token(x_admin_token: str | None) -> tuple[bool, dict | None]:
    if not _symbol_switch_requires_auth():
        return True, None
    return _check_admin_token(x_admin_token)


def _risk_config(decision: dict | None = None, execution: dict | None = None) -> dict:
    decision = decision or {}
    execution = execution or {}
    confidence = int(decision.get("confidence") or 0)
    direction = str(decision.get("direction") or "WAITING").upper()
    execution_detail = execution.get("detail") or ""
    execution_detail_lc = execution_detail.lower()
    daily_trades = get_today_trade_count()
    return {
        "mode":                execution_mode(),
        "confidence_min":      MIN_CONFIDENCE,
        "confidence":          confidence,
        "confidence_pass":     confidence >= MIN_CONFIDENCE,
        "decision_direction":  direction,
        "is_directional":      direction in ("LONG", "SHORT"),
        "cooldown_secs":       EXEC_COOLDOWN,
        "cooldown_blocked":    "cooldown" in execution_detail_lc,
        "daily_trades":        daily_trades,
        "max_daily":           MAX_DAILY_TRADES,
        "daily_limit_blocked": daily_trades >= MAX_DAILY_TRADES or "daily limit" in execution_detail_lc,
        "order_size_blocked":  (
            "budget too small" in execution_detail_lc
            or "insufficient balance" in execution_detail_lc
            or "min lot" in execution_detail_lc
        ),
        "size_pct":            EXEC_MAX_PCT,
        "llm_size_cap_pct":    MAX_SIZE_PCT,
        "loop_secs":           LOOP_SECS,
        "execution_detail":    execution_detail,
    }


def _audit_direction(action: str, side: str = "") -> str:
    action_u = str(action or "").upper()
    side_l = str(side or "").lower()
    if "OPEN_LONG" in action_u or (action_u.startswith("OPEN") and side_l == "long"):
        return "LONG"
    if "OPEN_SHORT" in action_u or (action_u.startswith("OPEN") and side_l == "short"):
        return "SHORT"
    if action_u.startswith("CLOSE"):
        return "CLOSE"
    if action_u.startswith("FAIL"):
        return "FAILED"
    return "FLAT"


def _audit_symbol(symbol: str) -> str:
    sym = str(symbol or agent.SYMBOL or "").upper()
    return sym.replace("USDT", "/USDT") if sym.endswith("USDT") else sym


def _audit_trade_rows(limit: int = 500) -> list[dict]:
    rows = []
    for t in reversed(get_trade_history(limit)):
        price = float(t.get("price") or 0)
        size = float(t.get("size") or 0)
        pnl = t.get("pnl")
        rows.append({
            "timestamp": t.get("ts"),
            "trading_pair": _audit_symbol(t.get("symbol")),
            "direction": _audit_direction(t.get("action"), t.get("side")),
            "price": round(price, 8),
            "quantity": round(size, 8),
            "account_balance_change": round(float(pnl or 0), 8),
            "mode": "paper" if "PAPER" in str(t.get("action") or "").upper() else "live",
            "action": t.get("action"),
            "side": t.get("side"),
            "confidence": t.get("confidence"),
            "order_id": t.get("order_id"),
            "detail": t.get("detail"),
            "audit": t.get("audit"),
        })
    return rows


def _audit_summary(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["direction"] == "CLOSE"]
    wins = [r for r in closed if float(r.get("account_balance_change") or 0) > 0]
    losses = [r for r in closed if float(r.get("account_balance_change") or 0) < 0]
    gross_win = sum(float(r["account_balance_change"]) for r in wins)
    gross_loss = abs(sum(float(r["account_balance_change"]) for r in losses))
    account = get_paper_account() if execution_mode() == "paper" else None
    return {
        "total_records": len(rows),
        "open_records": sum(1 for r in rows if r["direction"] in ("LONG", "SHORT")),
        "close_records": len(closed),
        "realized_pnl": round(sum(float(r.get("account_balance_change") or 0) for r in rows), 8),
        "win_rate": round((len(wins) / len(closed)) * 100, 2) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "paper_account": account,
    }


def _endpoint_map() -> dict:
    return {
        "status": "/api/status",
        "decisions": "/api/decisions?limit=50",
        "trades": "/api/trades?limit=50",
        "session_paper_trades": "/api/session/paper-trades?limit=50",
        "session_paper_reset": "/api/session/paper-reset",
        "evidence": "/api/evidence",
        "audit": "/api/audit/evidence",
        "trade_export_csv": "/api/audit/trades.csv",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_task, _wake_loop, _symbol_analysis_lock
    _wake_loop = asyncio.Event()
    _symbol_analysis_lock = asyncio.Lock()
    _agent_task = asyncio.create_task(agent_loop())
    yield
    _agent_task.cancel()


FRONTEND_DIR = Path(__file__).parent / "frontend"

app = FastAPI(title="BitAgent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
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
_symbol_analysis_lock: asyncio.Lock = None
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
            async with _symbol_analysis_lock:
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


class LiveUnlockRequest(BaseModel):
    acknowledge: bool = False
    phrase: str = ""


class SessionModeRequest(BaseModel):
    mode: str = "paper"


class SessionExecuteRequest(BaseModel):
    dry_run: bool = True
    acknowledge: bool = False
    phrase: str = ""


@app.post("/api/connect")
async def connect(req: ConnectRequest, x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    if not req.api_key or not req.secret_key or not req.passphrase:
        return {"ok": False, "error": "All three credential fields are required."}
    if not _basic_credential_shape_ok(req.api_key.strip(), req.secret_key.strip(), req.passphrase.strip()):
        return {"ok": False, "error": "API key, secret key, or passphrase format looks invalid."}

    try:
        sym = normalize_symbol(req.symbol)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    api_key = req.api_key.strip()
    secret_key = req.secret_key.strip()
    passphrase = req.passphrase.strip()
    temp_creds = {"api_key": api_key, "secret_key": secret_key, "passphrase": passphrase}

    loop = asyncio.get_running_loop()
    try:
        ok, balance, error = await asyncio.wait_for(
            loop.run_in_executor(_executor, _session_validate_credentials, temp_creds),
            timeout=SESSION_AUTH_TIMEOUT + 4,
        )
    except Exception as e:
        set_credentials("", "", "")
        return {"ok": False, "error": f"Could not reach Bitget: {e}"}
    if not ok:
        set_credentials("", "", "")
        return {"ok": False, "error": error}

    set_symbol(sym)
    set_credentials(api_key, secret_key, passphrase)
    if req.budget > 0:
        set_manual_balance(req.budget)
    pos     = await loop.run_in_executor(None, get_open_position)
    await _broadcast({"type": "connected", "balance": balance, "symbol": sym, "position": pos})
    return {"ok": True, "balance": balance, "symbol": sym, "position": pos}


@app.post("/api/session/connect")
async def session_connect(
    req: ConnectRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    if not req.api_key or not req.secret_key or not req.passphrase:
        return {"ok": False, "error": "All three credential fields are required."}
    if not _basic_credential_shape_ok(req.api_key.strip(), req.secret_key.strip(), req.passphrase.strip()):
        return {"ok": False, "error": "API key, secret key, or passphrase format looks invalid."}

    try:
        sym = normalize_symbol(req.symbol)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    api_key = req.api_key.strip()
    secret_key = req.secret_key.strip()
    passphrase = req.passphrase.strip()
    loop = asyncio.get_running_loop()
    temp_creds = {"api_key": api_key, "secret_key": secret_key, "passphrase": passphrase}
    try:
        ok, futures_balance, error = await asyncio.wait_for(
            loop.run_in_executor(_executor, _session_validate_credentials, temp_creds),
            timeout=SESSION_AUTH_TIMEOUT + 4,
        )
    except Exception as e:
        return {"ok": False, "error": f"Could not reach Bitget: {e}"}
    if not ok:
        return {"ok": False, "error": error}

    session["selected_symbol"] = sym
    session["account_balance"] = float(futures_balance)
    if req.budget > 0:
        session["paper_balance"] = float(req.budget)
    _store_session_credentials(session, api_key, secret_key, passphrase)
    _persist_session(session)
    price = await loop.run_in_executor(_executor, _quick_public_price, sym)
    state = _session_switch_placeholder(sym, price)
    state["execution"] = {
        "executed": False,
        "action": "SESSION_CONNECTED",
        "detail": "Credentials verified. Live execution remains locked.",
    }
    return {
        "ok": True,
        "symbol": sym,
        "balance": round(float(futures_balance), 2),
        "balance_source": "bitget_futures",
        "scope": "session_connected",
        "session": _session_payload(session),
        "state": state,
    }


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


@app.post("/api/session/disconnect")
async def session_disconnect(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    _clear_session_credentials(session)
    _persist_session(session)
    return {"ok": True, "session": _session_payload(session)}


@app.post("/api/session/live-unlock")
async def session_live_unlock(
    req: LiveUnlockRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    if not session.get("credentials"):
        return {"ok": False, "error": "Connect your Bitget account before unlocking live mode."}
    if not req.acknowledge or req.phrase.strip().upper() != "I ACCEPT LIVE RISK":
        return {"ok": False, "error": "Live unlock requires explicit risk acknowledgement."}
    session["live_unlocked"] = True
    session["mode"] = "live_ready"
    _persist_session(session)
    return {
        "ok": True,
        "session": _session_payload(session),
        "detail": "Live mode unlocked for this session. Execution remains disabled until per-session execution is enabled.",
    }


@app.post("/api/session/live-lock")
async def session_live_lock(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    session["live_unlocked"] = False
    if session.get("credentials"):
        session["mode"] = "session_connected"
    _persist_session(session)
    return {"ok": True, "session": _session_payload(session)}


@app.post("/api/session/mode")
async def session_mode(
    req: SessionModeRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    mode = (req.mode or "paper").strip().lower()
    if mode not in ("paper", "live"):
        return {"ok": False, "error": "Mode must be paper or live.", "session": _session_payload(session)}
    session["trade_mode"] = mode
    if mode == "paper":
        session["live_unlocked"] = False
    _persist_session(session)
    return {"ok": True, "mode": mode, "session": _session_payload(session)}


class BudgetRequest(BaseModel):
    budget: float = 0.0


@app.post("/api/session/paper-budget")
async def session_paper_budget(
    req: BudgetRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    budget = float(req.budget or 0)
    _reset_session_paper(session, budget)
    session["trade_mode"] = "paper"
    session["live_unlocked"] = False
    _persist_session(session)
    payload = _session_payload(session)
    return {
        "ok": True,
        "balance": payload["paper_equity"],
        "balance_source": "session_paper",
        "session": payload,
    }


@app.post("/api/session/paper-reset")
async def session_paper_reset(
    req: BudgetRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    budget = float(req.budget or 0)
    _reset_session_paper(session, budget if budget > 0 else None)
    session["trade_mode"] = "paper"
    session["live_unlocked"] = False
    _persist_session(session)
    payload = _session_payload(session)
    return {
        "ok": True,
        "balance": payload["paper_equity"],
        "balance_source": "session_paper",
        "session": payload,
        "trades": [],
    }


@app.get("/api/session/account")
async def session_account(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    creds = session.get("credentials")
    if not creds:
        return {"ok": False, "error": "No Bitget account connected.", "session": _session_payload(session)}
    loop = asyncio.get_running_loop()
    balance = await loop.run_in_executor(_executor, _session_futures_balance, creds)
    session["account_balance"] = float(balance)
    return {
        "ok": True,
        "balance": round(balance, 2),
        "balance_source": "bitget_futures",
        "symbol": session.get("selected_symbol") or agent.SYMBOL,
        "session": _session_payload(session),
    }


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
async def switch_symbol_route(
    req: SwitchRequest,
    response: Response,
    x_admin_token: str | None = Header(default=None),
    bitagent_session: str | None = Cookie(default=None),
):
    ok, err = _check_symbol_switch_token(x_admin_token)
    if not ok:
        return err

    session = _ensure_session(response, bitagent_session)
    try:
        sym = normalize_symbol(req.symbol)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    is_shared_switch = _admin_token_valid(x_admin_token) or _symbol_switch_requires_auth()
    if not is_shared_switch:
        session["selected_symbol"] = sym
        _persist_session(session)
        loop = asyncio.get_running_loop()
        price = await loop.run_in_executor(_executor, _quick_public_price, sym)
        placeholder = _session_switch_placeholder(sym, price)
        return {
            "ok": True,
            "symbol": sym,
            "price": price,
            "scope": "session_preview",
            "analysis_available": True,
            "session": _session_payload(session),
            "state": placeholder,
        }

    global _cycle, _sim_pnl, _last_price, _last_dir, _history, _consecutive_errors
    if not credentials_set():
        return {"ok": False, "error": "Not connected"}
    pos = await asyncio.get_running_loop().run_in_executor(_executor, get_open_position)
    if pos and pos.get("holdSide"):
        pos_symbol = (pos.get("symbol") or agent.SYMBOL).upper()
        if pos_symbol != sym:
            return {
                "ok": False,
                "error": f"Close the open {pos_symbol} position before switching assets.",
            }
    session["selected_symbol"] = sym
    _persist_session(session)
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


@app.post("/api/session/analyze")
async def analyze_session_symbol(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    sym = normalize_symbol(session.get("selected_symbol") or agent.SYMBOL)
    state = await _analyze_session_symbol(sym)
    session["last_analysis"] = state
    return {
        "ok": True,
        "symbol": sym,
        "scope": "session_preview",
        "session": _session_payload(session),
        "state": state,
    }


@app.post("/api/session/execute")
async def execute_session_trade(
    req: SessionExecuteRequest,
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    creds = session.get("credentials")
    if session.get("trade_mode", "paper") != "live":
        return {"ok": False, "executed": False, "error": "Switch to Live Account mode before live execution.", "session": _session_payload(session)}
    if not creds:
        return {"ok": False, "executed": False, "error": "Connect your Bitget account before execution.", "session": _session_payload(session)}
    if not session.get("live_unlocked"):
        return {"ok": False, "executed": False, "error": "Live mode is locked for this session.", "session": _session_payload(session)}
    analysis = session.get("last_analysis")
    if not analysis:
        return {"ok": False, "executed": False, "error": "Run session analysis before execution.", "session": _session_payload(session)}

    symbol = normalize_symbol(session.get("selected_symbol") or analysis.get("symbol") or agent.SYMBOL)
    decision = analysis.get("decision") or {}
    loop = asyncio.get_running_loop()
    balance = await loop.run_in_executor(_executor, _session_futures_balance, creds)
    direction = str(decision.get("direction") or "FLAT").upper()
    price = float(decision.get("entry_price") or 0)
    min_lot = _session_min_lot(symbol)
    proposed_size = 0.0
    if price > 0 and balance > 0:
        proposed_size = max(min_lot, round((balance * (EXEC_MAX_PCT / 100.0)) / price, 4))
    proposal = {
        "symbol": symbol,
        "direction": direction,
        "confidence": int(decision.get("confidence") or 0),
        "entry_price": price,
        "balance": round(balance, 2),
        "size": proposed_size,
        "dry_run": req.dry_run,
    }
    if req.dry_run:
        return {"ok": True, "executed": False, "proposal": proposal, "session": _session_payload(session)}
    if not req.acknowledge or req.phrase.strip().upper() != "PLACE LIVE ORDER":
        return {"ok": False, "executed": False, "error": "Live order requires explicit PLACE LIVE ORDER confirmation.", "proposal": proposal, "session": _session_payload(session)}

    result = await loop.run_in_executor(_executor, _session_place_order, creds, symbol, decision, balance)
    return {
        "ok": bool(result.get("executed")),
        "executed": bool(result.get("executed")),
        "result": result,
        "proposal": proposal,
        "session": _session_payload(session),
    }


@app.post("/api/session/cancel-orders")
async def cancel_session_orders(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    creds = session.get("credentials")
    if not creds:
        return {"ok": False, "error": "Connect your Bitget account before canceling orders.", "session": _session_payload(session)}
    symbol = normalize_symbol(session.get("selected_symbol") or agent.SYMBOL)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _session_cancel_open_orders, creds, symbol)
    return {"ok": bool(result.get("ok")), **result, "symbol": symbol, "session": _session_payload(session)}


@app.post("/api/session/close-position")
async def close_session_position(
    response: Response,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    creds = session.get("credentials")
    if not creds:
        return {"ok": False, "error": "Connect your Bitget account before closing a position.", "session": _session_payload(session)}
    symbol = normalize_symbol(session.get("selected_symbol") or agent.SYMBOL)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _session_close_position, creds, symbol)
    if result.get("executed"):
        pos = await loop.run_in_executor(_executor, _session_get_open_position, creds, symbol)
        return {"ok": True, "detail": result.get("detail"), "execution": result, "position": pos, "session": _session_payload(session)}
    return {"ok": False, "error": result.get("detail", "Close failed"), "execution": result, "session": _session_payload(session)}


@app.get("/api/status")
def status(response: Response, bitagent_session: str | None = Cookie(default=None)):
    session = _ensure_session(response, bitagent_session)
    account = get_paper_account() if execution_mode() == "paper" else None
    session_latest = _session_status_snapshot(session)
    session_account = _session_paper_account(
        session,
        session_latest.get("price") or None,
    )
    return {
        "cycles":    _cycle,
        "connected": len(_clients),
        "pnl":       _sim_pnl,
        "balance":   account["equity"] if account else _balance,
        "creds_set": credentials_set(),
        "symbol":    agent.SYMBOL,
        "auth_required": _auth_required(),
        "symbol_switch_requires_auth": _symbol_switch_requires_auth(),
        "session":   _session_payload(session),
        "session_scope": "shared_agent",
        "session_creds_set": bool(session.get("credentials")),
        "session_paper_account": session_account,
        "session_paper_trades": _session_paper_trades(session, 50),
        "last_error": _last_error,
        "uptime":    int((datetime.now() - _start).total_seconds()),
        "latest":    session_latest,
        "account":   account,
        "risk_config": _risk_config(session_latest.get("decision"), session_latest.get("execution")),
        "endpoints": _endpoint_map(),
    }


@app.get("/api/session/paper-trades")
def session_paper_trades(
    response: Response,
    limit: int = 50,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    return JSONResponse(_session_paper_trades(session, limit))


@app.get("/api/trades")
def trades(
    response: Response,
    limit: int = 50,
    bitagent_session: str | None = Cookie(default=None),
):
    session = _ensure_session(response, bitagent_session)
    if session.get("trade_mode", "paper") == "paper":
        session_trades = _session_paper_trades(session, limit)
        if session_trades:
            return JSONResponse(session_trades)
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
        "app_url": "https://bitagent.fly.dev",
        "mode": execution_mode(),
        "symbol": agent.SYMBOL,
        "cycles": _cycle,
        "decision_count": len(recent_decisions),
        "executed_trade_count": len(recent_trades),
        "paper_account": account,
        "risk_config": _risk_config((_latest or {}).get("decision"), (_latest or {}).get("execution")),
        "endpoints": _endpoint_map(),
        "notes": [
            "Decision Log records every perception to decision cycle.",
            "Executed Trades records only paper or live fills.",
            "Paper mode uses simulated funds and does not create real profit or loss.",
            "Live mode is operator controlled and can lose real money.",
        ],
    }


@app.get("/api/audit/evidence")
def audit_evidence(limit: int = 500):
    rows = _audit_trade_rows(limit)
    return {
        "project": "BitAgent AI Risk Cockpit",
        "category": "Trading Agent",
        "app_url": "https://bitagent.fly.dev",
        "github": "https://github.com/Datwebguy/bitagent",
        "mode": execution_mode(),
        "log_fields": [
            "timestamp",
            "trading_pair",
            "direction",
            "price",
            "quantity",
            "account_balance_change",
        ],
        "summary": _audit_summary(rows),
        "risk_rules": {
            "minimum_confidence": MIN_CONFIDENCE,
            "max_trade_size_pct": EXEC_MAX_PCT,
            "cooldown_seconds": EXEC_COOLDOWN,
            "max_daily_open_trades": MAX_DAILY_TRADES,
            "paper_balance": PAPER_BALANCE,
        },
        "latest_decisions": get_decision_history(50),
        "trade_log": rows,
        "exports": {
            "csv": "/api/audit/trades.csv",
            "json": "/api/audit/evidence",
            "raw_decisions": "/api/decisions?limit=50",
            "raw_trades": "/api/trades?limit=50",
        },
    }


@app.get("/api/audit/trades.csv")
def audit_trades_csv(limit: int = 500):
    rows = _audit_trade_rows(limit)
    columns = [
        "timestamp",
        "trading_pair",
        "direction",
        "price",
        "quantity",
        "account_balance_change",
        "mode",
        "confidence",
        "order_id",
        "detail",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="bitagent-trades.csv"'}
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)


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


@app.post("/api/cancel-orders")
async def cancel_orders_route(x_admin_token: str | None = Header(default=None)):
    ok, err = _check_admin_token(x_admin_token)
    if not ok:
        return err

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, cancel_open_orders)
    return {
        "ok": bool(result.get("ok")),
        "detail": result.get("detail", ""),
        "error": None if result.get("ok") else result.get("detail", "Cancel failed"),
        "action": result.get("action", "CANCEL_ORDERS"),
    }


@app.websocket("/ws")
async def ws_route(ws: WebSocket):
    await ws.accept()
    session = _session_from_websocket(ws)
    _clients.add(ws)
    await ws.send_text(json.dumps({
        "type":      "init",
        "creds_set": credentials_set(),
        "auth_required": _auth_required(),
        "symbol_switch_requires_auth": _symbol_switch_requires_auth(),
        "session":   _session_payload(session),
        "session_scope": "shared_agent",
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
def root(bitagent_session: str | None = Cookie(default=None)):
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    response = HTMLResponse(html, headers={"Cache-Control": "no-store"})
    _ensure_session(response, bitagent_session)
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"\nBitAgent  ->  http://localhost:{port}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False, log_level="warning")
