import os, json, time, hmac, hashlib, base64, sys, sqlite3, re
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

MANUAL_BALANCE: float = 0.0

BITGET_BASE    = "https://api.bitget.com"
QWEN_BASE      = "https://hackathon.bitgetops.com/v1"
QWEN_MODEL     = "qwen3.6-plus"

SYMBOL         = "BTCUSDT"
PRODUCT        = "USDT-FUTURES"
LOOP_SECS      = 60
MIN_CONFIDENCE = 60
MAX_SIZE_PCT   = 3.0

DATA_DIR       = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE       = DATA_DIR / "agent_log.jsonl"
POSITION_FILE  = DATA_DIR / "position.json"
TRADES_DB      = DATA_DIR / "trades.db"
CREDENTIALS_DISABLED_FILE = DATA_DIR / "credentials_disabled.flag"

if CREDENTIALS_DISABLED_FILE.exists():
    BITGET_API_KEY = ""
    BITGET_SECRET_KEY = ""
    BITGET_PASSPHRASE = ""

EXEC_MODE        = os.getenv("EXEC_MODE", "paper").strip().lower()
EXEC_ENABLED     = os.getenv("EXEC_ENABLED", "true").lower() == "true"
EXEC_MAX_PCT     = float(os.getenv("EXEC_MAX_PCT", "1.0"))
EXEC_COOLDOWN    = int(os.getenv("EXEC_COOLDOWN", "300"))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "10"))
PAPER_BALANCE    = float(os.getenv("PAPER_BALANCE", "10000"))

MIN_LOT_SIZES = {
    # Major
    "BTC":   0.0001, "ETH":  0.01,  "SOL":  0.1,   "BNB":  0.01,
    "LTC":   0.1,    "LINK": 1.0,   "AAVE": 0.1,
    # Mid-range
    "XRP":   1.0,    "ADA":  1.0,   "AVAX": 0.1,   "DOT":  1.0,
    "ATOM":  1.0,    "NEAR": 1.0,   "UNI":  1.0,   "INJ":  0.1,
    "APT":   0.001,  "SUI":  0.1,   "TIA":  0.1,   "JUP":  1.0,
    "WLD":   1.0,    "CRV":  1.0,   "OP":   0.1,   "ARB":  0.01,
    # Layer-1 / Ecosystems
    "FIL":   0.1,    "ICP":  0.1,   "ALGO": 1.0,   "EGLD": 0.1,
    "FLOW":  1.0,    "ASTR": 1.0,   "ETC":  0.1,   "XLM":  1.0,
    "VET":   10.0,   "HBAR": 10.0,  "ZIL":  10.0,  "KSM":  0.01,
    # DeFi / Governance
    "COMP":  0.01,   "MKR":  0.001, "YFI":  0.001, "SNX":  1.0,
    "SUSHI": 1.0,    "RUNE": 1.0,   "1INCH":1.0,   "ENS":  1.0,
    "LDO":   1.0,    "GRT":  10.0,  "DYDX": 1.0,   "FTM":  1.0,
    "KAVA":  1.0,    "RDNT": 1.0,   "MAGIC":1.0,   "MASK": 1.0,
    # Gaming / Metaverse
    "AXS":   0.1,    "ALICE":1.0,   "THETA":1.0,   "GMT":  1.0,
    "APE":   1.0,    "CAKE": 1.0,   "TWT":  1.0,
    # Infrastructure / Other
    "ANKR":  10.0,   "ROSE": 10.0,  "CELR": 10.0,  "OCEAN":1.0,
    "STORJ": 1.0,    "BTT":  1000.0,"HOT":  1000.0,"JASMY":10.0,
    "BLUR":  1.0,
    # Low-cost / Meme
    "TRX":   1.0,    "DOGE": 1.0,   "SHIB": 10000.0, "PEPE": 1000.0,
    "WIF":   0.1,    "SEI":  1.0,   "SAND": 1.0,   "MANA": 1.0,
    "GALA":  1.0,    "CHZ":  20.0,  "ENJ":  3.0,   "NOT":  10.0,
    "FLOKI": 1.0,    "TURBO":1.0,   "PYTH": 1.0,   "POPCAT":1.0,
    "PNUT":  1.0,    "MOODENG":1.0,
}


def set_credentials(api_key: str, secret_key: str, passphrase: str):
    global BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE
    global _balance_working_path, _balance_diagnosed, _balance_retry_ts
    global _balance_fail_count, _is_unified_account
    BITGET_API_KEY    = api_key
    BITGET_SECRET_KEY = secret_key
    BITGET_PASSPHRASE = passphrase
    if credentials_set() and CREDENTIALS_DISABLED_FILE.exists():
        CREDENTIALS_DISABLED_FILE.unlink(missing_ok=True)
    _balance_working_path = None
    _balance_diagnosed    = False
    _balance_retry_ts     = 0.0
    _balance_fail_count   = 0
    _is_unified_account   = None


def set_manual_balance(usdt: float):
    global MANUAL_BALANCE
    MANUAL_BALANCE = float(usdt)


def mark_credentials_disabled():
    CREDENTIALS_DISABLED_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def credentials_set() -> bool:
    return bool(BITGET_API_KEY and BITGET_SECRET_KEY and BITGET_PASSPHRASE)


def execution_mode() -> str:
    return "live" if EXEC_MODE == "live" else "paper"


def is_paper_mode() -> bool:
    return execution_mode() == "paper"


_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


def normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(sym):
        raise ValueError("Invalid symbol. Use a USDT futures pair such as BTCUSDT.")
    return sym


def set_symbol(symbol: str):
    global SYMBOL
    SYMBOL = normalize_symbol(symbol)


def _min_lot() -> float:
    return MIN_LOT_SIZES.get(SYMBOL.replace("USDT", ""), 0.01)


