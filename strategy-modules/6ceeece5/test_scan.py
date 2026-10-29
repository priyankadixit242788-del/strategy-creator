from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


class FakeClient:
    def __init__(self, dates, candles=None, nifty=(100, 110)):
        self.dates = dates
        self.candles = candles
        self.nifty = nifty
        self.queries = []

    def query(self, query, parameters):
        self.queries.append((query, parameters))
        if "SELECT minOrNull(toDate(date," in query:
            return SimpleNamespace(
                result_rows=[(min(self.dates), max(self.dates))]
                if self.dates
                else [(None, None)]
            )
        if query.strip().startswith("SELECT DISTINCT toDate(date,"):
            date_from = parameters.get("date_from")
            date_to = parameters.get("date_to")
            dates = [
                value
                for value in self.dates
                if (date_from is None or value >= date_from)
                and (date_to is None or value <= date_to)
            ]
            dates = sorted(dates, reverse=True)
            if "LIMIT 60" in query:
                dates = dates[:60]
            return SimpleNamespace(result_rows=[(value,) for value in dates])
        if "SELECT open, close" in query:
            nifty = (
                self.nifty.get(parameters["scan_date"])
                if isinstance(self.nifty, dict) else self.nifty
            )
            return SimpleNamespace(result_rows=[nifty] if nifty else [])
        if "WITH universe AS" in query:
            scan_date = parameters["scan_date"]
            opening = datetime.combine(scan_date, datetime.min.time()).replace(hour=9, minute=15)
            candles = [
                ("TCS", 100, 106, 99, 105, opening, 102, 103, 104, 103, 4000),
                ("INFY", 100, 105, 99, 104, opening, 102, 103, 106, 103, 3000),
                ("SBIN", 110, 125, 100, 120, opening, 115, 112, 118, 115, 2000),
                ("HDFCBANK", 100, 150, 99, 140, opening, 110, 120, 130, 151, 1000),
            ]
            if self.candles is not None:
                candles = self.candles
            first_candles = {}
            for candle in candles:
                if candle[5] == opening:
                    first_candles.setdefault(candle[0], candle)
            return SimpleNamespace(
                result_rows=[
                    first_candles.get(
                        candle[0],
                        (candle[0], None, None, None, None, None, None, None, None, None, candle[10]),
                    )
                    for candle in {candle[0]: candle for candle in candles}.values()
                ]
            )
        raise AssertionError(f"Unexpected query: {query}")


def test_health_route_has_strategy_prefix():
    assert client.get("/strategy-api/6ceeece5/health").json() == {"status": "ok"}


