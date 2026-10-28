from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_alerts_are_created_only_for_new_signals():
    candle_time = "2026-10-28T10:15:00"
    response = client.post(
        "/evaluate",
        json={
            "setup": {
                "setup_id": "0e087efa",
                "mode": "alert",
                "spec": {
                    "trade_legs": False,
                    "alert": {"channels": ["inapp", "email"], "include_columns": []},
                },
            },
            "run": {"candle_ts": candle_time},
            "signals": {
                "new": [
                    {"stock": "RELIANCE", "row": {"stock": "RELIANCE", "volume": 1200}},
                    {"stock": "TCS", "row": {"stock": "TCS", "volume": 900}},
                ],
                "still": [{"stock": "INFY", "row": {"stock": "INFY", "volume": 800}}],
                "dropped": [{"stock": "WIPRO"}],
            },
            "screener": {"columns": [{"key": "volume", "name": "Volume"}]},
        },
    )

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert len(actions) == 2
    assert [action["type"] for action in actions] == ["alert", "alert"]
    assert {action["stock"] for action in actions} == {"RELIANCE", "TCS"}
    for action, volume in zip(actions, (1200, 900)):
        assert action["stock"] in action["text"]
        assert candle_time in action["text"]
        assert f"volume: {volume}" in action["text"]
        assert len(action["text"]) < 220
