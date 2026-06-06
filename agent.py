#!/usr/bin/env python3
"""
BitAgent: Multi-Signal AI Trading Agent
Bitget AI Base Camp Hackathon S1

Loop: Perceive (5 signals) → Reason (Qwen LLM) → Decide → Execute (sim) → Risk check
"""

import os, json, time, hmac, hashlib, base64, sys, sqlite3
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlencode
import numpy as np
import requests

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
BITGET_API_KEY    = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
QWEN_API_KEY      = os.getenv("QWEN_API_KEY", "")

MANUAL_BALANCE: float = 0.0   # set from connect screen; used when API balance unavailable

def set_credentials(api_key: str, secret_key: str, passphrase: str):
    global BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE
    global _balance_working_path, _balance_diagnosed, _balance_retry_ts, _is_unified_account
    BITGET_API_KEY    = api_key
    BITGET_SECRET_KEY = secret_key
    BITGET_PASSPHRASE = passphrase
    # Reset probe state so the new account's endpoints are discovered fresh
    _balance_working_path = None
    _balance_diagnosed    = False
    _balance_retry_ts     = 0.0
    _is_unified_account   = None

def set_manual_balance(usdt: float):
    global MANUAL_BALANCE
    MANUAL_BALANCE = float(usdt)

def credentials_set() -> bool:
    return bool(BITGET_API_KEY and BITGET_SECRET_KEY and BITGET_PASSPHRASE)

def set_symbol(symbol: str):
    global SYMBOL
    SYMBOL = symbol.upper()

BITGET_BASE   = "https://api.bitget.com"
QWEN_BASE     = "https://hackathon.bitgetops.com/v1"
QWEN_MODEL    = "qwen3.6-plus"

SYMBOL        = "BTCUSDT"
PRODUCT       = "USDT-FUTURES"
LOOP_SECS      = 60          # run every 60 seconds
MIN_CONFIDENCE = 60          # only trade above 60% confidence
MAX_SIZE_PCT   = 3.0         # max 3% of portfolio per trade
LOG_FILE       = "agent_log.jsonl"

# ─── DATA DIRECTORY ───────────────────────────────────────────────────────────
DATA_DIR       = Path("data")
DATA_DIR.mkdir(exist_ok=True)
POSITION_FILE  = DATA_DIR / "position.json"
TRADES_DB      = DATA_DIR / "trades.db"

# ─── EXECUTION CONFIG ─────────────────────────────────────────────────────────
EXEC_ENABLED      = os.getenv("EXEC_ENABLED", "true").lower() == "true"
EXEC_MAX_PCT      = float(os.getenv("EXEC_MAX_PCT", "1.0"))   # % of balance per trade
EXEC_COOLDOWN     = int(os.getenv("EXEC_COOLDOWN", "300"))    # min seconds between trades
MAX_DAILY_TRADES  = int(os.getenv("MAX_DAILY_TRADES", "10"))  # hard cap per UTC day
MIN_LOT_SIZES     = {                                          # exchange minimums by base
    "BTC": 0.001, "ETH": 0.01, "SOL": 0.1, "BNB": 0.01,
    "XRP": 1.0,   "DOGE": 10.0,"ADA": 10.0,"AVAX": 0.1,
}

def _min_lot() -> float:
    base = SYMBOL.replace("USDT", "")
    return MIN_LOT_SIZES.get(base, 0.01)

