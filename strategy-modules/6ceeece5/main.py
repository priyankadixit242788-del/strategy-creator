import math
import os
from datetime import date, datetime
from typing import Any, Literal

import clickhouse_connect
from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse

COLUMNS = [
    {"key": "stock", "label": "Symbol", "format": "text"},
    {"key": "scan_date", "label": "scan_date", "format": "text"},
    {"key": "high", "label": "High", "format": "price"},
    {"key": "low", "label": "Low", "format": "price"},
    {"key": "range", "label": "range", "format": "text"},
    {"key": "date", "label": "Candle time", "format": "datetime"},
]

PRESENTATION = {
    "style": "table",
    "sort": {"key": "scan_date", "direction": "desc"},
    "ui_filters": ["symbol_search", "column_sort"],
    "limit": 0,
    "page_size": 10,
    "pagination": "pages",
    "notes": (
        "Show all matches grouped by date, newest first, with a date range filter. "
        "Daily trade selection is reported separately in calculations."
    ),
}

router = APIRouter(prefix="/strategy-api/6ceeece5")
app = FastAPI()


def _client() -> Any:
    connection_password = os.getenv("CLICKHOUSE_PASSWORD", "")
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "host.docker.internal"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USERNAME", "default"),
        **{"password": connection_password},
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        secure=os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
        in {"true", "1", "yes"},
    )


def _base_conditions() -> tuple[list[str], dict[str, Any]]:
    return (
        ["interval = {interval:String}"],
        {"interval": "5"},
    )


def _date_bounds_query() -> tuple[str, dict[str, Any]]:
    conditions, parameters = _base_conditions()
    query = f"""
        SELECT minOrNull(toDate(date, 'Asia/Kolkata')), maxOrNull(toDate(date, 'Asia/Kolkata'))
        FROM stock__candles_data
        WHERE {" AND ".join(conditions)}
    """
    return query, parameters


def _trading_dates_query(
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
) -> tuple[str, dict[str, Any]]:
    conditions, parameters = _base_conditions()
    if date_from is not None:
        conditions.append("toDate(date, 'Asia/Kolkata') >= {date_from:Date}")
        parameters["date_from"] = date_from
    if date_to is not None:
        conditions.append("toDate(date, 'Asia/Kolkata') <= {date_to:Date}")
        parameters["date_to"] = date_to
    limit_clause = f"LIMIT {limit}" if limit is not None else ""
    query = f"""
        SELECT DISTINCT toDate(date, 'Asia/Kolkata') AS scan_date
        FROM stock__candles_data
        WHERE {" AND ".join(conditions)}
        ORDER BY scan_date DESC
        {limit_clause}
    """
    return query, parameters


def _first_candle_query(
    scan_date: date, universe_size: int = 300, liquidity_lookback: int = 10
) -> tuple[str, dict[str, Any]]:
    _, parameters = _base_conditions()
    parameters.update(
        scan_date=scan_date,
        universe_size=universe_size,
        liquidity_lookback=liquidity_lookback,
        daily_interval=os.getenv("CLICKHOUSE_DAILY_INTERVAL", "day"),
        nifty_symbol=os.getenv("CLICKHOUSE_NIFTY_SYMBOL", "NIFTY 50"),
    )
    query = """
        WITH universe AS (
            SELECT stock, tupleElement(argMax(tuple(volume), date), 1) AS daily_volume
            FROM stock__candles_data
            WHERE interval = {daily_interval:String}
                AND NOT endsWith(upper(stock), 'CE')
                AND NOT endsWith(upper(stock), 'PE')
                AND NOT endsWith(upper(stock), 'FUT')
                AND stock != {nifty_symbol:String}
                AND toDate(date, 'Asia/Kolkata') IN (
                    SELECT DISTINCT toDate(date, 'Asia/Kolkata') AS trading_date
                    FROM stock__candles_data
                    WHERE interval = {interval:String}
                        AND toDate(date, 'Asia/Kolkata') <= {scan_date:Date}
                    ORDER BY trading_date DESC
                    LIMIT {liquidity_lookback:UInt32}
                )
            GROUP BY stock
            HAVING daily_volume IS NOT NULL AND isFinite(daily_volume)
                AND daily_volume >= 0
            ORDER BY daily_volume DESC, stock ASC
            LIMIT {universe_size:UInt32}
        )
        SELECT u.stock, c.open, c.high, c.low, c.close, c.date,
            c.sma20, c.sma50, c.sma200, c.superTrend, u.daily_volume
        FROM universe AS u
        LEFT JOIN (
            SELECT stock, open, high, low, close, date, sma20, sma50, sma200, superTrend
            FROM stock__candles_data
            WHERE interval = {interval:String}
                AND toDate(date, 'Asia/Kolkata') = {scan_date:Date}
                AND formatDateTime(date, '%H:%i:%S', 'Asia/Kolkata') = '09:15:00'
                AND stock IN (SELECT stock FROM universe)
            ORDER BY date ASC
            LIMIT 1 BY stock
        ) AS c ON u.stock = c.stock
        ORDER BY u.daily_volume DESC, u.stock ASC
        SETTINGS join_use_nulls = 1
    """
    return query, parameters


