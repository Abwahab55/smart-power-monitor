"""Smart Power Monitor - Output Visualizer for large datasets.

Reads JSONL telemetry and generates multiple artifacts:
  1) Time-series dashboard (with rolling averages)
  2) Distribution dashboard
  3) Comparison dashboard (normal vs anomaly, first-half vs second-half)
  4) Summary JSON
  5) Comparison JSON

Usage:
  python visualize_readings.py \
    --input sample_readings.jsonl \
    --output-dir output \
    --prefix readings
"""

import argparse
import json
from collections import Counter, deque
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt


THRESHOLDS = {
    "voltage_v": (207.0, 253.0),
    "current_a": (0.0, 16.0),
    "power_factor": (0.80, 1.0),
    "frequency_hz": (49.5, 50.5),
    "temperature_c": (0.0, 85.0),
}


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


def stats(values, digits=3):
    if not values:
        return {"avg": None, "min": None, "max": None}
    return {
        "avg": round(mean(values), digits),
        "min": round(min(values), digits),
        "max": round(max(values), digits),
    }


def rolling_average(values, window):
    if window <= 1:
        return values[:]
    q = deque()
    s = 0.0
    out = []
    for v in values:
        q.append(v)
        s += v
        if len(q) > window:
            s -= q.popleft()
        out.append(s / len(q))
    return out


