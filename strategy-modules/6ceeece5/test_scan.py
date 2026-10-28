from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_health_route_has_strategy_prefix():
    assert client.get("/strategy-api/6ceeece5/health").json() == {"status": "ok"}


def test_scan_returns_ranked_first_candle_rows_and_caps_after_counting(monkeypatch):
    observed = {}

    class FakeClient:
        def query(self, query, parameters):
            observed["query"] = query
            observed["parameters"] = parameters
            return SimpleNamespace(
                result_rows=[
                    ("TCS", 105, 100, datetime(2026, 10, 28, 9, 15)),
                    ("RELIANCE", 120, 110, datetime(2026, 10, 28, 9, 15)),
                ]
            )

    monkeypatch.setattr(main, "_client", lambda: FakeClient())
    response = client.get("/strategy-api/6ceeece5/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["columns"] == main.COLUMNS
    assert payload["presentation"] == main.PRESENTATION
    assert payload["parameters"] == []
    assert payload["total"] == 2
    assert payload["matches"] == [
        {
            "stock": "RELIANCE",
            "high": 120.0,
            "low": 110.0,
            "range": 10.0,
            "date": "2026-10-28T09:15:00",
        }
    ]
    assert "row_number()" in observed["query"]
    assert "PARTITION BY stock, toDate(date)" in observed["query"]
    assert observed["parameters"]["interval"] == "5"
    assert len(observed["parameters"]["stocks"]) == 50


def test_date_bounds_and_sort_override_are_applied(monkeypatch):
    observed = {}

    class FakeClient:
        def query(self, query, parameters):
            observed["query"] = query
            observed.update(parameters)
            return SimpleNamespace(
                result_rows=[
                    ("TCS", 105, 100, datetime(2026, 10, 28, 9, 15)),
                    ("RELIANCE", 120, 110, datetime(2026, 10, 28, 9, 15)),
                ]
            )

    monkeypatch.setattr(main, "_client", lambda: FakeClient())
    response = client.get(
        "/strategy-api/6ceeece5/scan",
        params={
            "date_from": "2026-10-01",
            "date_to": "2026-10-28",
            "sort_by": "stock",
            "sort_dir": "asc",
            "limit": 0,
        },
    )

    assert response.status_code == 200
    assert observed["date_from"].isoformat() == "2026-10-01"
    assert observed["date_to"].isoformat() == "2026-10-28"
    assert response.json()["matches"][0]["stock"] == "RELIANCE"
    assert response.json()["matches"][1]["stock"] == "TCS"
    assert "toDate(date) >= {date_from:Date}" in observed["query"]
    assert "toDate(date) <= {date_to:Date}" in observed["query"]


def test_invalid_date_range_is_rejected_without_querying(monkeypatch):
    monkeypatch.setattr(
        main, "_client", lambda: (_ for _ in ()).throw(AssertionError("queried"))
    )
    response = client.get(
        "/strategy-api/6ceeece5/scan",
        params={"date_from": "2026-10-29", "date_to": "2026-10-28"},
    )

    assert response.status_code == 422
