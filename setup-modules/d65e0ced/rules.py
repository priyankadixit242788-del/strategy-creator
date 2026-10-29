"""Translate newly detected SMA Crossover Range Hunter signals into setup actions."""

DIRECTION_MODES = ("fixed", "candle", "column")
_LONG_WORDS = ("buy", "long", "bull", "up", "green")
_SHORT_WORDS = ("sell", "short", "bear", "down", "red")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_direction(raw):
    if isinstance(raw, str):
        raw = {"mode": raw}
    if not isinstance(raw, dict):
        raw = {}
    mode = str(raw.get("mode") or "fixed").strip().lower()
    if mode in ("signal_candle", "follow_candle", "candle_direction"):
        mode = "candle"
    if mode not in DIRECTION_MODES:
        mode = "fixed"
    column = str(raw.get("column") or "").strip()[:60] if mode == "column" else ""
    if mode == "column" and not column:
        mode = "fixed"
    return mode, column


def _resolve_side(direction, signal, candles, default):
    mode, column = _normalize_direction(direction)
    if mode == "candle" and candles:
        candle = candles[-1] if isinstance(candles[-1], dict) else {}
        opening, closing = _num(candle.get("open")), _num(candle.get("close"))
        if opening is not None and closing is not None and opening != closing:
            return "long" if closing > opening else "short"
    elif mode == "column":
        row = signal.get("row") or {}
        value = row.get(column) if isinstance(row, dict) else None
        number = _num(value)
        if number is not None and number != 0:
            return "long" if number > 0 else "short"
        text = str(value or "").lower()
        if any(word in text for word in _LONG_WORDS):
            return "long"
        if any(word in text for word in _SHORT_WORDS):
            return "short"
    return default


def _row_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return ""


class _TemplateValues(dict):
    def __missing__(self, key):
        return ""


def _alert_text(stock, row, template, run):
    date = _row_value(row, "Date", "scan_date", "date")
    candle_time = _row_value(row, "Candle time", "candle_time", "time", "date", "ts")
    if not candle_time:
        candle_time = run.get("candle_ts") or run.get("time_ist") or ""
    values = _TemplateValues(
        stock=stock,
        date=date,
        high=_row_value(row, "High", "high"),
        low=_row_value(row, "Low", "low"),
        range=_row_value(row, "range", "Range"),
        time=candle_time,
    )
    try:
        text = str(template).format_map(values)
    except (ValueError, KeyError):
        text = f"{stock}, {date}, {values['high']}, {values['low']}, {values['range']}, {candle_time}"
    return text[:219]


def evaluate(payload):
    """Return one alert and, when configured, one entry for each new signal."""
    setup = payload.get("setup") or {}
    spec = setup.get("spec") or {}
    legs = setup.get("legs") or spec.get("legs") or {}
    mode = str(setup.get("mode") or spec.get("mode") or "alert").lower()
    trade = bool(spec.get("trade_legs") or spec.get("legs")) or mode in ("paper", "live")
    default_side = (
        "short"
        if str(legs.get("side") or "long").lower() in ("short", "sell")
        else "long"
    )
    run = payload.get("run") or {}
    candles = payload.get("candles") or {}
    alert_spec = spec.get("alert") or {}
    template = alert_spec.get("template") or "{stock}, {date}, {high}, {low}, {range}, {time}"
    actions = []

    for signal in (payload.get("signals") or {}).get("new") or []:
        if not isinstance(signal, dict):
            continue
        stock = str(signal.get("stock") or "")
        if not stock:
            continue
        row = signal.get("row") or {}
        if not isinstance(row, dict):
            row = {}
        actions.append(
            {
                "type": "alert",
                "stock": stock,
                "title": f"SMA Crossover Range Hunter: {stock}"[:100],
                "text": _alert_text(stock, row, template, run),
                "severity": "info",
            }
        )
        if trade:
            stock_candles = candles.get(stock) or []
            side = _resolve_side(
                spec.get("direction"),
                signal,
                stock_candles if isinstance(stock_candles, list) else [],
                default_side,
            )
            actions.append(
                {
                    "type": "enter",
                    "stock": stock,
                    "side": side,
                    "note": "New SMA Crossover Range Hunter signal.",
                }
            )

    return {
        "actions": actions,
        "notes": f"Generated actions for {len(actions) // (2 if trade else 1)} new signal(s).",
        "screener_params": {},
        "has_override_key": False,
    }
