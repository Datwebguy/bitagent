import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import agent
import server


VALID_PAYLOAD = {
    "api_key": "a" * 12,
    "secret_key": "b" * 24,
    "passphrase": "valid-passphrase",
    "symbol": "BTCUSDT",
}


class BitgetCredentialValidationTests(unittest.TestCase):
    def setUp(self):
        server._sessions.clear()
        agent.set_credentials("", "", "")
        self.client = TestClient(server.app)

    def tearDown(self):
        server._sessions.clear()
        agent.set_credentials("", "", "")

    def test_session_connect_rejects_wrong_passphrase(self):
        with patch.object(
            server,
            "_session_validate_credentials",
            return_value=(False, 0.0, "Invalid Bitget API credentials: invalid ACCESS-PASSPHRASE"),
        ):
            response = self.client.post("/api/session/connect", json=VALID_PAYLOAD)

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("Invalid Bitget API credentials", payload["error"])

        status = self.client.get("/api/status").json()
        self.assertFalse(status["session_creds_set"])
        self.assertFalse(status["session"]["credentials_set"])
        self.assertNotEqual(status["session"].get("balance_source"), "bitget_futures")

    def test_validator_does_not_accept_non_success_probe_code(self):
        with patch.object(
            server,
            "_session_auth_get_raw",
            return_value={"code": "40404", "msg": "not found"},
        ):
            ok, balance, error = server._session_validate_credentials(
                {
                    "api_key": VALID_PAYLOAD["api_key"],
                    "secret_key": VALID_PAYLOAD["secret_key"],
                    "passphrase": "wrong-passphrase",
                }
            )

        self.assertFalse(ok)
        self.assertEqual(balance, 0.0)
        self.assertIn("Could not verify Bitget credentials", error)

    def test_session_connect_stores_credentials_and_balance_only_after_success(self):
        with (
            patch.object(server, "_session_validate_credentials", return_value=(True, 123.45, "")),
            patch.object(server, "_quick_public_price", return_value=65000.0),
        ):
            response = self.client.post("/api/session/connect", json=VALID_PAYLOAD)

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["balance_source"], "bitget_futures")
        self.assertEqual(payload["balance"], 123.45)
        self.assertTrue(payload["session"]["credentials_set"])
        self.assertEqual(payload["session"]["account_balance"], 123.45)

    def test_operator_connect_does_not_store_credentials_before_validation(self):
        with patch.object(
            server,
            "_session_validate_credentials",
            return_value=(False, 0.0, "Invalid Bitget API credentials: invalid signature"),
        ):
            response = self.client.post("/api/connect", json={**VALID_PAYLOAD, "budget": 5000})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertFalse(agent.credentials_set())


if __name__ == "__main__":
    unittest.main()
