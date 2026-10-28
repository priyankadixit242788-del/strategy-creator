from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


class FakeClient:
    def __init__(self, dates):
        self.dates = dates
        self.queries = []

    def query(self, query, parameters):
        self.queries.append((query, parameters))
        if "min(toDate(date))" in query:
            return SimpleNamespace(
                result_rows=[(min(self.dates), max(self.dates))]
                if self.dates
                else [(None, None)]
            )
        if "SELECT DISTINCT toDate(date)" in query:
            date_from = parameters.get("date_from")
            date_to = parameters.get("date_to")
            dates = [
                value
                for value in self.dates
                if (date_from is None or value >= date_from)
                and (date_to is None or value <= date_to)
            ]
            return SimpleNamespace(result_rows=[(value,) for value in sorted(dates, reverse=True)])
        if "row_number()" in query:
            scan_date = parameters["scan_date"]
            return SimpleNamespace(
                result_rows=[
                    ("TCS", 105, 100, datetime.combine(scan_date, datetime.min.time())),
                    (
                        "RELIANCE",
                        120,
                        110,
                        datetime.combine(scan_date, datetime.min.time()),
                    ),
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
    assert payload["parameters"] == []
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
    ]:
        response = client.get("/strategy-api/6ceeece5/scan", params=params)
        assert response.status_code == 400
        assert response.json() == {"success": False, "error": error}