def test_default_window_returns_all_matches_and_selects_one_trade_per_date(monkeypatch):
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
    } == {"universe_size": 300, "liquidity_lookback": 10, "nifty_direction_filter": "yes"}
    assert "SuperTrend" in payload["criteria"]
    assert "Top 300 stocks by latest daily candle volume" in payload["criteria"]
    assert payload["presentation"]["page_size"] == 10
    assert payload["presentation"]["pagination"] == "pages"
    assert payload["presentation"]["sort"] == {"key": "scan_date", "direction": "desc"}
    assert payload["presentation"]["limit"] == 0
    assert payload["total"] == 6
    assert payload["date_range"] == {
        "mode": "range",
        "from_date": "2026-10-26",
        "to_date": "2026-10-28",
        "default_days": 60,
        "max_days": 90,
        "min_date": "2026-10-26",
        "max_date": "2026-10-28",
        "dates": ["2026-10-28", "2026-10-27", "2026-10-26"],
    }
    assert len(payload["matches"]) == 6
    assert [row["scan_date"] for row in payload["matches"]] == [
        day for day in ("2026-10-28", "2026-10-27", "2026-10-26") for _ in range(2)
    ]
    for calculation in payload["calculations"]:
        assert calculation["selected"] == "SBIN"
        assert calculation["status"] == "TRADE"
        assert calculation["nifty"]["side"] == "BUY"
        assert len(calculation["evaluations"]) == 4
        selected = next(row for row in calculation["evaluations"] if row["stock"] == "SBIN")
        assert selected["lo"] == 110
        assert selected["hi"] == 120
        assert selected["pct"] == pytest.approx(10 / 110 * 100)
        assert all(selected["crossed"].values())
    assert all(
        row["date"].endswith("T09:15:00")
        and row["range"] == row["high"] - row["low"]
        and set(row) == {column["key"] for column in payload["columns"]}
        for row in payload["matches"]
    )
    assert all(
        "toDate(date, 'Asia/Kolkata') = {scan_date:Date}" in query
        for query, _ in fake_client.queries
        if "WITH universe AS" in query
    )
    assert all(
        "formatDateTime(date, '%H:%i:%S', 'Asia/Kolkata') = '09:15:00'" in query
        for query, _ in fake_client.queries
        if "WITH universe AS" in query
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
        ("2026-10-28", "SBIN"),
        ("2026-10-28", "TCS"),
        ("2026-10-01", "SBIN"),
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


@pytest.mark.parametrize("indicator_index", [6, 7, 8, 9])
@pytest.mark.parametrize("level", [99, 100, 105, 106, None, float("nan")])
def test_all_indicators_must_be_strictly_inside_opening_body(monkeypatch, indicator_index, level):
    scan_date = date(2026, 10, 28)
    candle = ["TCS", 100, 110, 90, 105, datetime(2026, 10, 28, 9, 15), 101, 102, 103, 104, 1000]
    candle[indicator_index] = level
    fake_client = FakeClient(
        [scan_date],
        candles=[
            tuple(candle),
            ("TCS", 90, 120, 80, 110, datetime(2026, 10, 28, 9, 20), 101, 102, 103, 104, 1000),
        ],
    )
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["matches"] == []
    assert response.json()["calculations"][0]["status"] == "NO TRADE"
    evaluation = response.json()["calculations"][0]["evaluations"][0]
    assert evaluation["qualified"] is False
    assert evaluation["reason"].startswith(("Missing indicator", "Not strictly inside body"))


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
        "daily_interval": "day",
        "nifty_symbol": "NIFTY 50",
    }
    universe_rows, trading_dates = query.split(
        "AND toDate(date, 'Asia/Kolkata') IN (", 1
    )
    assert "WHERE interval = {daily_interval:String}" in universe_rows
    assert "WHERE interval = {interval:String}" not in universe_rows
    assert "WHERE interval = {interval:String}" in trading_dates
    opening_rows = query.split("LEFT JOIN (", 1)[1]
    assert "WHERE interval = {interval:String}" in opening_rows
    assert "WHERE interval = {daily_interval:String}" not in opening_rows
    assert "AND toDate(date, 'Asia/Kolkata') <= {scan_date:Date}" in query
    assert "ORDER BY trading_date DESC" in query
    assert "LIMIT {liquidity_lookback:UInt32}" in query
    assert "argMax(tuple(volume), date)" in query
    assert "ORDER BY daily_volume DESC, stock ASC" in query
    assert "sum(close * volume)" not in query
    for suffix in ("CE", "PE", "FUT"):
        assert f"NOT endsWith(upper(stock), '{suffix}')" in query
    assert "SETTINGS join_use_nulls = 1" in query
    assert "LIMIT {universe_size:UInt32}" in query
    assert "stocks" not in parameters
    payload = response.json()
    assert {
        parameter["key"]: parameter["value"] for parameter in payload["parameters"]
    } == {"universe_size": 100, "liquidity_lookback": 10, "nifty_direction_filter": "yes"}
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


def test_default_window_uses_only_last_sixty_trading_sessions(monkeypatch):
    dates = [
        date(2026, 8, 1) + timedelta(days=offset)
        for offset in range(88)
        if (date(2026, 8, 1) + timedelta(days=offset)).weekday() < 5
    ]
    fake_client = FakeClient(dates)
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    payload = client.get("/strategy-api/6ceeece5/scan").json()

    assert payload["date_range"]["from_date"] == dates[-60].isoformat()
    assert payload["date_range"]["dates"] == [
        day.isoformat() for day in reversed(dates[-60:])
    ]
    assert payload["total"] == 120
    assert len(payload["matches"]) == 120
    assert [
        parameters["scan_date"]
        for query, parameters in fake_client.queries
        if "WITH universe AS" in query
    ] == list(reversed(dates[-60:]))


