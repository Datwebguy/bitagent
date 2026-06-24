import unittest

import bitget_auth


class BitgetAuthModuleTests(unittest.TestCase):
    def test_non_success_probe_code_is_not_valid(self):
        def fake_auth_get_raw(*_args, **_kwargs):
            return {"code": "40404", "msg": "not found"}

        ok, balance, error = bitget_auth.validate_credentials(
            {"api_key": "a", "secret_key": "b", "passphrase": "c"},
            fake_auth_get_raw,
            "USDT-FUTURES",
        )

        self.assertFalse(ok)
        self.assertEqual(balance, 0.0)
        self.assertIn("Could not verify Bitget credentials", error)

    def test_auth_failure_stops_without_trying_other_endpoints(self):
        calls = []

        def fake_auth_get_raw(*_args, **_kwargs):
            calls.append(1)
            return {"code": "40005", "msg": "invalid ACCESS-PASSPHRASE"}

        ok, balance, error = bitget_auth.validate_credentials(
            {"api_key": "a", "secret_key": "b", "passphrase": "wrong"},
            fake_auth_get_raw,
            "USDT-FUTURES",
        )

        self.assertFalse(ok)
        self.assertEqual(balance, 0.0)
        self.assertIn("Invalid Bitget API credentials", error)
        self.assertEqual(len(calls), 1)

    def test_success_extracts_usdt_balance(self):
        def fake_auth_get_raw(*_args, **_kwargs):
            return {
                "code": "00000",
                "data": {
                    "assetsList": [
                        {"coin": "BTC", "available": "1"},
                        {"coin": "USDT", "availableBalance": "456.78"},
                    ]
                },
            }

        ok, balance, error = bitget_auth.validate_credentials(
            {"api_key": "a", "secret_key": "b", "passphrase": "valid"},
            fake_auth_get_raw,
            "USDT-FUTURES",
        )

        self.assertTrue(ok)
        self.assertEqual(balance, 456.78)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
