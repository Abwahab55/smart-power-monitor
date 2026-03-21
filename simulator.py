"""
Smart Power Monitor — Edge Device Simulator
Simulates a power sensor (voltage, current, power factor) and
publishes readings to AWS IoT Core via MQTT over TLS.
"""

import json
import time
import random
import math
import argparse
from datetime import datetime, timezone

try:
    from awsiot import mqtt_connection_builder
    from awscrt import mqtt
    AWS_IOT_AVAILABLE = True
except ImportError:
    AWS_IOT_AVAILABLE = False
    print("[WARNING] awsiotsdk not installed — running in LOCAL mode (print only)")


# ── Configuration ────────────────────────────────────────────────────────────

TOPIC        = "power/monitor/data"
ALERT_TOPIC  = "power/monitor/alerts"
CLIENT_ID    = "smart-power-monitor-device-01"
PUBLISH_INTERVAL = 5  # seconds between readings

EQUIPMENT_PROFILES = {
    "general_load": {"nominal_voltage": 230.0, "nominal_current": 10.0},
    "facility_hvac": {"nominal_voltage": 230.0, "nominal_current": 12.0},
    "industrial_pump": {"nominal_voltage": 230.0, "nominal_current": 14.0},
    "lighting_panel": {"nominal_voltage": 230.0, "nominal_current": 4.0},
}


# ── Sensor Simulation ────────────────────────────────────────────────────────

class PowerSensorSimulator:
    """Simulates a 3-phase power monitoring sensor."""

    def __init__(self, nominal_voltage=230.0, nominal_current=10.0, equipment_profile="general_load"):
        self.nominal_voltage = nominal_voltage
        self.nominal_current = nominal_current
        self.equipment_profile = equipment_profile
        self.t = 0

    def _add_noise(self, value, noise_pct=0.02):
        return value * (1 + random.uniform(-noise_pct, noise_pct))

    def _inject_anomaly(self):
        """Randomly inject a fault event (5% probability)."""
        return random.random() < 0.05

    def read(self):
        self.t += 1
        anomaly = self._inject_anomaly()

        voltage = self._add_noise(self.nominal_voltage)
        current = self._add_noise(self.nominal_current)

        if anomaly:
            fault_type = random.choice(["overvoltage", "overcurrent", "undervoltage"])
            if fault_type == "overvoltage":
                voltage *= random.uniform(1.15, 1.30)
            elif fault_type == "overcurrent":
                current *= random.uniform(1.20, 1.50)
            elif fault_type == "undervoltage":
                voltage *= random.uniform(0.70, 0.85)
        else:
            fault_type = None

        power_factor = self._add_noise(0.92, noise_pct=0.03)
        apparent_power = voltage * current
        active_power   = apparent_power * power_factor
        reactive_power = math.sqrt(max(apparent_power**2 - active_power**2, 0))
        frequency      = self._add_noise(50.0, noise_pct=0.005)
        temperature    = self._add_noise(45.0 + current * 0.5)

        return {
            "device_id":       CLIENT_ID,
            "equipment_profile": self.equipment_profile,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "voltage_v":       round(voltage, 3),
            "current_a":       round(current, 3),
            "active_power_w":  round(active_power, 2),
            "apparent_power_va": round(apparent_power, 2),
            "reactive_power_var": round(reactive_power, 2),
            "power_factor":    round(power_factor, 4),
            "frequency_hz":    round(frequency, 3),
            "temperature_c":   round(temperature, 2),
            "anomaly":         anomaly,
            "fault_type":      fault_type,
        }


# ── MQTT Publisher ───────────────────────────────────────────────────────────

class IoTPublisher:

    def __init__(self, endpoint, cert, key, ca, client_id):
        self.connection = mqtt_connection_builder.mtls_from_path(
            endpoint=endpoint,
            cert_filepath=cert,
            pri_key_filepath=key,
            ca_filepath=ca,
            client_id=client_id,
            clean_session=False,
            keep_alive_secs=30,
        )

    def connect(self):
        print("[IoT] Connecting to AWS IoT Core...")
        future = self.connection.connect()
        future.result()
        print("[IoT] Connected.")

    def publish(self, topic, payload):
        self.connection.publish(
            topic=topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )

    def disconnect(self):
        self.connection.disconnect().result()


