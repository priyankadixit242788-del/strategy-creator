import math
import os
from typing import Any, Literal

import clickhouse_connect
from fastapi import APIRouter, FastAPI, Query

COLUMNS = [
    {"key": "stock", "label": "Symbol", "format": "text"},
    {"key": "close", "label": "Last price (LTP)", "format": "price"},
    {"key": "pivotClassicS2", "label": "pivotClassicS2", "format": "text"},
    {"key": "pivotClassicS3", "label": "pivotClassicS3", "format": "text"},
]

PRESENTATION = {
    "style": "table",
    "sort": {"key": "close", "direction": "desc"},
    "ui_filters": ["symbol_search", "column_sort"],
    "limit": 50,
    "page_size": 10,
    "pagination": "pages",
    "notes": "",
}

router = APIRouter(prefix="/strategy-api/aaac78ea")
app = FastAPI()


def _client() -> Any:
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "host.docker.internal"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USERNAME", "default"),
        **{"password": os.getenv("CLICKHOUSE_PASSWORD", "")},
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        secure=os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
        in {"true", "1", "yes"},
    )


def _universe_query() -> tuple[str, dict[str, Any]]:
    return (
        """
        SELECT symbol
        FROM universe_members FINAL
        WHERE universe_id = {universe_id:String} AND active = 1
        ORDER BY symbol
        """,
        {"universe_id": "nifty_50"},
    )


def _candles_query(
    symbols: list[str], lookback_candles: int
) -> tuple[str, dict[str, Any]]:
    return (
        """
        SELECT stock, date, open, high, low, close, volume,
            pivotClassicS2, pivotClassicS3
        FROM (
            SELECT stock, date, open, high, low, close, volume,
                pivotClassicS2, pivotClassicS3,
                row_number() OVER (PARTITION BY stock ORDER BY date DESC) AS candle_rank
            FROM stock__candles_data
            WHERE interval = {interval:String}
                AND stock IN {symbols:Array(String)}
                AND date >= (
                    SELECT max(date) - INTERVAL 7 DAY
                    FROM stock__candles_data
                    WHERE interval = {interval:String}
                        AND stock IN {symbols:Array(String)}
                )
        )
        WHERE candle_rank <= {lookback_candles:UInt32}
        ORDER BY stock, date DESC
        """,
        {
            "interval": "5",
            "symbols": symbols,
            "lookback_candles": lookback_candles,
        },
    )


def _matches_pivot(
    row: tuple[Any, ...], wick_multiplier: float
) -> dict[str, Any] | None:
    (
        stock,
        _candle_date,
        open_price,
        high,
        low,
        close,
        volume,
        pivot_s2,
        pivot_s3,
    ) = row
    values = (open_price, high, low, close, volume)
    if any(value is None for value in values) or float(volume) <= 0:
        return None

    opening = float(open_price)
    high_value = float(high)
    low_value = float(low)
    close_value = float(close)
    body = abs(close_value - opening)
    lower_wick = min(opening, close_value) - low_value
    upper_wick = high_value - max(opening, close_value)
    expected_lower_wick = wick_multiplier * body + upper_wick
    if upper_wick < 0 or lower_wick < 0 or not math.isclose(
        lower_wick, expected_lower_wick, rel_tol=1e-6, abs_tol=1e-8
    ):
        return None

    at_both_supports = all(
        pivot is not None
        and low_value <= float(pivot) <= min(opening, close_value)
        for pivot in (pivot_s2, pivot_s3)
    )
    if not at_both_supports:
        return None

    return {
        "stock": str(stock),
        "close": close_value,
        "pivotClassicS2": float(pivot_s2) if pivot_s2 is not None else None,
        "pivotClassicS3": float(pivot_s3) if pivot_s3 is not None else None,
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/scan")
def scan(
    limit: int = Query(default=50, ge=0),
    sort_by: Literal["stock", "close", "pivotClassicS2", "pivotClassicS3"] = "close",
    sort_dir: Literal["asc", "desc"] = "desc",
    lookback_candles: int = Query(default=5, ge=1, le=100),
    wick_multiplier: float = Query(default=2.0, gt=0),
) -> dict[str, Any]:
    client = _client()
    query, parameters = _universe_query()
    symbols = [
        str(row[0])
        for row in client.query(query, parameters=parameters).result_rows
        if row and row[0] is not None
    ]

    rows: list[tuple[Any, ...]] = []
    if symbols:
        query, parameters = _candles_query(symbols, lookback_candles)
        rows = client.query(query, parameters=parameters).result_rows

    by_stock: dict[str, dict[str, Any]] = {}
    for row in rows:
        match = _matches_pivot(row, wick_multiplier)
        if match is not None and match["stock"] not in by_stock:
            by_stock[match["stock"]] = match

    matches = list(by_stock.values())
    matches.sort(
        key=lambda item: (
            item[sort_by] is not None,
            item[sort_by] if item[sort_by] is not None else "",
            item["stock"],
        ),
        reverse=sort_dir == "desc",
    )
    total = len(matches)

    return {
        "success": True,
        "criteria": (
            "NIFTY 50 stocks with positive-volume 5-minute candles in the most "
            f"recent {lookback_candles} candles that touch both Pivot S2 and S3 and have "
            f"a lower wick equal to {wick_multiplier:g} times the body plus the "
            "upper wick."
        ),
        "columns": COLUMNS,
        "presentation": PRESENTATION,
        "parameters": [],
        "total": total,
        "matches": matches[:limit] if limit else matches,
    }


app.include_router(router)
