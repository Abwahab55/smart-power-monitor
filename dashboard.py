"""Smart Power Monitor - Live Dashboard (local).

Launch:
  python dashboard.py --input output/large_readings_20260321_231644.jsonl
or simply:
  python dashboard.py

If --input is omitted, the app loads the newest JSONL file from output/.
"""

import argparse
import glob
import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template_string

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
    .head { margin-bottom: 16px; }
    .head h1 { margin: 0; font-size: 1.6rem; }
    .head p { margin: 6px 0 0; color: var(--sub); }
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
    <h1>Smart Power Monitor - Live Dashboard</h1>
    <p id="source"></p>
  </div>
  <div class="grid" id="kpis"></div>
  <div class="charts">
    <div class="card full"><canvas id="lineChart"></canvas></div>
    <div class="card"><canvas id="anomalyChart"></canvas></div>
    <div class="card"><canvas id="halfChart"></canvas></div>
  </div>
</div>
<script>
async function load() {
  const [summaryRes, seriesRes, compRes] = await Promise.all([
    fetch('/api/summary'), fetch('/api/series'), fetch('/api/comparison')
  ]);
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
  new Chart(document.getElementById('lineChart').getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Voltage (V)', data: series.voltage_v, borderColor: '#2563eb', pointRadius: 0 },
        { label: 'Current (A)', data: series.current_a, borderColor: '#ea580c', pointRadius: 0 },
        { label: 'Power (W)', data: series.active_power_w, borderColor: '#16a34a', pointRadius: 0 },
        { label: 'Temp (C)', data: series.temperature_c, borderColor: '#dc2626', pointRadius: 0 },
      ]
    },
    options: { responsive: true, maintainAspectRatio: false }
  });

  const an = comp.normal_vs_anomaly;
  new Chart(document.getElementById('anomalyChart').getContext('2d'), {
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
  new Chart(document.getElementById('halfChart').getContext('2d'), {
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
}
load();
</script>
</body>
</html>
"""


def create_app(input_path):
    app = Flask(__name__)
    payload = load_payload(input_path)

    @app.get("/")
    def index():
        return render_template_string(HTML)

    @app.get("/api/summary")
    def api_summary():
        data = dict(payload["summary"])
        data["source"] = payload["source"]
        return jsonify(data)

    @app.get("/api/series")
    def api_series():
        data = dict(payload["series"])
        data["reading_index"] = list(range(1, len(payload["rows"]) + 1))
        data["anomaly_points"] = payload["anomaly_points"]
        return jsonify(data)

    @app.get("/api/comparison")
    def api_comparison():
        return jsonify(payload["comparison"])

    return app


def main():
    parser = argparse.ArgumentParser(description="Run Smart Power Monitor dashboard")
    parser.add_argument("--input", help="Telemetry JSONL path. Defaults to latest output/*_readings_*.jsonl")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()

    input_path = args.input or pick_latest_jsonl("output")
    if not input_path:
        raise FileNotFoundError("No input JSONL found. Generate data first (bootstrap --auto-report).")

    app = create_app(input_path)
    print(f"[DASHBOARD] Source: {input_path}")
    print(f"[DASHBOARD] URL: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
