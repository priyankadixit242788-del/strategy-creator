from typing import Any

from fastapi import FastAPI

app = FastAPI()


def _column_names(screener: dict[str, Any], row: dict[str, Any], include_columns: list[Any]) -> list[str]:
    if include_columns:
        return [str(column) for column in include_columns]

    key_columns = screener.get("key_columns")
    if isinstance(key_columns, list) and key_columns:
        return [str(column) for column in key_columns]

    columns = screener.get("columns")
    if isinstance(columns, list):
        flagged = [
            str(column.get("key") or column.get("name"))
            for column in columns
            if isinstance(column, dict)
            and (column.get("is_key") or column.get("key_column") or column.get("primary"))
            and (column.get("key") or column.get("name"))
        ]
        if flagged:
            return flagged

    return [str(column) for column in row if column != "stock"]


def _alert_text(
    stock: str,
    row: dict[str, Any],
    column_names: list[str],
    candle_time: str,
) -> str:
    details = [
        f"{name}: {row[name]}"
        for name in column_names
        if name in row and row[name] is not None
    ]
    suffix = f" · candle {candle_time} · New Bullish Moribozu signal."
    prefix = stock
    available = max(0, 219 - len(prefix) - len(suffix) - 3)
    column_text = ", ".join(details)
    if len(column_text) > available:
        column_text = column_text[: max(0, available - 1)].rstrip(" ,") + "…"
    text = f"{prefix}{f' · {column_text}' if column_text else ''}{suffix}"
    return text[:219]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/evaluate")
def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    setup = payload.get("setup") or {}
    spec = setup.get("spec") or {}
    alert_spec = spec.get("alert") or {}
    run = payload.get("run") or {}
    signals = payload.get("signals") or {}
    candles = payload.get("candles") or {}
    screener = payload.get("screener") or {}
    column_names = _column_names(
        screener,
        {},
        alert_spec.get("include_columns") or [],
    )
    actions = []

    for signal in signals.get("new") or []:
        stock = str(signal.get("stock") or "")
        row = signal.get("row") or {}
        selected_columns = _column_names(
            screener,
            row,
            alert_spec.get("include_columns") or column_names,
        )
        stock_candles = candles.get(stock) or []
        latest_candle = stock_candles[-1] if stock_candles else {}
        candle_time = str(
            run.get("candle_ts")
            or latest_candle.get("ts")
            or run.get("time_ist")
            or "unknown"
        )
        actions.append(
            {
                "type": "alert",
                "stock": stock,
                "title": f"Bullish Moribozu: {stock}",
                "text": _alert_text(stock, row, selected_columns, candle_time),
                "severity": "info",
            }
        )

    return {
        "actions": actions,
        "notes": (
            f"Generated {len(actions)} alert(s) for new Bullish Moribozu signals."
            if actions
            else "No new Bullish Moribozu signals."
        ),
    }