# ─── BITGET API ───────────────────────────────────────────────────────────────
def _sign(ts: str, method: str, path: str, body: str = "") -> str:
    msg = ts + method + path + body
    return base64.b64encode(
        hmac.new(BITGET_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()

def _auth_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY":       BITGET_API_KEY,
        "ACCESS-SIGN":      _sign(ts, method, path, body),
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE":BITGET_PASSPHRASE,
        "Content-Type":     "application/json",
    }

def public_get(path: str, params: dict = None) -> dict:
    r = requests.get(BITGET_BASE + path, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json().get("data", {})

def _auth_get(path: str, params: dict = None) -> dict:
    qs = urlencode(params or {})
    full_path = f"{path}?{qs}" if qs else path
    headers = _auth_headers("GET", full_path, "")
    r = requests.get(BITGET_BASE + full_path, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def _auth_get_raw(path: str, params: dict = None) -> dict:
    """Like _auth_get but does not raise on HTTP errors — returns full JSON."""
    qs = urlencode(params or {})
    full_path = f"{path}?{qs}" if qs else path
    headers = _auth_headers("GET", full_path, "")
    r = requests.get(BITGET_BASE + full_path, headers=headers, timeout=15)
    if not r.text.strip():
        # v3 API returns 200 with empty body when result set is empty
        return {"code": "00000", "data": {}}
    return r.json()

def _auth_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body, separators=(',', ':'))
    headers = _auth_headers("POST", path, body_str)
    r = requests.post(BITGET_BASE + path, data=body_str, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def _with_retry(fn, *args, retries: int = 3, **kwargs):
    """Call fn(*args, **kwargs) up to `retries` times with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt          # 1s, 2s, 4s
            time.sleep(wait)

# ─── TRADE JOURNAL (SQLite) ───────────────────────────────────────────────────
def _init_db():
    con = sqlite3.connect(TRADES_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            symbol      TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            side        TEXT,
            size        REAL,
            price       REAL,
            order_id    TEXT,
            balance_before REAL,
            confidence  INTEGER,
            detail      TEXT
        )
    """)
    con.commit()
    con.close()

_init_db()

def _log_trade(action: str, side: str, size: float, price: float,
               order_id: str, balance_before: float, confidence: int, detail: str):
    try:
        con = sqlite3.connect(TRADES_DB)
        con.execute(
            "INSERT INTO trades (ts,symbol,action,side,size,price,order_id,"
            "balance_before,confidence,detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), SYMBOL, action, side,
             size, price, order_id, balance_before, confidence, detail)
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[journal] {e}")

def get_trade_history(limit: int = 50) -> list:
    try:
        con = sqlite3.connect(TRADES_DB)
        rows = con.execute(
            "SELECT ts,symbol,action,side,size,price,order_id,confidence,detail "
            "FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        keys = ["ts","symbol","action","side","size","price","order_id","confidence","detail"]
        return [dict(zip(keys, r)) for r in rows]
    except Exception:
        return []

def _count_today_trades() -> int:
    try:
        today = date.today().isoformat()
        con   = sqlite3.connect(TRADES_DB)
        n     = con.execute(
            "SELECT COUNT(*) FROM trades WHERE ts LIKE ? AND action LIKE 'OPEN%'",
            (f"{today}%",)
        ).fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0

# ─── BALANCE ──────────────────────────────────────────────────────────────────
_BALANCE_CANDIDATES = [
    # Unified Trading Account (One-Account) — v3 API
    ("/api/v3/account/assets",              {}),
    # Classic API (fail with 40085 on unified accounts)
    ("/api/v2/spot/account/assets",         {"coin": "USDT"}),
    ("/api/v2/spot/account/assets",         {}),
    ("/api/v2/mix/account/accounts",        {"productType": PRODUCT}),
    ("/api/v2/account/all-account-balance", {"coin": "USDT"}),
]

_balance_working_path = None
_balance_diagnosed    = False
_balance_retry_ts     = 0.0

def _extract_usdt(data) -> float | None:
    if isinstance(data, dict):
        # Check coin filter first — if response has list of coins, find USDT entry
        coin = data.get("coin", data.get("coinName", ""))
        if coin and coin.upper() not in ("USDT", ""):
            return None
        for f in ("available", "availableAmount", "crossMaxAvailable", "free",
                  "equity", "usdtEquity", "availableBalance", "walletBalance",
                  "totalAmount", "netAsset"):
            v = data.get(f)
            if v not in (None, "", "0", 0):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return float(data.get("available", 0) or 0)
    if isinstance(data, list):
        for item in data:
            coin = (item.get("coin") or item.get("currency") or "").upper()
            if coin == "USDT" or coin == "":
                for f in ("available","availableAmount","crossMaxAvailable","free"):
                    v = item.get(f)
                    if v not in (None, "", "0", 0):
                        return float(v)
    return None

def get_futures_balance() -> float:
    """Returns available USDT — tries every known Bitget endpoint variant."""
    global _balance_working_path, _balance_diagnosed, _balance_retry_ts

    def _prefer_manual(api_val: float) -> float:
        """Return api_val if > 0, else fall back to manual budget."""
        return api_val if api_val > 0 else (MANUAL_BALANCE if MANUAL_BALANCE > 0 else 0.0)

    if _balance_working_path:
        try:
            r = _auth_get_raw(_balance_working_path, {})
            if r.get("code") == "00000":
                v = _extract_usdt(r.get("data"))
                return _prefer_manual(v if v is not None else 0.0)
        except Exception:
            _balance_working_path = None

    if time.time() - _balance_retry_ts < 300 and _balance_retry_ts > 0:
        return MANUAL_BALANCE if MANUAL_BALANCE > 0 else 0.0
    _balance_retry_ts = time.time()

    for path, params in _BALANCE_CANDIDATES:
        try:
            r = _auth_get_raw(path, params)
            code = r.get("code", "")
            if code == "00000":
                v = _extract_usdt(r.get("data"))
                if v is not None:
                    print(f"[balance] found via {path} -> ${v:.2f}")
                    _balance_working_path = path
                    _balance_diagnosed    = True
                    return _prefer_manual(v)
            elif not _balance_diagnosed:
                print(f"[balance probe] {path} -> {code}: {r.get('msg','')}")
        except Exception as ex:
            if not _balance_diagnosed:
                print(f"[balance probe] {path} -> {ex}")

    if not _balance_diagnosed:
        if MANUAL_BALANCE > 0:
            print(f"[balance] no API endpoint worked — using manual budget ${MANUAL_BALANCE:.2f}")
        else:
            print("[balance] no endpoint returned USDT balance — showing N/A in UI")
        _balance_diagnosed = True
    return MANUAL_BALANCE

# ─── POSITION TRACKING ────────────────────────────────────────────────────────
_is_unified_account: bool | None = None

def _load_position_from_disk() -> dict | None:
    try:
        if POSITION_FILE.exists():
            data = json.loads(POSITION_FILE.read_text())
            if data and data.get("symbol") == SYMBOL:
                return data
    except Exception:
        pass
    return None

def _save_position_to_disk(pos: dict | None):
    try:
        POSITION_FILE.write_text(json.dumps(pos or {}))
    except Exception as e:
        print(f"[state] position save failed: {e}")

def _set_local_position(side: str | None, size: float = 0.0,
                        entry_price: float = 0.0, order_id: str = ""):
    pos = None if (side is None or size == 0) else {
        "holdSide":   side,
        "total":      str(size),
        "available":  str(size),
        "symbol":     SYMBOL,
        "entryPrice": str(entry_price),
        "orderId":    order_id,
        "openedAt":   datetime.now(timezone.utc).isoformat(),
    }
    _save_position_to_disk(pos)

def _get_position_v3() -> dict | None:
    """Query open position via Unified Trading Account v3 API."""
    try:
        r = _auth_get_raw("/api/v3/position/current-position", {
            "category": PRODUCT, "symbol": SYMBOL
        })
        if r.get("code") == "00000":
            positions = (r.get("data") or {}).get("list") or []
            for p in positions:
                if (p.get("symbol", "").upper() == SYMBOL
                        and float(p.get("total", 0)) > 0):
                    # Normalize to v2-style keys so callers are unchanged
                    norm = {
                        "holdSide":   p.get("posSide", "").lower(),
                        "total":      p.get("total"),
                        "available":  p.get("available"),
                        "symbol":     p.get("symbol"),
                        "entryPrice": p.get("avgPrice"),
                        "openedAt":   p.get("createdTime"),
                        "_source":    "v3",
                    }
                    _save_position_to_disk(norm)
                    return norm
            _save_position_to_disk(None)
            return None
        else:
            print(f"[position v3] code={r.get('code')} msg={r.get('msg')}")
    except Exception as e:
        print(f"[position v3 error] {e}")
    return None


def get_open_position() -> dict | None:
    """Returns current open position for SYMBOL, or None.
    Tries v3 (Unified Account) first, falls back to v2 then disk state."""
    global _is_unified_account

    # Unified Account: use v3 API
    if _is_unified_account is True:
        pos = _get_position_v3()
        return pos if pos is not None else _load_position_from_disk()

    # Unknown or Classic account: try v2
    if _is_unified_account is not False:
        try:
            r = _auth_get_raw("/api/v2/mix/position/all-position", {
                "productType": PRODUCT, "marginCoin": "USDT"
            })
            code = r.get("code")
            if code == "00000":
                _is_unified_account = False
                for p in (r.get("data") or []):
                    if p.get("symbol", "").upper() == SYMBOL and float(p.get("total", 0)) > 0:
                        _save_position_to_disk(p)
                        return p
                _save_position_to_disk(None)
                return None
            elif code == "40085":
                if _is_unified_account is None:
                    print("[account] Unified Account detected - switching to v3 API")
                _is_unified_account = True
                pos = _get_position_v3()
                return pos if pos is not None else _load_position_from_disk()
            else:
                print(f"[position] code={code} msg={r.get('msg')}")
        except Exception as e:
            print(f"[position error] {e}")

    # Classic account or any API failure: read from disk
    if _is_unified_account is False:
        return _load_position_from_disk()
    return _load_position_from_disk()

# ─── ORDER PLACEMENT ──────────────────────────────────────────────────────────
def _safe_post(path: str, body: dict) -> dict:
    """_auth_post wrapper that returns the error JSON instead of raising."""
    try:
        return _with_retry(_auth_post, path, body, retries=2)
    except Exception as e:
        if hasattr(e, "response") and e.response is not None:
            try:
                return e.response.json()
            except Exception:
                pass
        return {"code": "ERROR", "msg": str(e)}


def place_order(side: str, trade_side: str, size: str,
                sl: float = None, tp: float = None) -> dict:
    """
    Place a futures order. Routes to v3 (Unified Account) or v2 (Classic) automatically.
    side:       'buy' | 'sell'
    trade_side: 'open' | 'close'
    """
    if _is_unified_account:
        # v3 Unified Trading Account API
        # posSide of the POSITION: open=buy→long/sell→short; close=sell→long/buy→short
        if trade_side == "open":
            pos_side = "long" if side == "buy" else "short"
        else:
            pos_side = "long" if side == "sell" else "short"

        body: dict = {
            "category":  PRODUCT,
            "symbol":    SYMBOL,
            "qty":       size,
            "side":      side,
            "orderType": "market",
            "posSide":   pos_side,
        }
        if trade_side == "close":
            body["reduceOnly"] = "yes"
        if sl and trade_side == "open":
            body["stopLossPrice"]   = str(round(sl, 2))
        if tp and trade_side == "open":
            body["takeProfitPrice"] = str(round(tp, 2))
        return _safe_post("/api/v3/trade/place-order", body)

    # Classic v2 API
    body = {
        "symbol":      SYMBOL,
        "productType": PRODUCT,
        "marginMode":  "isolated",
        "marginCoin":  "USDT",
        "size":        size,
        "side":        side,
        "tradeSide":   trade_side,
        "orderType":   "market",
    }
    if sl and trade_side == "open":
        body["presetStopLossPrice"]   = str(round(sl, 2))
    if tp and trade_side == "open":
        body["presetTakeProfitPrice"] = str(round(tp, 2))
    return _safe_post("/api/v2/mix/order/place-order", body)

# ─── EXECUTION SAFEGUARDS ─────────────────────────────────────────────────────
_last_exec_ts: float = 0.0

def execute_trade(decision: dict, signals: dict) -> dict:
    global _last_exec_ts
    result = {"executed": False, "action": "SKIP", "detail": ""}

    if not EXEC_ENABLED:
        result["detail"] = "execution disabled"
        return result
    if not BITGET_API_KEY:
        result["detail"] = "no API key"
        return result

    direction  = decision["direction"]
    confidence = decision["confidence"]
    price      = float(signals["technical"]["price"])

    pos          = get_open_position()
    current_side = pos.get("holdSide", "").lower() if pos else ""

    # ── FLAT: close open position ─────────────────────────────────────────────
    if direction == "FLAT":
        if pos and current_side:
            close_side = "sell" if current_side == "long" else "buy"
            size       = str(pos.get("available") or pos.get("total") or "0")
            r          = place_order(close_side, "close", size)
            if r.get("code") == "00000":
                _set_local_position(None)
                _log_trade("CLOSE", current_side, float(size), price,
                           r.get("data", {}).get("orderId", ""), 0, confidence,
                           f"closed {current_side} {size}")
                result.update({"executed": True, "action": "CLOSE",
                                "detail": f"closed {current_side} {size}"})
            else:
                result["detail"] = r.get("msg", f"close failed ({r.get('code')})")
        else:
            result["detail"] = "FLAT — no open position"
        return result

    want_side = "long" if direction == "LONG" else "short"

    # ── Already in the same direction — hold ─────────────────────────────────
    if current_side == want_side:
        result["detail"] = f"holding {want_side}"
        return result

    # ── Cooldown: don't trade too frequently ──────────────────────────────────
    secs_since = time.time() - _last_exec_ts
    if _last_exec_ts > 0 and secs_since < EXEC_COOLDOWN:
        remaining = int(EXEC_COOLDOWN - secs_since)
        result["detail"] = f"cooldown — {remaining}s remaining"
        return result

    # ── Daily trade cap ───────────────────────────────────────────────────────
    today_count = _count_today_trades()
    if today_count >= MAX_DAILY_TRADES:
        result["detail"] = f"daily limit reached ({today_count}/{MAX_DAILY_TRADES} trades)"
        return result

    # ── Close opposite before reversing ──────────────────────────────────────
    if pos and current_side:
        close_side = "sell" if current_side == "long" else "buy"
        size       = str(pos.get("available") or pos.get("total") or "0")
        r          = place_order(close_side, "close", size)
        if r.get("code") == "00000":
            _set_local_position(None)

    # ── Size calculation ──────────────────────────────────────────────────────
    balance    = get_futures_balance()
    if balance < 3.0:
        result["detail"] = f"insufficient balance (${balance:.2f} USDT)"
        return result

    usdt_alloc = balance * (EXEC_MAX_PCT / 100.0)
    asset_size = round(usdt_alloc / price, 4)
    asset_size = max(_min_lot(), asset_size)

    # ── Place order ───────────────────────────────────────────────────────────
    open_side = "buy" if direction == "LONG" else "sell"
    r = place_order(open_side, "open", str(asset_size),
                    sl=decision.get("stop_loss"),
                    tp=decision.get("take_profit"))

    if r.get("code") == "00000":
        order_id    = r.get("data", {}).get("orderId", "")
        _last_exec_ts = time.time()
        _set_local_position(want_side, asset_size, price, order_id)
        _log_trade(f"OPEN_{direction}", want_side, asset_size, price,
                   order_id, balance, confidence,
                   f"{asset_size} @ ~${price:,.0f}")
        result.update({
            "executed":  True,
            "action":    f"OPEN_{direction}",
            "size_btc":  asset_size,
            "order_id":  order_id,
            "detail":    f"{asset_size} {SYMBOL.replace('USDT','')} @ ~${price:,.0f} | {order_id}",
        })
    else:
        msg = r.get("msg", f"order failed ({r.get('code','?')})")
        _log_trade(f"FAIL_{direction}", want_side, asset_size, price,
                   "", balance, confidence, msg)
        result["detail"] = msg

    return result

# ─── SIGNAL 1: TECHNICAL ──────────────────────────────────────────────────────
def get_technical_signal() -> dict:
    raw = public_get("/api/v2/mix/market/candles", {
        "symbol": SYMBOL, "productType": PRODUCT,
        "granularity": "15m", "limit": 60
    })
    candles = raw if isinstance(raw, list) else raw.get("candles", [])
    if len(candles) < 26:
        return {"signal": "NEUTRAL", "rsi": 50, "trend": "FLAT", "macd": "FLAT"}

    # candle format: [ts, open, high, low, close, baseVol, quoteVol]
    closes = np.array([float(c[4]) for c in candles])

    # RSI(14)
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag = np.mean(gains[-14:]);  al = np.mean(losses[-14:])
    rsi = round(100 - 100 / (1 + ag / (al + 1e-9)), 1)

    # SMA20 trend
    sma20 = np.mean(closes[-20:])
    price = closes[-1]
    trend = "UP" if price > sma20 * 1.002 else "DOWN" if price < sma20 * 0.998 else "FLAT"

    # EMA12 vs EMA26 (MACD direction)
    ema12 = np.mean(closes[-12:])
    ema26 = np.mean(closes[-26:])
    macd_dir = "POSITIVE" if ema12 > ema26 else "NEGATIVE"

    # Derive signal
    if rsi < 38 and trend != "DOWN":
        signal = "BULLISH"
    elif rsi > 68 and trend != "UP":
        signal = "BEARISH"
    elif trend == "UP" and macd_dir == "POSITIVE":
        signal = "BULLISH"
    elif trend == "DOWN" and macd_dir == "NEGATIVE":
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "signal":   signal,
        "rsi":      rsi,
        "trend":    trend,
        "macd":     macd_dir,
        "price":    round(price, 2),
        "sma20":    round(float(sma20), 2),
    }

# ─── SIGNAL 2: SENTIMENT ──────────────────────────────────────────────────────
def get_sentiment_signal() -> dict:
    fr_data = public_get("/api/v2/mix/market/current-fund-rate", {
        "symbol": SYMBOL, "productType": PRODUCT
    })
    rate = float(fr_data[0]["fundingRate"]) if isinstance(fr_data, list) else 0.0

    # Funding rate interpretation:
    # High positive → longs paying → crowd is bullish → contrarian BEARISH
    # High negative → shorts paying → crowd is bearish → contrarian BULLISH
    if rate > 0.0003:
        signal = "BEARISH"   # crowd too bullish
        note   = "High funding: longs overextended"
    elif rate < -0.0001:
        signal = "BULLISH"   # crowd too bearish
        note   = "Negative funding: shorts overextended"
    else:
        signal = "NEUTRAL"
        note   = "Funding near zero: balanced positioning"

    return {
        "signal":       signal,
        "funding_rate": round(rate * 100, 5),
        "note":         note,
    }

# ─── SIGNAL 3: MOMENTUM ───────────────────────────────────────────────────────
def get_momentum_signal() -> dict:
    t = public_get("/api/v2/mix/market/ticker", {
        "symbol": SYMBOL, "productType": PRODUCT
    })
    ticker = t[0] if isinstance(t, list) else t

    oi_raw = public_get("/api/v2/mix/market/open-interest", {
        "symbol": SYMBOL, "productType": PRODUCT
    })
    oi = float(oi_raw.get("openInterestList", [{}])[0].get("size", 0))

    change_24h    = float(ticker.get("change24h", 0)) * 100
    volume_24h    = float(ticker.get("usdtVolume", 0))
    high_24h      = float(ticker.get("high24h", 0))
    low_24h       = float(ticker.get("low24h", 0))
    price         = float(ticker.get("lastPr", 0))

    # Where is price in the 24h range? >70% = strength
    range_pct = (price - low_24h) / (high_24h - low_24h + 1e-9) * 100 if high_24h != low_24h else 50

    if change_24h > 1.5 and range_pct > 60:
        signal = "BULLISH"
    elif change_24h < -1.5 and range_pct < 40:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {
        "signal":         signal,
        "change_24h_pct": round(change_24h, 2),
        "volume_24h_usd": round(volume_24h, 0),
        "range_position": round(range_pct, 1),
        "open_interest":  round(oi, 1),
    }

# ─── SIGNAL 4: MARKET DEPTH ───────────────────────────────────────────────────
def get_depth_signal() -> dict:
    raw = public_get("/api/v2/mix/market/merge-depth", {
        "symbol": SYMBOL, "productType": PRODUCT, "limit": "20"
    })
    bids = raw.get("bids", [])
    asks = raw.get("asks", [])

    if not bids or not asks:
        return {"signal": "NEUTRAL", "imbalance": 0.0, "spread_pct": 0.0}

    best_bid = float(bids[0][0]);  best_ask = float(asks[0][0])
    spread_pct = (best_ask - best_bid) / best_bid * 100

    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])
    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)

    signal = "BULLISH" if imbalance > 0.15 else "BEARISH" if imbalance < -0.15 else "NEUTRAL"

    return {
        "signal":     signal,
        "imbalance":  round(imbalance, 3),
        "spread_pct": round(spread_pct, 5),
        "best_bid":   best_bid,
        "best_ask":   best_ask,
    }