def test_single_date_range_is_inclusive(monkeypatch):
    fake_client = FakeClient([date(2026, 10, 27), date(2026, 10, 28)])
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    payload = client.get(
        "/strategy-api/6ceeece5/scan",
        params={"from_date": "2026-10-27", "to_date": "2026-10-27"},
    ).json()

    assert payload["date_range"]["dates"] == ["2026-10-27"]
    assert payload["total"] == 2
    assert len(payload["matches"]) == 2
    assert payload["matches"][0]["scan_date"] == "2026-10-27"


def test_empty_candle_history_returns_no_matches(monkeypatch):
    fake_client = FakeClient([])
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["matches"] == []
    assert response.json()["date_range"]["dates"] == []
    assert response.json()["date_range"]["from_date"] is None
    assert response.json()["date_range"]["to_date"] is None
    assert response.json()["calculations"] == []


def test_missing_daily_universe_is_reported_without_inventing_values(monkeypatch):
    fake = FakeClient([date(2026, 10, 28)], candles=[])
    monkeypatch.setattr(main, "_client", lambda: fake)
    payload = client.get("/strategy-api/6ceeece5/scan").json()
    assert payload["total"] == 0
    assert payload["matches"] == []
    calculation = payload["calculations"][0]
    assert calculation["status"] == "NO TRADE"
    assert calculation["evaluations"] == []
    assert calculation["universe"]["size"] == 0
    assert "No eligible daily volume data" in calculation["universe"]["reason"]


@pytest.mark.parametrize("from_day", ["2026-10-29", "2026-01-01"])
def test_inferred_end_date_still_validates_range(monkeypatch, from_day):
    fake = FakeClient([date(2026, 10, 28)])
    monkeypatch.setattr(main, "_client", lambda: fake)
    response = client.get("/strategy-api/6ceeece5/scan", params={"from_date": from_day})
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert len(fake.queries) == 1


def test_ninety_calendar_days_is_inclusive_and_allowed(monkeypatch):
    day = date(2026, 10, 28)
    fake = FakeClient([day])
    monkeypatch.setattr(main, "_client", lambda: fake)
    response = client.get("/strategy-api/6ceeece5/scan", params={
        "from_date": (day - timedelta(days=89)).isoformat(),
        "to_date": day.isoformat(),
    })
    assert response.status_code == 200
    assert response.json()["total"] == 2


@pytest.mark.parametrize(
    "nifty,setting,expected,applied",
    [
        ((100, 110), "yes", ["BUYER"], True),
        ((110, 100), "yes", ["SELLER"], True),
        ((100, 100), "yes", ["BUYER", "SELLER"], False),
        (None, "yes", ["BUYER", "SELLER"], False),
        ((None, 110), "yes", ["BUYER", "SELLER"], False),
        ((100, float("inf")), "yes", ["BUYER", "SELLER"], False),
        ((100, 110), "no", ["BUYER", "SELLER"], False),
        ((100, 110), "invalid", ["BUYER"], True),
    ],
)
def test_nifty_direction_filter_and_fallback(monkeypatch, nifty, setting, expected, applied):
    day = date(2026, 10, 28)
    opening = datetime(2026, 10, 28, 9, 15)
    fake = FakeClient([day], [
        ("BUYER", 100, 110, 90, 105, opening, 101, 102, 103, 104, 1000),
        ("SELLER", 105, 110, 90, 100, opening, 101, 102, 103, 104, 900),
    ], nifty=nifty)
    monkeypatch.setattr(main, "_client", lambda: fake)

    payload = client.get(
        "/strategy-api/6ceeece5/scan",
        params={"nifty_direction_filter": setting, "sort_by": "stock", "sort_dir": "asc"},
    ).json()

    assert [row["stock"] for row in payload["matches"]] == expected
    calculation = payload["calculations"][0]
    assert calculation["nifty"]["filter_applied"] is applied
    if applied:
        rejected = [row for row in calculation["evaluations"] if not row["qualified"]]
        assert rejected[0]["reason"] == "Side disagrees with NIFTY direction"
    assert payload["parameters"][-1]["value"] == ("no" if setting == "no" else "yes")


