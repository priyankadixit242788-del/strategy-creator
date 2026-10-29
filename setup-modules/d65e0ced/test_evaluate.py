from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def _payload(spec=None, mode="alert", signals=None):
    return {
        "setup": {
            "setup_id": "d65e0ced",
            "mode": mode,
            "spec": spec or {},
            "params": {},
            "legs": {"side": "long"},
        },
        "run": {"run_id": "r", "candle_ts": "2026-10-28T10:15:00", "interval": "5"},
        "signals": {
            "new": signals
            if signals is not None
            else [
                {
                    "stock": "TCS",
                    "row": {
                        "Symbol": "TCS",
                        "Date": "2026-10-28",
                        "High": 105.0,
                        "Low": 100.0,
                        "range": 5.0,
                        "Candle time": "2026-10-28T09:15:00",
                    },
                }
            ],
            "still": [{"stock": "INFY"}],
            "dropped": [{"stock": "WIPRO"}],
        },
        "positions": [],
        "candles": {},
        "sectors": {},
        "market": {},
    }


def test_health_manifest_and_unknown_panel():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/manifest").json() == {"parameters": [], "panels": []}
    assert client.post("/panels/unknown", json={}).status_code == 404


def test_alert_uses_fixed_template_and_only_new_signal():
    response = client.post("/evaluate", json=_payload())

    assert response.status_code == 200
    result = response.json()
    assert [action["type"] for action in result["actions"]] == ["alert"]
    alert = result["actions"][0]
    assert alert["stock"] == "TCS"
    assert alert["text"] == "TCS, 2026-10-28, 105.0, 100.0, 5.0, 2026-10-28T09:15:00"
    assert len(alert["text"]) < 220


def test_trade_legs_create_entry_alongside_alert():
    spec = {
        "trade_legs": True,
        "legs": {
            "side": "long",
            "entry": {"kind": "limit", "limit_offset_pct": 0.2},
            "sl": {"kind": "atr", "value": 1.0},
            "targets": [
                {"kind": "fib", "value": 1.0, "qty_pct": 50.0},
                {"kind": "fib", "value": 2.0, "qty_pct": 50.0},
            ],
        },
    }

    actions = client.post("/evaluate", json=_payload(spec=spec)).json()["actions"]

    assert [action["type"] for action in actions] == ["alert", "enter"]
    assert actions[1]["side"] == "long"


def test_trade_entry_respects_direction_and_api_row_aliases():
    spec = {
        "trade_legs": True,
        "direction": {"mode": "candle"},
        "alert": {
            "template": "{stock}, {date}, {high}, {low}, {range}, {time}"
        },
    }
    payload = _payload(spec=spec)
    payload["signals"]["new"][0]["row"] = {
        "stock": "TCS",
        "scan_date": "2026-10-28",
        "high": 105,
        "low": 100,
        "range": 5,
        "date": "2026-10-28T09:15:00",
    }
    payload["candles"] = {
        "TCS": [{"open": 105, "close": 104, "high": 106, "low": 103}]
    }

    actions = client.post("/evaluate", json=payload).json()["actions"]

    assert actions[0]["text"] == "TCS, 2026-10-28, 105, 100, 5, 2026-10-28T09:15:00"
    assert actions[1]["side"] == "short"


def test_no_new_signals_means_no_actions():
    result = client.post("/evaluate", json=_payload(signals=[])).json()
    assert result["actions"] == []