# ─── SIGNAL 5: VOLATILITY REGIME ──────────────────────────────────────────────
def get_volatility_signal() -> dict:
    raw = public_get("/api/v2/mix/market/candles", {
        "symbol": SYMBOL, "productType": PRODUCT,
        "granularity": "1H", "limit": 24
    })
    candles = raw if isinstance(raw, list) else []
    if len(candles) < 10:
        return {"signal": "NEUTRAL", "regime": "UNKNOWN", "atr_pct": 0.0}

    highs  = np.array([float(c[2]) for c in candles])
    lows   = np.array([float(c[3]) for c in candles])
    closes = np.array([float(c[4]) for c in candles])

    # ATR as % of price
    tr     = highs - lows
    atr    = np.mean(tr[-14:])
    atr_pct = atr / closes[-1] * 100

    # Regime: expanding volatility = breakout potential
    recent_atr = np.mean(tr[-5:]);  older_atr = np.mean(tr[-14:-5])
    if recent_atr > older_atr * 1.3:
        regime = "EXPANDING"
        signal = "BULLISH" if closes[-1] > closes[-5] else "BEARISH"
    elif recent_atr < older_atr * 0.7:
        regime = "CONTRACTING"
        signal = "NEUTRAL"
    else:
        regime = "STABLE"
        signal = "NEUTRAL"

    return {
        "signal":  signal,
        "regime":  regime,
        "atr_pct": round(atr_pct, 3),
    }

