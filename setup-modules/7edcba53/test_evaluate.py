from fastapi.testclient import TestClient
import pytest

import main
import rules

client = TestClient(main.app)


def _payload(mode="alert", spec=None, new=("AAA", "BBB")):
    return {
        "setup": {"setup_id": "t", "mode": mode, "spec": spec or {}, "params": {}, "legs": {"side": "long"}},
        "run": {"run_id": "r", "candle_ts": "2026-10-28T10:15:00", "interval": "5"},
        "matches": [{"stock": s} for s in new],
        "signals": {"new": [{"stock": s, "row": {"stock": s}} for s in new], "still": [], "dropped": []},
        "positions": [], "candles": {}, "sectors": {}, "market": {},
    }


def test_health_and_manifest():
    assert client.get("/health").json() == {"status": "ok"}
    m = client.get("/manifest").json()
    assert isinstance(m["parameters"], list) and isinstance(m["panels"], list)


def test_one_action_per_new_signal():
    res = client.post("/evaluate", json=_payload()).json()
    assert [a["stock"] for a in res["actions"]] == ["AAA", "BBB"]
    assert {a["type"] for a in res["actions"]} == {"alert"}


def test_trade_legs_enter():
    res = client.post("/evaluate", json=_payload(spec={"trade_legs": True})).json()
    assert [a["type"] for a in res["actions"]] == ["alert", "enter", "alert", "enter"]
    assert [a["side"] for a in res["actions"] if a["type"] == "enter"] == ["long", "long"]


def test_no_signals_no_actions():
    assert client.post("/evaluate", json=_payload(new=())).json()["actions"] == []


@pytest.mark.parametrize("row", [
    {"Symbol": "TCS", "Date": "2026-10-27", "High": 105, "Low": 100,
     "range": 5, "Candle time": "2026-10-27T09:15:00"},
    {"stock": "TCS", "scan_date": "2026-10-27", "high": 105, "low": 100,
     "range": 5, "date": "2026-10-27T09:15:00"},
])
def test_alert_contains_screener_columns_and_original_candle_time(row):
    payload = _payload(new=("TCS",))
    payload["signals"]["new"][0]["row"] = row
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "actions": [{
            "type": "alert",
            "stock": "TCS",
            "title": "SMA Crossover Range Hunter: TCS",
            "text": (
                "TCS · Date: 2026-10-27 · High: 105 · Low: 100 · Range: 5 · "
                "Candle time: 2026-10-27T09:15:00 · New SMA crossover match."
            ),
            "severity": "info",
        }],
        "notes": "",
        "screener_params": {},
        "has_override_key": False,
    }


def test_only_new_signals_are_used_without_reranking_or_date_filtering():
    payload = _payload(new=("TCS", "INFY"))
    payload["signals"]["new"][0]["row"] = {"Date": "2026-10-26", "range": 1}
    payload["signals"]["new"][1]["row"] = {"Date": "2026-10-27", "range": 10}
    payload["matches"].append({"stock": "RELIANCE"})
    payload["signals"]["still"] = [{"stock": "SBIN", "row": {}}]
    payload["signals"]["dropped"] = [{"stock": "ITC"}]
    actions = client.post("/evaluate", json=payload).json()["actions"]
    assert [action["stock"] for action in actions] == ["TCS", "INFY"]
    assert all(action["type"] == "alert" for action in actions)


@pytest.mark.parametrize("row", [None, {}, {"High": None, "Low": 0, "range": 0}])
def test_missing_optional_data_is_unknown_and_zero_is_preserved(row):
    payload = _payload(new=("TCS",))
    payload["signals"]["new"][0]["row"] = row
    payload["candles"] = payload["market"] = payload["sectors"] = None
    action = client.post("/evaluate", json=payload).json()["actions"][0]
    assert "High: unknown" in action["text"]
    assert "Candle time: unknown" in action["text"]
    if row:
        assert "Low: 0 · Range: 0" in action["text"]


def test_alert_text_is_short_plain_text():
    payload = _payload(new=("TCS",))
    payload["signals"]["new"][0]["row"] = {"High": "123\n" * 100}
    text = client.post("/evaluate", json=payload).json()["actions"][0]["text"]
    assert len(text) < 220
    assert "\n" not in text


def test_no_custom_parameters_or_panels():
    assert client.get("/manifest").json() == {"parameters": [], "panels": []}
    assert client.post("/panels/unknown", json={}).status_code == 404


def test_empty_payload_and_positions_without_new_signals_do_nothing():
    assert client.post("/evaluate", json={}).json()["actions"] == []
    payload = _payload(mode="paper", new=())
    payload["signals"] = None
    payload["positions"] = [{"position_id": "p", "stock": "TCS", "status": "open"}]
    assert client.post("/evaluate", json=payload).json()["actions"] == []


@pytest.mark.parametrize("mode,spec", [
    ("paper", {}), ("live", {}), ("alert", {"legs": {"side": "short"}}),
    ("alert", {"entry_style": "market"}), ("alert", {"exit_rules": "time exit"}),
])
def test_future_trade_configuration_keeps_alerts(mode, spec):
    payload = _payload(mode=mode, spec=spec, new=("TCS",))
    payload["setup"]["legs"] = {"side": "short"}
    actions = client.post("/evaluate", json=payload).json()["actions"]
    assert [action["type"] for action in actions] == ["alert", "enter"]
    assert actions[1]["side"] == "short"


@pytest.mark.parametrize("status", ["open", "pending_pullback", "partially_closed"])
def test_future_trade_configuration_never_reenters_open_positions(status):
    payload = _payload(mode="paper", new=("TCS",))
    payload["positions"] = [{"position_id": "p", "stock": "TCS", "status": status}]
    actions = client.post("/evaluate", json=payload).json()["actions"]
    assert [action["type"] for action in actions] == ["alert"]


def test_evaluate_is_pure_and_does_not_dedupe_between_calls():
    from copy import deepcopy

    payload = _payload(new=("TCS",))
    original = deepcopy(payload)
    first = rules.evaluate(payload)
    assert rules.evaluate(payload) == first
    assert payload == original