# ─── BITGET REST API ──────────────────────────────────────────────────────────
def _sign(ts: str, method: str, path: str, body: str = "") -> str:
    return base64.b64encode(
        hmac.new(
            BITGET_SECRET_KEY.encode(),
            (ts + method + path + body).encode(),
            hashlib.sha256,
        ).digest()
    ).decode()


def _auth_headers(method: str, path: str, body: str = "") -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY":        BITGET_API_KEY,
        "ACCESS-SIGN":       _sign(ts, method, path, body),
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": BITGET_PASSPHRASE,
        "Content-Type":      "application/json",
    }


def public_get(path: str, params: dict = None) -> dict:
    r = requests.get(BITGET_BASE + path, params=params or {}, timeout=15)
    r.raise_for_status()
    # Use "or {}" — .get("data", {}) only falls back when key is absent,
    # but Bitget sometimes sends "data": null (key present, value null).
    return r.json().get("data") or {}


def _auth_get_raw(path: str, params: dict = None) -> dict:
    qs        = urlencode(params or {})
    full_path = f"{path}?{qs}" if qs else path
    r = requests.get(
        BITGET_BASE + full_path,
        headers=_auth_headers("GET", full_path),
        timeout=15,
    )
    if not r.text.strip():
        # Bitget v3 returns HTTP 200 with an empty body when the result set is empty
        return {"code": "00000", "data": {}}
    return r.json()


def _auth_post(path: str, body: dict) -> dict:
    body_str = json.dumps(body, separators=(",", ":"))
    r = requests.post(
        BITGET_BASE + path,
        data=body_str,
        headers=_auth_headers("POST", path, body_str),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _with_retry(fn, *args, retries: int = 3, **kwargs):
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def _safe_post(path: str, body: dict) -> dict:
    try:
        return _with_retry(_auth_post, path, body, retries=2)
    except Exception as e:
        if hasattr(e, "response") and e.response is not None:
            try:
                return e.response.json()
            except Exception:
                pass
        return {"code": "ERROR", "msg": str(e)}


# ─── TRADE JOURNAL ────────────────────────────────────────────────────────────
def _init_db():
    con = sqlite3.connect(TRADES_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            action         TEXT NOT NULL,
            side           TEXT,
            size           REAL,
            price          REAL,
            order_id       TEXT,
            balance_before REAL,
            confidence     INTEGER,
            detail         TEXT
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
             size, price, order_id, balance_before, confidence, detail),
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[journal] {e}")


def get_trade_history(limit: int = 50) -> list:
    try:
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 50
        row_limit = max(limit * 3, 150)
        con  = sqlite3.connect(TRADES_DB)
        rows = con.execute(
            "SELECT ts,symbol,action,side,size,price,order_id,confidence,detail "
            f"FROM trades ORDER BY id DESC LIMIT {row_limit}",
        ).fetchall()
        con.close()
        keys = ["ts", "symbol", "action", "side", "size",
                "price", "order_id", "confidence", "detail"]
        out = []
        open_by_symbol = {}
        for r in rows:
            item = dict(zip(keys, r))
            action = str(item.get("action") or "").upper()
            symbol = item.get("symbol") or SYMBOL
            side = str(item.get("side") or "").lower()
            size = float(item.get("size") or 0)
            price = float(item.get("price") or 0)
            item["pnl"] = None
            item["entry_price"] = None
            item["exit_price"] = None
            item["audit"] = "Executed paper/live fill" if "PAPER" not in action else "Executed paper fill"

            if action.startswith("OPEN_"):
                open_by_symbol[symbol] = {"side": side, "size": size, "price": price}
                item["entry_price"] = price
                item["audit"] = f"Opened {side or 'position'} after the decision passed execution rules."
            elif action.startswith("CLOSE_"):
                prev = open_by_symbol.get(symbol)
                item["exit_price"] = price
                if prev:
                    item["entry_price"] = prev["price"]
                    if prev["side"] == "long":
                        item["pnl"] = round((price - prev["price"]) * prev["size"], 4)
                    elif prev["side"] == "short":
                        item["pnl"] = round((prev["price"] - price) * prev["size"], 4)
                    item["audit"] = f"Closed {prev['side']} from ${prev['price']:,.4g} to ${price:,.4g}."
                    open_by_symbol.pop(symbol, None)
                else:
                    item["audit"] = "Close fill recorded; matching open was not found in current journal window."
            out.append(item)
        return out[:limit]
    except Exception:
        return []


def get_paper_account(mark_price: float | None = None) -> dict:
    initial = MANUAL_BALANCE if MANUAL_BALANCE > 0 else PAPER_BALANCE
    realized = 0.0
    pos_side = ""
    pos_size = 0.0
    pos_entry = 0.0

    try:
        con = sqlite3.connect(TRADES_DB)
        rows = con.execute(
            "SELECT symbol,action,side,size,price FROM trades "
            "WHERE action LIKE '%_PAPER' ORDER BY id ASC",
        ).fetchall()
        con.close()
    except Exception:
        rows = []

    open_by_symbol: dict[str, dict] = {}
    for symbol, action, side, size, price in rows:
        symbol = str(symbol or SYMBOL).upper()
        action = str(action or "").upper()
        side = str(side or "").lower()
        size = float(size or 0)
        price = float(price or 0)
        if size <= 0 or price <= 0:
            continue
        if action.startswith("OPEN_"):
            open_by_symbol[symbol] = {"side": side, "size": size, "entry": price}
        elif action.startswith("CLOSE_"):
            open_pos = open_by_symbol.get(symbol)
            if open_pos and open_pos["size"] > 0:
                if open_pos["side"] == "long":
                    realized += (price - open_pos["entry"]) * open_pos["size"]
                elif open_pos["side"] == "short":
                    realized += (open_pos["entry"] - price) * open_pos["size"]
                open_by_symbol.pop(symbol, None)

    active_pos = open_by_symbol.get(SYMBOL)
    if active_pos:
        pos_side = active_pos["side"]
        pos_size = active_pos["size"]
        pos_entry = active_pos["entry"]

    if mark_price is None:
        try:
            ticker = public_get(
                "/api/v2/mix/market/ticker",
                {"symbol": SYMBOL, "productType": PRODUCT},
            )
            ticker_item = ticker[0] if isinstance(ticker, list) and ticker else (ticker if isinstance(ticker, dict) else {})
            mark_price = float(ticker_item.get("lastPr", 0) or 0)
        except Exception:
            mark_price = 0.0

    unrealized = 0.0
    if pos_side and pos_size > 0 and pos_entry > 0 and mark_price and mark_price > 0:
        unrealized = (mark_price - pos_entry) * pos_size if pos_side == "long" else (pos_entry - mark_price) * pos_size

    equity = initial + realized + unrealized
    notional = pos_size * float(mark_price or pos_entry or 0) if pos_size > 0 else 0.0
    used_margin = notional if notional > 0 else 0.0
    free_equity = max(0.0, equity - used_margin)
    return {
        "mode":       "paper",
        "initial":    round(initial, 2),
        "realized":   round(realized, 4),
        "unrealized": round(unrealized, 4),
        "equity":     round(equity, 2),
        "used_margin": round(used_margin, 4),
        "free_equity": round(free_equity, 2),
        "notional":   round(notional, 4),
        "open_side":  pos_side or "flat",
        "open_size":  round(pos_size, 8),
        "entry":      round(pos_entry, 8),
        "mark":       round(float(mark_price or 0), 8),
    }


def get_decision_history(limit: int = 50) -> list:
    try:
        if not LOG_FILE.exists():
            return []
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-limit:]:
            if line.strip():
                out.append(json.loads(line))
        return out
    except Exception:
        return []


