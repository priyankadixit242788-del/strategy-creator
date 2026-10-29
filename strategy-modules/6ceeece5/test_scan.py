from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


class FakeClient:
    def __init__(self, dates, candles=None):
        self.dates = dates
        self.candles = candles
        self.queries = []

    def query(self, query, parameters):
        self.queries.append((query, parameters))
        if "min(toDate(date))" in query:
            return SimpleNamespace(
                result_rows=[(min(self.dates), max(self.dates))]
                if self.dates
                else [(None, None)]
            )
        if query.strip().startswith("SELECT DISTINCT toDate(date)"):
            date_from = parameters.get("date_from")
            date_to = parameters.get("date_to")
            dates = [
                value
                for value in self.dates
                if (date_from is None or value >= date_from)
                and (date_to is None or value <= date_to)
            ]
            dates = sorted(dates, reverse=True)
            if "LIMIT 10" in query:
                dates = dates[:10]
            return SimpleNamespace(result_rows=[(value,) for value in dates])
        if "row_number()" in query:
            scan_date = parameters["scan_date"]
            candles = [
                (
                    "TCS",
                    105,
                    100,
                    datetime.combine(scan_date, datetime.min.time()),
                    102,
                    103,
                    104,
                    103,
                ),
                (
                    "INFY",
                    105,
                    100,
                    datetime.combine(scan_date, datetime.min.time()),
                    102,
                    103,
                    106,
                    103,
                ),
                (
                    "RELIANCE",
                    120,
                    110,
                    datetime.combine(scan_date, datetime.min.time()),
                    115,
                    112,
                    118,
                    115,
                ),
                (
                    "HDFCBANK",
                    150,
                    100,
                    datetime.combine(scan_date, datetime.min.time()),
                    110,
                    120,
                    130,
                    151,
                ),
            ]
            if self.candles is not None:
                candles = self.candles
            first_candles = {}
            for candle in sorted(candles, key=lambda candle: candle[3]):
                first_candles.setdefault(candle[0], candle)
            return SimpleNamespace(
                result_rows=[
                    candle[:4]
                    for candle in first_candles.values()
                    if all(
                        indicator is not None and candle[2] < indicator < candle[1]
                        for indicator in candle[4:]
                    )
                ]
            )
        raise AssertionError(f"Unexpected query: {query}")


def test_health_route_has_strategy_prefix():
    assert client.get("/strategy-api/6ceeece5/health").json() == {"status": "ok"}


def test_default_window_returns_top_match_per_date(monkeypatch):
    dates = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
    fake_client = FakeClient(dates)
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["columns"] == main.COLUMNS
    assert payload["presentation"] == main.PRESENTATION
    assert {
        parameter["key"]: parameter["value"] for parameter in payload["parameters"]
    } == {"universe_size": 300, "liquidity_lookback": 20}
    assert "SuperTrend" in payload["criteria"]
    assert "Top 300 highly liquid stocks" in payload["criteria"]
    assert payload["total"] == 6
    assert payload["date_range"] == {
        "mode": "range",
        "from_date": "2026-10-26",
        "to_date": "2026-10-28",
        "default_days": 10,
        "max_days": 90,
        "min_date": "2026-10-26",
        "max_date": "2026-10-28",
        "dates": ["2026-10-28", "2026-10-27", "2026-10-26"],
    }
    assert payload["matches"] == [
        {
            "stock": "RELIANCE",
            "scan_date": scan_date,
            "high": 120.0,
            "low": 110.0,
            "range": 10.0,
            "date": f"{scan_date}T00:00:00",
        }
        for scan_date in ("2026-10-28", "2026-10-27", "2026-10-26")
    ]
    assert all(
        "toDate(date) = {scan_date:Date}" in query
        for query, _ in fake_client.queries
        if "row_number()" in query
    )
    assert all("PARTITION BY stock" in query for query, _ in fake_client.queries if "row_number()" in query)
    assert all(
        condition in query
        for query, _ in fake_client.queries
        if "row_number()" in query
        for condition in (
            "low < sma20 AND high > sma20",
            "low < sma50 AND high > sma50",
            "low < sma200 AND high > sma200",
            "low < superTrend AND high > superTrend",
        )
    )


