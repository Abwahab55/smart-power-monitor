from simulator import PowerSensorSimulator


def test_sensor_read_contains_expected_fields():
    sensor = PowerSensorSimulator()
    reading = sensor.read()

    expected = {
        "device_id",
        "timestamp",
        "voltage_v",
        "current_a",
        "active_power_w",
        "apparent_power_va",
        "reactive_power_var",
        "power_factor",
        "frequency_hz",
        "temperature_c",
        "anomaly",
        "fault_type",
    }
    assert expected.issubset(reading.keys())


def test_sensor_numeric_ranges_are_reasonable_for_nominal_case():
    sensor = PowerSensorSimulator()
    reading = sensor.read()

    assert 150.0 <= reading["voltage_v"] <= 310.0
    assert 0.0 <= reading["current_a"] <= 20.0
    assert 0.7 <= reading["power_factor"] <= 1.1
    assert 48.0 <= reading["frequency_hz"] <= 52.0