# ── Main Loop ────────────────────────────────────────────────────────────────

def write_output_line(output_file, reading):
    if output_file:
        output_file.write(json.dumps(reading) + "\n")
        output_file.flush()


def run_local(interval, count=0, output_path=None, profile="general_load"):
    """Run without AWS — prints readings to console."""
    cfg = EQUIPMENT_PROFILES[profile]
    sensor = PowerSensorSimulator(
        nominal_voltage=cfg["nominal_voltage"],
        nominal_current=cfg["nominal_current"],
        equipment_profile=profile,
    )
    generated = 0
    output_file = open(output_path, "a", encoding="utf-8") if output_path else None

    print(f"[LOCAL] Publishing every {interval}s. Press Ctrl+C to stop.\n")
    if output_path:
        print(f"[LOCAL] Writing JSONL output to: {output_path}\n")

    try:
        while count <= 0 or generated < count:
            reading = sensor.read()
            write_output_line(output_file, reading)
            print(json.dumps(reading, indent=2))
            if reading["anomaly"]:
                print(f"  *** ALERT: {reading['fault_type'].upper()} detected! ***")
            generated += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[LOCAL] Stopped.")
    finally:
        if output_file:
            output_file.close()
        if count > 0:
            print(f"[LOCAL] Completed {generated}/{count} readings.")


def run_aws(endpoint, cert, key, ca, interval, count=0, output_path=None, profile="general_load"):
    """Publish to AWS IoT Core via MQTT."""
    cfg = EQUIPMENT_PROFILES[profile]
    sensor = PowerSensorSimulator(
        nominal_voltage=cfg["nominal_voltage"],
        nominal_current=cfg["nominal_current"],
        equipment_profile=profile,
    )
    publisher = IoTPublisher(endpoint, cert, key, ca, CLIENT_ID)
    generated = 0
    output_file = open(output_path, "a", encoding="utf-8") if output_path else None
    publisher.connect()

    print(f"[IoT] Publishing to '{TOPIC}' every {interval}s. Ctrl+C to stop.\n")
    if output_path:
        print(f"[IoT] Writing JSONL output to: {output_path}\n")

    try:
        while count <= 0 or generated < count:
            reading = sensor.read()
            publisher.publish(TOPIC, reading)
            write_output_line(output_file, reading)
            print(f"[{reading['timestamp']}] V={reading['voltage_v']}V "
                  f"I={reading['current_a']}A P={reading['active_power_w']}W "
                  f"{'*** FAULT: ' + str(reading['fault_type']) + ' ***' if reading['anomaly'] else ''}")
            generated += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[IoT] Stopping...")
    finally:
        if output_file:
            output_file.close()
        publisher.disconnect()
        if count > 0:
            print(f"[IoT] Completed {generated}/{count} readings.")


def main():
    parser = argparse.ArgumentParser(description="Smart Power Monitor — Device Simulator")
    parser.add_argument("--endpoint",  help="AWS IoT Core endpoint")
    parser.add_argument("--cert",      help="Path to device certificate (.pem)")
    parser.add_argument("--key",       help="Path to private key (.pem)")
    parser.add_argument("--ca",        help="Path to root CA certificate")
    parser.add_argument("--interval",  type=int, default=PUBLISH_INTERVAL)
    parser.add_argument("--count",     type=int, default=0,
                        help="Number of readings to publish before exiting (0 = infinite)")
    parser.add_argument("--output",    help="Optional JSONL output file path")
    parser.add_argument(
        "--profile",
        choices=sorted(EQUIPMENT_PROFILES.keys()),
        default="general_load",
        help="Equipment profile for industrial/facility simulation",
    )
    parser.add_argument("--local",     action="store_true", help="Run in local mode (no AWS)")
    args = parser.parse_args()

    if args.local or not AWS_IOT_AVAILABLE:
        run_local(args.interval, args.count, args.output, args.profile)
    else:
        if not all([args.endpoint, args.cert, args.key, args.ca]):
            print("ERROR: Provide --endpoint, --cert, --key, --ca for AWS mode.")
            print("       Or run with --local for offline testing.")
            return
        run_aws(args.endpoint, args.cert, args.key, args.ca, args.interval, args.count, args.output, args.profile)


if __name__ == "__main__":
    main()
