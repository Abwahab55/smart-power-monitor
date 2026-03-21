from visualize_readings import (
    build_comparison,
    build_summary,
    downsample,
    rolling_average,
)


def sample_rows():
    return [
        {
            "voltage_v": 230.0,
            "current_a": 10.0,
            "active_power_w": 2100.0,
            "temperature_c": 49.5,
            "power_factor": 0.92,
            "frequency_hz": 50.0,
            "anomaly": False,
            "fault_type": None,
        },
        {
            "voltage_v": 225.0,
            "current_a": 10.2,
            "active_power_w": 2200.0,
            "temperature_c": 50.2,
            "power_factor": 0.91,
            "frequency_hz": 49.9,
            "anomaly": True,
            "fault_type": "overcurrent",
        },
        {
            "voltage_v": 228.0,
            "current_a": 9.9,
            "active_power_w": 2050.0,
            "temperature_c": 49.8,
            "power_factor": 0.93,
            "frequency_hz": 50.1,
            "anomaly": False,
            "fault_type": None,
        },
    ]


def test_build_summary_counts_and_keys():
    summary = build_summary(sample_rows())
    assert summary["reading_count"] == 3
    assert summary["anomaly_count"] == 1
    assert "voltage_v" in summary
    assert "fault_type_counts" in summary


def test_build_comparison_has_sections():
    comp = build_comparison(sample_rows())
    assert "normal_vs_anomaly" in comp
    assert "first_half_vs_second_half" in comp
    assert "active_power_w" in comp["normal_vs_anomaly"]


def test_rolling_average_and_downsample_behaviors():
    values = [1, 2, 3, 4, 5]
    rolled = rolling_average(values, 3)
    assert len(rolled) == 5
    assert round(rolled[-1], 3) == 4.0

    idx = [1, 2, 3, 4, 5, 6]
    ds_idx, ds_values = downsample(idx, idx, 3)
    assert len(ds_idx) <= 4
    assert ds_idx[-1] == 6
    assert ds_values[-1] == 6