def _count_today_trades() -> int:
    try:
        con = sqlite3.connect(TRADES_DB)
        n   = con.execute(
            "SELECT COUNT(*) FROM trades WHERE ts LIKE ? AND action LIKE 'OPEN%'",
            (f"{date.today().isoformat()}%",),
        ).fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


def get_today_trade_count() -> int:
    return _count_today_trades()


# ─── BALANCE ──────────────────────────────────────────────────────────────────
_BALANCE_CANDIDATES = [
    ("/api/v3/account/assets",              {}),               # Unified Account (v3)
    ("/api/v2/spot/account/assets",         {"coin": "USDT"}), # Classic
    ("/api/v2/mix/account/accounts",        {"productType": PRODUCT}),
    ("/api/v2/account/all-account-balance", {"coin": "USDT"}),
]

_balance_working_path: str | None = None
_balance_diagnosed: bool           = False
_balance_retry_ts: float           = 0.0
_balance_fail_count: int           = 0


def _path_with_query(path: str, params: dict | None = None) -> str:
    qs = urlencode(params or {})
    return f"{path}?{qs}" if qs else path


def _extract_usdt(data) -> float | None:
    if isinstance(data, dict):
        for nested_key in ("assetsList", "coinAssets", "assets", "list"):
            nested = data.get(nested_key)
            if isinstance(nested, list):
                v = _extract_usdt(nested)
                if v is not None:
                    return v
        coin = data.get("coin") or data.get("coinName") or ""
        if coin and coin.upper() not in ("USDT", ""):
            return None
        for field in ("available", "availableAmount", "crossMaxAvailable", "free",
                      "equity", "usdtEquity", "availableBalance", "walletBalance",
                      "totalAmount", "netAsset"):
            v = data.get(field)
            if v not in (None, "", "0", 0):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return None
    if isinstance(data, list):
        for item in data:
            coin = (item.get("coin") or item.get("currency") or "").upper()
            if coin in ("USDT", ""):
                for field in ("available", "availableAmount", "crossMaxAvailable", "free",
                              "equity", "usdtEquity", "availableBalance", "walletBalance",
                              "totalAmount", "netAsset"):
                    v = item.get(field)
                    if v not in (None, "", "0", 0):
                        return float(v)
    return None


def get_futures_balance() -> float:
    global _balance_working_path, _balance_diagnosed, _balance_retry_ts, _balance_fail_count

    if is_paper_mode():
        return get_paper_account().get("equity", PAPER_BALANCE)

    def _resolve(api_val: float) -> float:
        return api_val if api_val > 0 else MANUAL_BALANCE

    if _balance_working_path:
        try:
            r = _auth_get_raw(_balance_working_path)
            if r.get("code") == "00000":
                _balance_fail_count = 0
                return _resolve(_extract_usdt(r.get("data")) or 0.0)
        except Exception:
            _balance_working_path = None

    # Only back off after 2 consecutive full-scan failures to avoid locking
    # out on the very first connection attempt due to a transient error.
    now = time.time()
    if _balance_fail_count >= 2 and _balance_retry_ts > 0 and now - _balance_retry_ts < 300:
        return MANUAL_BALANCE
    _balance_retry_ts = now

    for path, params in _BALANCE_CANDIDATES:
        try:
            r    = _auth_get_raw(path, params)
            code = r.get("code", "")
            if code == "00000":
                v = _extract_usdt(r.get("data"))
                if v is not None:
                    print(f"[balance] {path} -> ${v:.2f}")
                    _balance_working_path = _path_with_query(path, params)
                    _balance_diagnosed    = True
                    _balance_fail_count   = 0
                    return _resolve(v)
            elif not _balance_diagnosed:
                print(f"[balance] {path} -> {code} {r.get('msg', '')}")
        except Exception as ex:
            if not _balance_diagnosed:
                print(f"[balance] {path} -> {ex}")

    _balance_fail_count += 1
    if not _balance_diagnosed:
        suffix = f" — using manual budget ${MANUAL_BALANCE:.2f}" if MANUAL_BALANCE else ""
        print(f"[balance] no working endpoint found{suffix}")
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


