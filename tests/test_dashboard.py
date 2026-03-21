import json
from pathlib import Path

from dashboard import create_app


def test_dashboard_api_endpoints(tmp_path):
    sample = tmp_path / "sample.jsonl"
    rows = [
        {
            "device_id": "dev-1",
            "timestamp": "2026-03-21T00:00:00+00:00",
            "voltage_v": 230.0,
            "current_a": 10.0,
            "active_power_w": 2100.0,
            "apparent_power_va": 2200.0,
            "reactive_power_var": 700.0,
            "power_factor": 0.95,
            "frequency_hz": 50.0,
            "temperature_c": 49.5,
            "anomaly": False,
            "fault_type": None,
        }
    ]
    sample.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    app = create_app(str(sample))
    client = app.test_client()

    resp_summary = client.get("/api/summary")
    assert resp_summary.status_code == 200
    assert resp_summary.get_json()["reading_count"] == 1

    resp_series = client.get("/api/series")
    assert resp_series.status_code == 200
    assert resp_series.get_json()["reading_index"] == [1]

    resp_comp = client.get("/api/comparison")
    assert resp_comp.status_code == 200
    assert "normal_vs_anomaly" in resp_comp.get_json()
