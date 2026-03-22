"""Smart Power Monitor - Live Dashboard + Free Real-Time API.

Modes:
  1) Live mode (default): generates in-memory telemetry and serves API/dashboard.
  2) File mode: load telemetry from an existing JSONL file.

Launch examples:
  python dashboard.py --live --profile facility_hvac --interval 1
  python dashboard.py --input output/facility_large_readings_20260321_234925.jsonl
"""

import argparse
import glob
import os
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template_string

from simulator import EQUIPMENT_PROFILES, PowerSensorSimulator
from visualize_readings import build_comparison, build_summary, read_jsonl, series


def pick_latest_jsonl(output_dir="output"):
    candidates = sorted(glob.glob(os.path.join(output_dir, "*_readings_*.jsonl")))
    if not candidates:
        sample = Path("sample_readings.jsonl")
        if sample.exists():
            return str(sample)
        return None
    return candidates[-1]


def load_payload(path):
    rows = read_jsonl(path)
    return {
        "source": path,
        "rows": rows,
        "summary": build_summary(rows),
        "comparison": build_comparison(rows),
        "series": {
            "voltage_v": series(rows, "voltage_v"),
            "current_a": series(rows, "current_a"),
            "active_power_w": series(rows, "active_power_w"),
            "temperature_c": series(rows, "temperature_c"),
        },
        "anomaly_points": [i + 1 for i, r in enumerate(rows) if r.get("anomaly")],
    }


def payload_from_rows(rows, source):
    return {
        "source": source,
        "rows": rows,
        "summary": build_summary(rows),
        "comparison": build_comparison(rows),
        "series": {
            "voltage_v": series(rows, "voltage_v"),
            "current_a": series(rows, "current_a"),
            "active_power_w": series(rows, "active_power_w"),
            "temperature_c": series(rows, "temperature_c"),
        },
        "anomaly_points": [i + 1 for i, r in enumerate(rows) if r.get("anomaly")],
    }


class LiveTelemetryStore:
    def __init__(self, profile="general_load", interval=1.0, buffer_size=5000):
        cfg = EQUIPMENT_PROFILES[profile]
        self.sensor = PowerSensorSimulator(
            nominal_voltage=cfg["nominal_voltage"],
            nominal_current=cfg["nominal_current"],
            equipment_profile=profile,
        )
        self.profile = profile
        self.interval = interval
        self.rows = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set():
            reading = self.sensor.read()
            with self._lock:
                self.rows.append(reading)
            time.sleep(self.interval)

    def snapshot(self):
        with self._lock:
            return list(self.rows)


HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Smart Power Monitor Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #182230;
      --sub: #4d5b70;
      --accent: #0f766e;
      --danger: #c2410c;
      --line: #d8dee9;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at 20% 20%, #eef4ff, var(--bg));
      color: var(--text);
    }
    .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
    .head { margin-bottom: 16px; display: flex; align-items: end; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .head h1 { margin: 0; font-size: 1.6rem; }
    .head p { margin: 6px 0 0; color: var(--sub); }
    .controls { display: flex; align-items: center; gap: 8px; color: var(--sub); font-size: 0.92rem; }
    .controls select, .controls button {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 6px 8px;
      background: #fff;
      color: var(--text);
    }
    .controls button { cursor: pointer; }
    .status { font-weight: 600; color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
    }
    .label { color: var(--sub); font-size: 0.82rem; }
    .value { font-size: 1.35rem; font-weight: 700; margin-top: 4px; }
    .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
    .full { grid-column: span 2; }
    canvas { width: 100%; height: 320px; }
    @media (max-width: 960px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .charts { grid-template-columns: 1fr; }
      .full { grid-column: span 1; }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div>
      <h1>Smart Power Monitor - Live Dashboard</h1>
      <p id="source"></p>
    </div>
    <div class="controls">
      <span>Refresh</span>
      <select id="refreshEvery">
        <option value="0">Off</option>
        <option value="3">3s</option>
        <option value="5" selected>5s</option>
        <option value="10">10s</option>
      </select>
      <button id="refreshNow">Refresh now</button>
      <span class="status" id="status">Loading...</span>
    </div>
  </div>
  <div class="grid" id="kpis"></div>
  <div class="charts">
    <div class="card full"><canvas id="lineChart"></canvas></div>
    <div class="card"><canvas id="anomalyChart"></canvas></div>
    <div class="card"><canvas id="halfChart"></canvas></div>
  </div>
</div>
<script>
let lineChart = null;
let anomalyChart = null;
let halfChart = null;
let refreshTimer = null;

function pointRadii(length, anomalyPoints) {
  const out = new Array(length).fill(0);
  for (const p of anomalyPoints || []) {
    const idx = p - 1;
    if (idx >= 0 && idx < out.length) out[idx] = 3;
  }
  return out;
}

async function load() {
  const status = document.getElementById('status');
  status.innerText = 'Refreshing...';

  const [summaryRes, seriesRes, compRes] = await Promise.all([
    fetch('/api/summary'), fetch('/api/series'), fetch('/api/comparison')
  ]);

  if (!summaryRes.ok || !seriesRes.ok || !compRes.ok) {
    status.innerText = 'API error';
    return;
  }

  const summary = await summaryRes.json();
  const series = await seriesRes.json();
  const comp = await compRes.json();

  document.getElementById('source').innerText = `Source: ${summary.source}`;

  const kpis = [
    ['Readings', summary.reading_count],
    ['Anomalies', summary.anomaly_count],
    ['Anomaly Rate', `${summary.anomaly_rate_pct}%`],
    ['Avg Power', `${summary.active_power_w.avg} W`],
    ['Avg Voltage', `${summary.voltage_v.avg} V`],
    ['Avg Current', `${summary.current_a.avg} A`],
    ['Avg Temp', `${summary.temperature_c.avg} C`],
    ['Avg PF', summary.power_factor.avg],
  ];
  document.getElementById('kpis').innerHTML = kpis.map(([k,v]) =>
    `<div class="card"><div class="label">${k}</div><div class="value">${v}</div></div>`
  ).join('');

  const labels = series.reading_index;
  const anomalyRadii = pointRadii(labels.length, series.anomaly_points);
  if (lineChart) lineChart.destroy();
  lineChart = new Chart(document.getElementById('lineChart').getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Voltage (V)', data: series.voltage_v, borderColor: '#2563eb', pointRadius: anomalyRadii, pointBackgroundColor: '#c2410c' },
          { label: 'Current (A)', data: series.current_a, borderColor: '#ea580c', pointRadius: anomalyRadii, pointBackgroundColor: '#c2410c' },
          { label: 'Power (W)', data: series.active_power_w, borderColor: '#16a34a', pointRadius: anomalyRadii, pointBackgroundColor: '#c2410c' },
          { label: 'Temp (C)', data: series.temperature_c, borderColor: '#dc2626', pointRadius: anomalyRadii, pointBackgroundColor: '#c2410c' },
        ]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

  const an = comp.normal_vs_anomaly;
  if (anomalyChart) anomalyChart.destroy();
  anomalyChart = new Chart(document.getElementById('anomalyChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: ['Voltage', 'Current', 'Power', 'Temp'],
      datasets: [
        { label: 'Normal', data: [an.voltage_v.normal_avg, an.current_a.normal_avg, an.active_power_w.normal_avg, an.temperature_c.normal_avg], backgroundColor: '#0ea5e9' },
        { label: 'Anomaly', data: [an.voltage_v.anomaly_avg, an.current_a.anomaly_avg, an.active_power_w.anomaly_avg, an.temperature_c.anomaly_avg], backgroundColor: '#f97316' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false }
  });

  const half = comp.first_half_vs_second_half;
  if (halfChart) halfChart.destroy();
  halfChart = new Chart(document.getElementById('halfChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: ['Voltage', 'Current', 'Power', 'Temp'],
      datasets: [
        { label: 'First Half', data: [half.voltage_v.first_half_avg, half.current_a.first_half_avg, half.active_power_w.first_half_avg, half.temperature_c.first_half_avg], backgroundColor: '#14b8a6' },
        { label: 'Second Half', data: [half.voltage_v.second_half_avg, half.current_a.second_half_avg, half.active_power_w.second_half_avg, half.temperature_c.second_half_avg], backgroundColor: '#8b5cf6' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false }
  });

  const now = new Date();
  status.innerText = `Updated ${now.toLocaleTimeString()}`;
}

function scheduleRefresh() {
  const seconds = Number(document.getElementById('refreshEvery').value);
  if (refreshTimer) clearInterval(refreshTimer);
  if (seconds > 0) {
    refreshTimer = setInterval(load, seconds * 1000);
  }
}

document.getElementById('refreshEvery').addEventListener('change', scheduleRefresh);
document.getElementById('refreshNow').addEventListener('click', load);
load();
scheduleRefresh();
</script>
</body>
</html>
"""


def create_app(input_path=None, live=False, profile="general_load", interval=1.0, buffer_size=5000):
    app = Flask(__name__)

    live_store = None
    if live:
        live_store = LiveTelemetryStore(profile=profile, interval=interval, buffer_size=buffer_size)
        live_store.start()

    def get_payload():
        if live_store is not None:
            rows = live_store.snapshot()
            if not rows:
                # Ensure API has at least one reading shortly after startup.
                rows = [live_store.sensor.read()]
            return payload_from_rows(rows, f"live://{profile}")

        # File mode: reload each request so appended telemetry is visible.
        return load_payload(input_path)

    @app.get("/")
    def index():
        return render_template_string(HTML)

    @app.get("/api/summary")
    def api_summary():
        payload = get_payload()
        data = dict(payload["summary"])
        data["source"] = payload["source"]
        return jsonify(data)

    @app.get("/api/health")
    def api_health():
        payload = get_payload()
        mode = "live" if live_store is not None else "file"
        return jsonify({
            "status": "ok",
            "mode": mode,
            "source": payload["source"],
            "reading_count": len(payload["rows"]),
        })

    @app.get("/api/series")
    def api_series():
        payload = get_payload()
        data = dict(payload["series"])
        data["reading_index"] = list(range(1, len(payload["rows"]) + 1))
        data["anomaly_points"] = payload["anomaly_points"]
        return jsonify(data)

    @app.get("/api/readings/latest")
    def api_reading_latest():
        payload = get_payload()
        latest = payload["rows"][-1] if payload["rows"] else None
        return jsonify({"source": payload["source"], "latest": latest})

    @app.get("/api/comparison")
    def api_comparison():
        payload = get_payload()
        return jsonify(payload["comparison"])

    return app


def main():
    parser = argparse.ArgumentParser(description="Run Smart Power Monitor dashboard")
    parser.add_argument("--input", help="Telemetry JSONL path. Defaults to latest output/*_readings_*.jsonl")
    parser.add_argument("--live", action="store_true", help="Use free live API mode (no AWS required)")
    parser.add_argument(
        "--profile",
        choices=sorted(EQUIPMENT_PROFILES.keys()),
        default="general_load",
        help="Equipment profile for live mode",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds for live mode")
    parser.add_argument("--buffer-size", type=int, default=5000, help="Max retained readings in memory for live mode")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()

    live_mode = args.live
    input_path = args.input

    if not live_mode and not input_path:
        # Default to live mode so users always get a free active API even without files.
        live_mode = True

    if not live_mode and input_path is None:
        input_path = pick_latest_jsonl("output")

    if not live_mode and not input_path:
        raise FileNotFoundError("No input JSONL found. Use --live or generate data first.")

    app = create_app(
        input_path=input_path,
        live=live_mode,
        profile=args.profile,
        interval=max(args.interval, 0.0),
        buffer_size=max(args.buffer_size, 100),
    )
    source = f"live://{args.profile}" if live_mode else input_path
    print(f"[DASHBOARD] Source: {source}")
    print(f"[DASHBOARD] URL: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, use_reloader=False)


if __name__ == "__main__":
    main()
