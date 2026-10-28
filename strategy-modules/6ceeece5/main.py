import os
from datetime import date, datetime
from typing import Any, Literal

import clickhouse_connect
from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse

NIFTY_50 = [
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BEL",
    "BHARTIARTL",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INDIGO",
    "INDUSINDBK",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "LTIM",
    "M&M",
    "MARUTI",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SHRIRAMFIN",
    "SUNPHARMA",
    "TATACONSUM",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
]

COLUMNS = [
    {"key": "stock", "label": "Symbol", "format": "text"},
    {"key": "scan_date", "label": "Date", "format": "date"},
    {"key": "high", "label": "High", "format": "price"},
    {"key": "low", "label": "Low", "format": "price"},
    {"key": "range", "label": "range", "format": "text"},
    {"key": "date", "label": "Candle time", "format": "datetime"},
]

PRESENTATION = {
    "style": "table",
    "sort": {"key": "range", "direction": "desc"},
    "ui_filters": ["symbol_search", "column_sort"],
    "limit": 1,
    "page_size": 1,
    "pagination": "top_n",
    "notes": (
        "Show the stock with the highest range from the first 5-minute candle. "
        "Also on UI show a date range filter so that I can see for multiple dates."
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
        ["interval = {interval:String}", "stock IN {stocks:Array(String)}"],
        {"interval": "5", "stocks": NIFTY_50},
    )


def _date_bounds_query() -> tuple[str, dict[str, Any]]:
    conditions, parameters = _base_conditions()
    query = f"""
        SELECT min(toDate(date)), max(toDate(date))
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
        conditions.append("toDate(date) >= {date_from:Date}")
        parameters["date_from"] = date_from
    if date_to is not None:
        conditions.append("toDate(date) <= {date_to:Date}")
        parameters["date_to"] = date_to
    limit_clause = f"LIMIT {limit}" if limit is not None else ""
    query = f"""
        SELECT DISTINCT toDate(date) AS scan_date
        FROM stock__candles_data
        WHERE {" AND ".join(conditions)}
        ORDER BY scan_date DESC
        {limit_clause}
    """
    return query, parameters


def _first_candle_query(scan_date: date) -> tuple[str, dict[str, Any]]:
    conditions, parameters = _base_conditions()
    conditions.append("toDate(date) = {scan_date:Date}")
    parameters["scan_date"] = scan_date
    query = f"""
        SELECT stock, high, low, date
        FROM (
            SELECT
                stock,
                high,
                low,
                date,
                row_number() OVER (
                    PARTITION BY stock
                    ORDER BY date ASC
                ) AS candle_rank
            FROM stock__candles_data
            WHERE {" AND ".join(conditions)}
        )
        WHERE candle_rank = 1
    """
    return query, parameters


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
    limit: int = Query(default=1, ge=0),
    sort_by: Literal[
        "stock", "scan_date", "high", "low", "range", "date"
    ] = "range",
    sort_dir: Literal["asc", "desc"] = "desc",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
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
        query, parameters = _trading_dates_query(date_to=applied_to, limit=10)
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
    for scan_date in scan_dates:
        query, parameters = _first_candle_query(scan_date)
        rows = client.query(query, parameters=parameters).result_rows
        daily_matches = []
        for stock, high, low, candle_date in rows:
            if high is None or low is None:
                continue
            high_value = float(high)
            low_value = float(low)
            daily_matches.append(
                {
                    "stock": str(stock),
                    "scan_date": scan_date.isoformat(),
                    "high": high_value,
                    "low": low_value,
                    "range": high_value - low_value,
                    "date": _as_datetime(candle_date),
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
            "NIFTY 50 stocks ranked by the High - Low range of each stock's "
            f"first 5-minute candle from {applied_from} through {applied_to}."
        ),
        "columns": COLUMNS,
        "presentation": PRESENTATION,
        "parameters": [],
        "date_range": {
            "mode": "range",
            "from_date": applied_from.isoformat() if applied_from else None,
            "to_date": applied_to.isoformat() if applied_to else None,
            "default_days": 10,
            "max_days": 90,
            "min_date": min_date.isoformat() if min_date else None,
            "max_date": max_date.isoformat() if max_date else None,
            "dates": [scan_date.isoformat() for scan_date in scan_dates],
        },
        "total": total,
        "matches": matches,
    }


app.include_router(router)
