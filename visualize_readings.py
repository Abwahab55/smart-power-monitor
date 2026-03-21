"""
Smart Power Monitor — Output Visualizer
Reads JSONL telemetry and generates:
  1) Multi-panel PNG chart
  2) Summary stats JSON

Usage:
  python visualize_readings.py \
    --input sample_readings.jsonl \
    --chart output/readings_dashboard.png \
    --summary output/readings_summary.json
"""

import argparse
import json
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def series(rows, key):
    return [float(r.get(key, 0.0)) for r in rows]


def build_summary(rows):
    voltages = series(rows, "voltage_v")
    currents = series(rows, "current_a")
    power = series(rows, "active_power_w")
    temp = series(rows, "temperature_c")
    pf = series(rows, "power_factor")
    anomalies = sum(1 for r in rows if r.get("anomaly"))

    n = max(len(rows), 1)
    return {
        "reading_count": len(rows),
        "anomaly_count": anomalies,
        "anomaly_rate_pct": round(anomalies * 100.0 / n, 2),
        "voltage_v": {
            "avg": round(mean(voltages), 3),
            "min": round(min(voltages), 3),
            "max": round(max(voltages), 3),
        },
        "current_a": {
            "avg": round(mean(currents), 3),
            "min": round(min(currents), 3),
            "max": round(max(currents), 3),
        },
        "active_power_w": {
            "avg": round(mean(power), 3),
            "min": round(min(power), 3),
            "max": round(max(power), 3),
        },
        "temperature_c": {
            "avg": round(mean(temp), 3),
            "min": round(min(temp), 3),
            "max": round(max(temp), 3),
        },
        "power_factor": {
            "avg": round(mean(pf), 4),
            "min": round(min(pf), 4),
            "max": round(max(pf), 4),
        },
    }


def plot_rows(rows, chart_path, title):
    idx = list(range(1, len(rows) + 1))
    voltage = series(rows, "voltage_v")
    current = series(rows, "current_a")
    power = series(rows, "active_power_w")
    temp = series(rows, "temperature_c")

    fig, axes = plt.subplots(2, 2, figsize=(12, 7), dpi=120)
    fig.suptitle(title, fontsize=14, fontweight="bold")

    axes[0, 0].plot(idx, voltage, color="#1f77b4", linewidth=2)
    axes[0, 0].set_title("Voltage (V)")
    axes[0, 0].set_xlabel("Reading #")
    axes[0, 0].grid(alpha=0.3)

    axes[0, 1].plot(idx, current, color="#ff7f0e", linewidth=2)
    axes[0, 1].set_title("Current (A)")
    axes[0, 1].set_xlabel("Reading #")
    axes[0, 1].grid(alpha=0.3)

    axes[1, 0].plot(idx, power, color="#2ca02c", linewidth=2)
    axes[1, 0].set_title("Active Power (W)")
    axes[1, 0].set_xlabel("Reading #")
    axes[1, 0].grid(alpha=0.3)

    axes[1, 1].plot(idx, temp, color="#d62728", linewidth=2)
    axes[1, 1].set_title("Temperature (C)")
    axes[1, 1].set_xlabel("Reading #")
    axes[1, 1].grid(alpha=0.3)

    plt.tight_layout(rect=(0, 0, 1, 0.95))
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart_path)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Visualize Smart Power Monitor JSONL output")
    parser.add_argument("--input", required=True, help="Input JSONL file path")
    parser.add_argument("--chart", default="output/readings_dashboard.png", help="Output PNG chart path")
    parser.add_argument("--summary", default="output/readings_summary.json", help="Output summary JSON path")
    parser.add_argument("--title", default="Smart Power Monitor - Telemetry Overview", help="Chart title")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    rows = read_jsonl(input_path)
    if not rows:
        raise ValueError("Input JSONL is empty")

    chart_path = Path(args.chart)
    summary_path = Path(args.summary)

    plot_rows(rows, chart_path, args.title)

    summary = build_summary(rows)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"[OK] Chart generated: {chart_path}")
    print(f"[OK] Summary generated: {summary_path}")


if __name__ == "__main__":
    main()