def _recover_paper_position_from_journal() -> dict | None:
    try:
        con = sqlite3.connect(TRADES_DB)
        rows = con.execute(
            "SELECT ts,action,side,size,price,order_id FROM trades "
            "WHERE symbol = ? AND action LIKE '%_PAPER' "
            "ORDER BY id DESC LIMIT 2",
            (SYMBOL,),
        ).fetchall()
        con.close()
        if not rows:
            return None
        ts, action, side, size, price, order_id = rows[0]
        if str(action).upper().startswith("CLOSE_"):
            return None
        if not str(action).upper().startswith("OPEN_") or float(size or 0) <= 0:
            return None
        pos = {
            "holdSide":   side,
            "total":      str(size),
            "available":  str(size),
            "symbol":     SYMBOL,
            "entryPrice": str(price or 0),
            "orderId":    order_id or "paper",
            "openedAt":   ts,
        }
        _save_position_to_disk(pos)
        return pos
    except Exception as e:
        print(f"[position] paper recovery failed: {e}")
        return None


def _save_position_to_disk(pos: dict | None):
    try:
        POSITION_FILE.write_text(json.dumps(pos or {}))
    except Exception as e:
        print(f"[position] disk write failed: {e}")


def _set_local_position(side: str | None, size: float = 0.0,
                        entry_price: float = 0.0, order_id: str = ""):
    pos = None if not side or not size else {
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
    try:
        r = _auth_get_raw("/api/v3/position/current-position",
                          {"category": PRODUCT, "symbol": SYMBOL})
        if r.get("code") != "00000":
            print(f"[position] v3 -> {r.get('code')} {r.get('msg', '')}")
            return None
        data = r.get("data") or {}
        # Bitget v3 may return data as a list directly or as {"list": [...]}
        positions = data if isinstance(data, list) else (data.get("list") or [])
        for p in positions:
            if p.get("symbol", "").upper() == SYMBOL and float(p.get("total", 0)) > 0:
                norm = {
                    "holdSide":   p.get("posSide", "").lower(),
                    "total":      p.get("total"),
                    "available":  p.get("available"),
                    "symbol":     p.get("symbol"),
                    "entryPrice": p.get("avgPrice"),
                    "openedAt":   p.get("createdTime"),
                }
                _save_position_to_disk(norm)
                return norm
        _save_position_to_disk(None)
    except Exception as e:
        print(f"[position] v3 error: {e}")
    return None


def get_open_position() -> dict | None:
    global _is_unified_account

    if is_paper_mode():
        return _load_position_from_disk() or _recover_paper_position_from_journal()

    if _is_unified_account is True:
        return _get_position_v3() or _load_position_from_disk()

    try:
        r    = _auth_get_raw("/api/v2/mix/position/all-position",
                             {"productType": PRODUCT, "marginCoin": "USDT"})
        code = r.get("code")
        if code == "00000":
            _is_unified_account = False
            for p in r.get("data") or []:
                if p.get("symbol", "").upper() == SYMBOL and float(p.get("total", 0)) > 0:
                    _save_position_to_disk(p)
                    return p
            _save_position_to_disk(None)
            return None
        elif code == "40085":
            print("[account] Unified Account detected — switching to v3 API")
            _is_unified_account = True
            return _get_position_v3() or _load_position_from_disk()
        else:
            print(f"[position] {code} {r.get('msg', '')}")
    except Exception as e:
        print(f"[position] {e}")

    return _load_position_from_disk()


# ─── ORDER EXECUTION ──────────────────────────────────────────────────────────
def _price_dp(price: float) -> int:
    """Decimal places to use for a price of this magnitude (covers BTC down to DOGE)."""
    if price >= 1000: return 1
    if price >= 100:  return 2
    if price >= 10:   return 3
    if price >= 1:    return 4
    if price >= 0.1:  return 5
    return 6


def _round_price(price: float) -> float:
    return round(float(price), _price_dp(float(price)))


def place_order(side: str, trade_side: str, size: str,
                sl: float = None, tp: float = None) -> dict:
    if _is_unified_account:
        # For a close order the posSide is the position being reduced,
        # which is opposite to the closing order's side direction
        pos_side = ("long" if side == "buy"  else "short") if trade_side == "open" \
              else ("long" if side == "sell" else "short")
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
            body["stopLossPrice"]   = str(round(sl, _price_dp(sl)))
        if tp and trade_side == "open":
            body["takeProfitPrice"] = str(round(tp, _price_dp(tp)))
        return _safe_post("/api/v3/trade/place-order", body)

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
        body["presetStopLossPrice"]   = str(round(sl, _price_dp(sl)))
    if tp and trade_side == "open":
        body["presetTakeProfitPrice"] = str(round(tp, _price_dp(tp)))
    return _safe_post("/api/v2/mix/order/place-order", body)


def cancel_open_orders() -> dict:
    result = {"ok": False, "action": "CANCEL_ORDERS", "detail": ""}
    if is_paper_mode():
        result.update({"ok": True, "detail": "paper mode has no resting exchange orders"})
        return result
    if not credentials_set():
        result["detail"] = "no API key"
        return result

    candidates = []
    if _is_unified_account:
        candidates.append((
            "/api/v3/trade/cancel-all-order",
            {"category": PRODUCT, "symbol": SYMBOL},
        ))
    candidates.extend([
        (
            "/api/v2/mix/order/cancel-all-orders",
            {"symbol": SYMBOL, "productType": PRODUCT, "marginCoin": "USDT"},
        ),
        (
            "/api/v2/mix/order/cancel-all-orders",
            {"productType": PRODUCT, "marginCoin": "USDT"},
        ),
    ])

    last = {}
    for path, body in candidates:
        r = _safe_post(path, body)
        last = r
        if r.get("code") == "00000":
            result.update({"ok": True, "detail": "open orders canceled", "response": r})
            return result
    result["detail"] = last.get("msg", f"cancel failed ({last.get('code', '?')})")
    result["response"] = last
    return result


_last_exec_ts: float = 0.0
_session_analysis_symbol: str | None = None


def execute_trade(decision: dict, signals: dict) -> dict:
    global _last_exec_ts
    result = {"executed": False, "action": "SKIP", "detail": ""}

    if _session_analysis_symbol and _session_analysis_symbol != SYMBOL:
        result["detail"] = "symbol analysis in progress — skipping execution"
        return result

    if not EXEC_ENABLED:
        result["detail"] = "execution disabled"
        return result
    if not is_paper_mode() and not BITGET_API_KEY:
        result["detail"] = "no API key"
        return result

    direction  = decision["direction"]
    confidence = decision["confidence"]
    price      = float(signals["technical"]["price"])
    if price <= 0:
        result["detail"] = "market price unavailable"
        return result
    pos        = _load_position_from_disk() if is_paper_mode() else get_open_position()
    curr_side  = pos.get("holdSide", "").lower() if pos else ""

    if direction == "FLAT":
        if pos and curr_side:
            size       = str(pos.get("available") or pos.get("total") or "0")
            if is_paper_mode():
                _set_local_position(None)
                _log_trade("CLOSE_PAPER", curr_side, float(size), price,
                           "paper", 0, confidence, f"paper close {curr_side} {size}")
                result.update({"executed": True, "action": "CLOSE_PAPER",
                               "detail": f"paper closed {curr_side} {size}"})
                return result

            close_side = "sell" if curr_side == "long" else "buy"
            r          = place_order(close_side, "close", size)
            if r.get("code") == "00000":
                _set_local_position(None)
                _log_trade("CLOSE", curr_side, float(size), price,
                           r.get("data", {}).get("orderId", ""), 0, confidence,
                           f"closed {curr_side} {size}")
                result.update({"executed": True, "action": "CLOSE",
                               "detail": f"closed {curr_side} {size}"})
            else:
                result["detail"] = r.get("msg", f"close failed ({r.get('code')})")
        else:
            result["detail"] = "FLAT — no open position"
        return result

    want_side = "long" if direction == "LONG" else "short"

    if curr_side == want_side:
        result["detail"] = f"holding {want_side}"
        return result

    elapsed = time.time() - _last_exec_ts
    if _last_exec_ts > 0 and elapsed < EXEC_COOLDOWN:
        result["detail"] = f"cooldown — {int(EXEC_COOLDOWN - elapsed)}s remaining"
        return result

    if _count_today_trades() >= MAX_DAILY_TRADES:
        result["detail"] = f"daily limit reached ({MAX_DAILY_TRADES} trades)"
        return result

    if pos and curr_side:
        if is_paper_mode():
            size = str(pos.get("available") or pos.get("total") or "0")
            _set_local_position(None)
            _log_trade("CLOSE_PAPER", curr_side, float(size), price,
                       "paper", 0, confidence, f"paper flip close {curr_side} {size}")
        else:
            r = place_order(
                "sell" if curr_side == "long" else "buy",
                "close",
                str(pos.get("available") or pos.get("total") or "0"),
            )
            if r.get("code") == "00000":
                _set_local_position(None)
            else:
                result["detail"] = r.get("msg", f"flip-close failed ({r.get('code')})")
                return result

    balance = get_futures_balance()
    if balance < 0.5:
        result["detail"] = f"insufficient balance (${balance:.2f} USDT)"
        return result

    budget_usdt = balance * (EXEC_MAX_PCT / 100.0)
    min_lot = _min_lot()
    min_lot_notional = min_lot * price
    if budget_usdt < min_lot_notional:
        result["detail"] = (
            f"budget too small for min lot: need ~${min_lot_notional:.2f} "
            f"for {min_lot:g} {SYMBOL.replace('USDT', '')}"
        )
        return result

    asset_size = round(budget_usdt / price, 4)
    if asset_size < min_lot:
        asset_size = min_lot

    if is_paper_mode():
        _last_exec_ts = time.time()
        _set_local_position(want_side, asset_size, price, "paper")
        _log_trade(f"OPEN_{direction}_PAPER", want_side, asset_size, price,
                   "paper", balance, confidence, f"paper {asset_size} @ ~${price:,.6g}")
        result.update({
            "executed": True,
            "action":   f"OPEN_{direction}_PAPER",
            "size":     asset_size,
            "order_id": "paper",
            "detail":   f"paper {asset_size} {SYMBOL.replace('USDT', '')} @ ~${price:,.6g}",
        })
        return result

    open_side  = "buy" if direction == "LONG" else "sell"
    r = place_order(open_side, "open", str(asset_size),
                    sl=decision.get("stop_loss"), tp=decision.get("take_profit"))

    if r.get("code") == "00000":
        order_id      = r.get("data", {}).get("orderId", "")
        _last_exec_ts = time.time()
        _set_local_position(want_side, asset_size, price, order_id)
        _log_trade(f"OPEN_{direction}", want_side, asset_size, price,
                   order_id, balance, confidence, f"{asset_size} @ ~${price:,.0f}")
        result.update({
            "executed": True,
            "action":   f"OPEN_{direction}",
            "size":     asset_size,
            "order_id": order_id,
            "detail":   f"{asset_size} {SYMBOL.replace('USDT', '')} @ ~${price:,.0f} | {order_id}",
        })
    else:
        msg = r.get("msg", f"order failed ({r.get('code', '?')})")
        _log_trade(f"FAIL_{direction}", want_side, asset_size, price,
                   "", balance, confidence, msg)
        result["detail"] = msg

    return result


def close_open_position() -> dict:
    """Manually close the current open position (called from REST endpoint)."""
    result = {"executed": False, "action": "SKIP", "detail": ""}
    try:
        pos = _load_position_from_disk() if is_paper_mode() else get_open_position()
        if not pos or not pos.get("holdSide"):
            result["detail"] = "no open position"
            return result

        curr_side  = pos.get("holdSide", "").lower()
        size       = str(pos.get("available") or pos.get("total") or "0")

        # Best-effort price for the trade log
        price = 0.0
        try:
            price = float(public_get(
                "/api/v2/mix/market/ticker",
                {"symbol": SYMBOL, "productType": PRODUCT},
            )[0].get("lastPr", 0) or 0)
        except Exception:
            pass

        if is_paper_mode():
            _set_local_position(None)
            _log_trade("CLOSE_PAPER", curr_side, float(size), price,
                       "paper", 0, 0, f"manual paper close: {curr_side} {size}")
            result.update({"executed": True, "action": "CLOSE_PAPER",
                           "detail": f"paper closed {curr_side} {size}"})
            return result

        close_side = "sell" if curr_side == "long" else "buy"
        r = place_order(close_side, "close", size)
        if r.get("code") == "00000":
            _set_local_position(None)
            _log_trade("CLOSE", curr_side, float(size), price,
                       r.get("data", {}).get("orderId", ""), 0, 0,
                       f"manual close: {curr_side} {size}")
            result.update({"executed": True, "action": "CLOSE",
                           "detail": f"closed {curr_side} {size}"})
        else:
            result["detail"] = r.get("msg", f"close failed ({r.get('code', '?')})")
    except Exception as e:
        result["detail"] = str(e)
    return result


# ─── SIGNALS ──────────────────────────────────────────────────────────────────
def get_technical_signal() -> dict:
    _neutral = {"signal": "NEUTRAL", "rsi": 50, "stoch_rsi": 50,
                "trend": "FLAT", "macd": "FLAT", "bb_pct": 0.5, "price": 0.0}
    try:
        raw = public_get("/api/v2/mix/market/candles",
                         {"symbol": SYMBOL, "productType": PRODUCT,
                          "granularity": "15m", "limit": 60})
    except Exception:
        return _neutral
    candles = raw if isinstance(raw, list) else (raw.get("candles") or [] if isinstance(raw, dict) else [])
    if len(candles) < 26:
        return _neutral

    closes = np.array([float(c[4]) for c in candles])
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    rsi    = round(100 - 100 / (1 + np.mean(gains[-14:]) / (np.mean(losses[-14:]) + 1e-9)), 1)

    price = closes[-1]
    sma20 = np.mean(closes[-20:])
    std20 = np.std(closes[-20:])
    bb_upper = float(sma20 + 2 * std20)
    bb_lower = float(sma20 - 2 * std20)
    bb_pct   = (price - bb_lower) / (bb_upper - bb_lower + 1e-9)

    trend    = "UP" if price > sma20 * 1.002 else "DOWN" if price < sma20 * 0.998 else "FLAT"
    macd_dir = "POSITIVE" if np.mean(closes[-12:]) > np.mean(closes[-26:]) else "NEGATIVE"

    # Stochastic RSI (14-period)
    rsi_series = []
    for i in range(14, len(closes)):
        d = np.diff(closes[i - 14:i + 1])
        g = np.where(d > 0, d, 0.0)
        l = np.where(d < 0, -d, 0.0)
        rsi_series.append(100 - 100 / (1 + np.mean(g) / (np.mean(l) + 1e-9)))
    stoch_rsi = 50.0
    if len(rsi_series) >= 14:
        window   = rsi_series[-14:]
        lo, hi   = min(window), max(window)
        stoch_rsi = round((rsi_series[-1] - lo) / (hi - lo + 1e-9) * 100, 1)

    if   rsi < 35 and bb_pct < 0.2 and trend != "DOWN":  signal = "BULLISH"
    elif rsi > 65 and bb_pct > 0.8 and trend != "UP":    signal = "BEARISH"
    elif trend == "UP"   and macd_dir == "POSITIVE":      signal = "BULLISH"
    elif trend == "DOWN" and macd_dir == "NEGATIVE":      signal = "BEARISH"
    else:                                                  signal = "NEUTRAL"

    return {
        "signal": signal, "rsi": rsi, "stoch_rsi": stoch_rsi,
        "trend": trend, "macd": macd_dir,
        "price": _round_price(price), "sma20": _round_price(sma20),
        "bb_pct": round(bb_pct, 3),
        "bb_upper": _round_price(bb_upper), "bb_lower": _round_price(bb_lower),
    }


def get_sentiment_signal() -> dict:
    rate = 0.0
    try:
        fr_data = public_get("/api/v2/mix/market/current-fund-rate",
                             {"symbol": SYMBOL, "productType": PRODUCT})
        if isinstance(fr_data, list) and fr_data:
            rate = float(fr_data[0].get("fundingRate", 0) or 0)
        elif isinstance(fr_data, dict):
            rate = float(fr_data.get("fundingRate", 0) or 0)
    except Exception:
        pass

    # Long/short ratio from Bitget
    ls_ratio = 1.0
    try:
        ls_raw = public_get("/api/v2/mix/market/long-short-ratio",
                            {"symbol": SYMBOL, "productType": PRODUCT, "period": "1h"})
        if isinstance(ls_raw, list) and ls_raw:
            ls_ratio = float(ls_raw[-1].get("longShortRatio", 1.0))
    except Exception:
        pass

    # Fear & Greed index (alternative.me — free, no auth required)
    fear_greed, fg_label = 50, "Neutral"
    try:
        fg = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()
        fear_greed = int(fg["data"][0]["value"])
        fg_label   = fg["data"][0]["value_classification"]
    except Exception:
        pass

    # Positive funding = longs pay shorts = crowd overweight long = contrarian bearish
    f_sig  = "BEARISH" if rate > 0.0003 else "BULLISH" if rate < -0.0001 else "NEUTRAL"
    # >1.2 longs crowded (bearish), <0.8 shorts crowded (bullish)
    ls_sig = "BEARISH" if ls_ratio > 1.2 else "BULLISH" if ls_ratio < 0.8 else "NEUTRAL"
    # Extreme fear = contrarian bullish, extreme greed = contrarian bearish
    fg_sig = "BULLISH" if fear_greed < 30 else "BEARISH" if fear_greed > 70 else "NEUTRAL"

    votes  = [f_sig, f_sig, ls_sig, fg_sig]  # funding weighted double
    bulls  = votes.count("BULLISH")
    bears  = votes.count("BEARISH")
    signal = "BULLISH" if bulls > bears else "BEARISH" if bears > bulls else "NEUTRAL"
    note   = f"F&G {fear_greed} ({fg_label}) | L/S {ls_ratio:.2f} | Funding {rate*100:+.4f}%"

    return {
        "signal":           signal,
        "funding_rate":     round(rate * 100, 5),
        "long_short_ratio": round(ls_ratio, 3),
        "fear_greed":       fear_greed,
        "fear_greed_label": fg_label,
        "note":             note,
    }


def get_momentum_signal() -> dict:
    try:
        t = public_get("/api/v2/mix/market/ticker",
                       {"symbol": SYMBOL, "productType": PRODUCT})
    except Exception:
        t = {}
    ticker = t[0] if isinstance(t, list) else (t if isinstance(t, dict) else {})

    oi = 0.0
    try:
        oi_raw = public_get("/api/v2/mix/market/open-interest",
                            {"symbol": SYMBOL, "productType": PRODUCT})
        if isinstance(oi_raw, list) and oi_raw:
            oi = float(oi_raw[0].get("size", 0) or 0)
        elif isinstance(oi_raw, dict):
            oi_list = oi_raw.get("openInterestList") or []
            oi = float(oi_list[0].get("size", 0)) if oi_list else float(oi_raw.get("size", 0) or 0)
    except Exception:
        pass

    price  = float(ticker.get("lastPr",  0) or 0)
    high   = float(ticker.get("high24h", 0) or 0)
    low    = float(ticker.get("low24h",  0) or 0)
    change = float(ticker.get("change24h", 0) or 0) * 100
    vol    = float(ticker.get("usdtVolume", 0) or 0)
    range_pct = (price - low) / (high - low + 1e-9) * 100 if high != low else 50.0

    if   change > 1.5  and range_pct > 60: signal = "BULLISH"
    elif change < -1.5 and range_pct < 40: signal = "BEARISH"
    else:                                  signal = "NEUTRAL"

    return {
        "signal": signal, "change_24h_pct": round(change, 2),
        "volume_24h_usd": round(vol, 0), "range_position": round(range_pct, 1),
        "open_interest": round(oi, 1),
    }


def get_depth_signal() -> dict:
    try:
        raw = public_get("/api/v2/mix/market/merge-depth",
                         {"symbol": SYMBOL, "productType": PRODUCT, "limit": "20"})
    except Exception:
        raw = {}
    bids = raw.get("bids", []) if isinstance(raw, dict) else []
    asks = raw.get("asks", []) if isinstance(raw, dict) else []

    if not bids or not asks:
        return {"signal": "NEUTRAL", "imbalance": 0.0, "spread_pct": 0.0}

    best_bid   = float(bids[0][0])
    best_ask   = float(asks[0][0])
    spread_pct = (best_ask - best_bid) / best_bid * 100
    bid_vol    = sum(float(b[1]) for b in bids[:10])
    ask_vol    = sum(float(a[1]) for a in asks[:10])
    imbalance  = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
    signal     = "BULLISH" if imbalance > 0.15 else "BEARISH" if imbalance < -0.15 else "NEUTRAL"

    return {
        "signal": signal, "imbalance": round(imbalance, 3),
        "spread_pct": round(spread_pct, 5),
        "best_bid": best_bid, "best_ask": best_ask,
    }


def get_volatility_signal() -> dict:
    _neutral = {"signal": "NEUTRAL", "regime": "UNKNOWN", "atr_pct": 0.0}
    try:
        raw     = public_get("/api/v2/mix/market/candles",
                             {"symbol": SYMBOL, "productType": PRODUCT,
                              "granularity": "1H", "limit": 24})
        candles = raw if isinstance(raw, list) else []
        if len(candles) < 10:
            return _neutral

        highs  = np.array([float(c[2]) for c in candles])
        lows   = np.array([float(c[3]) for c in candles])
        closes = np.array([float(c[4]) for c in candles])
        tr     = highs - lows

        atr_pct    = np.mean(tr[-14:]) / closes[-1] * 100
        recent_atr = np.mean(tr[-5:])
        older_atr  = np.mean(tr[-14:-5])

        if recent_atr > older_atr * 1.3:
            regime = "EXPANDING"
            signal = "BULLISH" if closes[-1] > closes[-5] else "BEARISH"
        elif recent_atr < older_atr * 0.7:
            regime, signal = "CONTRACTING", "NEUTRAL"
        else:
            regime, signal = "STABLE", "NEUTRAL"

        return {"signal": signal, "regime": regime, "atr_pct": round(atr_pct, 3)}
    except Exception:
        return _neutral


def get_macro_signal() -> dict:
    btc_dom    = 50.0
    mcap_change = 0.0
    try:
        data        = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=8, headers={"accept": "application/json"},
        ).json().get("data", {})
        btc_dom     = round(data.get("market_cap_percentage", {}).get("btc", 50.0), 1)
        mcap_change = round(data.get("market_cap_change_percentage_24h_usd", 0.0), 2)
    except Exception:
        pass

    if mcap_change > 2.0:
        signal = "BULLISH"
        note   = f"Total market cap +{mcap_change}% — broad risk-on"
    elif mcap_change < -2.0:
        signal = "BEARISH"
        note   = f"Total market cap {mcap_change}% — broad risk-off"
    elif btc_dom > 60 and mcap_change > 0.5:
        signal = "BULLISH"
        note   = f"BTC dominance {btc_dom}% + rising market — institutional accumulation"
    else:
        signal = "NEUTRAL"
        note   = f"BTC dom {btc_dom}% | market {mcap_change:+.1f}%"

    return {
        "signal":       signal,
        "btc_dominance": btc_dom,
        "mcap_change_24h": mcap_change,
        "note":         note,
    }