def test_explicit_range_sort_override_and_unlimited_per_date(monkeypatch):
    dates = [date(2026, 10, 1), date(2026, 10, 28)]
    fake_client = FakeClient(dates)
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get(
        "/strategy-api/6ceeece5/scan",
        params={
            "from_date": "2026-10-01",
            "to_date": "2026-10-28",
            "sort_by": "stock",
            "sort_dir": "asc",
            "limit": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["date_range"]["from_date"] == "2026-10-01"
    assert payload["date_range"]["to_date"] == "2026-10-28"
    assert payload["total"] == 4
    assert [(row["scan_date"], row["stock"]) for row in payload["matches"]] == [
        ("2026-10-28", "RELIANCE"),
        ("2026-10-28", "TCS"),
        ("2026-10-01", "RELIANCE"),
        ("2026-10-01", "TCS"),
    ]


def test_invalid_date_ranges_return_400_without_querying(monkeypatch):
    monkeypatch.setattr(
        main, "_client", lambda: (_ for _ in ()).throw(AssertionError("queried"))
    )
    for params, error in [
        (
            {"from_date": "2026-10-29", "to_date": "2026-10-28"},
            "from_date must be on or before to_date",
        ),
        (
            {"from_date": "2026-01-01", "to_date": "2026-04-01"},
            "date range must not exceed 90 calendar days",
        ),
        ({"from_date": "not-a-date"}, "from_date must be YYYY-MM-DD"),
        ({"to_date": "2026-02-30"}, "to_date must be YYYY-MM-DD"),
        ({"from_date": "20261028"}, "from_date must be YYYY-MM-DD"),
    ]:
        response = client.get("/strategy-api/6ceeece5/scan", params=params)
        assert response.status_code == 400
        assert response.json() == {"success": False, "error": error}


@pytest.mark.parametrize("super_trend", [99, 100, 105, 106, None])
def test_supertrend_must_be_strictly_inside_first_candle(monkeypatch, super_trend):
    scan_date = date(2026, 10, 28)
    fake_client = FakeClient(
        [scan_date],
        candles=[
            ("TCS", 105, 100, datetime(2026, 10, 28, 9, 15), 101, 102, 103, super_trend),
            ("TCS", 120, 90, datetime(2026, 10, 28, 9, 20), 101, 102, 103, 104),
        ],
    )
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["matches"] == []
    query = fake_client.queries[-1][0]
    assert "superTrend," in query
    assert query.index("WHERE candle_rank = 1") > query.index("ORDER BY date ASC")
    assert query.index("AND low < superTrend") > query.index("WHERE candle_rank = 1")


def test_liquidity_universe_is_parameterized_and_has_no_lookahead(monkeypatch):
    fake_client = FakeClient([date(2026, 10, 28)])
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get(
        "/strategy-api/6ceeece5/scan",
        params={"universe_size": 100, "liquidity_lookback": 10},
    )

    assert response.status_code == 200
    query, parameters = fake_client.queries[-1]
    assert parameters == {
        "interval": "5",
        "scan_date": date(2026, 10, 28),
        "universe_size": 100,
        "liquidity_lookback": 10,
    }
    assert "stock IN (" in query
    assert "AND toDate(date) < {scan_date:Date}" in query
    assert "ORDER BY trading_date DESC" in query
    assert "LIMIT {liquidity_lookback:UInt32}" in query
    assert "HAVING sum(close * volume) > 0" in query
    assert "ORDER BY sum(close * volume) DESC, stock ASC" in query
    assert "LIMIT {universe_size:UInt32}" in query
    assert "stocks" not in parameters
    payload = response.json()
    assert {
        parameter["key"]: parameter["value"] for parameter in payload["parameters"]
    } == {"universe_size": 100, "liquidity_lookback": 10}
    assert all(
        set(row) == {column["key"] for column in payload["columns"]}
        for row in payload["matches"]
    )


@pytest.mark.parametrize(
    "params",
    [
        {"universe_size": 0},
        {"universe_size": 301},
        {"liquidity_lookback": 0},
        {"liquidity_lookback": 91},
    ],
)
def test_invalid_liquidity_parameters_are_rejected(monkeypatch, params):
    monkeypatch.setattr(
        main, "_client", lambda: (_ for _ in ()).throw(AssertionError("queried"))
    )
    assert client.get("/strategy-api/6ceeece5/scan", params=params).status_code == 422


def test_default_window_uses_only_last_ten_trading_sessions(monkeypatch):
    dates = [date(2026, 10, day) for day in (8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
    fake_client = FakeClient(dates)
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    payload = client.get("/strategy-api/6ceeece5/scan").json()

    assert payload["date_range"]["from_date"] == "2026-10-12"
    assert payload["date_range"]["dates"] == [
        day.isoformat() for day in reversed(dates[-10:])
    ]
    assert payload["total"] == 20
    assert len(payload["matches"]) == 10
    assert [
        parameters["scan_date"]
        for query, parameters in fake_client.queries
        if "row_number()" in query
    ] == list(reversed(dates[-10:]))


def test_single_date_range_is_inclusive(monkeypatch):
    fake_client = FakeClient([date(2026, 10, 27), date(2026, 10, 28)])
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    payload = client.get(
        "/strategy-api/6ceeece5/scan",
        params={"from_date": "2026-10-27", "to_date": "2026-10-27"},
    ).json()

    assert payload["date_range"]["dates"] == ["2026-10-27"]
    assert payload["total"] == 2
    assert len(payload["matches"]) == 1
    assert payload["matches"][0]["scan_date"] == "2026-10-27"


def test_empty_candle_history_returns_no_matches(monkeypatch):
    fake_client = FakeClient([])
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["matches"] == []
    assert response.json()["date_range"]["dates"] == []
