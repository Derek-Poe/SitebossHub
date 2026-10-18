from __future__ import annotations

import json

from fastapi.testclient import TestClient

from sbh_demo.app import create_app


API_KEY = "demo"


def test_root_redirects() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"] == "/SitebossHub"


def test_pages_exist() -> None:
    app = create_app()
    client = TestClient(app)

    r = client.get("/SitebossHub")
    assert r.status_code == 200
    assert "Siteboss Hub" in r.text

    r = client.get("/EP_SitebossHub")
    assert r.status_code == 200
    assert "Siteboss Hub" in r.text

    r = client.get("/SitebossHistory")
    assert r.status_code == 200
    assert "Siteboss History" in r.text


def test_all_and_histall_shapes_align() -> None:
    app = create_app()
    client = TestClient(app)

    r_all = client.get(f"/{API_KEY}/all")
    assert r_all.status_code == 200
    all_data = r_all.json()
    assert isinstance(all_data, list)
    assert len(all_data) >= 1
    assert {"CellSite", "IP", "Ping", "Temp", "Humid", "Door", "Battery", "Propane", "Generator", "Uptime", "LastUpdated"}.issubset(
        set(all_data[0].keys())
    )

    r_histall = client.get(f"/{API_KEY}/histAll")
    assert r_histall.status_code == 200
    hist_all = r_histall.json()
    assert isinstance(hist_all, list)
    assert len(hist_all) == len(all_data)
    assert hist_all[0]["CellSite"] == all_data[0]["CellSite"]

    # Sparkline contract: <Metric>1..7 keys exist for at least one metric.
    has_any_ring = any(k.endswith("1") for k in hist_all[0].keys())
    assert has_any_ring


def test_genrunsget_delimiter_payload() -> None:
    app = create_app()
    client = TestClient(app)

    r = client.get(f"/{API_KEY}/genRunsGet")
    assert r.status_code == 200
    text = r.text
    assert "<~~~~~>" in text
    json_part, ts_part = text.split("<~~~~~>", 1)
    data = json.loads(json_part)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert set(data[0].keys()) == {
        "Site",
        "GenON_Time",
        "GenON_Propane",
        "GenOFF_Time",
        "GenOFF_Propane",
        "PropaneLost",
    }
    assert ts_part.strip() != ""


def test_pollsite_returns_sensors_and_logs_snmp() -> None:
    app = create_app()
    client = TestClient(app)

    r = client.get(f"/{API_KEY}/pollSite/1")
    assert r.status_code == 200
    sensors = r.json()
    assert isinstance(sensors, list)
    assert len(sensors) >= 1
    assert set(sensors[0].keys()) == {"SensorName", "PollValue", "LastPoll"}

    r = client.get("/debug/snmp_calls?site=1&limit=10")
    assert r.status_code == 200
    calls = r.json()
    assert isinstance(calls, list)
    assert len(calls) >= 1
    assert {"ts", "site", "sensor", "primary_oid", "fallback_oid", "returned_value"}.issubset(
        set(calls[0].keys())
    )