def _nifty_query(scan_date: date) -> tuple[str, dict[str, Any]]:
    return (
        """
        SELECT open, close
        FROM stock__candles_data
        WHERE interval = {interval:String} AND stock = {nifty_symbol:String}
            AND toDate(date, 'Asia/Kolkata') = {scan_date:Date}
            AND formatDateTime(date, '%H:%i:%S', 'Asia/Kolkata') = '09:15:00'
        ORDER BY date ASC
        LIMIT 1
        """,
        {
            "interval": "5",
            "scan_date": scan_date,
            "nifty_symbol": os.getenv("CLICKHOUSE_NIFTY_SYMBOL", "NIFTY 50"),
        },
    )


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _side(open_price: float, close_price: float) -> str | None:
    return "BUY" if close_price > open_price else "SHORT" if close_price < open_price else None


def _evaluate(row: tuple, nifty_side: str | None) -> dict[str, Any]:
    stock, open_price, high, low, close, candle_date, *values = row
    indicators = dict(zip(("sma20", "sma50", "sma200", "superTrend"), map(_number, values[:4])))
    result = {
        "stock": str(stock),
        "daily_volume": _number(values[4]),
        "date": _as_datetime(candle_date) if candle_date is not None else None,
        "open": _number(open_price),
        "high": _number(high),
        "low": _number(low),
        "close": _number(close),
        "indicators": indicators,
        "qualified": False,
    }
    if candle_date is None:
        result["reason"] = "Missing 09:15 IST opening candle"
        return result
    missing = [key for key in ("open", "high", "low", "close") if result[key] is None]
    if missing:
        result["reason"] = "Missing OHLC: " + ", ".join(missing)
        return result
    o, c = result["open"], result["close"]
    if o <= 0:
        result["reason"] = "Opening price must be positive to calculate pct"
        return result
    result.update(lo=min(o, c), hi=max(o, c), side=_side(o, c))
    if result["side"] is None:
        result["reason"] = "Doji: close equals open"
        return result
    missing = [key for key, value in indicators.items() if value is None]
    if missing:
        result["reason"] = "Missing indicator: " + ", ".join(missing)
        return result
    result["crossed"] = {
        key: result["lo"] < value < result["hi"] for key, value in indicators.items()
    }
    if not all(result["crossed"].values()):
        result["reason"] = "Not strictly inside body: " + ", ".join(
            key for key, crossed in result["crossed"].items() if not crossed
        )
        return result
    if nifty_side is not None and result["side"] != nifty_side:
        result["reason"] = "Side disagrees with NIFTY direction"
        return result
    result.update(
        qualified=True,
        reason="All four levels strictly crossed; NIFTY direction accepted",
        pct=abs(c - o) / o * 100,
    )
    return result