# ─── REASONING ────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are BitAgent, an autonomous crypto trading analyst. "
    "You receive 6 market signals for a perpetual futures pair and output a structured trade decision. "
    "Signal priority: Technical > Sentiment > Macro > Momentum > Depth > Volatility. "
    "Sentiment includes Fear & Greed index and long/short ratio in addition to funding rate. "
    "When 4+ signals agree, confidence is high. When signals conflict, reduce size and confidence. "
    "Always output FLAT if confidence is below 60."
)


def reason_with_qwen(signals: dict) -> dict:
    if not QWEN_API_KEY:
        return _rule_based_decision(signals)

    from openai import OpenAI
    client   = OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE)
    user_msg = (
        f"Current {SYMBOL} signals:\n\n"
        f"TECHNICAL:  {json.dumps(signals['technical'])}\n"
        f"SENTIMENT:  {json.dumps(signals['sentiment'])}\n"
        f"MACRO:      {json.dumps(signals['macro'])}\n"
        f"MOMENTUM:   {json.dumps(signals['momentum'])}\n"
        f"DEPTH:      {json.dumps(signals['depth'])}\n"
        f"VOLATILITY: {json.dumps(signals['volatility'])}\n\n"
        'Respond ONLY with valid JSON (no markdown):\n'
        '{"direction":"LONG"|"SHORT"|"FLAT","confidence":<0-100>,"size_pct":<1.0-3.0>,'
        '"entry_price":<float>,"stop_loss":<float>,"take_profit":<float>,'
        '"signal_votes":{"technical":"...","sentiment":"...","macro":"...",'
        '"momentum":"...","depth":"...","volatility":"..."},'
        '"reasoning":"<2 sentences>","risk_note":"<1 sentence>"}'
    )
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
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[qwen] JSON parse error ({e}) — falling back to rule-based. Raw: {raw[:200]}")
        return _rule_based_decision(signals)