# ─── LLM REASONING (QWEN) ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are BitAgent, an autonomous crypto trading analyst.
You receive 5 market signals for BTC/USDT perpetual futures and output a structured trade decision.
Signal priority: Technical > Sentiment > Momentum > Depth > Volatility.
When 3+ signals agree, confidence is high. When signals conflict, reduce size and confidence.
Always output FLAT if confidence is below 60."""

def reason_with_qwen(signals: dict) -> dict:
    if not QWEN_API_KEY:
        return _rule_based_decision(signals)

    from openai import OpenAI
    client = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE)

    user_msg = f"""Current BTC/USDT signals:

TECHNICAL:   {json.dumps(signals['technical'])}
SENTIMENT:   {json.dumps(signals['sentiment'])}
MOMENTUM:    {json.dumps(signals['momentum'])}
DEPTH:       {json.dumps(signals['depth'])}
VOLATILITY:  {json.dumps(signals['volatility'])}

Respond ONLY with valid JSON (no markdown):
{{
  "direction": "LONG" | "SHORT" | "FLAT",
  "confidence": <0-100>,
  "size_pct": <1.0-3.0>,
  "entry_price": <float>,
  "stop_loss": <float>,
  "take_profit": <float>,
  "signal_votes": {{"technical":"BULLISH|BEARISH|NEUTRAL","sentiment":"...","momentum":"...","depth":"...","volatility":"..."}},
  "reasoning": "<2 sentences>",
  "risk_note": "<1 sentence>"
}}"""

    resp = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=600,
    )
    raw = resp.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1].replace("json", "").strip()
    return json.loads(raw)

def _rule_based_decision(signals: dict) -> dict:
    """Fallback when no Qwen key — simple majority vote."""
    votes = [
        signals["technical"]["signal"],
        signals["sentiment"]["signal"],
        signals["momentum"]["signal"],
        signals["depth"]["signal"],
        signals["volatility"]["signal"],
    ]
    bulls = votes.count("BULLISH");  bears = votes.count("BEARISH")
    price = signals["technical"]["price"]

    if bulls >= 3:
        direction = "LONG";  confidence = 55 + bulls * 5
    elif bears >= 3:
        direction = "SHORT"; confidence = 55 + bears * 5
    else:
        direction = "FLAT";  confidence = 40

    stop_dist = price * 0.015
    tp_dist   = price * 0.03
    return {
        "direction":   direction,
        "confidence":  min(confidence, 85),
        "size_pct":    2.0,
        "entry_price": price,
        "stop_loss":   round(price - stop_dist if direction == "LONG" else price + stop_dist, 2),
        "take_profit": round(price + tp_dist   if direction == "LONG" else price - tp_dist,  2),
        "signal_votes":{"technical": votes[0],"sentiment": votes[1],"momentum": votes[2],
                        "depth": votes[3],"volatility": votes[4]},
        "reasoning":   f"Rule-based: {bulls} bullish, {bears} bearish signals out of 5.",
        "risk_note":   "No LLM key — rule-based fallback active.",
    }

# ─── RISK MANAGEMENT ──────────────────────────────────────────────────────────
def apply_risk_rules(decision: dict) -> dict:
    if decision["confidence"] < MIN_CONFIDENCE:
        decision["direction"] = "FLAT"
        decision["risk_note"] = (decision.get("risk_note") or "") + \
            f" Confidence {decision['confidence']}% below {MIN_CONFIDENCE}% threshold — forced FLAT."
    if decision["size_pct"] > MAX_SIZE_PCT:
        decision["size_pct"] = MAX_SIZE_PCT
    decision.setdefault("risk_note", "")
    return decision

# ─── OUTPUT ───────────────────────────────────────────────────────────────────
ICONS = {"BULLISH": "[+]", "BEARISH": "[-]", "NEUTRAL": "[ ]"}
DIR_ICONS = {"LONG": "LONG ", "SHORT": "SHORT", "FLAT": "FLAT "}

def print_decision(decision: dict, cycle: int):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    d  = decision
    print(f"\n{'═'*62}")
    print(f"  BITAGENT  |  Cycle {cycle}  |  {ts}")
    print(f"{'═'*62}")
    print("  Signal breakdown:")
    for k, v in d["signal_votes"].items():
        print(f"    {ICONS.get(v,'⚪')}  {k:<14} {v}")
    print(f"\n  {DIR_ICONS.get(d['direction'],'➡️')} {d['direction']}  "
          f"(confidence: {d['confidence']}%  |  size: {d['size_pct']}%)")
    print(f"  Entry:      ${d['entry_price']:,.2f}")
    print(f"  Stop loss:  ${d['stop_loss']:,.2f}")
    print(f"  Take profit:${d['take_profit']:,.2f}")
    print(f"\n  {d['reasoning']}")
    print(f"  ⚠  {d['risk_note']}")
    print(f"{'═'*62}")

def save_log(cycle: int, signals: dict, decision: dict):
    entry = {
        "cycle":    cycle,
        "ts":       datetime.now(timezone.utc).isoformat(),
        "signals":  signals,
        "decision": decision,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def run():
    print("\nBitAgent -- Multi-Signal AI Trading Agent")
    print(f"    Symbol: {SYMBOL}  |  Mode: SIM  |  Interval: {LOOP_SECS}s")
    if not QWEN_API_KEY:
        print("    [!] QWEN_API_KEY not set -- using rule-based fallback")
    print()

    cycle = 0
    while True:
        cycle += 1
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle} — collecting signals...")

        try:
            # 1. PERCEIVE
            technical  = get_technical_signal()
            sentiment  = get_sentiment_signal()
            momentum   = get_momentum_signal()
            depth      = get_depth_signal()
            volatility = get_volatility_signal()

            signals = {
                "technical":  technical,
                "sentiment":  sentiment,
                "momentum":   momentum,
                "depth":      depth,
                "volatility": volatility,
            }

            print(f"  signals  RSI={technical['rsi']} | "
                  f"funding={sentiment['funding_rate']}% | "
                  f"24h={momentum['change_24h_pct']}% | "
                  f"depth={depth['imbalance']} | "
                  f"vol={volatility['regime']}")

            # 2. REASON
            print("  reasoning with LLM...")
            decision = reason_with_qwen(signals)

            # 3. RISK CHECK
            decision = apply_risk_rules(decision)

            # 4. DISPLAY + LOG
            print_decision(decision, cycle)
            save_log(cycle, signals, decision)

        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"  ERROR: {e}")

        print(f"\n  Next cycle in {LOOP_SECS}s  (Ctrl+C to stop)\n")
        time.sleep(LOOP_SECS)


if __name__ == "__main__":
    run()
