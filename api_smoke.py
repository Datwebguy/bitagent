"""HTTP smoke checks for a running BitAgent server."""
import argparse
import http.cookiejar
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


CHECKS = (
    ("/", "html"),
    ("/api/status", "json"),
    ("/api/evidence", "json"),
    ("/api/audit/evidence", "json"),
    ("/api/audit/trades.csv", "csv"),
    ("/api/decisions?limit=3", "json"),
    ("/api/trades?limit=3", "json"),
)


def fetch(base_url: str, path: str, timeout: float):
    url = base_url.rstrip("/") + path
    with urlopen(url, timeout=timeout) as response:
        body = response.read()
        content_type = response.headers.get("content-type", "")
        return response.status, content_type, body


def fetch_json_with_cookies(opener, base_url: str, path: str, timeout: float, payload: dict | None = None):
    url = base_url.rstrip("/") + path
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method="POST" if payload is not None else "GET")
    with opener.open(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def check(base_url: str, path: str, kind: str, timeout: float) -> tuple[bool, str]:
    try:
        status, content_type, body = fetch(base_url, path, timeout)
    except HTTPError as exc:
        return False, f"{path}: HTTP {exc.code}"
    except URLError as exc:
        return False, f"{path}: {exc.reason}"
    except TimeoutError:
        return False, f"{path}: timed out"

    if status != 200:
        return False, f"{path}: HTTP {status}"
    if kind == "json":
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"{path}: invalid JSON ({exc})"
        if path == "/api/status":
            required = {
                "cycles",
                "creds_set",
                "session",
                "session_scope",
                "symbol",
                "symbol_switch_requires_auth",
                "risk_config",
            }
            missing = sorted(required - set(parsed))
            if missing:
                return False, f"{path}: missing keys {', '.join(missing)}"
            session_required = {
                "id",
                "mode",
                "scope",
                "selected_symbol",
                "credentials_set",
                "live_unlocked",
                "can_use_live",
            }
            session = parsed.get("session") or {}
            session_missing = sorted(session_required - set(session))
            if session_missing:
                return False, f"{path}: session missing keys {', '.join(session_missing)}"
            rc_required = {
                "confidence_pass",
                "cooldown_blocked",
                "daily_limit_blocked",
                "daily_trades",
                "is_directional",
                "order_size_blocked",
            }
            rc = parsed.get("risk_config") or {}
            rc_missing = sorted(rc_required - set(rc))
            if rc_missing:
                return False, f"{path}: risk_config missing keys {', '.join(rc_missing)}"
        if path == "/api/evidence":
            required = {"project", "mode", "endpoints", "risk_config"}
            missing = sorted(required - set(parsed))
            if missing:
                return False, f"{path}: missing keys {', '.join(missing)}"
    elif kind == "csv":
        text = body.decode("utf-8", errors="ignore")
        if "timestamp,trading_pair,direction,price,quantity" not in text:
            return False, f"{path}: CSV header not found"
    else:
        text = body.decode("utf-8", errors="ignore")
        if "BITAGENT" not in text:
            return False, f"{path}: frontend marker not found"

    size = len(body)
    return True, f"{path}: ok ({content_type or 'unknown'}, {size} bytes)"


def check_session_paper_budget(base_url: str, timeout: float) -> tuple[bool, str]:
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        status, payload = fetch_json_with_cookies(opener, base_url, "/api/status", timeout)
        if status != 200 or (payload.get("session") or {}).get("trade_mode") != "paper":
            return False, "/api/session/paper-budget: initial session invalid"

        status, payload = fetch_json_with_cookies(opener, base_url, "/api/session/mode", timeout, {"mode": "live"})
        if status != 200 or (payload.get("session") or {}).get("trade_mode") != "live":
            return False, "/api/session/mode: could not select live mode"

        status, payload = fetch_json_with_cookies(opener, base_url, "/api/session/paper-budget", timeout, {"budget": 2500})
        session = payload.get("session") or {}
        if status != 200 or not payload.get("ok"):
            return False, f"/api/session/paper-budget: rejected ({payload.get('error') or status})"
        if session.get("trade_mode") != "paper":
            return False, "/api/session/paper-budget: did not return session to paper mode"
        if float(session.get("paper_equity") or 0) != 2500:
            return False, "/api/session/paper-budget: paper equity did not update"
    except HTTPError as exc:
        return False, f"/api/session/paper-budget: HTTP {exc.code}"
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return False, f"/api/session/paper-budget: {exc}"
    return True, "/api/session/paper-budget: ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke check BitAgent HTTP endpoints.")
    parser.add_argument("base_url", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    print(f"BitAgent API smoke: {args.base_url.rstrip('/')}\n")
    failed = False
    for path, kind in CHECKS:
        ok, message = check(args.base_url, path, kind, args.timeout)
        print(("PASS " if ok else "FAIL ") + message)
        failed = failed or not ok
    ok, message = check_session_paper_budget(args.base_url, args.timeout)
    print(("PASS " if ok else "FAIL ") + message)
    failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
