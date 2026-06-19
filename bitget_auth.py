from collections.abc import Callable


AUTH_ERROR_CODES = {"40001", "40002", "40003", "40004", "40005", "40031"}
AUTH_SUCCESS_CODE = "00000"


def bitget_error_message(response: dict) -> str:
    return str(
        response.get("msg")
        or response.get("message")
        or response.get("code")
        or "credential validation failed"
    )


def is_auth_failure(response: dict) -> bool:
    code = str(response.get("code") or "")
    msg = bitget_error_message(response).lower()
    return (
        code in AUTH_ERROR_CODES
        or "passphrase" in msg
        or "signature" in msg
        or "api key" in msg
        or "apikey" in msg
        or "invalid key" in msg
        or "invalid sign" in msg
        or "sign error" in msg
        or "permission" in msg
        or "unauthorized" in msg
    )


def extract_usdt(data) -> float | None:
    if isinstance(data, dict):
        for nested_key in ("assetsList", "coinAssets", "assets", "list"):
            nested = data.get(nested_key)
            if isinstance(nested, list):
                value = extract_usdt(nested)
                if value is not None:
                    return value
        coin = str(data.get("coin") or data.get("marginCoin") or data.get("asset") or "").upper()
        if coin == "USDT" or not coin:
            for key in ("available", "availableBalance", "usdtEquity", "equity", "balance"):
                try:
                    if key in data and data[key] not in ("", None):
                        return float(data[key])
                except Exception:
                    pass
    if isinstance(data, list):
        for item in data:
            value = extract_usdt(item)
            if value is not None:
                return value
    return None


def credential_probe_candidates(product_type: str) -> list[tuple[str, dict]]:
    return [
        ("/api/v3/account/assets", {}),
        ("/api/v2/mix/account/accounts", {"productType": product_type}),
        ("/api/v2/account/all-account-balance", {"coin": "USDT"}),
        ("/api/v2/spot/account/assets", {"coin": "USDT"}),
    ]


def validate_credentials(
    creds: dict,
    auth_get_raw: Callable[[str, str, str, str, dict | None], dict],
    product_type: str,
) -> tuple[bool, float, str]:
    last_error = "No private Bitget account endpoint accepted these credentials."
    for path, params in credential_probe_candidates(product_type):
        try:
            response = auth_get_raw(
                creds["api_key"],
                creds["secret_key"],
                creds["passphrase"],
                path,
                params,
            )
        except Exception as exc:
            last_error = str(exc)
            continue

        code = str(response.get("code") or "")
        if code == AUTH_SUCCESS_CODE:
            balance = extract_usdt(response.get("data"))
            return True, max(0.0, float(balance or 0)), ""
        if is_auth_failure(response):
            return False, 0.0, f"Invalid Bitget API credentials: {bitget_error_message(response)}"
        last_error = f"{path}: {bitget_error_message(response)}"
    return False, 0.0, f"Could not verify Bitget credentials: {last_error}"