def _rule_based_decision(signals: dict) -> dict:
    votes = [signals[k]["signal"]
             for k in ("technical", "sentiment", "macro", "momentum", "depth", "volatility")]
    bulls = votes.count("BULLISH")
    bears = votes.count("BEARISH")
    price = signals["technical"]["price"]

    if   bulls >= 4: direction, confidence = "LONG",  min(55 + bulls * 5, 85)
    elif bears >= 4: direction, confidence = "SHORT", min(55 + bears * 5, 85)
    elif bulls >= 3: direction, confidence = "LONG",  60
    elif bears >= 3: direction, confidence = "SHORT", 60
    else:            direction, confidence = "FLAT",  40

    dist = price * 0.015
    return {
        "direction":    direction,
        "confidence":   confidence,
        "size_pct":     2.0,
        "entry_price":  _round_price(price),
        "stop_loss":    _round_price(price - dist if direction == "LONG" else price + dist),
        "take_profit":  _round_price(price + dist * 2 if direction == "LONG" else price - dist * 2),
        "signal_votes": dict(zip(
            ("technical", "sentiment", "macro", "momentum", "depth", "volatility"), votes)),
        "reasoning":    f"Rule-based: {bulls} bullish, {bears} bearish signals out of 6.",
        "risk_note":    "No LLM key — rule-based fallback active.",
    }


