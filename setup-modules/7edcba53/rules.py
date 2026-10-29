"""Built-in setup rules — what the engine does with the stocks that newly appear in the screener.

A setup has no script of its own. Every decision comes from the setup's configuration (spec / legs / sizing /
guardrails); this module only turns the new signals into the generic actions the executors understand:

- alert mode without trade legs  -> `alert` per new stock
- trade legs defined, or paper / live mode -> `enter` per new stock (the alert executor records it as "would trade")

Direction (`spec.direction`): `{"mode": "fixed"}` (default, uses `legs.side`), `{"mode": "candle"}` (long when the signal
candle closed above its open, short when below) or `{"mode": "column", "column": "<screener column>"}` (long/short from
that column's text — buy/long/bull/up — or sign).
"""

DIRECTION_MODES = ("fixed", "candle", "column")
_LONG_WORDS = ("buy", "long", "bull", "up", "green")
_SHORT_WORDS = ("sell", "short", "bear", "down", "red")


def normalize_direction(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {"mode": raw} if isinstance(raw, str) else {}
    mode = str(raw.get("mode") or "fixed").strip().lower()
    if mode in ("signal_candle", "follow_candle", "candle_direction"):
        mode = "candle"
    if mode not in DIRECTION_MODES:
        mode = "fixed"
    column = str(raw.get("column") or "").strip()[:60] if mode == "column" else ""
    if mode == "column" and not column:
        mode = "fixed"
    return {"mode": mode, "column": column}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_side(direction: dict, signal: dict, candles: list | None, default: str) -> str:
    """'long' | 'short' for one signal; falls back to `default` when the data needed is missing."""
    d = normalize_direction(direction)
    if d["mode"] == "candle" and candles:
        last = candles[-1]
        o, c = _num(last.get("open")), _num(last.get("close"))
        if o is not None and c is not None and o != c:
            return "long" if c > o else "short"
    elif d["mode"] == "column":
        value = (signal.get("row") or {}).get(d["column"])
        n = _num(value)
        if n is not None and n != 0:
            return "long" if n > 0 else "short"
        text = str(value or "").lower()
        if any(w in text for w in _LONG_WORDS):
            return "long"
        if any(w in text for w in _SHORT_WORDS):
            return "short"
    return default


def evaluate(payload: dict) -> dict:
    """Engine payload -> {actions, notes, screener_params, has_override_key} (same shape the old module returned)."""
    setup = payload.get("setup") or {}
    spec = setup.get("spec") or {}
    legs = setup.get("legs") or spec.get("legs") or {}
    trade = bool(spec.get("trade_legs")) or setup.get("mode") in ("paper", "live")
    default_side = "short" if str(legs.get("side") or "long").lower() in ("short", "sell") else "long"
    candles = payload.get("candles") or {}
    actions = []
    for sig in (payload.get("signals") or {}).get("new") or []:
        stock = sig["stock"]
        if not trade:
            actions.append({"type": "alert", "stock": stock})
            continue
        side = resolve_side(spec.get("direction"), sig, candles.get(stock), default_side)
        actions.append({"type": "enter", "stock": stock, "side": side})
    return {"actions": actions, "notes": "", "screener_params": {}, "has_override_key": False}
