import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class LiveExecutionEdgeTests(unittest.TestCase):
    def setUp(self):
        server._sessions.clear()
        self.client = TestClient(server.app)
        self.client.get("/api/status")
        self.session_id = self.client.cookies.get(server.SESSION_COOKIE)
        self.session = server._sessions[self.session_id]

    def tearDown(self):
        server._sessions.clear()
        server._latest.clear()

    def test_execute_requires_live_mode(self):
        self.session["credentials"] = {"api_key": "a", "secret_key": "b", "passphrase": "c"}
        self.session["live_unlocked"] = True
        self.session["last_analysis"] = self._analysis("LONG")

        response = self.client.post("/api/session/execute", json={"dry_run": True})
        payload = response.json()

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["executed"])
        self.assertIn("Switch to Live Account mode", payload["error"])

    def test_execute_requires_credentials_after_live_mode(self):
        self.session["trade_mode"] = "live"
        self.session["live_unlocked"] = True
        self.session["last_analysis"] = self._analysis("LONG")

        response = self.client.post("/api/session/execute", json={"dry_run": True})
        payload = response.json()

        self.assertFalse(payload["ok"])
        self.assertIn("Connect your Bitget account", payload["error"])

    def test_execute_requires_live_unlock(self):
        self.session["trade_mode"] = "live"
        self.session["credentials"] = {"api_key": "a", "secret_key": "b", "passphrase": "c"}
        self.session["last_analysis"] = self._analysis("LONG")

        response = self.client.post("/api/session/execute", json={"dry_run": True})
        payload = response.json()

        self.assertFalse(payload["ok"])
        self.assertIn("locked", payload["error"])

    def test_dry_run_never_places_real_order(self):
        self.session["trade_mode"] = "live"
        self.session["live_unlocked"] = True
        self.session["credentials"] = {"api_key": "a", "secret_key": "b", "passphrase": "c"}
        self.session["selected_symbol"] = "ETHUSDT"
        self.session["last_analysis"] = self._analysis("SHORT", price=1750.0)

        with (
            patch.object(server, "_session_futures_balance", return_value=100.0),
            patch.object(server, "_session_place_order") as place_order,
        ):
            response = self.client.post("/api/session/execute", json={"dry_run": True})

        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["executed"])
        self.assertEqual(payload["proposal"]["direction"], "SHORT")
        place_order.assert_not_called()

    def test_status_uses_session_symbol_not_stale_shared_latest(self):
        self.session["selected_symbol"] = "SOLUSDT"
        server._latest.clear()
        server._latest.update(
            {
                "type": "update",
                "symbol": "BTCUSDT",
                "price": 63000.0,
                "decision": {"direction": "LONG", "confidence": 62, "entry_price": 63000.0},
                "execution": {"executed": True, "action": "OPEN_LONG_PAPER", "detail": "stale BTC"},
                "position": {
                    "holdSide": "long",
                    "symbol": "BTCUSDT",
                    "total": "0.0016",
                    "entryPrice": "63000",
                },
                "risk_config": {"mode": "paper"},
            }
        )

        with patch.object(server, "_quick_public_price", return_value=130.0):
            response = self.client.get("/api/status")

        payload = response.json()
        self.assertEqual(payload["session"]["selected_symbol"], "SOLUSDT")
        self.assertEqual(payload["latest"]["symbol"], "SOLUSDT")
        self.assertIsNone(payload["latest"]["position"])
        self.assertNotEqual(payload["latest"]["execution"]["detail"], "stale BTC")

    def test_session_paper_account_reports_used_and_free_equity(self):
        self.session["paper_balance"] = 10000.0
        self.session["paper_open_position"] = {
            "side": "long",
            "entry": 100.0,
            "size": 2.0,
        }

        account = server._session_paper_account(self.session, mark_price=110.0)

        self.assertEqual(account["notional"], 220.0)
        self.assertEqual(account["used_margin"], 220.0)
        self.assertEqual(account["unrealized"], 20.0)
        self.assertEqual(account["equity"], 10020.0)
        self.assertEqual(account["free_equity"], 9800.0)

    def _analysis(self, direction: str, price: float = 65000.0) -> dict:
        return {
            "symbol": "ETHUSDT",
            "decision": {
                "direction": direction,
                "confidence": 75,
                "entry_price": price,
            },
        }


if __name__ == "__main__":
    unittest.main()