# ─── RISK MANAGEMENT ──────────────────────────────────────────────────────────
def apply_risk_rules(decision: dict) -> dict:
    decision = dict(decision)
    decision.setdefault("direction", "FLAT")
    decision.setdefault("confidence", 0)
    decision.setdefault("size_pct", 0)
    decision["direction"] = str(decision["direction"]).upper()
    decision["confidence"] = max(0, min(100, int(float(decision["confidence"] or 0))))
    decision["size_pct"] = max(0.0, float(decision["size_pct"] or 0))
    if decision["direction"] not in ("LONG", "SHORT", "FLAT"):
        decision["direction"] = "FLAT"
    if decision["confidence"] < MIN_CONFIDENCE:
        decision["direction"] = "FLAT"
        decision["risk_note"] = (
            (decision.get("risk_note") or "") +
            f" Confidence {decision['confidence']}% below threshold — forced FLAT."
        )
    decision["size_pct"] = min(decision["size_pct"], MAX_SIZE_PCT)
    decision.setdefault("risk_note", "")
    return decision


# ─── LOGGING ──────────────────────────────────────────────────────────────────
_LOG_MAX_BYTES = 10_000_000  # 10 MB — keep last 500 entries on rotation


def save_log(cycle: int, signals: dict, decision: dict):
    entry = json.dumps({
        "cycle":    cycle,
        "ts":       datetime.now(timezone.utc).isoformat(),
        "signals":  signals,
        "decision": decision,
    }) + "\n"
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
            LOG_FILE.write_text("".join(lines[-500:]), encoding="utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[log] {e}")


# ─── STANDALONE ENTRY POINT ───────────────────────────────────────────────────
def run():
    print(f"BitAgent  |  {SYMBOL}  |  {LOOP_SECS}s interval")
    if not QWEN_API_KEY:
        print("  QWEN_API_KEY not set — rule-based fallback active")
    cycle = 0
    while True:
        cycle += 1
        print(f"[{datetime.now().strftime('%H:%M:%S')}] cycle {cycle}")
        try:
            signals  = {
                "technical":  get_technical_signal(),
                "sentiment":  get_sentiment_signal(),
                "macro":      get_macro_signal(),
                "momentum":   get_momentum_signal(),
                "depth":      get_depth_signal(),
                "volatility": get_volatility_signal(),
            }
            decision = apply_risk_rules(reason_with_qwen(signals))
            print(f"  {decision['direction']}  conf={decision['confidence']}%  "
                  f"RSI={signals['technical']['rsi']}")
            save_log(cycle, signals, decision)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            print(f"  error: {e}")
        time.sleep(LOOP_SECS)


if __name__ == "__main__":
    run()
