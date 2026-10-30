from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


class FakeClient:
    def __init__(self):
        self.queries = []

    def query(self, query, parameters):
        self.queries.append((query, parameters))
        if "FROM universe_members FINAL" in query:
            return SimpleNamespace(result_rows=[("RELIANCE",), ("TCS",)])
        if "FROM stock__candles_data" in query:
            return SimpleNamespace(
                result_rows=[
                    (
                        "RELIANCE",
                        datetime(2026, 10, 29, 10, 0),
                        100.0,
                        106.0,
                        93.0,
                        101.0,
                        1200.0,
                        95.0,
                        94.0,
                    ),
                    (
                        "TCS",
                        datetime(2026, 10, 29, 10, 0),
                        100.0,
                        106.0,
                        92.0,
                        102.0,
                        1000.0,
                        95.0,
                        94.0,
                    ),
                ]
            )
        raise AssertionError(f"Unexpected query: {query}")


def test_health_route_has_strategy_prefix():
    assert client.get("/strategy-api/aaac78ea/health").json() == {"status": "ok"}


def test_scan_returns_matching_stocks_with_confirmed_presentation(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get("/strategy-api/aaac78ea/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["columns"] == main.COLUMNS
    assert payload["presentation"] == main.PRESENTATION
    assert payload["total"] == 2
    assert payload["matches"] == [
        {
            "stock": "TCS",
            "close": 102.0,
            "pivotClassicS2": 95.0,
            "pivotClassicS3": 94.0,
        },
        {
            "stock": "RELIANCE",
            "close": 101.0,
            "pivotClassicS2": 95.0,
            "pivotClassicS3": 94.0,
        }
    ]
    assert set(payload["matches"][0]) == {
        column["key"] for column in payload["columns"]
    }
    assert "universe_id = {universe_id:String}" in fake_client.queries[0][0]
    assert fake_client.queries[0][1] == {"universe_id": "nifty_50"}
    assert fake_client.queries[1][1] == {
        "interval": "5",
        "symbols": ["RELIANCE", "TCS"],
        "lookback_candles": 5,
    }
    assert "row_number() OVER (PARTITION BY stock ORDER BY date DESC)" in fake_client.queries[1][0]


def test_scan_supports_sort_override_and_unlimited_results(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    response = client.get(
        "/strategy-api/aaac78ea/scan",
        params={
            "limit": 0,
            "sort_by": "stock",
            "sort_dir": "asc",
            "lookback_candles": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [row["stock"] for row in payload["matches"]] == ["RELIANCE", "TCS"]
    assert fake_client.queries[1][1]["lookback_candles"] == 3
    assert "2 times the body" in payload["criteria"]


def test_limit_caps_rows_after_counting_matches(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(main, "_client", lambda: fake_client)

    payload = client.get(
        "/strategy-api/aaac78ea/scan",
        params={"limit": 1, "sort_by": "stock", "sort_dir": "asc"},
    ).json()

    assert payload["total"] == 2
    assert [row["stock"] for row in payload["matches"]] == ["RELIANCE"]


def test_match_requires_lower_wick_shape_and_a_support_touch():
    row = (
        "RELIANCE",
        datetime(2026, 10, 29, 10, 0),
        100.0,
        106.0,
        93.0,
        101.0,
        1200.0,
        95.0,
        94.0,
    )

    assert main._matches_pivot(row, 2.0)["stock"] == "RELIANCE"
    assert main._matches_pivot(row[:6] + (0.0,) + row[7:], 2.0) is None
    assert main._matches_pivot(row[:7] + (95.0, 80.0), 2.0) is None
    assert main._matches_pivot(row[:7] + (80.0, 70.0), 2.0) is None