def downsample(idx, values, max_points):
    if len(values) <= max_points:
        return idx, values
    step = max(1, len(values) // max_points)
    ds_idx = idx[::step]
    ds_vals = values[::step]
    if ds_idx[-1] != idx[-1]:
        ds_idx.append(idx[-1])
        ds_vals.append(values[-1])
    return ds_idx, ds_vals


def split_anomaly(rows):
    normal = [r for r in rows if not r.get("anomaly")]
    anomaly = [r for r in rows if r.get("anomaly")]
    return normal, anomaly


def percent_change(old, new):
    if old in (None, 0):
        return None
    return round((new - old) * 100.0 / old, 2)


def build_summary(rows):
    normal, anomaly = split_anomaly(rows)
    fault_counts = Counter(r.get("fault_type") or "none" for r in rows)

    voltage = series(rows, "voltage_v")
    current = series(rows, "current_a")
    power = series(rows, "active_power_w")
    temp = series(rows, "temperature_c")
    pf = series(rows, "power_factor")
    freq = series(rows, "frequency_hz")

    n = max(len(rows), 1)
    return {
        "reading_count": len(rows),
        "anomaly_count": len(anomaly),
        "normal_count": len(normal),
        "anomaly_rate_pct": round(len(anomaly) * 100.0 / n, 2),
        "voltage_v": stats(voltage),
        "current_a": stats(current),
        "active_power_w": stats(power),
        "temperature_c": stats(temp),
        "power_factor": stats(pf, digits=4),
        "frequency_hz": stats(freq),
        "fault_type_counts": dict(fault_counts),
    }


def build_comparison(rows):
    normal, anomaly = split_anomaly(rows)
    half = len(rows) // 2
    first_half = rows[:half] if half > 0 else rows
    second_half = rows[half:] if half > 0 else rows

    metrics = ["voltage_v", "current_a", "active_power_w", "temperature_c", "power_factor"]
    normal_vs_anomaly = {}
    first_vs_second = {}

    for m in metrics:
        n_vals = series(normal, m)
        a_vals = series(anomaly, m)
        f_vals = series(first_half, m)
        s_vals = series(second_half, m)

        n_avg = round(mean(n_vals), 4) if n_vals else None
        a_avg = round(mean(a_vals), 4) if a_vals else None
        f_avg = round(mean(f_vals), 4) if f_vals else None
        s_avg = round(mean(s_vals), 4) if s_vals else None

        normal_vs_anomaly[m] = {
            "normal_avg": n_avg,
            "anomaly_avg": a_avg,
            "delta": round(a_avg - n_avg, 4) if n_avg is not None and a_avg is not None else None,
        }
        first_vs_second[m] = {
            "first_half_avg": f_avg,
            "second_half_avg": s_avg,
            "pct_change": percent_change(f_avg, s_avg) if f_avg is not None and s_avg is not None else None,
        }

    return {
        "normal_vs_anomaly": normal_vs_anomaly,
        "first_half_vs_second_half": first_vs_second,
    }


def plot_timeseries(rows, path, title, window, max_points):
    idx = list(range(1, len(rows) + 1))
    metrics = [
        ("voltage_v", "Voltage (V)", "#1f77b4"),
        ("current_a", "Current (A)", "#ff7f0e"),
        ("active_power_w", "Active Power (W)", "#2ca02c"),
        ("temperature_c", "Temperature (C)", "#d62728"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), dpi=120)
    fig.suptitle(f"{title} - Time Series", fontsize=14, fontweight="bold")

    for ax, (key, label, color) in zip(axes.flat, metrics):
        vals = series(rows, key)
        smooth = rolling_average(vals, window)
        ds_idx, ds_vals = downsample(idx, vals, max_points)
        ds_idx2, ds_smooth = downsample(idx, smooth, max_points)

        ax.plot(ds_idx, ds_vals, color=color, linewidth=1.2, alpha=0.35, label="Raw")
        ax.plot(ds_idx2, ds_smooth, color=color, linewidth=2.0, label=f"Rolling avg ({window})")

        if key in THRESHOLDS:
            mn, mx = THRESHOLDS[key]
            ax.axhline(mn, color="#888888", linestyle="--", linewidth=0.9)
            ax.axhline(mx, color="#888888", linestyle="--", linewidth=0.9)

        ax.set_title(label)
        ax.set_xlabel("Reading #")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", fontsize=8)

    plt.tight_layout(rect=(0, 0, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_distributions(rows, path, title):
    metrics = [
        ("voltage_v", "Voltage (V)", "#1f77b4"),
        ("current_a", "Current (A)", "#ff7f0e"),
        ("active_power_w", "Active Power (W)", "#2ca02c"),
        ("temperature_c", "Temperature (C)", "#d62728"),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), dpi=120)
    fig.suptitle(f"{title} - Distributions", fontsize=14, fontweight="bold")

    for ax, (key, label, color) in zip(axes.flat, metrics):
        vals = series(rows, key)
        ax.hist(vals, bins=25, color=color, alpha=0.7, edgecolor="white")
        if key in THRESHOLDS:
            mn, mx = THRESHOLDS[key]
            ax.axvline(mn, color="#666666", linestyle="--", linewidth=1.0)
            ax.axvline(mx, color="#666666", linestyle="--", linewidth=1.0)
        ax.set_title(label)
        ax.set_xlabel("Value")
        ax.set_ylabel("Count")
        ax.grid(alpha=0.25)

    plt.tight_layout(rect=(0, 0, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_comparisons(rows, path, title):
    normal, anomaly = split_anomaly(rows)
    half = len(rows) // 2
    first_half = rows[:half] if half > 0 else rows
    second_half = rows[half:] if half > 0 else rows

    metrics = ["voltage_v", "current_a", "active_power_w", "temperature_c"]
    metric_labels = ["Voltage", "Current", "Power", "Temp"]

    normal_avgs = [mean(series(normal, m)) if normal else 0.0 for m in metrics]
    anomaly_avgs = [mean(series(anomaly, m)) if anomaly else 0.0 for m in metrics]
    first_avgs = [mean(series(first_half, m)) if first_half else 0.0 for m in metrics]
    second_avgs = [mean(series(second_half, m)) if second_half else 0.0 for m in metrics]

    fault_counts = Counter(r.get("fault_type") or "none" for r in rows if r.get("anomaly"))
    if not fault_counts:
        fault_counts = Counter({"none": 1})

    x = list(range(len(metrics)))
    width = 0.35

    fig, axes = plt.subplots(1, 3, figsize=(16, 5), dpi=120)
    fig.suptitle(f"{title} - Comparisons", fontsize=14, fontweight="bold")

    ax = axes[0]
    ax.bar([i - width / 2 for i in x], normal_avgs, width=width, label="Normal", color="#4c78a8")
    ax.bar([i + width / 2 for i in x], anomaly_avgs, width=width, label="Anomaly", color="#e45756")
    ax.set_title("Normal vs Anomaly Average")
    ax.set_xticks(x)
    ax.set_xticklabels(metric_labels)
    ax.grid(alpha=0.25, axis="y")
    ax.legend()

    ax = axes[1]
    ax.bar([i - width / 2 for i in x], first_avgs, width=width, label="First Half", color="#72b7b2")
    ax.bar([i + width / 2 for i in x], second_avgs, width=width, label="Second Half", color="#f58518")
    ax.set_title("First Half vs Second Half")
    ax.set_xticks(x)
    ax.set_xticklabels(metric_labels)
    ax.grid(alpha=0.25, axis="y")
    ax.legend()

    ax = axes[2]
    names = list(fault_counts.keys())
    vals = list(fault_counts.values())
    ax.bar(names, vals, color="#54a24b")
    ax.set_title("Anomaly Fault Type Counts")
    ax.set_ylabel("Count")
    ax.grid(alpha=0.25, axis="y")
    for tick in ax.get_xticklabels():
        tick.set_rotation(20)

    plt.tight_layout(rect=(0, 0, 1, 0.95))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Visualize Smart Power Monitor JSONL output")
    parser.add_argument("--input", required=True, help="Input JSONL file path")
    parser.add_argument("--output-dir", default="output", help="Directory for generated artifacts")
    parser.add_argument("--prefix", default="readings", help="Prefix for output artifact filenames")
    parser.add_argument("--title", default="Smart Power Monitor - Telemetry Overview", help="Chart title")
    parser.add_argument("--window", type=int, default=20, help="Rolling window size for time series smoothing")
    parser.add_argument("--max-points", type=int, default=1200, help="Maximum points plotted per series")
    # Backward-compatibility flags.
    parser.add_argument("--chart", help="Optional legacy single dashboard output path")
    parser.add_argument("--summary", help="Optional legacy summary JSON output path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    rows = read_jsonl(input_path)
    if not rows:
        raise ValueError("Input JSONL is empty")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timeseries_path = output_dir / f"{args.prefix}_timeseries.png"
    distributions_path = output_dir / f"{args.prefix}_distributions.png"
    comparison_path = output_dir / f"{args.prefix}_comparison.png"
    summary_path = output_dir / f"{args.prefix}_summary.json"
    comparison_json_path = output_dir / f"{args.prefix}_comparison.json"

    plot_timeseries(rows, timeseries_path, args.title, max(1, args.window), max(50, args.max_points))
    plot_distributions(rows, distributions_path, args.title)
    plot_comparisons(rows, comparison_path, args.title)

    summary = build_summary(rows)
    comparison = build_comparison(rows)
    write_json(summary_path, summary)
    write_json(comparison_json_path, comparison)

    if args.chart:
        plot_timeseries(rows, Path(args.chart), args.title, max(1, args.window), max(50, args.max_points))
    if args.summary:
        write_json(Path(args.summary), summary)

    print(f"[OK] Time series chart: {timeseries_path}")
    print(f"[OK] Distribution chart: {distributions_path}")
    print(f"[OK] Comparison chart: {comparison_path}")
    print(f"[OK] Summary JSON: {summary_path}")
    print(f"[OK] Comparison JSON: {comparison_json_path}")


if __name__ == "__main__":
    main()