def test_selection_ranks_body_percentage_not_range_and_breaks_ties_alphabetically(monkeypatch):
    opening = datetime(2026, 10, 28, 9, 15)
    fake = FakeClient([opening.date()], [
        ("WIDE", 100, 200, 1, 105, opening, 101, 102, 103, 104, 5000),
        ("ZETA", 100, 111, 99, 110, opening, 101, 102, 103, 104, 4000),
        ("ALPHA", 200, 221, 199, 220, opening, 201, 202, 203, 204, 3000),
    ])
    monkeypatch.setattr(main, "_client", lambda: fake)

    payload = client.get(
        "/strategy-api/6ceeece5/scan", params={"limit": 1, "sort_by": "range"}
    ).json()

    assert payload["total"] == 3
    assert payload["matches"][0]["stock"] == "WIDE"
    assert payload["calculations"][0]["selected"] == "ALPHA"
    assert len(payload["matches"]) == 1


@pytest.mark.parametrize("index,value,reason", [
    (1, None, "Missing OHLC: open"),
    (2, None, "Missing OHLC: high"),
    (3, float("nan"), "Missing OHLC: low"),
    (4, None, "Missing OHLC: close"),
    (1, 0, "Opening price must be positive"),
    (4, 100, "Doji"),
])
def test_missing_prices_and_doji_stop_calculation(index, value, reason):
    row = ["TEST", 100, 110, 90, 105, datetime(2026, 10, 28, 9, 15), 101, 102, 103, 104, 1000]
    row[index] = value
    result = main._evaluate(tuple(row), None)
    assert result["qualified"] is False
    assert result["reason"].startswith(reason)
    assert "pct" not in result
    assert "crossed" not in result


def test_later_candle_cannot_replace_missing_opening_candle(monkeypatch):
    fake = FakeClient([date(2026, 10, 28)], [
        ("LATE", 100, 110, 90, 105, datetime(2026, 10, 28, 9, 20), 101, 102, 103, 104, 1000),
    ])
    monkeypatch.setattr(main, "_client", lambda: fake)
    payload = client.get("/strategy-api/6ceeece5/scan").json()
    assert payload["matches"] == []
    calculation = payload["calculations"][0]
    assert calculation["status"] == "NO TRADE"
    assert calculation["selected"] is None
    assert calculation["evaluations"][0]["reason"] == "Missing 09:15 IST opening candle"


def test_nifty_is_recomputed_per_date_and_limit_is_per_date(monkeypatch):
    days = [date(2026, 10, 27), date(2026, 10, 28)]
    fake = FakeClient(days, nifty={days[0]: (110, 100), days[1]: (100, 110)})
    monkeypatch.setattr(main, "_client", lambda: fake)
    payload = client.get("/strategy-api/6ceeece5/scan", params={"limit": 1}).json()
    assert payload["total"] == 2
    assert len(payload["matches"]) == 1
    assert payload["matches"][0]["scan_date"] == "2026-10-28"
    assert [row["nifty"]["side"] for row in payload["calculations"]] == ["BUY", "SHORT"]
    assert [row["status"] for row in payload["calculations"]] == ["TRADE", "NO TRADE"]

    fake.nifty = (100, 110)
    payload = client.get("/strategy-api/6ceeece5/scan", params={"limit": 1}).json()
    assert payload["total"] == 4
    assert len(payload["matches"]) == 2


def test_storage_identifiers_are_parameterized(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_DAILY_INTERVAL", "1D")
    monkeypatch.setenv("CLICKHOUSE_NIFTY_SYMBOL", "NIFTY")
    query, parameters = main._first_candle_query(date(2026, 10, 28))
    assert parameters["daily_interval"] == "1D"
    assert parameters["nifty_symbol"] == "NIFTY"
    assert "1D" not in query
    query, parameters = main._nifty_query(date(2026, 10, 28))
    assert parameters["nifty_symbol"] == "NIFTY"
    assert "stock = {nifty_symbol:String}" in query
