import json

import pytest
import requests

import app as app_module


@pytest.fixture(autouse=True)
def reset_global_state():
    with app_module.AIRCRAFT_DB_LOCK:
        app_module.AIRCRAFT_DB.clear()
    app_module.set_last_metadata_error(None)
    yield
    with app_module.AIRCRAFT_DB_LOCK:
        app_module.AIRCRAFT_DB.clear()
    app_module.set_last_metadata_error(None)


def test_get_live_data_returns_empty_when_json_missing(tmp_path, monkeypatch):
    missing_file = tmp_path / "missing-aircraft.json"
    monkeypatch.setattr(app_module, "DUMP1090_JSON_PATH", str(missing_file))
    monkeypatch.setattr(app_module, "AUTO_SIMULATION_WHEN_NO_FEED", False)
    monkeypatch.setattr(app_module, "SIMULATION_MODE", False)

    data = app_module.get_live_data()

    assert data == {"now": 0, "messages": 0, "aircraft": []}


def test_get_live_data_auto_falls_back_to_simulation(tmp_path, monkeypatch):
    missing_file = tmp_path / "missing-aircraft.json"
    monkeypatch.setattr(app_module, "DUMP1090_JSON_PATH", str(missing_file))
    monkeypatch.setattr(app_module, "AUTO_SIMULATION_WHEN_NO_FEED", True)
    monkeypatch.setattr(app_module, "SIMULATION_MODE", False)

    data = app_module.get_live_data()

    assert len(data["aircraft"]) > 0
    assert data["aircraft"][0].get("lat") is not None
    assert data["aircraft"][0].get("lon") is not None


def test_get_live_data_enriches_from_cached_metadata(tmp_path, monkeypatch):
    aircraft_file = tmp_path / "aircraft.json"
    payload = {
        "now": 123,
        "messages": 10,
        "aircraft": [{"hex": "ABC123", "lat": 44.4, "lon": 26.1, "r": "OLDREG"}],
    }
    aircraft_file.write_text(json.dumps(payload), encoding="utf-8")

    with app_module.AIRCRAFT_DB_LOCK:
        app_module.AIRCRAFT_DB["abc123"] = {
            "airline": "DemoAir",
            "type": "A320",
            "registration": "YR-DEM",
        }

    monkeypatch.setattr(app_module, "DUMP1090_JSON_PATH", str(aircraft_file))

    data = app_module.get_live_data()
    first = data["aircraft"][0]

    assert first["airline"] == "DemoAir"
    assert first["type"] == "A320"
    assert first["r"] == "YR-DEM"


def test_get_live_data_handles_invalid_json(tmp_path, monkeypatch):
    bad_file = tmp_path / "aircraft.json"
    bad_file.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr(app_module, "DUMP1090_JSON_PATH", str(bad_file))

    data = app_module.get_live_data()

    assert data == {"now": 0, "messages": 0, "aircraft": []}


def test_fetch_aircraft_metadata_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ownOp": "Sky Test",
                "desc": "Boeing 737",
                "r": "YR-ABC",
                "t": "B738",
            }

    def fake_get(url, headers, timeout):
        assert "abc123" in url
        assert headers["User-Agent"].startswith("Signal-ADSB")
        assert timeout == app_module.REQUEST_TIMEOUT_SECONDS
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    app_module.fetch_aircraft_metadata("abc123")

    with app_module.AIRCRAFT_DB_LOCK:
        cached = app_module.AIRCRAFT_DB["abc123"]

    assert cached["airline"] == "Sky Test"
    assert cached["type"] == "Boeing 737"
    assert cached["registration"] == "YR-ABC"
    assert app_module.LAST_METADATA_ERROR is None


def test_fetch_aircraft_metadata_request_failure_sets_fallback(monkeypatch):
    def fake_get(url, headers, timeout):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    app_module.fetch_aircraft_metadata("deadbe")

    with app_module.AIRCRAFT_DB_LOCK:
        cached = app_module.AIRCRAFT_DB["deadbe"]

    assert cached["airline"] == "Unknown"
    assert cached["fetched"] is True
    assert "Metadata request failed" in app_module.LAST_METADATA_ERROR


def test_status_endpoint_includes_runtime_health(monkeypatch, tmp_path):
    present_file = tmp_path / "aircraft.json"
    present_file.write_text('{"aircraft": []}', encoding="utf-8")
    monkeypatch.setattr(app_module, "DUMP1090_JSON_PATH", str(present_file))

    with app_module.AIRCRAFT_DB_LOCK:
        app_module.AIRCRAFT_DB["abc123"] = {"airline": "DemoAir"}

    app_module.set_last_metadata_error("sample warning")

    client = app_module.app.test_client()
    response = client.get("/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["running"] is True
    assert payload["cached_aircraft"] == 1
    assert payload["metadata_error"] == "sample warning"