def _parse_date(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _as_datetime(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/scan")
def scan(
    limit: int = Query(default=0, ge=0),
    sort_by: Literal[
        "stock", "scan_date", "high", "low", "range", "date"
    ] = "scan_date",
    sort_dir: Literal["asc", "desc"] = "desc",
    from_date: str | None = None,
    to_date: str | None = None,
    universe_size: int = Query(default=300, ge=1, le=300),
    liquidity_lookback: int = Query(default=10, ge=1, le=90),
    nifty_direction_filter: str = "yes",
) -> dict[str, Any]:
    if nifty_direction_filter not in {"yes", "no"}:
        nifty_direction_filter = "yes"
    parsed_from = _parse_date(from_date, "from_date")
    parsed_to = _parse_date(to_date, "to_date")
    if from_date is not None and parsed_from is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "from_date must be YYYY-MM-DD"},
        )
    if to_date is not None and parsed_to is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "to_date must be YYYY-MM-DD"},
        )
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "from_date must be on or before to_date",
            },
        )
    if (
        parsed_from is not None
        and parsed_to is not None
        and (parsed_to - parsed_from).days + 1 > 90
    ):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "date range must not exceed 90 calendar days",
            },
        )

    client = _client()
    query, parameters = _date_bounds_query()
    bounds_rows = client.query(query, parameters=parameters).result_rows
    min_date, max_date = bounds_rows[0] if bounds_rows else (None, None)
    min_date = date.fromisoformat(str(min_date)) if min_date is not None else None
    max_date = date.fromisoformat(str(max_date)) if max_date is not None else None

    applied_to = parsed_to or max_date
    applied_from = parsed_from
    if applied_to is not None and applied_from is None:
        query, parameters = _trading_dates_query(date_to=applied_to, limit=60)
        recent_dates = [
            row[0] for row in client.query(query, parameters=parameters).result_rows
        ]
        applied_from = recent_dates[-1] if recent_dates else applied_to
    elif applied_from is None:
        applied_from = applied_to

    if (
        applied_from is not None
        and applied_to is not None
        and applied_from > applied_to
    ):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "from_date must be on or before to_date",
            },
        )
    if (
        applied_from is not None
        and applied_to is not None
        and (applied_to - applied_from).days + 1 > 90
    ):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "date range must not exceed 90 calendar days",
            },
        )

    if applied_from is not None and applied_to is not None:
        query, parameters = _trading_dates_query(applied_from, applied_to)
        scan_dates = [
            row[0] for row in client.query(query, parameters=parameters).result_rows
        ]
    else:
        scan_dates = []

    total = 0
    matches = []
    calculations = []
    for scan_date in scan_dates:
        query, parameters = _nifty_query(scan_date)
        nifty_rows = client.query(query, parameters=parameters).result_rows
        nifty_open, nifty_close = (
            tuple(map(_number, nifty_rows[0])) if nifty_rows else (None, None)
        )
        nifty_side = (
            _side(nifty_open, nifty_close)
            if nifty_open is not None and nifty_close is not None else None
        )
        query, parameters = _first_candle_query(
            scan_date, universe_size, liquidity_lookback
        )
        rows = client.query(query, parameters=parameters).result_rows
        evaluations = [
            _evaluate(row, nifty_side if nifty_direction_filter == "yes" else None)
            for row in rows
        ]
        qualified = sorted(
            (row for row in evaluations if row["qualified"]),
            key=lambda row: (-row["pct"], row["stock"]),
        )
        calculations.append({
            "scan_date": scan_date.isoformat(),
            "universe": {
                "size": len(rows),
                "reason": (
                    "Ranked by latest daily volume"
                    if rows else "No eligible daily volume data in the trading-session lookback"
                ),
            },
            "nifty": {
                "open": nifty_open,
                "close": nifty_close,
                "side": nifty_side,
                "filter_applied": nifty_direction_filter == "yes" and nifty_side is not None,
                "reason": (
                    "Missing opening OHLC; no filter applied"
                    if nifty_open is None or nifty_close is None
                    else "Doji; no filter applied" if nifty_side is None
                    else "Direction from close versus open"
                ),
            },
            "evaluations": evaluations,
            "selected": qualified[0]["stock"] if qualified else None,
            "status": "TRADE" if qualified else "NO TRADE",
        })
        daily_matches = []
        for row in qualified:
            daily_matches.append(
                {
                    "stock": row["stock"],
                    "scan_date": scan_date.isoformat(),
                    "high": row["high"],
                    "low": row["low"],
                    "range": row["high"] - row["low"],
                    "date": row["date"],
                }
            )
        daily_matches.sort(
            key=lambda row: (row[sort_by], row["stock"], row["date"]),
            reverse=sort_dir == "desc",
        )
        total += len(daily_matches)
        matches.extend(daily_matches[:limit] if limit else daily_matches)

    return {
        "success": True,
        "criteria": (
            f"Top {universe_size} stocks by latest daily candle volume within "
            f"the last {liquidity_lookback} trading sessions as of each scan date, "
            "excluding CE/PE/FUT. The 09:15 IST 5-minute candle body must strictly "
            "cross SMA20, SMA50, SMA200, and SuperTrend. "
            f"NIFTY direction filter: {nifty_direction_filter}. "
            "Select one trade per date by abs(close - open) / open * 100 descending, "
            "then symbol ascending; display all qualifying matches, from "
            f"{applied_from} through {applied_to}."
        ),
        "columns": COLUMNS,
        "presentation": PRESENTATION,
        "parameters": [
            {
                "key": "universe_size",
                "label": "Liquid stock count",
                "type": "number",
                "default": 300,
                "value": universe_size,
                "min": 1,
                "max": 300,
            },
            {
                "key": "liquidity_lookback",
                "label": "Liquidity lookback (trading sessions)",
                "type": "number",
                "default": 10,
                "value": liquidity_lookback,
                "min": 1,
                "max": 90,
            },
            {
                "key": "nifty_direction_filter",
                "label": "NIFTY Direction Filter",
                "type": "select",
                "default": "yes",
                "value": nifty_direction_filter,
                "options": [
                    {"value": "yes", "label": "Yes"},
                    {"value": "no", "label": "No"},
                ],
            },
        ],
        "date_range": {
            "mode": "range",
            "from_date": applied_from.isoformat() if applied_from else None,
            "to_date": applied_to.isoformat() if applied_to else None,
            "default_days": 60,
            "max_days": 90,
            "min_date": min_date.isoformat() if min_date else None,
            "max_date": max_date.isoformat() if max_date else None,
            "dates": [scan_date.isoformat() for scan_date in scan_dates],
        },
        "total": total,
        "matches": matches,
        "calculations": calculations,
    }


app.include_router(router)
