import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "sessions.db"

JSON_FIELDS = ("paper_open_position", "paper_trades")
SESSION_COLUMNS = (
    "id",
    "created_at",
    "last_seen",
    "mode",
    "trade_mode",
    "paper_balance",
    "paper_realized",
    "paper_open_position",
    "paper_trades",
    "selected_symbol",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_session(session_id: str, default_symbol: str, default_balance: float) -> dict[str, Any]:
    now = _now()
    return {
        "id": session_id,
        "created_at": now,
        "last_seen": now,
        "mode": "shared_agent",
        "trade_mode": "paper",
        "paper_balance": float(default_balance),
        "paper_realized": 0.0,
        "paper_open_position": None,
        "paper_trades": [],
        "selected_symbol": default_symbol,
    }


class SessionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init_db(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        trade_mode TEXT NOT NULL,
                        paper_balance REAL NOT NULL,
                        paper_realized REAL NOT NULL,
                        paper_open_position TEXT,
                        paper_trades TEXT NOT NULL,
                        selected_symbol TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            self._ready = True

    def load(self, session_id: str, default_symbol: str, default_balance: float) -> dict[str, Any] | None:
        self.init_db()
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            row_data = dict(row) if row else None
        if not row_data:
            return None

        session = default_session(session_id, default_symbol, default_balance)
        for key in SESSION_COLUMNS:
            if key in row_data:
                session[key] = row_data[key]
        for field in JSON_FIELDS:
            raw = session.get(field)
            if raw in (None, ""):
                session[field] = [] if field == "paper_trades" else None
                continue
            try:
                session[field] = json.loads(raw)
            except (TypeError, ValueError):
                session[field] = [] if field == "paper_trades" else None
        session["paper_balance"] = float(session.get("paper_balance") or default_balance)
        session["paper_realized"] = float(session.get("paper_realized") or 0.0)
        return session

    def save(self, session: dict[str, Any]) -> None:
        self.init_db()
        sanitized = self.sanitize(session)
        values = [sanitized.get(key) for key in SESSION_COLUMNS]
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, created_at, last_seen, mode, trade_mode, paper_balance,
                    paper_realized, paper_open_position, paper_trades, selected_symbol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    mode=excluded.mode,
                    trade_mode=excluded.trade_mode,
                    paper_balance=excluded.paper_balance,
                    paper_realized=excluded.paper_realized,
                    paper_open_position=excluded.paper_open_position,
                    paper_trades=excluded.paper_trades,
                    selected_symbol=excluded.selected_symbol
                """,
                values,
            )
            conn.commit()

    def sanitize(self, session: dict[str, Any]) -> dict[str, Any]:
        clean = {key: session.get(key) for key in SESSION_COLUMNS}
        clean["created_at"] = clean.get("created_at") or _now()
        clean["last_seen"] = clean.get("last_seen") or _now()
        clean["mode"] = clean.get("mode") or "shared_agent"
        clean["trade_mode"] = clean.get("trade_mode") or "paper"
        clean["paper_balance"] = float(clean.get("paper_balance") or 0.0)
        clean["paper_realized"] = float(clean.get("paper_realized") or 0.0)
        clean["paper_open_position"] = json.dumps(clean.get("paper_open_position"))
        clean["paper_trades"] = json.dumps(clean.get("paper_trades") or [])
        clean["selected_symbol"] = clean.get("selected_symbol") or "BTCUSDT"
        return clean


default_store = SessionStore()
