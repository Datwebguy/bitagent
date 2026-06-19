import tempfile
import unittest
from pathlib import Path

from session_store import SessionStore, default_session


class SessionStoreTests(unittest.TestCase):
    def test_round_trips_paper_state_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.db")
            session = default_session("sid-1", "BTCUSDT", 10000.0)
            session.update(
                {
                    "selected_symbol": "ETHUSDT",
                    "paper_balance": 2500.0,
                    "paper_realized": 12.34,
                    "paper_open_position": {
                        "side": "long",
                        "entry": 1700.0,
                        "size": 0.5,
                    },
                    "paper_trades": [
                        {
                            "ts": "2026-06-19T00:00:00+00:00",
                            "action": "OPEN_LONG_PAPER",
                            "symbol": "ETHUSDT",
                            "price": 1700.0,
                            "size": 0.5,
                        }
                    ],
                    "credentials": {
                        "api_key": "do-not-store",
                        "secret_key": "do-not-store",
                        "passphrase": "do-not-store",
                    },
                    "live_unlocked": True,
                }
            )

            store.save(session)
            loaded = store.load("sid-1", "BTCUSDT", 10000.0)

        self.assertEqual(loaded["selected_symbol"], "ETHUSDT")
        self.assertEqual(loaded["paper_balance"], 2500.0)
        self.assertEqual(loaded["paper_realized"], 12.34)
        self.assertEqual(loaded["paper_open_position"]["side"], "long")
        self.assertEqual(loaded["paper_trades"][0]["action"], "OPEN_LONG_PAPER")
        self.assertNotIn("credentials", loaded)
        self.assertNotIn("live_unlocked", loaded)

    def test_missing_session_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.db")
            self.assertIsNone(store.load("missing", "BTCUSDT", 10000.0))


if __name__ == "__main__":
    unittest.main()
