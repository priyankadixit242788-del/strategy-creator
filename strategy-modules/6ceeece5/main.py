import os
from datetime import date, datetime
from typing import Any, Literal

import clickhouse_connect
from fastapi import APIRouter, FastAPI, HTTPException, Query

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
    {"key": "high", "label": "High", "format": "price"},
    {"key": "low", "label": "Low", "format": "price"},
    {"key": "range", "label": "Range (High - Low)", "format": "price"},
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
        "also on UI show a date range filter so that I can see for multiple dates"
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


def _first_candle_query(
    date_from: date | None, date_to: date | None
) -> tuple[str, dict[str, Any]]:
    conditions = [
        "interval = {interval:String}",
        "stock IN {stocks:Array(String)}",
    ]
    parameters: dict[str, Any] = {"interval": "5", "stocks": NIFTY_50}

    if date_from is not None:
        conditions.append("toDate(date) >= {date_from:Date}")
        parameters["date_from"] = date_from
    if date_to is not None:
        conditions.append("toDate(date) <= {date_to:Date}")
        parameters["date_to"] = date_to

    query = f"""
        SELECT stock, high, low, date
        FROM (
            SELECT
                stock,
                high,
                low,
                date,
                row_number() OVER (
                    PARTITION BY stock, toDate(date)
                    ORDER BY date ASC
                ) AS candle_rank
            FROM stock__candles_data
            WHERE {" AND ".join(conditions)}
        )
        WHERE candle_rank = 1
    """
    return query, parameters


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
    sort_by: Literal["stock", "high", "low", "range", "date"] = "range",
    sort_dir: Literal["asc", "desc"] = "desc",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422, detail="date_from must be on or before date_to"
        )

    query, parameters = _first_candle_query(date_from, date_to)
    result = _client().query(query, parameters=parameters)
    matches = []
    for stock, high, low, candle_date in result.result_rows:
        if high is None or low is None:
            continue
        high_value = float(high)
        low_value = float(low)
        matches.append(
            {
                "stock": str(stock),
                "high": high_value,
                "low": low_value,
                "range": high_value - low_value,
                "date": _as_datetime(candle_date),
            }
        )

    matches.sort(
        key=lambda row: (row[sort_by], row["stock"], row["date"]),
        reverse=sort_dir == "desc",
    )
    total = len(matches)
    if limit:
        matches = matches[:limit]

    return {
        "success": True,
        "criteria": (
            "NIFTY 50 stocks ranked by the High - Low range of each stock's "
            "first 5-minute candle of the day."
        ),
        "columns": COLUMNS,
        "presentation": PRESENTATION,
        "parameters": [],
        "total": total,
        "matches": matches,
    }


app.include_router(router)
