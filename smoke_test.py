"""One-cycle smoke test for BitAgent.

This script intentionally reads credentials only from environment variables.
Leave QWEN_API_KEY unset to verify the rule-based fallback path.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from agent import (
    apply_risk_rules,
    get_depth_signal,
    get_macro_signal,
    get_momentum_signal,
    get_sentiment_signal,
    get_technical_signal,
    get_volatility_signal,
    reason_with_qwen,
    save_log,
)


def main() -> int:
    print("BitAgent -- single cycle smoke test\n")

    if not os.getenv("BITGET_API_KEY"):
        print("  BITGET_API_KEY not set; public signals will run where available.")
    if not os.getenv("QWEN_API_KEY"):
        print("  QWEN_API_KEY not set; rule-based fallback active.\n")

    print("  [1/6] Technical signal...")
    technical = get_technical_signal()
    print(
        f"       RSI={technical['rsi']} | trend={technical['trend']} | "
        f"signal={technical['signal']}"
    )

    print("  [2/6] Sentiment signal...")
    sentiment = get_sentiment_signal()
    print(f"       funding={sentiment['funding_rate']}% | signal={sentiment['signal']}")

    print("  [3/6] Macro signal...")
    macro = get_macro_signal()
    print(f"       BTC dominance={macro['btc_dominance']} | signal={macro['signal']}")

    print("  [4/6] Momentum signal...")
    momentum = get_momentum_signal()
    print(f"       24h change={momentum['change_24h_pct']}% | signal={momentum['signal']}")

    print("  [5/6] Depth signal...")
    depth = get_depth_signal()
    print(f"       imbalance={depth['imbalance']} | signal={depth['signal']}")

    print("  [6/6] Volatility signal...")
    volatility = get_volatility_signal()
    print(f"       regime={volatility['regime']} | atr={volatility['atr_pct']}% | signal={volatility['signal']}")

    signals = {
        "technical": technical,
        "sentiment": sentiment,
        "macro": macro,
        "momentum": momentum,
        "depth": depth,
        "volatility": volatility,
    }

    print("\n  [Decision] Reasoning...")
    decision = apply_risk_rules(reason_with_qwen(signals))
    print(json.dumps(decision, indent=2, sort_keys=True))
    save_log(1, signals, decision)
    print("\nSmoke test complete. Check data/agent_log.jsonl for saved output.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
